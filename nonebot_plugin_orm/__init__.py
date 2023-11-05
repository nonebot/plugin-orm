from __future__ import annotations

import sys
import logging
from asyncio import gather
from operator import methodcaller
from typing import Any, AsyncGenerator
from functools import wraps, partial, lru_cache

from nonebot.params import Depends
import sqlalchemy.ext.asyncio as sa_async
from sqlalchemy.util import greenlet_spawn
from nonebot.matcher import current_matcher
from nonebot.plugin import Plugin, PluginMetadata
from sqlalchemy import URL, Table, MetaData, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from nonebot import logger, require, get_driver, get_plugin_by_module_name

from . import migrate
from .config import Config
from .utils import LoguruHandler, StreamToLogger

if sys.version_info >= (3, 10):
    from typing import Annotated
else:
    from typing_extensions import Annotated

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
    "ORMParam",
    # config
    "Config",
    "config",
    # migrate
    "AlembicConfig",
    "list_templates",
    "init",
    "revision",
    "check",
    "merge",
    "upgrade",
    "downgrade",
    "sync",
    "show",
    "history",
    "heads",
    "branches",
    "current",
    "stamp",
    "edit",
    "ensure_version",
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
_session_factory: sa_async.async_sessionmaker[AsyncSession]

_driver = get_driver()


@_driver.on_startup
async def init_orm() -> None:
    _init_orm()

    with migrate.AlembicConfig(stdout=StreamToLogger()) as alembic_config:
        if plugin_config.alembic_startup_check:
            try:
                await greenlet_spawn(migrate.check, alembic_config)
            except click.UsageError:
                logger.error("启动检查失败")
                raise
        else:
            logger.warning("跳过启动检查, 正在同步数据库模式...")
            await greenlet_spawn(migrate.sync, alembic_config)


def _init_orm():
    global _session_factory

    _init_engines()
    _init_table()
    _session_factory = sa_async.async_sessionmaker(
        **{
            **dict(bind=_engines[""], binds=_binds),
            **plugin_config.sqlalchemy_session_options,
        }
    )


@wraps(lambda: None)  # NOTE: for dependency injection
def get_session(**local_kw: Any) -> sa_async.AsyncSession:
    try:
        return _session_factory(**local_kw)
    except NameError:
        raise RuntimeError("nonebot-plugin-orm 未初始化") from None


AsyncSession = Annotated[sa_async.AsyncSession, Depends(get_session)]


async def get_scoped_session() -> (
    AsyncGenerator[sa_async.async_scoped_session[AsyncSession], None]
):
    try:
        scoped_session = async_scoped_session(
            _session_factory, scopefunc=partial(current_matcher.get, None)
        )
        yield scoped_session
    except NameError:
        raise RuntimeError("nonebot-plugin-orm 未初始化") from None

    await gather(*map(methodcaller("close"), scoped_session.registry.registry.values()))


async_scoped_session = Annotated[
    sa_async.async_scoped_session[sa_async.AsyncSession], Depends(get_scoped_session)
]


def _create_engine(engine: str | URL | dict[str, Any] | AsyncEngine) -> AsyncEngine:
    if isinstance(engine, AsyncEngine):
        return engine

    options = plugin_config.sqlalchemy_engine_options.copy()

    if plugin_config.sqlalchemy_echo:
        options["echo"] = options["echo_pool"] = True

    if isinstance(engine, dict):
        url: str | URL = engine.pop("url")
        options.update(engine)
    else:
        url = engine

    return create_async_engine(make_url(url), **options)


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
        require("nonebot_plugin_localstore")
        from nonebot_plugin_localstore import get_data_file
    except (ImportError, RuntimeError):
        raise ValueError(
            '必须指定一个默认数据库 (SQLALCHEMY_DATABASE_URL 或 SQLALCHEMY_BINDS[""]). '
            "可以通过 `pip install nonebot-plugin-orm[default]` 获得开箱即用的数据库配置."
        ) from None

    _engines[""] = _create_engine(
        f"sqlite+aiosqlite:///{get_data_file(__plugin_meta__.name, 'db.sqlite3')}"
    )


def _init_table():
    global _binds, _metadatas, _plugins

    _binds = {}
    _plugins = {}

    _get_plugin_by_module_name = lru_cache(None)(get_plugin_by_module_name)
    for model in get_subclasses(Model):
        table: Table | None = getattr(model, "__table__", None)

        if table is None or (bind_key := table.info.get("bind_key")) is None:
            continue

        if plugin := _get_plugin_by_module_name(model.__module__):
            _plugins[plugin.name.replace("-", "_")] = plugin

        _binds[model] = _engines.get(bind_key, _engines[""])
        table.to_metadata(_metadatas.get(bind_key, _metadatas[""]))


def _init_logger():
    log_level = _driver.config.log_level
    if isinstance(log_level, str):
        log_level = logger.level(log_level).no

    echo_log_level = log_level if plugin_config.sqlalchemy_echo else logging.WARNING

    levels = {
        "alembic": log_level,
        "sqlalchemy": log_level,
        **{
            _qual_logger_name_for_cls(cls): echo_log_level
            for cls in get_subclasses(Identified)
        },
    }

    handler = LoguruHandler()
    for name, level in levels.items():
        l = logging.getLogger(name)
        l.addHandler(handler)
        l.setLevel(level)


from .model import *
from .param import *
from .config import *
from .migrate import *

_init_logger()
