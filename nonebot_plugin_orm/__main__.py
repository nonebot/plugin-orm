from __future__ import annotations

from pathlib import Path
from typing import Iterable
from argparse import Namespace

import click
from alembic.script import Script

from . import migrate
from .migrate import AlembicConfig
from .config import config as plugin_config


@click.group()
@click.option(
    "-c", "--config", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("-n", "--name", default="alembic")
@click.option("-x", multiple=True)
@click.option("-q", "--quite", is_flag=True)
@click.pass_context
def orm(
    ctx: click.Context, config: Path, name: str, x: tuple[str, ...], quite: bool
) -> None:
    if isinstance(plugin_config.alembic_config, AlembicConfig):
        ctx.obj = plugin_config.alembic_config
    else:
        ctx.obj = AlembicConfig(
            config, name, cmd_opts=Namespace(config=config, name=name, x=x, quite=quite)
        )

    ctx.with_resource(ctx.obj)


@orm.result_callback()
@click.pass_obj
def move_script(
    config_: AlembicConfig, scripts: Iterable[Script] | None, *_, **__
) -> None:
    if not scripts:
        return

    for script in scripts:
        config_.move_script(script)


@orm.command("list_templates")
@click.pass_obj
def list_templates(*args, **kwargs) -> None:
    return migrate.list_templates(*args, **kwargs)


@orm.command()
@click.argument(
    "directory",
    default=Path("migrations"),
    type=click.Path(file_okay=False, writable=True, resolve_path=True, path_type=Path),
)
@click.option("-t", "--template", default="multidb")
@click.option("--package", is_flag=True)
@click.pass_obj
def init(*args, **kwargs) -> None:
    return migrate.init(*args, **kwargs)


@orm.command()
@click.option("-m", "--message")
@click.option("--sql", is_flag=True)
@click.option("--head", default="head")
@click.option("--splice", is_flag=True)
@click.option("--branch-label")
@click.option(
    "--version-path",
    default=None,
    type=click.Path(file_okay=False, writable=True, resolve_path=True, path_type=Path),
)
@click.option("--rev-id")
@click.option("--depends-on")
@click.pass_obj
def revision(*args, **kwargs) -> Iterable[Script]:
    return migrate.revision(*args, **kwargs)


@orm.command()
@click.pass_obj
def check(*args, **kwargs) -> None:
    return migrate.check(*args, **kwargs)


@orm.command()
@click.argument("revisions", nargs=-1)
@click.option("-m", "--message")
@click.option("--branch-label")
@click.option("--rev-id")
@click.pass_obj
def merge(*args, **kwargs) -> Iterable[Script]:
    return migrate.merge(*args, **kwargs)


@orm.command()
@click.argument("revision", required=False)
@click.option("--sql", is_flag=True)
@click.option("--tag")
@click.pass_obj
def upgrade(*args, **kwargs) -> None:
    return migrate.upgrade(*args, **kwargs)


@orm.command()
@click.argument("revision")
@click.option("--sql", is_flag=True)
@click.option("--tag")
@click.pass_obj
def downgrade(*args, **kwargs) -> None:
    return migrate.downgrade(*args, **kwargs)


@orm.command()
@click.argument("rev", nargs=-1)
@click.pass_obj
def show(*args, **kwargs) -> None:
    return migrate.show(*args, **kwargs)


@orm.command()
@click.option("-r", "--rev-range", required=False)
@click.option("-v", "--verbose", is_flag=True)
@click.option("-i", "--indicate-current", is_flag=True)
@click.pass_obj
def history(*args, **kwargs) -> None:
    return migrate.history(*args, **kwargs)


@orm.command()
@click.option("-v", "--verbose", is_flag=True)
@click.option("--resolve-dependencies", is_flag=True)
@click.pass_obj
def heads(*args, **kwargs) -> None:
    return migrate.heads(*args, **kwargs)


@orm.command()
@click.option("-v", "--verbose", is_flag=True)
@click.pass_obj
def branches(*args, **kwargs) -> None:
    return migrate.branches(*args, **kwargs)


@orm.command()
@click.option("-v", "--verbose", is_flag=True)
@click.pass_obj
def current(*args, **kwargs) -> None:
    return migrate.current(*args, **kwargs)


@orm.command()
@click.argument("revisions", nargs=-1)
@click.option("--sql", is_flag=True)
@click.option("--tag")
@click.option("--purge", is_flag=True)
@click.pass_obj
def stamp(*args, **kwargs) -> None:
    return migrate.stamp(*args, **kwargs)


@orm.command()
@click.argument("rev", default="current")
@click.pass_obj
def edit(*args, **kwargs):
    return migrate.edit(*args, **kwargs)


@orm.command("ensure_version")
@click.option("--sql", is_flag=True)
@click.pass_obj
def ensure_version(*args, **kwargs) -> None:
    return migrate.ensure_version(*args, **kwargs)


def main():
    from . import _init_table, _init_engines

    _init_engines()
    _init_table()
    orm(prog_name="nb orm")


if __name__ == "__main__":
    main()
