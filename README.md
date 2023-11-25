<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <a href="https://nonebot.dev/"><img src="https://nonebot.dev/logo.png" width="200" height="200" alt="nonebot"></a>
</p>

<div align="center">

# NoneBot Plugin ORM

<!-- prettier-ignore-start -->
<!-- markdownlint-disable-next-line MD036 -->
_✨ NoneBot 数据库支持插件 ✨_
<!-- prettier-ignore-end -->

</div>

<p align="center">
  <a href="https://raw.githubusercontent.com/nonebot/plugin-orm/master/LICENSE">
    <img src="https://img.shields.io/github/license/nonebot/plugin-orm.svg" alt="license">
  </a>
  <a href="https://pypi.org/project/nonebot-plugin-orm/">
    <img src="https://img.shields.io/pypi/v/nonebot-plugin-orm.svg" alt="pypi">
  </a>
  <img src="https://img.shields.io/badge/python-3.8+-blue.svg" alt="python">
</p>

## 安装

```shell
pip install nonebot-plugin-orm
poetry add nonebot-plugin-orm
pdm add nonebot-plugin-orm

# 无需配置、开箱即用的默认依赖
pip install nonebot-plugin-orm[default]

# 特定数据库后端的依赖
pip install nonebot-plugin-orm[mysql]
pip install nonebot-plugin-orm[postgresql]
pip install nonebot-plugin-orm[sqlite]

# 特定数据库驱动的依赖
pip install nonebot-plugin-orm[asyncmy]
pip install nonebot-plugin-orm[aiomysql]
pip install nonebot-plugin-orm[psycopg]
pip install nonebot-plugin-orm[asyncpg]
pip install nonebot-plugin-orm[aiosqlite]
```

## 使用方式

### ORM

#### Model 依赖注入

```python
from nonebot.adapters import Event
from nonebot.params import Depends
from nonebot import require, on_message
from sqlalchemy.orm import Mapped, mapped_column

require("nonebot_plugin_orm")
from nonebot_plugin_orm import Model, async_scoped_session

matcher = on_message()


def get_user_id(event: Event) -> str:
    return event.get_user_id()


class User(Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = Depends(get_user_id)


@matcher.handle()
async def _(event: Event, sess: async_scoped_session, user: User | None):
    if user:
        await matcher.finish(f"Hello, {user.user_id}")

    sess.add(User(user_id=get_user_id(event)))
    await sess.commit()
    await matcher.finish("Hello, new user!")
```

#### SQL 依赖注入

```python
from sqlalchemy import select
from nonebot.adapters import Event
from nonebot.params import Depends
from nonebot import require, on_message
from sqlalchemy.orm import Mapped, mapped_column

require("nonebot_plugin_orm")
from nonebot_plugin_orm import Model, SQLDepends, async_scoped_session

matcher = on_message()


def get_session_id(event: Event) -> str:
    return event.get_session_id()


class Session(Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str]


@matcher.handle()
async def _(
    event: Event,
    sess: async_scoped_session,
    session: Session
    | None = SQLDepends(
        select(Session).where(Session.session_id == Depends(get_session_id))
    ),
):
    if session:
        await matcher.finish(f"Hello, {session.session_id}")

    sess.add(Session(session_id=get_session_id(event)))
    await sess.commit()
    await matcher.finish("Hello, new user!")

```

### CLI

依赖 [NB CLI](https://github.com/nonebot/nb-cli)

```properties
$ nb orm
Usage: nb orm [OPTIONS] COMMAND [ARGS]...

Options:
  -c, --config FILE  可选的配置文件；默认为 ALEMBIC_CONFIG 环境变量的值，或者 "alembic.ini"（如果存在）
  -n, --name TEXT    .ini 文件中用于 Alembic 配置的小节的名称  [default: alembic]
  -x TEXT            自定义 env.py 脚本使用的其他参数，例如：-x setting1=somesetting -x
                     setting2=somesetting
  -q, --quite        不要输出日志到标准输出
  --help             Show this message and exit.

Commands:
  branches        显示所有的分支。
  check           检查数据库是否与模型定义一致。
  current         显示当前的迁移。
  downgrade       回退到先前版本。
  edit            使用 $EDITOR 编辑迁移脚本。
  ensure_version  创建版本表。
  heads           显示所有的分支头。
  history         显示迁移的历史。
  init            初始化脚本目录。
  list_templates  列出所有可用的模板。
  merge           合并多个迁移。创建一个新的迁移脚本。
  revision        创建一个新迁移脚本。
  show            显示迁移的信息。
  stamp           将数据库标记为特定的迁移版本，不运行任何迁移。
  upgrade         升级到较新版本。
```

## 配置项

### sqlalchemy_database_url

默认数据库连接 URL。
参见：[Engine Configuration — SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/core/engines.html#database-urls)

```properties
SQLALCHEMY_DATABASE_URL=sqlite+aiosqlite://
```

### sqlalchemy_binds

bind keys 到 `AsyncEngine` 选项的映射。值可以是数据库连接 URL、`AsyncEngine` 选项字典或者 `AsyncEngine` 实例。

```properties
SQLALCHEMY_BINDS='{
    "": "sqlite+aiosqlite://",
    "nonebot_plugin_user": {
        "url": "postgresql+asyncpg://scott:tiger@localhost/mydatabase",
        "echo": true
    }
}'
```

### sqlalchemy_echo

所有 `AsyncEngine` 的 `echo` 和 `echo_pool` 选项的默认值。用于快速调试连接和 SQL 生成问题。

```properties
SQLALCHEMY_ECHO=true
```

### sqlalchemy_engine_options

所有 `AsyncEngine` 的默认选项字典。
参见：[Engine Configuration — SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/core/engines.html#engine-configuration)

```properties
SQLALCHEMY_ENGINE_OPTIONS='{
    "pool_size": 5,
    "max_overflow": 10,
    "pool_timeout": 30,
    "pool_recycle": 3600,
    "echo": true
}'
```

### sqlalchemy_session_options

`AsyncSession` 的选项字典。
参见：[Session API — SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.__init__)

```properties
SQLALCHEMY_SESSION_OPTIONS='{
    "autoflush": false,
    "autobegin": true,
    "expire_on_commit": true
}'
```

### alembic_config

配置文件路径或 `AlembicConfig` 实例。

```properties
ALEMBIC_CONFIG=alembic.ini
```

### alembic_script_location

脚本目录路径。

```properties
ALEMBIC_SCRIPT_LOCATION=migrations
```

### alembic_version_locations

迁移脚本目录路径或分支标签到迁移脚本目录路径的映射。

```properties
ALEMBIC_VERSION_LOCATIONS=migrations/versions

ALEMBIC_VERSION_LOCATIONS='{
    "": "migrations/versions",
    "nonebot_plugin_user": "src/nonebot_plugin_user/versions",
    "nonebot_plugin_chatrecorder": "migrations/versions/nonebot_plugin_chatrecorder"
}'
```

### alembic_context

`MigrationContext` 的选项字典。
参见：[Runtime Objects — Alembic 1.12.0 documentation](https://alembic.sqlalchemy.org/en/latest/api/runtime.html#alembic.runtime.environment.EnvironmentContext.configure)

```properties
ALEMBIC_CONTEXT='{
    "render_as_batch": true
}'
```

### alembic_startup_check

是否在启动时检查数据库与模型定义的一致性。

```properties
ALEMBIC_STARTUP_CHECK=true
```
