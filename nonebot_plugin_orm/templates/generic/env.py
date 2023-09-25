import asyncio
from typing import cast

from alembic import context
from sqlalchemy import Connection

from nonebot_plugin_orm import Model
from nonebot_plugin_orm.migrate import AlembicConfig
from nonebot_plugin_orm import config as plugin_config

# Alembic Config 对象，它提供正在使用的 .ini 文件中的值。
config = cast(AlembicConfig, context.config)

# 模型的 MetaData，用于 'autogenerate' 支持。
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Model.metadata

# 其他来自 config 的值，可以按 env.py 的需求定义，例如可以获取：
# my_important_option = config.get_main_option("my_important_option")
# ... 等等。


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=plugin_config.sqlalchemy_database_url.url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **plugin_config.alembic_context,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        **plugin_config.alembic_context,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    engine = plugin_config.sqlalchemy_database_url

    async with engine.begin() as connection:
        await connection.run_sync(do_run_migrations)


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
