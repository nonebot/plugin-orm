from __future__ import annotations

from functools import partial
from contextlib import AsyncExitStack, suppress

from nonebot import logger, get_driver
from sqlalchemy import Table, MetaData
from nonebot.plugin import PluginMetadata
from sqlalchemy.util import greenlet_spawn
from nonebot.matcher import current_matcher
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    async_scoped_session,
)

from .migrate import orm
from .model import Model
from .config import Config

__plugin_meta__ = PluginMetadata(
    name="nonebot-plugin-orm",
    description="SQLAlchemy ORM support for nonebot",
    usage="https://github.com/nonebot/plugin-orm",
    type="library",
    homepage="https://github.com/nonebot/plugin-orm",
    config=Config,
)


_driver = get_driver()

global_config = _driver.config
config = Config.parse_obj(global_config)

_binds: dict[type[Model], AsyncEngine] = {}
_session_factory = async_sessionmaker(
    config.sqlalchemy_database_url,
    **{**config.sqlalchemy_session_options, "binds": _binds},
)

_metadatas: dict[str, tuple[AsyncEngine, MetaData]] = {
    name: (engine, MetaData()) for name, engine in config.sqlalchemy_binds.items()
}


def _init_orm() -> None:
    for model in Model.__subclasses__():
        table: Table | None = getattr(model, "__table__", None)

        if table is None:
            continue

        if (bind_key := table.info.get("bind_key")) is None:
            return

        engine, metadata = _metadatas.get(bind_key, _metadatas[""])
        _binds[model] = engine
        table.to_metadata(metadata)


@_driver.on_startup
async def _() -> None:
    _init_orm()

    if config.alembic_startup_check:
        try:
            await greenlet_spawn(orm, ["check"])
        except SystemExit as e:
            if e.code:
                logger.critical(
                    "ORM 启动检查失败，请迁移数据库到最新修订版本，"
                    "或配置 ALEMBIC_STARTUP_CHECK = false 以关闭启动检查"
                    "（仅用于测试目的，谨慎使用）"
                )
                raise
    else:
        logger.warning("跳过 ORM 启动检查，直接创建所有表并标记数据库为最新修订版本")

        async with AsyncExitStack() as stack:
            for engine, metadata in _metadatas.values():
                connection = await stack.enter_async_context(engine.begin())
                await connection.run_sync(metadata.create_all)

            with suppress(SystemExit):
                await greenlet_spawn(orm, ["stamp"])


def get_scoped_session() -> async_scoped_session[AsyncSession]:
    return async_scoped_session(
        _session_factory, scopefunc=partial(current_matcher.get, None)
    )


from .sql import one as one
from .sql import all_ as all_
from .sql import first as first
from .model import Model as Model
from .sql import select as select
from .sql import scalars as scalars
from .config import Config as Config
from .sql import scalar_all as scalar_all
from .sql import scalar_one as scalar_one
from .sql import one_or_none as one_or_none
from .sql import scalar_first as scalar_first
from .sql import one_or_create as one_or_create
from .sql import scalar_one_or_none as scalar_one_or_none

__all__ = (
    "one",
    "all_",
    "first",
    "Model",
    "select",
    "scalars",
    "Config",
    "scalar_all",
    "scalar_one",
    "one_or_none",
    "scalar_first",
    "one_or_create",
    "scalar_one_or_none",
)
