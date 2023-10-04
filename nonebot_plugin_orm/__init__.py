from __future__ import annotations

from typing import Any
from functools import partial
from collections import defaultdict
from contextlib import AsyncExitStack

from nonebot import logger, get_driver
from nonebot.plugin import PluginMetadata
from sqlalchemy.util import greenlet_spawn
from nonebot.matcher import current_matcher
from sqlalchemy import URL, Table, MetaData, StaticPool, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
    async_scoped_session,
)

from . import migrate
from .model import Model
from .config import Config, config

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


_binds: dict[type[Model], AsyncEngine] = None  # type:ignore
_engines: dict[str, AsyncEngine] = None  # type: ignore
_metadatas: dict[str, MetaData] = None  # type: ignore
_session_factory: async_sessionmaker = None  # type: ignore


async def init_orm():
    global _session_factory

    _init_engines()
    _init_table()
    _session_factory = async_sessionmaker(
        _engines[""], binds=_binds, **config.sqlalchemy_session_options
    )

    with migrate.AlembicConfig() as alembic_config:
        if config.alembic_startup_check:
            await greenlet_spawn(migrate.check, alembic_config)
        else:
            logger.warning("跳过 ORM 启动检查，直接创建所有表并标记数据库为最新修订版本")

            async with AsyncExitStack() as stack:
                for name, engine in _engines.items():
                    connection = await stack.enter_async_context(engine.begin())
                    await connection.run_sync(_metadatas[name].create_all)

                await greenlet_spawn(migrate.stamp, alembic_config)


def get_scoped_session() -> async_scoped_session[AsyncSession]:
    return async_scoped_session(
        _session_factory, scopefunc=partial(current_matcher.get, None)
    )


def _create_engine(engine: str | URL | dict[str, Any] | AsyncEngine) -> AsyncEngine:
    if isinstance(engine, AsyncEngine):
        return engine

    options = config.sqlalchemy_engine_options.copy()

    if config.sqlalchemy_echo:
        options["echo"] = options["echo_pool"] = True

    if isinstance(engine, dict):
        url: str | URL = engine.pop("url")
        options.update(engine)
    else:
        url = engine

    return create_async_engine(make_url(url), **options)


def _init_engines():
    global _engines

    _engines = {
        name: _create_engine(engine) for name, engine in config.sqlalchemy_binds.items()
    }

    if config.sqlalchemy_database_url:
        _engines[""] = _create_engine(config.sqlalchemy_database_url)
        return

    try:
        import aiosqlite
        from nonebot_plugin_localstore import get_data_file

        del aiosqlite
    except ImportError:
        raise ValueError(
            "必须指定一个默认数据库引擎 (SQLALCHEMY_DATABASE_URL 或 SQLALCHEMY_BINDS[''])"
        ) from None

    _engines[""] = create_async_engine(
        f"sqlite+aiosqlite:///{get_data_file(__plugin_meta__.name, 'db.sqlite3')}"
    )


def _init_table():
    global _binds, _metadatas

    _binds = {}

    if len(_engines) == 1:  # NOTE: common case: only default engine
        _metadatas = {"": Model.metadata}
        return

    _metadatas = defaultdict(MetaData)

    for model in Model.__subclasses__():
        table: Table | None = getattr(model, "__table__", None)

        if table is None or (bind_key := table.info.get("bind_key")) is None:
            continue

        _binds[model] = _engines.get(bind_key, _engines[""])
        table.to_metadata(_metadatas.get(bind_key, _metadatas[""]))

    _metadatas = dict(_metadatas)


from .sql import *
from .model import *
from .config import *
from .migrate import *
