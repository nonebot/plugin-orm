from pathlib import Path
from typing import Any, Dict

from pydantic import Extra, BaseModel, validator

from alembic.config import Config as AlembicConfig

__all__ = ("Config",)


class Config(BaseModel, extra=Extra.ignore):
    sqlalchemy_database_uri: str
    sqlalchemy_engine_options: dict = {}
    sqlalchemy_session_options: dict = {}
    sqlalchemy_echo: bool = False

    alembic_config: dict = {}
    alembic_context: Dict[str, Any] = {"compare_type": True, "render_as_batch": True}

    @validator("alembic_context")
    def validate_alembic_context(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        return {**cls.alembic_context, **v}

    def get_alembic_config(self) -> AlembicConfig:
        config = AlembicConfig()

        config.set_main_option("sqlalchemy.url", self.sqlalchemy_database_uri)

        script_location = Path(self.alembic_config.get("script_location", "migrations"))
        config.set_main_option("script_location", str(script_location.resolve()))

        version_locations = [
            script_location / "versions",
            *(
                Path(item[1] if isinstance(item, tuple) else item)
                for item in self.alembic_config.get("version_locations", ())
            ),
        ]
        config.set_main_option(
            "version_locations",
            ",".join(map(str, map(Path.resolve, version_locations))),
        )

        for key, value in self.alembic_config.items():
            if key in ("script_location", "version_locations"):
                continue
            config.set_main_option(key, value)

        return config
