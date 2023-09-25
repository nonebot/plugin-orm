"""${message}

修订 ID：${up_revision}
父修订：${down_revision | comma,n}
创建时间：${create_date}

"""
from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

# 修订标识符，由 Alembic 使用。
revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade(name: str | None = None) -> None:
    if name is None:  # 兼容 generic 模板
        for name, func in globals().items():
            if name.startswith("upgrade_"):
                func()
        return

    with suppress(KeyError):
        globals()[f"upgrade_{name}"]()


def downgrade(name: str | None = None) -> None:
    if name is None:  # 兼容 generic 模板
        for name, func in reversed(globals().items()):
            if name.startswith("downgrade_"):
                func()
        return

    with suppress(KeyError):
        globals()[f"downgrade_{name}"]()

<%
    target_metadata = config.attributes["metadatas"]
%>

## 为所有 MetaData 生成一个 "upgrade_<xyz>() / downgrade_<xyz>()" 函数

% for name in target_metadata:

def upgrade_${name}() -> None:
    ${context.get(f"{name}_upgrades", "pass")}


def downgrade_${name}() -> None:
    ${context.get(f"{name}_downgrades", "pass")}

% endfor
