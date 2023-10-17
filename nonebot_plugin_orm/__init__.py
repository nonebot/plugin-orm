from __future__ import annotations

import sys
import logging
from asyncio import gather
from operator import methodcaller
from functools import wraps, partial
from typing import Any, AsyncGenerator

from nonebot.params import Depends
from nonebot.plugin import PluginMetadata
import sqlalchemy.ext.asyncio as sa_asyncio
from sqlalchemy.util import greenlet_spawn
from nonebot.matcher import current_matcher
from nonebot import logger, require, get_driver
from sqlalchemy import URL, Table, MetaData, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from . import migrate
from .model import Model
from .config import Config, plugin_config
from .utils import LoguruHandler, StreamToLogger

if sys.version_info >= (3, 10):
    from typing import Annotated
else:
    from typing_extensions import Annotated

__all__ = (
    # __init__
    "init_orm",
    "get_scoped_session",
    # sql
    "one",
    "all_",
    "first",
    "select",
    "scalars",
    "scalar_all",
    "scalar_one",
    "one_or_none",
    "scalar_first",
    "one_or_create",
    "scalar_one_or_none",
    # model
    "Model",
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
_session_factory: sa_asyncio.async_sessionmaker[AsyncSession]

_driver = get_driver()


@_driver.on_startup
async def init_orm() -> None:
    _init_orm()

    with migrate.AlembicConfig(stdout=StreamToLogger()) as alembic_config:
        if plugin_config.alembic_startup_check:
            await greenlet_spawn(migrate.check, alembic_config)
        else:
            logger.warning("跳过启动检查，直接创建所有表并标记数据库为最新修订版本")
            await migrate._upgrade_fast(alembic_config)
            await greenlet_spawn(migrate.stamp, alembic_config)


def _init_orm():
    global _session_factory

    _init_engines()
    _init_table()
    _session_factory = sa_asyncio.async_sessionmaker(
        _engines[""], binds=_binds, **plugin_config.sqlalchemy_session_options
    )


@wraps(lambda: None)  # NOTE: for dependency injection
def get_session(**local_kw: Any) -> sa_asyncio.AsyncSession:
    try:
        return _session_factory(**local_kw)
    except NameError:
        raise RuntimeError("nonebot-plugin-orm 未初始化") from None


AsyncSession = Annotated[sa_asyncio.AsyncSession, Depends(get_session)]


async def get_scoped_session() -> (
    AsyncGenerator[sa_asyncio.async_scoped_session[AsyncSession], None]
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
    sa_asyncio.async_scoped_session[AsyncSession], Depends(get_scoped_session)
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
    _metadatas = {}
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
            '必须指定一个默认数据库引擎 (SQLALCHEMY_DATABASE_URL 或 SQLALCHEMY_BINDS[""])'
        ) from None

    _engines[""] = create_async_engine(
        f"sqlite+aiosqlite:///{get_data_file(__plugin_meta__.name, 'db.sqlite3')}"
    )
    _metadatas[""] = MetaData()


def _init_table():
    global _binds, _metadatas

    _binds = {}

    if len(_engines) == 1:  # NOTE: common case: only default engine
        _metadatas = {"": Model.metadata}
        return

    for model in Model.__subclasses__():
        table: Table | None = getattr(model, "__table__", None)

        if table is None or (bind_key := table.info.get("bind_key")) is None:
            continue

        _binds[model] = _engines.get(bind_key, _engines[""])
        table.to_metadata(_metadatas.get(bind_key, _metadatas[""]))


def _init_logger():
    log_level = _driver.config.log_level
    if isinstance(log_level, str):
        log_level = logger.level(log_level).no

    levels = {"alembic": log_level, "sqlalchemy": log_level}
    if not plugin_config.sqlalchemy_echo:
        levels["sqlalchemy.engine"] = levels["sqlalchemy.pool"] = logging.WARNING

    handler = LoguruHandler()
    for name, level in levels.items():
        l = logging.getLogger(name)
        l.addHandler(handler)
        l.setLevel(level)


_init_logger()

from .sql import *
from .model import *
from .config import *
from .migrate import *
