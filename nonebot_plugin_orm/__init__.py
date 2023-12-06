from __future__ import annotations

import sys
import logging
from typing import Any
from argparse import Namespace
from contextlib import suppress
from functools import wraps, lru_cache

import click
from nonebot.rule import Rule
from nonebot.adapters import Event
import sqlalchemy.ext.asyncio as sa_async
from nonebot.permission import Permission
from sqlalchemy import URL, Table, MetaData
from nonebot.params import Depends, DefaultParam
from nonebot.plugin import Plugin, PluginMetadata
from sqlalchemy.util import ScopedRegistry, greenlet_spawn
from sqlalchemy.log import Identified, _qual_logger_name_for_cls
from nonebot.message import run_postprocessor, event_postprocessor
from nonebot.matcher import Matcher, current_event, current_matcher
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from nonebot import logger, require, get_driver, get_plugin_by_module_name

from . import migrate
from .param import ORMParam
from .config import Config, plugin_config
from .utils import LoguruHandler, StreamToLogger, get_subclasses

if sys.version_info >= (3, 9):
    from typing import Annotated
else:
    from typing_extensions import Annotated

require("nonebot_plugin_localstore")
from nonebot_plugin_localstore import get_data_dir

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
_scoped_sessions: ScopedRegistry[sa_async.async_scoped_session[sa_async.AsyncSession]]

_data_dir = get_data_dir(__plugin_meta__.name)
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
            except click.UsageError:
                logger.error("启动检查失败")
                raise
        else:
            logger.warning("跳过启动检查, 正在同步数据库模式...")
            cmd_opts.cmd = (migrate.sync, ["revision"], [])
            await greenlet_spawn(migrate.sync, alembic_config)


def _init_orm():
    global _session_factory, _scoped_sessions

    _init_engines()
    _init_table()
    _session_factory = sa_async.async_sessionmaker(
        **{
            **dict(bind=_engines[""], binds=_binds),
            **plugin_config.sqlalchemy_session_options,
        }
    )
    _scoped_sessions = ScopedRegistry(
        lambda: sa_async.async_scoped_session(
            _session_factory, lambda: current_matcher.get(None)
        ),
        lambda: id(current_event.get(None)),
    )

    # XXX: workaround for https://github.com/nonebot/nonebot2/issues/2475
    event_postprocessor(_clear_scoped_session)
    run_postprocessor(_close_scoped_session)


@wraps(lambda: None)  # NOTE: for dependency injection
def get_session(**local_kw: Any) -> sa_async.AsyncSession:
    try:
        return _session_factory(**local_kw)
    except NameError:
        raise RuntimeError("nonebot-plugin-orm 未初始化") from None


AsyncSession = Annotated[sa_async.AsyncSession, Depends(get_session)]


async def get_scoped_session() -> sa_async.async_scoped_session[sa_async.AsyncSession]:
    try:
        return _scoped_sessions()
    except NameError:
        raise RuntimeError("nonebot-plugin-orm 未初始化") from None


async_scoped_session = Annotated[
    sa_async.async_scoped_session[sa_async.AsyncSession], Depends(get_scoped_session)
]


# @event_postprocessor
def _clear_scoped_session(event: Event) -> None:
    with suppress(KeyError):
        del _scoped_sessions.registry[id(event)]


# @run_postprocessor
async def _close_scoped_session(event: Event, matcher: Matcher) -> None:
    with suppress(KeyError):
        session: sa_async.AsyncSession = _scoped_sessions.registry[
            id(event)
        ].registry.registry[matcher]
        del _scoped_sessions.registry[id(event)].registry.registry[matcher]
        await session.close()


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

    _engines[""] = _create_engine(f"sqlite+aiosqlite:///{_data_dir / 'db.sqlite3'}")


def _init_table():
    global _binds, _metadatas, _plugins

    _binds = {}
    _plugins = {}

    _get_plugin_by_module_name = lru_cache(None)(get_plugin_by_module_name)
    for model in set(get_subclasses(Model)):
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
            for cls in set(get_subclasses(Identified))
        },
    }

    handler = LoguruHandler()
    for name, level in levels.items():
        l = logging.getLogger(name)
        l.addHandler(handler)
        l.setLevel(level)


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
