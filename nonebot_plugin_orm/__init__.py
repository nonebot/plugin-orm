from functools import wraps
from typing import Any, Type, Union, TypeVar, Optional, AsyncGenerator

from nonebot import get_driver
from nonebot.plugin import PluginMetadata
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="nonebot-plugin-orm",
    description="SQLAlchemy ORM support for nonebot",
    usage="https://github.com/nonebot/plugin-orm",
    type="library",
    homepage="https://github.com/nonebot/plugin-orm",
    config=Config,
)

_AS = TypeVar("_AS", bound=AsyncSession)

_driver = get_driver()
_engine: AsyncEngine
_sessionmaker: async_sessionmaker[AsyncSession]

global_config = _driver.config
config = Config.parse_obj(global_config)


def get_engine() -> AsyncEngine:
    try:
        return _engine
    except NameError:
        raise ValueError("nonebot-plugin-orm has not been initialized") from None


@wraps(lambda: None)
async def get_session(
    *, class_: Optional[Type[_AS]] = None, **kwargs: Any
) -> AsyncGenerator[Union[_AS, AsyncSession], None]:
    try:
        session = (
            _sessionmaker(**kwargs)
            if class_ is None
            else class_(_engine, **{**config.sqlalchemy_session_options, **kwargs})
        )
    except NameError:
        raise ValueError("nonebot-plugin-orm has not been initialized") from None

    async with session:
        yield session


@_driver.on_startup
async def _() -> None:
    global _engine, _sessionmaker

    _engine = create_async_engine(
        config.sqlalchemy_database_uri,
        **{
            **config.sqlalchemy_engine_options,
            "echo": config.sqlalchemy_echo,
            "echo_pool": config.sqlalchemy_echo,
        },
    )
    _sessionmaker = async_sessionmaker(_engine, **config.sqlalchemy_session_options)

    # TODO: `alembic check` at startup


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
    "scalar_one_or_none",
)
