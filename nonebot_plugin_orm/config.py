import os
from pathlib import Path
from typing import Any, Dict, Union, Optional, cast

from pydantic import BaseModel, validator
from sqlalchemy import URL, StaticPool, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .migrate import AlembicConfig

__all__ = ("Config",)


class Config(BaseModel, arbitrary_types_allowed=True):
    sqlalchemy_echo: bool = False
    sqlalchemy_engine_options: Dict[str, Any] = {}

    @validator("sqlalchemy_engine_options", pre=True, always=True)
    def validate_sqlalchemy_engine_options(
        cls, v: Dict[str, Any], values: Dict[str, Any]
    ) -> Dict[str, Any]:
        if values["sqlalchemy_echo"]:
            v["echo"] = v["echo_pool"] = True

        return v

    sqlalchemy_binds: Dict[str, AsyncEngine] = {}

    @validator("sqlalchemy_binds", pre=True, each_item=True, always=True)
    def validate_sqlalchemy_binds(
        cls, v: Union[str, URL, dict, AsyncEngine], values: Dict[str, Any]
    ) -> AsyncEngine:
        if isinstance(v, AsyncEngine):
            return v

        if isinstance(v, dict):
            url = make_url(v.pop("url"))
            kw = {**values["sqlalchemy_engine_options"], **v}
        else:
            url = make_url(v)
            kw: dict[str, Any] = values["sqlalchemy_engine_options"].copy()

        if url.get_backend_name() == "sqlite" and not any(
            map(kw.__contains__, ("pool", "poolclass"))
        ):
            kw["poolclass"] = StaticPool

        return create_async_engine(url, **kw)

    sqlalchemy_database_url: AsyncEngine = None  # type: ignore[assignment]

    @validator("sqlalchemy_database_url", pre=True, always=True)
    def validate_sqlalchemy_database_url(
        cls, v: Union[str, URL, dict, AsyncEngine, None], values: Dict[str, Any]
    ) -> AsyncEngine:
        if v is None:
            v = values["sqlalchemy_binds"].get("", None)

        if v is None:
            raise ValueError('必须至少配置 sqlalchemy_database_url 或 sqlalchemy_binds[""] 之一')

        v = values["sqlalchemy_binds"][""] = cast(
            AsyncEngine, cls.validate_sqlalchemy_binds(v, values)
        )

        return v

    sqlalchemy_session_options: Dict[str, Any] = {}

    alembic_config: Union[str, os.PathLike[str], AlembicConfig, None] = None
    alembic_script_location: Path = Path("migrations")
    alembic_version_locations: Dict[str, Path] = None  # type: ignore[assignment]

    @validator("alembic_version_locations", pre=True, always=True)
    def validate_alembic_version_locations(
        cls, v: Optional[Dict[str, Path]], values: Dict[str, Any]
    ) -> Dict[str, Path]:
        return {"": values["alembic_script_location"] / "versions"} if v is None else v

    alembic_context: Dict[str, Any] = {"render_as_batch": True}
