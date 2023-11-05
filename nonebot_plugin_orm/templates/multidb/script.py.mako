"""${message}

迁移 ID: ${up_revision}
父迁移: ${down_revision | comma,n}
创建时间: ${create_date}

"""
from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade(name: str) -> None:
    with suppress(KeyError):
        globals()[f"upgrade_{name}"]()


def downgrade(name: str) -> None:
    with suppress(KeyError):
        globals()[f"downgrade_{name}"]()

% for name in config.attributes["metadatas"]:

def upgrade_${name}() -> None:
    ${context.get(f"{name}_upgrades", "pass")}


def downgrade_${name}() -> None:
    ${context.get(f"{name}_downgrades", "pass")}

% endfor
