from __future__ import annotations

import asyncio
from typing import Any, cast

from alembic import context
from sqlalchemy.util import await_only
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection
from sqlalchemy import MetaData, Connection, TwoPhaseTransaction

from nonebot_plugin_orm.env import no_drop_table
from nonebot_plugin_orm import AlembicConfig, plugin_config

# 是否使用二阶段提交 (Two-Phase Commit),
# 当同时迁移多个数据库时, 可以启用以保证迁移的原子性.
# 注意: 只有部分数据库支持（例如 SQLite 就不支持）.
USE_TWOPHASE = False

# Alembic Config 对象, 它提供正在使用的 .ini 文件中的值.
config = cast(AlembicConfig, context.config)

# bind key 到 AsyncEngine 的映射
engines: dict[str, AsyncEngine] = config.attributes["engines"]

# bind key 到 MetaData 的映射, 用于 "autogenerate" 支持.
# Metadata 对象必须仅包含对应数据库中的表.
# table.to_metadata() 在需要“复制”表到 MetaData 中时可能很有用.
# from myapp import mymodel
# target_metadata = {
#     "engine1": mymodel.metadata1,
#     "engine2": mymodel.metadata2
# }
target_metadatas: dict[str, MetaData] = config.attributes["metadatas"]

# 其他来自 config 的值, 可以按 env.py 的需求定义, 例如可以获取:
# my_important_option = config.get_main_option("my_important_option")
# ... 等等.


def run_migrations_offline() -> None:
    """在“离线”模式下运行迁移.

    虽然这里也可以获得 Engine, 但我们只需要一个 URL 即可配置 context.
    通过跳过 Engine 的创建, 我们甚至不需要 DBAPI 可用.

    在这里调用 context.execute() 会将给定的字符串写入到脚本输出.

    """

    for name, engine in engines.items():
        config.print_stdout(f"迁移数据库 {name or '<default>'} 中 ...")
        file_ = f"{name}.sql"
        with open(file_, "w") as buffer:
            opts: dict[str, Any] = {
                "url": engine.url,
                "dialect_opts": {"paramstyle": "named"},
                "output_buffer": buffer,
                "target_metadata": target_metadatas[name],
                "literal_binds": True,
            } | plugin_config.alembic_context
            context.configure(**opts)

            with context.begin_transaction():
                context.run_migrations(name=name)
            config.print_stdout(f"将输出写入到 {file_}")


def do_run_migrations(conn: Connection, name: str, metadata: MetaData) -> None:
    opts: dict[str, Any] = {
        "connection": conn,
        "render_as_batch": True,
        "target_metadata": metadata,
        "include_object": no_drop_table,
        "upgrade_token": f"{name}_upgrades",
        "downgrade_token": f"{name}_downgrades",
    } | plugin_config.alembic_context
    context.configure(**opts)

    context.run_migrations(name=name)


async def run_migrations_online() -> None:
    """在“在线”模式下运行迁移.

    这种情况下, 我们需要为 context 创建一个连接.
    """

    conns: dict[str, AsyncConnection] = {}
    txns: dict[str, TwoPhaseTransaction] = {}

    try:
        for name, engine in engines.items():
            config.print_stdout(f"迁移数据库 {name or '<default>'} 中 ...")
            conn = conns[name] = await engine.connect()
            if USE_TWOPHASE:
                txns[name] = await conn.run_sync(Connection.begin_twophase)
            else:
                await conn.begin()

            await conn.run_sync(do_run_migrations, name, target_metadatas[name])

        if USE_TWOPHASE:
            await asyncio.gather(
                *(
                    conn.run_sync(lambda _: txns[name].prepare())
                    for name, conn in conns.items()
                )
            )

        await asyncio.gather(*(conn.commit() for conn in conns.values()))
    except BaseException:
        await asyncio.gather(*(conn.rollback() for conn in conns.values()))
        raise
    finally:
        await asyncio.gather(*(conn.close() for conn in conns.values()))


if context.is_offline_mode():
    run_migrations_offline()
else:
    coro = run_migrations_online()

    try:
        asyncio.run(coro)
    except RuntimeError:
        await_only(coro)
