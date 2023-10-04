from __future__ import annotations

import asyncio
from typing import cast
from operator import methodcaller

from alembic import context
from sqlalchemy.util import await_fallback
from alembic.migration import MigrationContext
from alembic.operations import MigrateOperation
from alembic.operations.ops import MigrationScript
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection
from sqlalchemy import MetaData, Connection, TwoPhaseTransaction

from nonebot_plugin_orm import AlembicConfig
from nonebot_plugin_orm import config as plugin_config

# 是否使用二阶段提交 (Two-Phase Commit)，
# 当同时迁移多个数据库时，可以启用以保证迁移的原子性。
USE_TWOPHASE = False

# Alembic Config 对象，它提供正在使用的 .ini 文件中的值。
config = cast(AlembicConfig, context.config)

# gather section names referring to different
# databases.  These are named "engine1", "engine2"
# in the sample .ini file.
engines: dict[str, AsyncEngine] = config.attributes["engines"]


# bind key 到 MetaData 的映射，用于 'autogenerate' 支持。
# Metadata 对象必须仅包含对应数据库中的表。
# table.to_metadata() 在需要“复制”表到 MetaData 中时可能很有用。
# from myapp import mymodel
# target_metadata = {
#       'engine1':mymodel.metadata1,
#       'engine2':mymodel.metadata2
# }
target_metadatas: dict[str, MetaData] = config.attributes["metadatas"]

# 其他来自 config 的值，可以按 env.py 的需求定义，例如可以获取：
# my_important_option = config.get_main_option("my_important_option")
# ... 等等。


def run_migrations_offline() -> None:
    """在“离线”模式下运行迁移。

    虽然这里也可以获得 Engine，但我们只需要一个 URL 即可配置 context。
    通过跳过 Engine 的创建，我们甚至不需要 DBAPI 可用。

    在这里调用 context.execute() 会将给定的字符串写入到脚本输出。

    """
    # 使用 --sql 选项的情况下，将每个 URL 的迁移写入到单独的文件中。

    for name, engine in engines.items():
        file_ = f"{name}.sql"
        with open(file_, "w") as buffer:
            context.configure(
                url=engine.url,
                output_buffer=buffer,
                target_metadata=target_metadatas["name"],
                literal_binds=True,
                dialect_opts={"paramstyle": "named"},
                **plugin_config.alembic_context,
            )
            with context.begin_transaction(), config.status(
                f"迁移数据库 {name or '<default>'} 中"
            ):
                context.run_migrations(name=name)
            config.print_stdout(f"将输出写入到 {file_}")


def process_revision_directives(
    context: MigrationContext,
    revision: tuple[str, str],
    directives: list[MigrateOperation],
) -> None:
    # 此回调用于防止在模型没有更改时生成自动迁移。
    # 参见：https://alembic.sqlalchemy.org/en/latest/cookbook.html#don-t-generate-empty-migrations-with-autogenerate

    if getattr(config.cmd_opts, "autogenerate", False) and all(
        filter(
            methodcaller("is_empty"),
            cast("MigrationScript", directives[0]).upgrade_ops_list,
        )
    ):
        directives[:] = []
        config.print_stdout("未检测到模型更改")


def do_run_migrations(conn: Connection, name: str, metadata: MetaData) -> None:
    context.configure(
        connection=conn,
        upgrade_token=f"{name}_upgrades",
        downgrade_token=f"{name}_downgrades",
        target_metadata=metadata,
        process_revision_directives=process_revision_directives,
        **plugin_config.alembic_context,
    )
    with config.status(f"迁移数据库 {name or '<default>'} 中"):
        context.run_migrations(name=name)


async def run_migrations_online() -> None:
    """在“在线”模式下运行迁移。

    这种情况下，我们需要为 context 创建一个连接。

    """
    # 直接连接到数据库的情况下，对所有引擎启动事务，然后运行所有迁移，然后提交所有事务。

    conns: dict[str, AsyncConnection] = {}
    txns: dict[str, TwoPhaseTransaction] = {}

    try:
        for name, engine in engines.items():
            if not (conn := conns.get(name)):
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
    await_fallback(run_migrations_online())
