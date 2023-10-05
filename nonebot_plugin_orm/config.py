import os
from typing import TYPE_CHECKING, Any, Union

from sqlalchemy import URL
from nonebot import get_driver
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

if TYPE_CHECKING:
    from .migrate import AlembicConfig

__all__ = (
    "Config",
    "config",
)


class Config(BaseModel, arbitrary_types_allowed=True):
    sqlalchemy_database_url: Union[str, URL, AsyncEngine] = ""
    sqlalchemy_binds: dict[str, Union[str, URL, dict[str, Any], AsyncEngine]] = {}
    sqlalchemy_echo: bool = False
    sqlalchemy_engine_options: dict[str, Any] = {}
    sqlalchemy_session_options: dict[str, Any] = {}

    alembic_config: Union[str, os.PathLike[str], "AlembicConfig"] = ""
    alembic_script_location: Union[str, os.PathLike[str]] = ""
    alembic_version_locations: Union[
        str, os.PathLike[str], dict[str, os.PathLike[str]]
    ] = {}
    alembic_context: dict[str, Any] = {"render_as_batch": True}
    alembic_startup_check: bool = True


config = Config.parse_obj(get_driver().config)
