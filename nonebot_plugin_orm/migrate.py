from __future__ import annotations

import os
import sys
import shutil
from pathlib import Path
from argparse import Namespace
from typing import Any, TextIO, cast
from tempfile import TemporaryDirectory
from configparser import DuplicateSectionError
from importlib.resources import files, as_file
from contextlib import suppress, contextmanager
from collections.abc import Mapping, Iterable, Sequence, Generator

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

from .utils import is_editable
from .config import config as plugin_config

if sys.version_info >= (3, 12):
    from typing import Self
else:
    from typing_extensions import Self


__all__ = (
    "AlembicConfig",
    "list_templates",
    "init",
    "revision",
    "check",
    "merge",
    "upgrade",
    "downgrade",
    "show",
    "history",
    "heads",
    "branches",
    "current",
    "stamp",
    "edit",
    "ensure_version",
)


_SPLIT_ON_PATH = {
    None: " ",
    "space": " ",
    "os": os.pathsep,
    ":": ":",
    ";": ";",
}


class AlembicConfig(Config):
    _temp_dir: TemporaryDirectory
    _plugin_version_locations: dict[str, Path]

    def __init__(
        self,
        file_: str | os.PathLike[str] | None = None,
        ini_section: str = "alembic",
        output_buffer: TextIO | None = None,
        stdout: TextIO = sys.stdout,
        cmd_opts: Namespace | None = None,
        config_args: Mapping[str, Any] = {},
        attributes: dict = {},
    ) -> None:
        from . import _engines, _metadatas

        if plugin_config.alembic_script_location:
            script_location = plugin_config.alembic_script_location
        elif (
            Path("migrations/env.py").exists()
            and Path("migrations/script.py.mako").exists()
        ):
            script_location = "migrations"
        else:
            script_location = str(Path(__file__).parent / "templates" / "multidb")

        super().__init__(
            file_,
            ini_section,
            output_buffer,
            stdout,
            cmd_opts,
            {
                "script_location": script_location,
                "prepend_sys_path": ".",
                "revision_environment": "true",
                "version_path_separator": "os",
                **config_args,
            },
            {
                "engines": _engines,
                "metadatas": _metadatas,
                **attributes,
            },
        )

        self._init_post_write_hooks()

    def __enter__(self) -> Self:
        self._temp_dir = TemporaryDirectory()

        if self.get_main_option("version_locations"):
            # NOTE: skip if explicitly set
            return self

        if not plugin_config.alembic_version_locations:
            self._plugin_version_locations = {"": Path("migrations/versions")}
        elif isinstance(plugin_config.alembic_version_locations, dict):
            self._plugin_version_locations = {
                name: Path(path)
                for name, path in plugin_config.alembic_version_locations.items()
            }
        else:
            # NOTE: special case: mono repo with a central version location
            self._plugin_version_locations = {
                "": Path(plugin_config.alembic_version_locations)
            }

        temp_version_locations = Path(self._temp_dir.name)
        self._add_version_location(temp_version_locations)

        for plugin in get_loaded_plugins():
            if not plugin.metadata or not (
                version_module := plugin.metadata.extra.get("orm_version_location")
            ):
                continue

            with as_file(files(version_module)) as version_location:
                if is_editable(plugin):
                    self._add_version_location(version_location)
                else:
                    # NOTE: read-only, copy to temp dir
                    self._add_version_location(temp_version_locations / plugin.name)
                    shutil.copytree(
                        version_location, temp_version_locations / plugin.name
                    )

        for plugin_name, version_location in self._plugin_version_locations.items():
            with suppress(FileNotFoundError):
                shutil.copytree(
                    version_location,
                    temp_version_locations / plugin_name,
                    dirs_exist_ok=True,
                )

        return self

    def __exit__(self, *_) -> None:
        self._temp_dir.cleanup()

    def get_template_directory(self) -> str:
        return str(Path(__file__).parent / "templates")

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

    def move_script(self, script: Script) -> Path:
        script_path = Path(script.path)

        try:
            script_path = script_path.relative_to(self._temp_dir.name)
        except ValueError:
            return script_path

        plugin_name = (script_path.parent.parts or ("",))[0]
        if version_location := self._plugin_version_locations.get(plugin_name):
            pass
        elif version_location := self._plugin_version_locations.get(""):
            plugin_name = ""
        else:
            self.print_stdout(
                f'无法找到 {plugin_name or "<default>"} 对应的版本目录，忽略 "{script.path}"',
                fg="yellow",
            )
            return script_path

        (version_location / script_path.relative_to(plugin_name).parent).mkdir(
            parents=True, exist_ok=True
        )
        return shutil.move(
            script.path, version_location / script_path.relative_to(plugin_name)
        )

    def _add_post_write_hook(self, name: str, **kwargs: str) -> None:
        self.set_section_option(
            "post_write_hooks",
            "hooks",
            f"{self.get_section_option('post_write_hooks', 'hooks', '')}, {name}",
        )
        for key, value in kwargs.items():
            self.set_section_option("post_write_hooks", f"{name}.{key}", value)

    def _init_post_write_hooks(self) -> None:
        with suppress(DuplicateSectionError):
            self.file_config.add_section("post_write_hooks")

        if self.get_section_option("post_write_hooks", "hooks"):
            return

        with suppress(ImportError):
            import isort

            del isort
            self._add_post_write_hook(
                "isort",
                type="console_scripts",
                entrypoint="isort",
                options="REVISION_SCRIPT_FILENAME --profile black",
            )

        with suppress(ImportError):
            import black

            del black
            self._add_post_write_hook(
                "black",
                type="console_scripts",
                entrypoint="black",
                options="REVISION_SCRIPT_FILENAME",
            )

    def _add_version_location(self, path: str | Path) -> None:
        pathsep = _SPLIT_ON_PATH[self.get_main_option("version_path_separator")]
        self.set_main_option(
            "version_locations",
            f"{self.get_main_option('version_locations', '')}{pathsep}{path}",
        )


def list_templates(config: AlembicConfig) -> None:
    """列出所有可用的模板.

    参数:
        config: `AlembicConfig` 对象
    """

    config.print_stdout("可用的模板：\n")
    for tempname in Path(config.get_template_directory()).iterdir():
        with (tempname / "README").open() as readme:
            synopsis = readme.readline().rstrip()

        config.print_stdout(f"{tempname.name} - {synopsis}")

    config.print_stdout('\n可以通过 "init" 命令使用模板，例如：')
    config.print_stdout("\n  nb orm init --template generic ./scripts")


def init(
    config: AlembicConfig,
    directory: Path = Path("migrations"),
    template: str = "multidb",
    package: bool = False,
) -> None:
    """初始化脚本目录.

    参数:
        config: `AlembicConfig` 对象
        directory: 目标目录路径
        template: 使用的迁移环境模板
        package: 为 True 时，在脚本目录和版本目录中创建 `__init__.py` 文件
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
) -> Iterable[Script]:
    """创建一个新修订文件.

    参数:
        config: `AlembicConfig` 对象
        message: 修订的描述
        sql: 是否以 SQL 的形式输出修订脚本
        head: 修订的基准版本
        splice: 是否将修订作为一个新的分支的头; 当 `head` 不是一个分支的头时, 此项必须为 `True`
        branch_label: 修订的分支标签
        version_path: 存放修订文件的目录
        rev_id: 修订的 ID
        depends_on: 修订的依赖
        process_revision_directives: 修订的处理函数, 参见: `alembic.EnvironmentContext.configure.process_revision_directives`
    """
    if version_path is not None:
        if version_path.exists() and next(version_path.iterdir(), False):
            raise click.BadParameter(
                f'目录 "{version_path}" 已存在并且不为空', param_hint="--version-path"
            )
        config._add_version_location(version_path)
        config.print_stdout(
            f'临时将目录 "{version_path}" 添加到版本目录中，请稍后将其添加到 ALEMBIC_VERSION_LOCATIONS 中',
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

    return filter(None, revision_context.generate_scripts())


def check(config: AlembicConfig) -> None:
    """检查数据库是否与模型定义一致.

    参数:
        config: `AlembicConfig` 对象
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


def merge(
    config: AlembicConfig,
    revisions: tuple[str, ...],
    message: str | None = None,
    branch_label: str | None = None,
    rev_id: str | None = None,
) -> Iterable[Script]:
    """合并多个修订. 创建一个新的修订文件.

    参数:
        config: `AlembicConfig` 对象
        revisions: 要合并的修订
        message: 修订的描述
        branch_label: 修订的分支标签
        rev_id: 修订的 ID
    """

    script_directory = ScriptDirectory.from_config(config)
    template_args = {"config": config}

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
    return (script,) if script else ()


def upgrade(
    config: AlembicConfig,
    revision: str | None = None,
    sql: bool = False,
    tag: str | None = None,
) -> None:
    """升级到较新版本.

    参数:
        config: `AlembicConfig` 对象
        revision: 目标修订
        sql: 是否以 SQL 的形式输出修订脚本
        tag: 一个任意的字符串, 可在自定义的 `env.py` 中通过 `alembic.EnvironmentContext.get_tag_argument` 获得
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


def downgrade(
    config: AlembicConfig,
    revision: str,
    sql: bool = False,
    tag: str | None = None,
) -> None:
    """回退到先前版本.

    参数:
        config: `AlembicConfig` 对象
        revision: 目标修订
        sql: 是否以 SQL 的形式输出修订脚本
        tag: 一个任意的字符串, 可在自定义的 `env.py` 中通过 `alembic.EnvironmentContext.get_tag_argument` 获得
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


def show(config: AlembicConfig, revs: str | Sequence[str] = "current") -> None:
    """显示修订的信息.

    参数:
        config: `AlembicConfig` 对象
        revs: 目标修订范围
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


def history(
    config: AlembicConfig,
    rev_range: str | None = None,
    verbose: bool = False,
    indicate_current: bool = False,
) -> None:
    """显示修订的历史.

    参数:
        config: `AlembicConfig` 对象
        rev_range: 修订范围
        verbose: 是否显示详细信息
        indicate_current: 指示出当前修订
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


def heads(
    config: AlembicConfig, verbose: bool = False, resolve_dependencies: bool = False
) -> None:
    """显示所有的分支头.

    参数:
        config: `AlembicConfig` 对象
        verbose: 是否显示详细信息
        resolve_dependencies: 是否将依赖的修订视作父修订
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


def branches(config: AlembicConfig, verbose: bool = False) -> None:
    """显示所有的分支.

    参数:
        config: `AlembicConfig` 对象
        verbose: 是否显示详细信息
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


def current(config: AlembicConfig, verbose: bool = False) -> None:
    """显示当前的修订.

    参数:
        config: `AlembicConfig` 对象
        verbose: 是否显示详细信息
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


def stamp(
    config: AlembicConfig,
    revisions: tuple[str, ...] = ("heads",),
    sql: bool = False,
    tag: str | None = None,
    purge: bool = False,
) -> None:
    """将数据库标记为特定的修订版本, 不运行任何迁移.

    参数:
        config: `AlembicConfig` 对象
        revisions: 目标修订
        sql: 是否以 SQL 的形式输出修订脚本
        tag: 一个任意的字符串, 可在自定义的 `env.py` 中通过 `alembic.EnvironmentContext.get_tag_argument` 获得
        purge: 是否在标记前清空数据库版本表
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


def edit(config: AlembicConfig, rev: str = "current") -> None:
    """使用 `$EDITOR` 编辑修订文件.

    参数:
        config: `AlembicConfig` 对象
        rev: 目标修订
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
                script_path = config.move_script(sc)
                open_in_editor(str(script_path))

            return ()

        with EnvironmentContext(config, script, fn=edit_current):
            script.run_env()
    else:
        revs = cast(tuple[Script, ...], script.get_revisions(rev))

        if not revs:
            raise click.BadParameter(f'没有 "{rev}" 指示的修订文件')

        for sc in cast(tuple[Script], revs):
            script_path = config.move_script(sc)
            open_in_editor(str(script_path))


def ensure_version(config: AlembicConfig, sql: bool = False) -> None:
    """创建版本表.

    参数:
        config: `AlembicConfig` 对象
        sql: 是否以 SQL 的形式输出修订脚本
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
