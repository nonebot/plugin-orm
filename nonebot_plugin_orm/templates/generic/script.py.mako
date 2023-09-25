"""${message}

修订 ID：${up_revision}
父修订：${down_revision | comma,n}
创建时间：${create_date}

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# 修订标识符，由 Alembic 使用。
revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade(name: str = "") -> None:
    if name:  # 兼容 multidb 模板
        return

    ${upgrades if upgrades else "pass"}


def downgrade(name: str = "") -> None:
    if name:  # 兼容 multidb 模板
        return

    ${downgrades if downgrades else "pass"}
