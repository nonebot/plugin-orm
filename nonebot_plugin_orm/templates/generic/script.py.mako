"""${message}

迁移 ID: ${up_revision}
父迁移: ${down_revision | comma,n}
创建时间: ${create_date}

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade(name: str = "") -> None:
    if name:
        return
    ${upgrades}


def downgrade(name: str = "") -> None:
    if name:
        return
    ${downgrades}
