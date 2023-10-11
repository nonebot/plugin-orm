import os
from typing import Any, Dict, Union

from sqlalchemy import URL
from nonebot import get_driver
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from .migrate import AlembicConfig

__all__ = (
    "Config",
    "plugin_config",
)


class Config(BaseModel, arbitrary_types_allowed=True):
    sqlalchemy_database_url: Union[str, URL, AsyncEngine] = ""
    sqlalchemy_binds: Dict[str, Union[str, URL, Dict[str, Any], AsyncEngine]] = {}
    sqlalchemy_echo: bool = False
    sqlalchemy_engine_options: Dict[str, Any] = {}
    sqlalchemy_session_options: Dict[str, Any] = {}

    alembic_config: Union[str, os.PathLike[str], AlembicConfig] = ""
    alembic_script_location: Union[str, os.PathLike[str]] = ""
    alembic_version_locations: Union[
        str, os.PathLike[str], Dict[str, os.PathLike[str]]
    ] = {}
    alembic_context: Dict[str, Any] = {"render_as_batch": True}
    alembic_startup_check: bool = True


plugin_config = Config.parse_obj(get_driver().config)
