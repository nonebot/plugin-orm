from __future__ import annotations

from pathlib import Path
from typing import Iterable
from argparse import Namespace
from warnings import catch_warnings, filterwarnings

import click
from alembic.script import Script
from sqlalchemy.util import greenlet_spawn

from . import migrate
from .config import plugin_config
from .migrate import AlembicConfig


@click.group()
@click.option(
    "-c",
    "--config",
    envvar="ALEMBIC_CONFIG",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help='可选的配置文件；默认为 ALEMBIC_CONFIG 环境变量的值，或者 "alembic.ini"（如果存在）',
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
    help="自定义 env.py 脚本使用的其他参数，例如：-x setting1=somesetting -x setting2=somesetting",
)
@click.option("-q", "--quite", is_flag=True, help="不要输出日志到标准输出")
@click.pass_context
def orm(
    ctx: click.Context, config: Path, name: str, x: tuple[str, ...], quite: bool
) -> None:
    ctx.show_default = True
    use_tempdir = ctx.invoked_subcommand in ("revision", "merge", "edit")

    if isinstance(plugin_config.alembic_config, AlembicConfig):
        ctx.obj = plugin_config.alembic_config
    else:
        ctx.obj = AlembicConfig(
            config, name, cmd_opts=Namespace(**ctx.params), use_tempdir=use_tempdir
        )

    ctx.call_on_close(ctx.obj.close)
    if use_tempdir:
        ctx.with_resource(catch_warnings())
        filterwarnings("ignore", r"Revision \w* is present more than once", UserWarning)


@orm.result_callback()
@click.pass_obj
def move_script(config_: AlembicConfig, scripts: Iterable[Script] | None, **_) -> None:
    if not scripts:
        return

    for script in scripts:
        config_.move_script(script)


@orm.command("list_templates")
@click.pass_obj
def list_templates(*args, **kwargs) -> None:
    """列出所有可用的模板。"""
    return migrate.list_templates(*args, **kwargs)


@orm.command()
@click.argument(
    "directory",
    default=Path("migrations"),
    type=click.Path(file_okay=False, writable=True, resolve_path=True, path_type=Path),
)
@click.option("-t", "--template", default="generic", help="使用的迁移环境模板")
@click.option("--package", is_flag=True, help="在脚本目录和版本目录中创建 __init__.py 文件")
@click.pass_obj
def init(*args, **kwargs) -> None:
    """初始化脚本目录。"""
    return migrate.init(*args, **kwargs)


@orm.command()
@click.option("-m", "--message", help="描述")
@click.option("--sql", is_flag=True, help="以 SQL 的形式输出修订脚本")
@click.option("--head", default="head", help="基准版本")
@click.option("--splice", is_flag=True, help="允许非头部修订作为基准版本")
@click.option("--branch-label", help="分支标签")
@click.option(
    "--version-path",
    default=None,
    type=click.Path(file_okay=False, writable=True, resolve_path=True, path_type=Path),
    help="存放修订文件的目录",
)
@click.option("--rev-id", help="指定而不是使用生成的修订 ID")
@click.option("--depends-on", help="依赖的修订")
@click.pass_obj
def revision(*args, **kwargs) -> Iterable[Script]:
    """创建一个新修订文件。"""
    return migrate.revision(*args, **kwargs)


@orm.command()
@click.pass_obj
def check(*args, **kwargs) -> None:
    """检查数据库是否与模型定义一致。"""
    return migrate.check(*args, **kwargs)


@orm.command()
@click.argument("revisions", nargs=-1)
@click.option("-m", "--message", help="描述")
@click.option("--branch-label", help="分支标签")
@click.option("--rev-id", help="指定而不是使用生成的修订 ID")
@click.pass_obj
def merge(*args, **kwargs) -> Iterable[Script]:
    """合并多个修订。创建一个新的修订文件。"""
    return migrate.merge(*args, **kwargs)


@orm.command()
@click.argument("revision", required=False)
@click.option("--sql", is_flag=True, help="以 SQL 的形式输出修订脚本")
@click.option("--tag", help="一个任意的字符串, 可在自定义的 env.py 中使用")
@click.option(
    "--fast",
    is_flag=True,
    help="快速升级到最新版本，不运行修订脚本，直接创建当前的表（只应该在数据库为空、修订较多且只有表结构更改时使用）",
)
@click.pass_obj
def upgrade(*args, **kwargs) -> None:
    """升级到较新版本。"""
    return migrate.upgrade(*args, **kwargs)


@orm.command()
@click.argument("revision")
@click.option("--sql", is_flag=True, help="以 SQL 的形式输出修订脚本")
@click.option("--tag", help="一个任意的字符串, 可在自定义的 env.py 中使用")
@click.pass_obj
def downgrade(*args, **kwargs) -> None:
    """回退到先前版本。"""
    return migrate.downgrade(*args, **kwargs)


@orm.command()
@click.argument("revs", nargs=-1)
@click.pass_obj
def show(*args, **kwargs) -> None:
    """显示修订的信息。"""
    return migrate.show(*args, **kwargs)


@orm.command()
@click.option("-r", "--rev-range", required=False, help="范围")
@click.option("-v", "--verbose", is_flag=True, help="显示详细信息")
@click.option("-i", "--indicate-current", is_flag=True, help="指示出当前修订")
@click.pass_obj
def history(*args, **kwargs) -> None:
    """显示修订的历史。"""
    return migrate.history(*args, **kwargs)


@orm.command()
@click.option("-v", "--verbose", is_flag=True, help="显示详细信息")
@click.option("--resolve-dependencies", is_flag=True, help="将依赖的修订视作父修订")
@click.pass_obj
def heads(*args, **kwargs) -> None:
    """显示所有的分支头。"""
    return migrate.heads(*args, **kwargs)


@orm.command()
@click.option("-v", "--verbose", is_flag=True, help="显示详细信息")
@click.pass_obj
def branches(*args, **kwargs) -> None:
    """显示所有的分支。"""
    return migrate.branches(*args, **kwargs)


@orm.command()
@click.option("-v", "--verbose", is_flag=True, help="显示详细信息")
@click.pass_obj
def current(*args, **kwargs) -> None:
    """显示当前的修订。"""
    return migrate.current(*args, **kwargs)


@orm.command()
@click.argument("revisions", nargs=-1)
@click.option("--sql", is_flag=True, help="以 SQL 的形式输出修订脚本")
@click.option("--tag", help="一个任意的字符串, 可在自定义的 env.py 中使用")
@click.option("--purge", is_flag=True, help="在标记前清空数据库版本表")
@click.pass_obj
def stamp(*args, **kwargs) -> None:
    """将数据库标记为特定的修订版本，不运行任何迁移。"""
    return migrate.stamp(*args, **kwargs)


@orm.command()
@click.argument("rev", default="current")
@click.pass_obj
def edit(*args, **kwargs) -> None:
    """使用 $EDITOR 编辑修订文件。"""
    return migrate.edit(*args, **kwargs)


@orm.command("ensure_version")
@click.option("--sql", is_flag=True, help="以 SQL 的形式输出修订脚本")
@click.pass_obj
def ensure_version(*args, **kwargs) -> None:
    """创建版本表。"""
    return migrate.ensure_version(*args, **kwargs)


def main(*args, **kwargs) -> None:
    from . import _init_orm

    if not (args or kwargs):
        kwargs["prog_name"] = "nb orm"

    _init_orm()
    orm(*args, **kwargs)


if __name__ == "__main__":
    main()
