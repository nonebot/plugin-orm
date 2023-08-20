from __future__ import annotations

from typing import Any

from sqlalchemy import URL
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ("Config",)


class Config(BaseModel, arbitrary_types_allowed=True):
    sqlalchemy_database_url: str | URL = ""
    sqlalchemy_binds: dict[Any, str | URL | AsyncEngine] = {}
    sqlalchemy_engine_options: dict[str, Any] = {}
    sqlalchemy_session_options: dict[str, Any] = {}
    sqlalchemy_echo: bool = False

    alembic_config: dict[str, Any] = {}
    alembic_context: dict[str, Any] = {"compare_type": True, "render_as_batch": True}
