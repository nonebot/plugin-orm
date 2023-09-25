from __future__ import annotations

import os
import shutil
from os import PathLike
from pathlib import Path
from argparse import Namespace
from collections.abc import Generator
from typing import Any, Sequence, cast
from tempfile import TemporaryDirectory
from configparser import DuplicateSectionError
from contextlib import suppress, contextmanager

import click
from alembic.config import Config
from sqlalchemy.util import asbool
from nonebot import get_loaded_plugins
from alembic.operations.ops import UpgradeOps
from alembic.util.editor import open_in_editor
from alembic.script import Script, ScriptDirectory
from alembic.util.messaging import obfuscate_url_pw
from alembic.autogenerate.api import RevisionContext
from alembic.util.langhelpers import rev_id as _rev_id
from alembic.runtime.environment import EnvironmentContext, ProcessRevisionDirectiveFn


class AlembicConfig(Config):
    def get_template_directory(self) -> str:
        return str(Path(__file__).with_name("templates"))

    def print_stdout(self, text: str, *arg, **kwargs) -> None:
        if not getattr(self.cmd_opts, "quite", False):
            click.secho(text % arg, **kwargs)

    @contextmanager
    def status(
        self, status_msg: str, *args, nl: bool = False, **kwargs
    ) -> Generator[None, Any, None]:
        self.print_stdout(f"{status_msg} ...", *args, nl=nl, **kwargs)
        try:
            yield
        except:
            self.print_stdout(" 失败", fg="red")
            raise
        else:
            self.print_stdout(" 成功", fg="green")

    def add_version_path(self, *args: str | PathLike[str]) -> None:
        version_path_separator = {
            None: ", ",
            "space": " ",
            "os": os.pathsep,
            ":": ":",
            ";": ";",
        }[self.get_main_option("version_path_separator", "os")]
        self.set_main_option(
            "version_locations",
            version_path_separator.join(
                map(str, (self.get_main_option("version_locations", ""), *args))
            ),
        )

    def add_post_write_hooks(self, name: str, **kwargs: str) -> None:
        self.set_section_option(
            "post_write_hooks",
            "hooks",
            f"{self.get_section_option('post_write_hooks', 'hooks', '')}, {name}",
        )
        for key, value in kwargs.items():
            self.set_section_option("post_write_hooks", f"{name}.{key}", value)


@click.group()
@click.option(
    "-c", "--config", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("-n", "--name", default="alembic")
@click.option("-x", multiple=True)
@click.option("-q", "--quite", is_flag=True)
@click.pass_context
def orm(
    ctx: click.Context,
    config: str | PathLike[str] | AlembicConfig | None = None,
    name: str = "alembic",
    x: tuple[str, ...] = (),
    quite: bool = False,
) -> AlembicConfig:
    from . import _metadatas
    from . import config as plugin_config

    config = config or plugin_config.alembic_config
    if isinstance(config, AlembicConfig):
        ctx.obj = config
        return config

    config = AlembicConfig(
        config,
        ini_section=name,
        cmd_opts=Namespace(config=config, name=name, x=x, quite=quite),
        config_args={
            "script_location": plugin_config.alembic_script_location,
            "prepend_sys_path": ".",
            "revision_environment": "true",
            "version_path_separator": "os",
        },
        attributes={"metadatas": _metadatas},
    )

    # post write hooks

    with suppress(DuplicateSectionError):
        config.file_config.add_section("post_write_hooks")

    if config.get_section_option("post_write_hooks", "hooks") is None:
        with suppress(ImportError):
            import isort

            del isort
            config.add_post_write_hooks(
                "isort",
                type="console_scripts",
                entrypoint="isort",
                options="REVISION_SCRIPT_FILENAME --profile black",
            )

        with suppress(ImportError):
            import black

            del black
            config.add_post_write_hooks(
                "black",
                type="console_scripts",
                entrypoint="black",
                options="REVISION_SCRIPT_FILENAME",
            )

    # version locations

    temp_version_locations = ctx.meta[f"{__name__}.temp_version_locations"] = Path(
        ctx.with_resource(TemporaryDirectory())
    )
    version_locations_str = [temp_version_locations]

    # original scripts, port with plugin
    for plugin in get_loaded_plugins():
        if plugin.metadata and (
            version_location := plugin.metadata.extra.get("orm_version_location")
        ):
            with suppress(FileNotFoundError):
                shutil.copytree(version_location, temp_version_locations / plugin.name)
                version_locations_str.append(temp_version_locations / plugin.name)

    # project scope replacement scripts
    if version_locations := plugin_config.alembic_version_locations.get(""):
        with suppress(FileNotFoundError):
            shutil.copytree(
                version_locations, temp_version_locations, dirs_exist_ok=True
            )

    # plugin specific replacement scripts
    for (
        plugin_name,
        version_location,
    ) in plugin_config.alembic_version_locations.items():
        if not plugin_name:
            continue
        with suppress(FileNotFoundError):
            shutil.copytree(
                version_location,
                temp_version_locations / plugin_name,
                dirs_exist_ok=True,
            )

    config.add_version_path(*version_locations_str)

    ctx.obj = config
    return config


@orm.command("list_templates")
@click.pass_obj
def list_templates(config: AlembicConfig) -> None:
    """列出所有可用的模板。

    参数:
        config: AlembicConfig 对象
    """

    config.print_stdout("可用的模板：\n")
    for tempname in Path(config.get_template_directory()).iterdir():
        with (tempname / "README").open() as readme:
            synopsis = readme.readline().rstrip()

        config.print_stdout(f"{tempname.name} - {synopsis}")

    config.print_stdout('\n可以通过 "init" 命令使用模板，例如：')
    config.print_stdout("\n  nb orm init --template generic ./scripts")


@orm.command()
@click.argument(
    "directory",
    default=Path("migrations"),
    type=click.Path(file_okay=False, writable=True, resolve_path=True, path_type=Path),
)
@click.option("-t", "--template", default="generic")
@click.option("--package", is_flag=True)
@click.pass_obj
def init(
    config: AlembicConfig,
    directory: Path = Path("migrations"),
    template: str = "generic",
    package: bool = False,
) -> None:
    """初始化脚本目录。

    参数:
        config: AlembicConfig 对象
        directory: 目标目录路径
        template: 使用的迁移环境模板
        package: 为 True 时，在目标目录和 versions/ 中创建 `__init__.py` 文件
    """

    if directory.exists() and next(directory.iterdir(), False):
        raise click.BadParameter(f'目录 "{directory}" 已存在并且不为空', param_hint="DIRECTORY")

    template_dir = Path(config.get_template_directory()) / template
    if not template_dir.exists():
        raise click.BadParameter(f"模板 {template} 不存在", param_hint="--template")

    with config.status(f'生成目录 "{directory}"'):
        shutil.copytree(
            template_dir,
            directory,
            ignore=None if package else shutil.ignore_patterns("__init__.py"),
            dirs_exist_ok=True,
        )


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
def revision(
    config: AlembicConfig,
    message: str | None = None,
    sql: bool | None = False,
    head: str = "head",
    splice: bool = False,
    branch_label: str | None = None,
    version_path: Path | None = None,
    rev_id: str | None = None,
    depends_on: str | None = None,
    process_revision_directives: ProcessRevisionDirectiveFn | None = None,
) -> Script | list[Script | None] | None:
    if version_path is not None:
        if version_path.exists() and next(version_path.iterdir(), False):
            raise click.BadParameter(
                f'目录 "{version_path}" 已存在并且不为空', param_hint="--version-path"
            )
        config.add_version_path(version_path)
        config.print_stdout(
            f'临时将目录 "{version_path}" 添加到版本目录中，请稍后将其添加到 VERSION_LOCATIONS 中',
            fg="yellow",
        )

    script_directory = ScriptDirectory.from_config(config)

    revision_context = RevisionContext(
        config,
        script_directory,
        dict(
            message=message,
            autogenerate=not sql,
            sql=sql,
            head=head,
            splice=splice,
            branch_label=branch_label,
            version_path=version_path,
            rev_id=rev_id,
            depends_on=depends_on,
        ),
        process_revision_directives=process_revision_directives,
    )

    if sql:

        def retrieve_migrations(rev, context):
            revision_context.run_no_autogenerate(rev, context)
            return ()

    else:

        def retrieve_migrations(rev, context):
            if set(script_directory.get_revisions(rev)) != set(
                script_directory.get_revisions("heads")
            ):
                raise click.UsageError("目标数据库未更新到最新修订")
            revision_context.run_autogenerate(rev, context)
            return ()

    with EnvironmentContext(
        config,
        script_directory,
        fn=retrieve_migrations,
        as_sql=sql,
        template_args=revision_context.template_args,
        revision_context=revision_context,
    ):
        script_directory.run_env()

    scripts = list(revision_context.generate_scripts())

    if temp_version_locations := click.get_current_context().meta.get(
        f"{__name__}.temp_version_locations"
    ):
        for script in filter(None, scripts):
            move_revision_script(config, script, temp_version_locations)

    return scripts[0] if len(scripts) == 1 else scripts


def move_revision_script(
    config: AlembicConfig,
    script: Script,
    temp_version_locations: Path,
) -> None:
    from . import config as plugin_config

    try:
        script_path = Path(script.path).relative_to(temp_version_locations)
    except ValueError:
        return

    plugin_name = (script_path.parent.parts or ("",))[0]
    if version_location := plugin_config.alembic_version_locations.get(plugin_name):
        (version_location / script_path.relative_to(plugin_name).parent).mkdir(
            exist_ok=True
        )
        script.path = shutil.move(
            script.path, version_location / script_path.relative_to(plugin_name)
        )
    elif version_location := plugin_config.alembic_version_locations.get(""):
        (version_location / script_path.parent).mkdir(exist_ok=True)
        script.path = shutil.move(script.path, version_location / script_path)
    else:
        config.print_stdout(
            f'无法找到 {plugin_name} 对应的版本目录，忽略 "{script.path}"', fg="yellow"
        )


@orm.command()
@click.pass_obj
def check(config: AlembicConfig) -> None:
    """Check if revision command with autogenerate has pending upgrade ops.

    :param config: a :class:`.Config` object.

    .. versionadded:: 1.9.0

    """

    script_directory = ScriptDirectory.from_config(config)

    command_args = dict(
        message=None,
        autogenerate=True,
        sql=False,
        head="head",
        splice=False,
        branch_label=None,
        version_path=None,
        rev_id=None,
        depends_on=None,
    )
    revision_context = RevisionContext(
        config,
        script_directory,
        command_args,
    )

    def retrieve_migrations(rev, context):
        if set(script_directory.get_revisions(rev)) != set(
            script_directory.get_revisions("heads")
        ):
            raise click.UsageError("目标数据库未更新到最新修订")
        revision_context.run_autogenerate(rev, context)
        return ()

    with EnvironmentContext(
        config,
        script_directory,
        fn=retrieve_migrations,
        as_sql=False,
        template_args=revision_context.template_args,
        revision_context=revision_context,
    ):
        script_directory.run_env()

    # the revision_context now has MigrationScript structure(s) present.

    migration_script = revision_context.generated_revisions[-1]
    diffs = cast(UpgradeOps, migration_script.upgrade_ops).as_diffs()
    if diffs:
        raise click.UsageError(f"检测到新的升级操作：{diffs}")
    else:
        config.print_stdout("没有检测到新的升级操作")


@orm.command()
@click.argument("revisions", nargs=-1)
@click.option("-m", "--message")
@click.option("--branch-label")
@click.option("--rev-id")
@click.pass_obj
def merge(
    config: AlembicConfig,
    revisions: tuple[str, ...],
    message: str | None = None,
    branch_label: str | None = None,
    rev_id: str | None = None,
) -> Script | None:
    """Merge two revisions together.  Creates a new migration file.

    :param config: a :class:`.Config` instance

    :param message: string message to apply to the revision

    :param branch_label: string label name to apply to the new revision

    :param rev_id: hardcoded revision identifier instead of generating a new
     one.

    .. seealso::

        :ref:`branches`

    """

    script_directory = ScriptDirectory.from_config(config)
    template_args = {
        "config": config  # Let templates use config for
        # e.g. multiple databases
    }

    environment = asbool(config.get_main_option("revision_environment"))

    if environment:
        with EnvironmentContext(
            config,
            script_directory,
            fn=lambda *_: (),
            as_sql=False,
            template_args=template_args,
        ):
            script_directory.run_env()

    script = script_directory.generate_revision(
        rev_id or _rev_id(),
        message,
        refresh=True,
        head=revisions,  # type: ignore[arg-type]
        branch_labels=branch_label,
        **template_args,  # type: ignore[arg-type]
    )
    temp_version_locations = click.get_current_context().meta.get(
        f"{__name__}.temp_version_locations"
    )
    if script and temp_version_locations:
        move_revision_script(config, script, temp_version_locations)
    return script


@orm.command()
@click.argument("revision", required=False)
@click.option("--sql", is_flag=True)
@click.option("--tag")
@click.pass_obj
def upgrade(
    config: AlembicConfig,
    revision: str | None = None,
    sql: bool = False,
    tag: str | None = None,
) -> None:
    """Upgrade to a later version.

    :param config: a :class:`.Config` instance.

    :param revision: string revision target or range for --sql mode

    :param sql: if True, use ``--sql`` mode

    :param tag: an arbitrary "tag" that can be intercepted by custom
     ``env.py`` scripts via the :meth:`.EnvironmentContext.get_tag_argument`
     method.

    """

    script = ScriptDirectory.from_config(config)

    if revision is None:
        revision = "head" if len(script.get_heads()) == 1 else "heads"

    starting_rev = None
    if ":" in revision:
        if not sql:
            raise click.BadParameter("不允许在非 --sql 模式下使用修订范围", param_hint="REVISION")
        starting_rev, revision = revision.split(":", 2)

    def upgrade(rev, _):
        return script._upgrade_revs(revision, rev)

    with EnvironmentContext(
        config,
        script,
        fn=upgrade,
        as_sql=sql,
        starting_rev=starting_rev,
        destination_rev=revision,
        tag=tag,
    ):
        script.run_env()


@orm.command()
@click.argument("revision")
@click.option("--sql", is_flag=True)
@click.option("--tag")
@click.pass_obj
def downgrade(
    config: AlembicConfig,
    revision: str,
    sql: bool = False,
    tag: str | None = None,
) -> None:
    """Revert to a previous version.

    :param config: a :class:`.Config` instance.

    :param revision: string revision target or range for --sql mode

    :param sql: if True, use ``--sql`` mode

    :param tag: an arbitrary "tag" that can be intercepted by custom
     ``env.py`` scripts via the :meth:`.EnvironmentContext.get_tag_argument`
     method.

    """

    script = ScriptDirectory.from_config(config)
    starting_rev = None
    if ":" in revision:
        if not sql:
            raise click.BadParameter("不允许在非 --sql 模式下使用修订范围", param_hint="REVISION")
        starting_rev, revision = revision.split(":", 2)
    elif sql:
        raise click.BadParameter(
            "--sql 模式下降级必须指定修订范围 <fromrev>:<torev>", param_hint="REVISION"
        )

    def downgrade(rev, _):
        return script._downgrade_revs(revision, rev)

    with EnvironmentContext(
        config,
        script,
        fn=downgrade,
        as_sql=sql,
        starting_rev=starting_rev,
        destination_rev=revision,
        tag=tag,
    ):
        script.run_env()


@orm.command()
@click.argument("rev", nargs=-1)
@click.pass_obj
def show(config, revs: str | Sequence[str] = "current"):
    """Show the revision(s) denoted by the given symbol.

    :param config: a :class:`.Config` instance.

    :param revision: string revision target

    """

    script = ScriptDirectory.from_config(config)

    if revs in {(), "current", ("current",)}:
        revs = []

        with EnvironmentContext(
            config, script, fn=lambda rev, _: revs.append(rev) or ()
        ):
            script.run_env()

    for sc in cast("tuple[Script]", script.get_revisions(revs)):
        config.print_stdout(sc.log_entry)


@orm.command()
@click.option("-r", "--rev-range", required=False)
@click.option("-v", "--verbose", is_flag=True)
@click.option("-i", "--indicate-current", is_flag=True)
@click.pass_obj
def history(
    config: AlembicConfig,
    rev_range: str | None = None,
    verbose: bool = False,
    indicate_current: bool = False,
) -> None:
    """List changeset scripts in chronological order.

    :param config: a :class:`.Config` instance.

    :param rev_range: string revision range

    :param verbose: output in verbose mode.

    :param indicate_current: indicate current revision.

    """

    script = ScriptDirectory.from_config(config)
    if rev_range is not None:
        if ":" not in rev_range:
            raise click.BadParameter(
                "历史范围应为 [start]:[end]、[start]: 或 :[end]", param_hint="REV_RANGE"
            )
        base, head = rev_range.strip().split(":")
    else:
        base = head = None

    environment = (
        asbool(config.get_main_option("revision_environment")) or indicate_current
    )

    def _display_history(config, script, base, head, currents=()):
        for sc in script.walk_revisions(base=base or "base", head=head or "heads"):
            if indicate_current:
                sc._db_current_indicator = sc.revision in currents

            config.print_stdout(
                sc.cmd_format(
                    verbose=verbose,
                    include_branches=True,
                    include_doc=True,
                    include_parents=True,
                )
            )

    def _display_history_w_current(config, script, base, head):
        def _display_current_history(rev):
            if head == "current":
                _display_history(config, script, base, rev, rev)
            elif base == "current":
                _display_history(config, script, rev, head, rev)
            else:
                _display_history(config, script, base, head, rev)

        revs = []
        with EnvironmentContext(
            config, script, fn=lambda rev, _: revs.append(rev) or ()
        ):
            script.run_env()

        for rev in revs:
            _display_current_history(rev)

    if base == "current" or head == "current" or environment:
        _display_history_w_current(config, script, base, head)
    else:
        _display_history(config, script, base, head)


@orm.command()
@click.option("-v", "--verbose", is_flag=True)
@click.option("--resolve-dependencies", is_flag=True)
@click.pass_obj
def heads(config, verbose: bool = False, resolve_dependencies: bool = False):
    """Show current available heads in the script directory.

    :param config: a :class:`.Config` instance.

    :param verbose: output in verbose mode.

    :param resolve_dependencies: treat dependency version as down revisions.

    """

    script = ScriptDirectory.from_config(config)
    if resolve_dependencies:
        heads = script.get_revisions("heads")
    else:
        heads = script.get_revisions(script.get_heads())

    for rev in cast("tuple[Script]", heads):
        config.print_stdout(
            rev.cmd_format(verbose, include_branches=True, tree_indicators=False)
        )


@orm.command()
@click.option("-v", "--verbose", is_flag=True)
@click.pass_obj
def branches(config, verbose=False):
    """Show current branch points.

    :param config: a :class:`.Config` instance.

    :param verbose: output in verbose mode.

    """

    script = ScriptDirectory.from_config(config)
    for sc in script.walk_revisions():
        if not sc.is_branch_point:
            continue

        config.print_stdout(
            "%s\n%s\n",
            sc.cmd_format(verbose, include_branches=True),
            "\n".join(
                "%s -> %s"
                % (
                    " " * len(str(sc.revision)),
                    cast(Script, script.get_revision(rev)).cmd_format(
                        False, include_branches=True, include_doc=verbose
                    ),
                )
                for rev in sc.nextrev
            ),
        )


@orm.command()
@click.option("-v", "--verbose", is_flag=True)
@click.pass_obj
def current(config: AlembicConfig, verbose: bool = False) -> None:
    """Display the current revision for a database.

    :param config: a :class:`.Config` instance.

    :param verbose: output in verbose mode.

    """

    script = ScriptDirectory.from_config(config)

    def display_version(rev, context):
        if verbose:
            config.print_stdout(
                "Current revision(s) for %s:",
                obfuscate_url_pw(context.connection.engine.url),
            )
        for rev in cast("set[Script]", script.get_all_current(rev)):
            config.print_stdout(rev.cmd_format(verbose))

        return ()

    with EnvironmentContext(config, script, fn=display_version, dont_mutate=True):
        script.run_env()


@orm.command()
@click.argument("revisions", nargs=-1)
@click.option("--sql", is_flag=True)
@click.option("--tag")
@click.option("--purge", is_flag=True)
@click.pass_obj
def stamp(
    config: AlembicConfig,
    revisions: tuple[str, ...] = ("heads",),
    sql: bool = False,
    tag: str | None = None,
    purge: bool = False,
) -> None:
    """'stamp' the revision table with the given revision; don't
    run any migrations.

    :param config: a :class:`.Config` instance.

    :param revision: target revision or list of revisions.   May be a list
     to indicate stamping of multiple branch heads.

     .. note:: this parameter is called "revisions" in the command line
        interface.

    :param sql: use ``--sql`` mode

    :param tag: an arbitrary "tag" that can be intercepted by custom
     ``env.py`` scripts via the :class:`.EnvironmentContext.get_tag_argument`
     method.

    :param purge: delete all entries in the version table before stamping.

    """

    revisions = revisions or ("heads",)
    script = ScriptDirectory.from_config(config)

    starting_rev = None
    if sql:
        destination_revs = []
        for revision in revisions:
            if ":" in revision:
                srev, revision = revision.split(":", 2)

                if starting_rev != srev:
                    if starting_rev is None:
                        starting_rev = srev
                    else:
                        raise click.BadParameter(
                            "--sql 模式下标记操作仅支持一个起始修订", param_hint="REVISIONS"
                        )
            destination_revs.append(revision)
    else:
        destination_revs = revisions

    def do_stamp(rev, _):
        return script._stamp_revs(destination_revs, rev)

    with EnvironmentContext(
        config,
        script,
        fn=do_stamp,
        as_sql=sql,
        starting_rev=starting_rev,
        destination_rev=destination_revs,
        tag=tag,
        purge=purge,
    ):
        script.run_env()


@orm.command()
@click.argument("rev", default="current")
@click.pass_obj
def edit(config: AlembicConfig, rev: str = "current") -> None:
    """Edit revision script(s) using $EDITOR.

    :param config: a :class:`.Config` instance.

    :param rev: target revision.

    """

    script = ScriptDirectory.from_config(config)
    temp_version_locations = click.get_current_context().meta.get(
        f"{__name__}.temp_version_locations"
    )

    if rev == "current":

        def edit_current(rev, _):
            if not rev:
                raise click.UsageError("当前没有修订")

            for sc in cast("tuple[Script]", script.get_revisions(rev)):
                if temp_version_locations:
                    move_revision_script(config, sc, temp_version_locations)

                open_in_editor(sc.path)

            return ()

        with EnvironmentContext(config, script, fn=edit_current):
            script.run_env()
    else:
        revs = script.get_revisions(rev)
        if not revs:
            raise click.BadParameter(f'没有 "{rev}" 指示的修订文件')
        for sc in revs:
            assert sc

            if temp_version_locations:
                move_revision_script(config, sc, temp_version_locations)

            open_in_editor(sc.path)


@orm.command("ensure_version")
@click.option("--sql", is_flag=True)
@click.pass_obj
def ensure_version(config: Config, sql: bool = False) -> None:
    """Create the alembic version table if it doesn't exist already .

    :param config: a :class:`.Config` instance.

    :param sql: use ``--sql`` mode

     .. versionadded:: 1.7.6

    """

    script = ScriptDirectory.from_config(config)

    def do_ensure_version(rev, context):
        context._ensure_version_table()
        return ()

    with EnvironmentContext(
        config,
        script,
        fn=do_ensure_version,
        as_sql=sql,
    ):
        script.run_env()


def main() -> None:
    from . import _init_orm

    _init_orm()
    orm(prog_name="nb orm")
