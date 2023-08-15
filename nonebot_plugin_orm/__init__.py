from __future__ import annotations

from functools import partial

from sqlalchemy import URL
from nonebot import get_driver
from nonebot.plugin import PluginMetadata
from nonebot.matcher import current_matcher
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
    async_scoped_session,
)

from .config import Config
from .model import Model, _models

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


_default_bind = config.sqlalchemy_database_url or config.sqlalchemy_binds.pop(None)
if isinstance(_default_bind, (str, URL)):
    _default_bind = create_async_engine(
        _default_bind,
        **{
            **config.sqlalchemy_engine_options,
            "echo": config.sqlalchemy_echo,
            "echo_pool": config.sqlalchemy_echo,
        },
    )
_binds = {
    key: create_async_engine(
        bind,
        **{
            **config.sqlalchemy_engine_options,
            "echo": config.sqlalchemy_echo,
            "echo_pool": config.sqlalchemy_echo,
        },
    )
    if isinstance(bind, (str, URL))
    else bind
    for key, bind in config.sqlalchemy_binds.items()
    if key is not None
}
_session_factory = async_sessionmaker(
    _default_bind, **{**config.sqlalchemy_session_options, "binds": _binds}
)


async def get_scoped_session() -> async_scoped_session[AsyncSession]:
    return async_scoped_session(
        _session_factory, scopefunc=partial(current_matcher.get, None)
    )


@_driver.on_startup
async def _() -> None:
    for key, models in _models.items():
        if key is None or (bind := _binds.get(key)) is None:
            continue
        for model in models:
            _binds[model] = bind
    ...  # TODO: `alembic check` at startup


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
