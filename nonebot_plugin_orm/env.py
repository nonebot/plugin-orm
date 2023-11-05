from __future__ import annotations

from alembic import context
from sqlalchemy.sql.schema import SchemaItem

from nonebot_plugin_orm import migrate


def no_drop_table(
    _, __, type_: str, reflected: bool, compare_to: SchemaItem | None
) -> bool:
    return not (
        getattr(context.config.cmd_opts, "cmd", (None,))[0] == migrate.check
        and type_ == "table"
        and reflected
        and compare_to is None
    )
