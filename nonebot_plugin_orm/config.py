from pathlib import Path
from typing import Any, Union, Optional

from sqlalchemy import URL
from pydantic import BaseModel
from nonebot import get_plugin_config
from sqlalchemy.ext.asyncio import AsyncEngine

from .migrate import AlembicConfig

__all__ = (
    "Config",
    "plugin_config",
)


class Config(BaseModel, arbitrary_types_allowed=True):
    sqlalchemy_database_url: Union[str, URL, AsyncEngine] = ""
    sqlalchemy_binds: dict[str, Union[str, URL, dict[str, Any], AsyncEngine]] = {}
    sqlalchemy_echo: bool = False
    sqlalchemy_engine_options: dict[str, Any] = {}
    sqlalchemy_session_options: dict[str, Any] = {}

    alembic_config: Union[Path, AlembicConfig, None] = None
    alembic_script_location: Optional[Path] = None
    alembic_version_locations: Union[Path, dict[str, Path], None] = None
    alembic_context: dict[str, Any] = {}
    alembic_startup_check: bool = True


plugin_config = get_plugin_config(Config)
