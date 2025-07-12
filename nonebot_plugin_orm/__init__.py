from __future__ import annotations

import logging
from argparse import Namespace
from functools import cache, wraps
from collections.abc import Generator
from contextlib import contextmanager
from typing_extensions import Any, Annotated

import click
from nonebot.rule import Rule
from alembic.op import get_bind
import sqlalchemy.ext.asyncio as sa_async
from nonebot.permission import Permission
from sqlalchemy.util import greenlet_spawn
from sqlalchemy import URL, Table, MetaData
from nonebot.message import run_postprocessor
from nonebot.params import Depends, DefaultParam
from nonebot.plugin import Plugin, PluginMetadata
from sqlalchemy.log import Identified, _qual_logger_name_for_cls
from nonebot.matcher import Matcher, current_event, current_matcher
from nonebot import logger, require, get_driver, get_plugin_by_module_name
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection, create_async_engine

from . import migrate
from .param import ORMParam
from .config import Config, plugin_config
from .utils import LoguruHandler, StreamToLogger, coroutine, get_subclasses

require("nonebot_plugin_localstore")
from nonebot_plugin_localstore import get_data_dir, get_plugin_data_dir

__all__ = (
    # __init__
    "init_orm",
    "get_session",
    "AsyncSession",
    "get_scoped_session",
    "async_scoped_session",
    # model
    "Model",
    # param
    "SQLDepends",
    # config
    "Config",
    "plugin_config",
    # migrate
    "AlembicConfig",
)
__plugin_meta__ = PluginMetadata(
    name="nonebot-plugin-orm",
    description="SQLAlchemy ORM support for nonebot",
    usage="https://github.com/nonebot/plugin-orm",
    type="library",
    homepage="https://github.com/nonebot/plugin-orm",
    config=Config,
)

_binds: dict[type[Model], AsyncEngine]
_engines: dict[str, AsyncEngine]
_metadatas: dict[str, MetaData]
_plugins: dict[str, Plugin]
_session_factory: sa_async.async_sessionmaker[sa_async.AsyncSession]
_scoped_sessions: sa_async.async_scoped_session[sa_async.AsyncSession]

_data_dir = get_plugin_data_dir()
if (
    _deprecated_data_dir := get_data_dir(None) / "nonebot-plugin-orm"
).exists() and next(_deprecated_data_dir.iterdir(), None):
    if next(_data_dir.iterdir(), None):
        raise RuntimeError(
            "无法自动迁移数据目录, 请手动将 "
            f"{_deprecated_data_dir} 中的数据移动到 {_data_dir} 中."
        )
    _data_dir.rmdir()
    _deprecated_data_dir.rename(_data_dir)

_driver = get_driver()


@_driver.on_startup
async def init_orm() -> None:
    _init_orm()

    cmd_opts = Namespace()
    with migrate.AlembicConfig(
        stdout=StreamToLogger(), cmd_opts=cmd_opts
    ) as alembic_config:
        if plugin_config.alembic_startup_check:
            cmd_opts.cmd = (migrate.check, [], [])
            try:
                await greenlet_spawn(migrate.check, alembic_config)
            except click.UsageError as e:
                try:
                    click.confirm("目标数据库未更新到最新迁移, 是否更新?", abort=True)
                except click.Abort:
                    raise e

                cmd_opts.cmd = (migrate.upgrade, [], [])
                await greenlet_spawn(migrate.upgrade, alembic_config)
        else:
            logger.warning("跳过启动检查, 正在同步数据库模式...")
            cmd_opts.cmd = (migrate.sync, ["revision"], [])
            await greenlet_spawn(migrate.sync, alembic_config)


def get_session(**local_kw: Any) -> sa_async.AsyncSession:
    try:
        return _session_factory(**local_kw)
    except NameError:
        _init_orm()

    return _session_factory(**local_kw)


# NOTE: NoneBot DI will run sync function in thread pool executor,
# which is poor performance for this simple function, so we wrap it as a coroutine function.
AsyncSession = Annotated[
    sa_async.AsyncSession,
    Depends(coroutine(wraps(lambda: None)(get_session)), use_cache=False),
]


def get_scoped_session() -> sa_async.async_scoped_session[sa_async.AsyncSession]:
    try:
        return _scoped_sessions
    except NameError:
        _init_orm()

    return _scoped_sessions


async_scoped_session = Annotated[
    sa_async.async_scoped_session[sa_async.AsyncSession],
    Depends(coroutine(get_scoped_session)),
]


@contextmanager
def _patch_migrate_session() -> Generator[None, Any, None]:
    global _session_factory, _scoped_sessions

    session_factory, scoped_sessions = _session_factory, _scoped_sessions

    _session_factory = sa_async.async_sessionmaker(
        AsyncConnection._retrieve_proxy_for_target(get_bind()),
        **plugin_config.sqlalchemy_session_options,
    )
    _scoped_sessions = sa_async.async_scoped_session(
        _session_factory,
        lambda: (id(current_event.get(None)), current_matcher.get(None)),
    )

    yield

    _session_factory, _scoped_sessions = session_factory, scoped_sessions


def _create_engine(engine: str | URL | dict[str, Any] | AsyncEngine) -> AsyncEngine:
    if isinstance(engine, AsyncEngine):
        return engine

    options = plugin_config.sqlalchemy_engine_options.copy()

    if isinstance(engine, dict):
        url: str | URL = engine.pop("url")
        options.update(engine)
    else:
        url = engine

    return create_async_engine(url, **options)


def _init_engines():
    global _engines, _metadatas

    _engines = {}
    _metadatas = {"": MetaData()}
    for name, engine in plugin_config.sqlalchemy_binds.items():
        _engines[name] = _create_engine(engine)
        _metadatas[name] = MetaData()

    if plugin_config.sqlalchemy_database_url:
        _engines[""] = _create_engine(plugin_config.sqlalchemy_database_url)

    if "" in _engines:
        return

    try:
        import aiosqlite

        del aiosqlite
    except (ImportError, RuntimeError):
        raise ValueError(
            '必须指定一个默认数据库 (SQLALCHEMY_DATABASE_URL 或 SQLALCHEMY_BINDS[""]). '
            "可以通过 `pip install nonebot-plugin-orm[default]` 获得开箱即用的数据库配置."
        ) from None

    _engines[""] = _create_engine(
        URL.create("sqlite+aiosqlite", database=str(_data_dir / "db.sqlite3"))
    )


def _init_table():
    global _binds, _metadatas, _plugins

    _binds = {}
    _plugins = {}

    _get_plugin_by_module_name = cache(get_plugin_by_module_name)
    for model in set(get_subclasses(Model)):
        table: Table | None = getattr(model, "__table__", None)

        if table is None or (bind_key := table.info.get("bind_key")) is None:
            continue

        if plugin := _get_plugin_by_module_name(model.__module__):
            _plugins[plugin.name.replace("-", "_")] = plugin

        _binds[model] = _engines.get(bind_key, _engines[""])
        table.to_metadata(_metadatas.get(bind_key, _metadatas[""]))


def _init_orm():
    global _session_factory, _scoped_sessions

    _init_engines()
    _init_table()
    _session_factory = sa_async.async_sessionmaker(
        _engines[""], binds=_binds, **plugin_config.sqlalchemy_session_options
    )
    _scoped_sessions = sa_async.async_scoped_session(
        _session_factory,
        lambda: (id(current_event.get(None)), current_matcher.get(None)),
    )

    run_postprocessor(_scoped_sessions.remove)


def _init_logger():
    handler = LoguruHandler()
    logging.getLogger("alembic").addHandler(handler)
    logging.getLogger("sqlalchemy").addHandler(handler)

    log_level = _driver.config.log_level
    if isinstance(log_level, str):
        log_level = logger.level(log_level).no

    echo_log_level = log_level if plugin_config.sqlalchemy_echo else logging.WARNING

    levels = {
        "alembic": log_level,
        "sqlalchemy": log_level,
        **{
            _qual_logger_name_for_cls(cls): echo_log_level
            for cls in set(get_subclasses(Identified))
        },
    }

    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


_init_logger()


def _init_param():
    for cls in (Rule, Permission):
        cls.HANDLER_PARAM_TYPES.insert(-1, ORMParam)

    Matcher.HANDLER_PARAM_TYPES = Matcher.HANDLER_PARAM_TYPES[:-1] + (
        ORMParam,
        DefaultParam,
    )


_init_param()


from .model import Model as Model
from .config import Config as Config
from .param import SQLDepends as SQLDepends
from .config import plugin_config as plugin_config
from .migrate import AlembicConfig as AlembicConfig
