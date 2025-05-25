from __future__ import annotations

from pathlib import Path
from functools import wraps
from argparse import Namespace
from collections.abc import Callable, Iterable
from typing_extensions import TypeVar, ParamSpec, Concatenate

import click
from alembic.script import Script

from . import migrate
from .config import plugin_config
from .migrate import AlembicConfig

_P = ParamSpec("_P")
_R = TypeVar("_R")


@click.group()
@click.option(
    "-c",
    "--config",
    envvar="ALEMBIC_CONFIG",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help='可选的配置文件; 默认为 ALEMBIC_CONFIG 环境变量的值, 或者 "alembic.ini" (如果存在)',
)
@click.option(
    "-n",
    "--name",
    default="alembic",
    show_default=True,
    help=".ini 文件中用于 Alembic 配置的小节的名称",
)
@click.option(
    "-x",
    multiple=True,
    help="自定义 env.py 脚本使用的其他参数, 例如：-x setting1=somesetting -x setting2=somesetting",
)
@click.option("-q", "--quite", is_flag=True, help="不要输出日志到标准输出")
@click.pass_context
def orm(ctx: click.Context, config: Path, name: str, **_) -> None:
    ctx.show_default = True

    if isinstance(plugin_config.alembic_config, AlembicConfig):
        ctx.obj = plugin_config.alembic_config
    else:
        cmd_opts = Namespace(**ctx.params)

        if ctx.invoked_subcommand:
            arguments = []
            options = []

            for param in globals()[ctx.invoked_subcommand].params:
                if isinstance(param, click.Argument):
                    arguments.append(param.name)
                elif isinstance(param, click.Option):
                    options.append(param.name)

            cmd_opts.cmd = (
                getattr(migrate, ctx.invoked_subcommand),
                arguments,
                options,
            )

        ctx.obj = AlembicConfig(config, ini_section=name, cmd_opts=cmd_opts)

    ctx.call_on_close(ctx.obj.close)


def update_cmd_opts(
    f: Callable[Concatenate[AlembicConfig, _P], _R]
) -> Callable[_P, _R]:
    @wraps(f)
    @click.pass_context
    def wrapper(ctx: click.Context, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        for key, value in kwargs.items():
            setattr(ctx.obj.cmd_opts, key, value)

        return f(ctx.obj, *args, **kwargs)

    return wrapper


@orm.result_callback()
@click.pass_obj
def move_script(config_: AlembicConfig, scripts: Iterable[Script] | None, **_) -> None:
    if not scripts:
        return

    for script in scripts:
        config_.move_script(script)


@orm.command("list_templates")
@update_cmd_opts
def list_templates(*args, **kwargs) -> None:
    """列出所有可用的模板."""

    return migrate.list_templates(*args, **kwargs)


@orm.command()
@click.argument(
    "directory",
    default=Path("migrations"),
    type=click.Path(file_okay=False, writable=True, resolve_path=True, path_type=Path),
)
@click.option("-t", "--template", default="generic", help="使用的迁移环境模板")
@click.option(
    "--package", is_flag=True, help="在脚本目录和版本目录中创建 __init__.py 文件"
)
@update_cmd_opts
def init(*args, **kwargs) -> None:
    """初始化脚本目录."""

    return migrate.init(*args, **kwargs)


@orm.command()
@click.option("-m", "--message", help="描述")
@click.option("--sql", is_flag=True, help="以 SQL 的形式输出迁移脚本")
@click.option("--head", help="基准版本")
@click.option("--splice", is_flag=True, help="允许非头部迁移作为基准版本")
@click.option("--branch-label", help="分支标签")
@click.option(
    "--version-path",
    default=None,
    type=click.Path(file_okay=False, writable=True, resolve_path=True, path_type=Path),
    help="存放迁移脚本的目录",
)
@click.option("--rev-id", help="指定而不是使用生成的迁移 ID")
@click.option("--depends-on", help="依赖的迁移")
@update_cmd_opts
def revision(*args, **kwargs) -> Iterable[Script]:
    """创建一个新迁移脚本."""

    return migrate.revision(*args, **kwargs)


@orm.command()
@update_cmd_opts
def check(*args, **kwargs) -> None:
    """检查数据库是否与模型定义一致."""

    return migrate.check(*args, **kwargs)


@orm.command()
@click.argument("revisions", nargs=-1)
@click.option("-m", "--message", help="描述")
@click.option("--branch-label", help="分支标签")
@click.option("--rev-id", help="指定而不是使用生成的迁移 ID")
@update_cmd_opts
def merge(*args, **kwargs) -> Iterable[Script]:
    """合并多个迁移.创建一个新的迁移脚本."""

    return migrate.merge(*args, **kwargs)


@orm.command()
@click.argument("revision", required=False)
@click.option("--sql", is_flag=True, help="以 SQL 的形式输出迁移脚本")
@click.option("--tag", help="一个任意的字符串, 可在自定义的 env.py 中使用")
@update_cmd_opts
def upgrade(*args, **kwargs) -> None:
    """升级到较新版本."""

    return migrate.upgrade(*args, **kwargs)


@orm.command()
@click.argument("revision")
@click.option("--sql", is_flag=True, help="以 SQL 的形式输出迁移脚本")
@click.option("--tag", help="一个任意的字符串, 可在自定义的 env.py 中使用")
@update_cmd_opts
def downgrade(*args, **kwargs) -> None:
    """回退到先前版本."""

    return migrate.downgrade(*args, **kwargs)


@orm.command()
@click.argument("revision", required=False)
@update_cmd_opts
def sync(*args, **kwargs) -> None:
    """同步数据库模式 (仅用于开发)."""

    return migrate.sync(*args, **kwargs)


@orm.command()
@click.argument("revs", nargs=-1)
@update_cmd_opts
def show(*args, **kwargs) -> None:
    """显示迁移的信息."""

    return migrate.show(*args, **kwargs)


@orm.command()
@click.option("-r", "--rev-range", required=False, help="范围")
@click.option("-v", "--verbose", is_flag=True, help="显示详细信息")
@click.option("-i", "--indicate-current", is_flag=True, help="指示出当前迁移")
@update_cmd_opts
def history(*args, **kwargs) -> None:
    """显示迁移的历史."""

    return migrate.history(*args, **kwargs)


@orm.command()
@click.option("-v", "--verbose", is_flag=True, help="显示详细信息")
@click.option("--resolve-dependencies", is_flag=True, help="将依赖的迁移视作父迁移")
@update_cmd_opts
def heads(*args, **kwargs) -> None:
    """显示所有的分支头."""

    return migrate.heads(*args, **kwargs)


@orm.command()
@click.option("-v", "--verbose", is_flag=True, help="显示详细信息")
@update_cmd_opts
def branches(*args, **kwargs) -> None:
    """显示所有的分支."""

    return migrate.branches(*args, **kwargs)


@orm.command()
@click.option("-v", "--verbose", is_flag=True, help="显示详细信息")
@update_cmd_opts
def current(*args, **kwargs) -> None:
    """显示当前的迁移."""

    return migrate.current(*args, **kwargs)


@orm.command()
@click.argument("revisions", nargs=-1)
@click.option("--sql", is_flag=True, help="以 SQL 的形式输出迁移脚本")
@click.option("--tag", help="一个任意的字符串, 可在自定义的 env.py 中使用")
@click.option("--purge", is_flag=True, help="在标记前清空数据库版本表")
@update_cmd_opts
def stamp(*args, **kwargs) -> None:
    """将数据库标记为特定的迁移版本, 不运行任何迁移."""

    return migrate.stamp(*args, **kwargs)


@orm.command()
@click.argument("rev", default="current")
@update_cmd_opts
def edit(*args, **kwargs) -> None:
    """使用 $EDITOR 编辑迁移脚本."""

    return migrate.edit(*args, **kwargs)


@orm.command("ensure_version")
@click.option("--sql", is_flag=True, help="以 SQL 的形式输出迁移脚本")
@update_cmd_opts
def ensure_version(*args, **kwargs) -> None:
    """创建版本表."""

    return migrate.ensure_version(*args, **kwargs)


def main(*args, **kwargs) -> None:
    from . import _init_orm

    if not (args or kwargs):
        kwargs["prog_name"] = "nb orm"

    _init_orm()
    orm(*args, **kwargs)


if __name__ == "__main__":
    main()
