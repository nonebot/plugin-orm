import sys
from pathlib import Path
from typing import Any, Union, Optional

from sqlalchemy import URL
from nonebot import get_driver
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from .migrate import AlembicConfig

if sys.version_info >= (3, 9):
    Dict = dict
else:
    from typing import Dict

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

    alembic_config: Union[Path, AlembicConfig, None] = None
    alembic_script_location: Optional[Path] = None
    alembic_version_locations: Union[Path, Dict[str, Path], None] = None
    alembic_context: Dict[str, Any] = {}
    alembic_startup_check: bool = True


plugin_config = Config.parse_obj(get_driver().config)
