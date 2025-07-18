from __future__ import annotations

import os
import sys
import shutil
import inspect
from pathlib import Path
from pprint import pformat
from argparse import Namespace
from operator import attrgetter
from itertools import filterfalse
from tempfile import TemporaryDirectory
from configparser import DuplicateSectionError
from typing_extensions import Any, Self, TextIO, cast
from contextlib import ExitStack, suppress, contextmanager
from collections.abc import Mapping, Iterable, Sequence, Generator

import click
import alembic
import sqlalchemy
from alembic.config import Config
from sqlalchemy.util import asbool
from nonebot import logger, get_plugin
from nonebot.matcher import current_matcher
from sqlalchemy import MetaData, Connection
from alembic.util.editor import open_in_editor
from alembic.script import Script, ScriptDirectory
from alembic.util.langhelpers import rev_id as _rev_id
from alembic.operations.ops import UpgradeOps, DowngradeOps
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker
from alembic.migration import StampStep, RevisionStep, MigrationContext
from alembic.runtime.environment import EnvironmentContext, ProcessRevisionDirectiveFn
from alembic.autogenerate.api import (
    RevisionContext,
    produce_migrations,
    render_python_code,
)

from .exception import AutogenerateDiffsDetected
from .utils import is_editable, get_parent_plugins, return_progressbar

if sys.version_info >= (3, 12):
    from importlib.resources import files, as_file
else:
    from importlib_resources import files, as_file


__all__ = (
    "AlembicConfig",
    "list_templates",
    "init",
    "revision",
    "check",
    "merge",
    "upgrade",
    "downgrade",
    "sync",
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
    _exit_stack: ExitStack
    _plugin_version_locations: dict[str, Path]
    _temp_dir: Path

    def __init__(
        self,
        file_: str | os.PathLike[str] | None = None,
        toml_file: str | os.PathLike[str] | None = None,
        ini_section: str = "alembic",
        output_buffer: TextIO | None = None,
        stdout: TextIO = sys.stdout,
        cmd_opts: Namespace | None = None,
        config_args: Mapping[str, Any] = {},
        attributes: dict = {},
    ) -> None:
        from . import _engines, _metadatas, plugin_config

        self._exit_stack = ExitStack()
        self._plugin_version_locations = {}
        self._temp_dir = Path(self._exit_stack.enter_context(TemporaryDirectory()))

        if file_ is None and isinstance(plugin_config.alembic_config, Path):
            file_ = plugin_config.alembic_config

        if plugin_config.alembic_script_location:
            script_location = plugin_config.alembic_script_location
        elif (
            Path("migrations", "env.py").is_file()
            and Path("migrations", "script.py.mako").is_file()
        ):
            script_location = Path("migrations")
        elif len(_engines) == 1:
            script_location = self._exit_stack.enter_context(
                as_file(files(__name__) / "templates" / "generic")
            )
        else:
            script_location = self._exit_stack.enter_context(
                as_file(files(__name__) / "templates" / "multidb")
            )

        super().__init__(
            file_,
            toml_file,
            ini_section,
            output_buffer,
            stdout,
            cmd_opts,
            {
                "script_location": script_location,
                "prepend_sys_path": ".",
                "revision_environment": "true",
                "version_path_separator": "os",
            }
            | dict(config_args),
            {
                "engines": _engines,
                "metadatas": _metadatas,
            }
            | attributes,
        )

        self._init_post_write_hooks()
        self._init_version_locations()

    def __enter__(self: Self) -> Self:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        self._exit_stack.close()

    def get_template_directory(self) -> str:
        return str(Path(__file__).parent / "templates")

    def print_stdout(self, text: str, *arg, **kwargs) -> None:
        if not getattr(self.cmd_opts, "quite", False):
            click.secho(text % arg, self.stdout, **kwargs)

    @contextmanager
    def status(self, status_msg: str) -> Generator[None, Any, None]:
        self.print_stdout(f"{status_msg} ...", nl=False)

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
            script_path = script_path.relative_to(self._temp_dir)
        except ValueError:
            return script_path

        plugin_name = script_path.parent.name
        version_location = self._plugin_version_locations.get(plugin_name)

        if not version_location:
            version_location = self._plugin_version_locations.get("")

        if not version_location:
            self.print_stdout(
                f'无法找到 {plugin_name or "<default>"} 对应的版本目录, 忽略 "{script.path}"',
                fg="yellow",
            )
            return script_path

        version_location.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(script.path, version_location))

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

    def _init_version_locations(self) -> None:
        if self.get_main_option("version_locations"):
            return

        from . import _plugins, _data_dir, plugin_config

        alembic_version_locations = plugin_config.alembic_version_locations
        if isinstance(alembic_version_locations, dict):
            main_version_location = alembic_version_locations.get("")
        else:
            main_version_location = alembic_version_locations

        self._plugin_version_locations[""] = main_version_location or Path(
            "migrations", "versions"
        )

        temp_version_locations: dict[Path, Path] = {
            _data_dir / "migrations": self._temp_dir
        }

        for plugin in _plugins.values():
            if plugin.metadata and (
                version_module := plugin.metadata.extra.get("orm_version_location")
            ):
                if sys.version_info[:2] == (3, 9):
                    # importlib_resources.files() return a opaque Traversable object
                    # even if the anchor is a namespace package in Python 3.9
                    version_location = Path(version_module.__path__[0])
                else:
                    version_location = files(version_module)
            else:
                if sys.version_info[:2] == (3, 9):
                    version_location = Path(plugin.module.__path__[0]) / "migrations"
                else:
                    version_location = files(plugin.module) / "migrations"

            temp_version_location = Path(
                *map(attrgetter("name"), reversed(list(get_parent_plugins(plugin)))),
            )

            if (
                not main_version_location
                and is_editable(plugin)
                and isinstance(version_location, Path)
            ):
                self._plugin_version_locations[plugin.name] = version_location
            else:
                self._plugin_version_locations[plugin.name] = (
                    self._plugin_version_locations[""] / temp_version_location
                )

            temp_version_locations[
                self._exit_stack.enter_context(as_file(version_location))
            ] = (self._temp_dir / temp_version_location)

        if isinstance(alembic_version_locations, dict):
            for plugin_name, version_location in alembic_version_locations.items():
                if not (plugin := get_plugin(plugin_name)):
                    continue

                version_location = Path(version_location)
                self._plugin_version_locations[plugin_name] = version_location
                temp_version_locations[version_location] = self._temp_dir.joinpath(
                    *map(
                        attrgetter("name"),
                        reversed(list(get_parent_plugins(plugin))),
                    )
                )

        temp_version_locations[self._plugin_version_locations[""]] = self._temp_dir

        for src, dst in temp_version_locations.items():
            dst.mkdir(parents=True, exist_ok=True)
            with suppress(FileNotFoundError, shutil.Error):
                shutil.copytree(src, dst, dirs_exist_ok=True)

        pathsep = _SPLIT_ON_PATH[self.get_main_option("version_path_separator")]
        self.set_main_option(
            "version_locations",
            pathsep.join(
                str(path)
                for path in self._temp_dir.glob("**")
                if path.name != "__pycache__"
            ),
        )


def _move_run_scripts(config: AlembicConfig, script: ScriptDirectory, current) -> None:
    from . import _data_dir

    def ignore(path: str, names: list[str]) -> set[str]:
        path_ = Path(path)

        return set(
            name
            for name in names
            if Path(name).suffix in {".py", ".pyc", ".pyo"}
            and path_ / name not in run_script_path
        )

    run_script_path = set(
        Path(sc.path) for sc in script.walk_revisions(base="base", head=current)
    )
    shutil.rmtree(_data_dir / "migrations", ignore_errors=True)
    shutil.copytree(
        config._temp_dir, _data_dir / "migrations", ignore=ignore, dirs_exist_ok=True
    )


def list_templates(config: AlembicConfig) -> None:
    """列出所有可用的模板.

    参数:
        config: `AlembicConfig` 对象
    """

    config.print_stdout("可用的模板：\n")
    for tempname in Path(config.get_template_directory()).iterdir():
        with (tempname / "README").open(encoding="utf-8") as readme:
            synopsis = readme.readline().rstrip()

        config.print_stdout(f"{tempname.name} - {synopsis}")

    config.print_stdout('\n可以通过 "init" 命令使用模板, 例如: ')
    config.print_stdout("\n  nb orm init --template generic ./scripts")


def init(
    config: AlembicConfig,
    directory: Path = Path("migrations"),
    template: str = "generic",
    package: bool = False,
) -> None:
    """初始化脚本目录.

    参数:
        config: `AlembicConfig` 对象
        directory: 目标目录路径
        template: 使用的迁移环境模板
        package: 为 True 时, 在脚本目录和版本目录中创建 `__init__.py` 脚本
    """

    if (
        directory.is_dir()
        and next(directory.iterdir(), False)
        and not click.confirm(f'目录 "{directory}" 已存在并且不为空, 是否继续初始化?')
    ):
        raise click.BadParameter(
            f'目录 "{directory}" 已存在并且不为空', param_hint="DIRECTORY"
        )

    template_dir = Path(config.get_template_directory()) / template
    if not template_dir.is_dir():
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
    head: str | None = None,
    splice: bool = False,
    branch_label: str | None = None,
    version_path: str | Path | None = None,
    rev_id: str | None = None,
    depends_on: str | None = None,
    process_revision_directives: ProcessRevisionDirectiveFn | None = None,
) -> Iterable[Script]:
    """创建一个新迁移脚本.

    参数:
        config: `AlembicConfig` 对象
        message: 迁移的描述
        sql: 是否以 SQL 的形式输出迁移脚本
        head: 迁移的基准版本, 如果提供了 branch_label 默认为 `branch_label@head`, 否则为主分支的头
        splice: 是否将迁移作为一个新的分支的头; 当 `head` 不是一个分支的头时, 此项必须为 `True`
        branch_label: 迁移的分支标签
        version_path: 存放迁移脚本的目录
        rev_id: 迁移的 ID
        depends_on: 迁移的依赖
        process_revision_directives: 迁移的处理函数, 参见: `alembic.EnvironmentContext.configure.process_revision_directives`
    """
    from . import _plugins

    if version_path:
        version_path = Path(version_path).resolve()
        version_locations = config.get_main_option("version_locations", "")
        pathsep = _SPLIT_ON_PATH[config.get_main_option("version_path_separator")]

        if version_path not in (
            Path(path).resolve() for path in version_locations.split(pathsep)
        ):
            config.set_main_option(
                "version_locations", f"{version_locations}{pathsep}{version_path}"
            )
            logger.warning(
                f'临时将目录 "{version_path}" 添加到版本目录中, 请稍后将其添加到 ALEMBIC_VERSION_LOCATIONS 中'
            )
    elif branch_label and (plugin := _plugins.get(branch_label)):
        version_path = config._temp_dir.joinpath(
            *map(
                attrgetter("name"),
                reversed(list(get_parent_plugins(plugin))),
            )
        )
    elif not head:
        version_path = config._temp_dir

    if isinstance(version_path, Path):
        version_path = str(version_path)

    script = ScriptDirectory.from_config(config)

    if not head:
        scripts = script.get_revisions(script.get_heads())
        if branch_label:
            if any(branch_label in sc.branch_labels for sc in scripts):
                head = f"{branch_label}@head"
                branch_label = None
            else:
                head = "base"
        elif len(scripts) <= 1:
            head = "head"
        else:
            try:
                head = next(filterfalse(attrgetter("branch_labels"), scripts)).revision
            except StopIteration:
                head = "base"

    revision_context = RevisionContext(
        config,
        script,
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

        def retrieve_migrations(
            rev, context: MigrationContext
        ) -> Iterable[StampStep | RevisionStep]:
            revision_context.run_no_autogenerate(rev, context)
            return ()

    else:

        def retrieve_migrations(
            rev, context: MigrationContext
        ) -> Iterable[StampStep | RevisionStep]:
            if set(script.get_revisions(rev)) != set(script.get_revisions("heads")):
                raise click.UsageError(
                    "目标数据库未更新到最新迁移. 请通过 `nb orm upgrade` 升级数据库后重试."
                )
            revision_context.run_autogenerate(rev, context)
            return ()

    with EnvironmentContext(
        config,
        script,
        fn=retrieve_migrations,
        as_sql=sql,
        template_args=revision_context.template_args,
        revision_context=revision_context,
    ):
        script.run_env()

    return filter(None, revision_context.generate_scripts())


def check(config: AlembicConfig) -> None:
    """检查数据库是否与模型定义一致.

    参数:
        config: `AlembicConfig` 对象
    """

    script = ScriptDirectory.from_config(config)

    revision_context = RevisionContext(
        config,
        script,
        dict(
            message=None,
            autogenerate=True,
            sql=False,
            head="head",
            splice=False,
            branch_label=None,
            version_path=None,
            rev_id=None,
            depends_on=None,
        ),
    )

    def retrieve_migrations(
        rev, context: MigrationContext
    ) -> Iterable[StampStep | RevisionStep]:
        if set(script.get_revisions(rev)) != set(script.get_revisions("heads")):
            raise click.UsageError(
                "目标数据库未更新到最新迁移. 请通过 `nb orm upgrade` 升级数据库后重试."
            )
        revision_context.run_autogenerate(rev, context)
        return ()

    with EnvironmentContext(
        config,
        script,
        fn=retrieve_migrations,
        as_sql=False,
        template_args=revision_context.template_args,
        revision_context=revision_context,
    ):
        script.run_env()

    migration_script = revision_context.generated_revisions[-1]
    diffs = cast(UpgradeOps, migration_script.upgrade_ops).as_diffs()
    if diffs:
        raise AutogenerateDiffsDetected(
            f"检测到新的升级操作:\n{pformat(diffs)}", revision_context, diffs
        )
    else:
        config.print_stdout("没有检测到新的升级操作")


def merge(
    config: AlembicConfig,
    revisions: tuple[str, ...],
    message: str | None = None,
    branch_label: str | None = None,
    rev_id: str | None = None,
) -> Iterable[Script]:
    """合并多个迁移. 创建一个新的迁移脚本.

    参数:
        config: `AlembicConfig` 对象
        revisions: 要合并的迁移
        message: 迁移的描述
        branch_label: 迁移的分支标签
        rev_id: 迁移的 ID
    """

    script = ScriptDirectory.from_config(config)
    template_args: dict[str, Any] = {"config": config}

    environment = asbool(config.get_main_option("revision_environment"))

    if environment:
        with EnvironmentContext(
            config,
            script,
            fn=lambda *_: (),
            as_sql=False,
            template_args=template_args,
        ):
            script.run_env()

    sc = script.generate_revision(
        rev_id or _rev_id(),
        message,
        refresh=True,
        head=revisions,
        branch_labels=branch_label,
        **template_args,
    )
    return (sc,) if sc else ()


def upgrade(
    config: AlembicConfig,
    revision: str | None = None,
    sql: bool = False,
    tag: str | None = None,
) -> None:
    """升级到较新版本.

    参数:
        config: `AlembicConfig` 对象
        revision: 目标迁移
        sql: 是否以 SQL 的形式输出迁移脚本
        tag: 一个任意的字符串, 可在自定义的 `env.py` 中通过 `alembic.EnvironmentContext.get_tag_argument` 获得
    """

    script = ScriptDirectory.from_config(config)

    if revision is None:
        revision = "head" if len(script.get_heads()) == 1 else "heads"

    starting_rev = None
    if ":" in revision:
        if not sql:
            raise click.BadParameter(
                "不允许在非 --sql 模式下使用迁移范围", param_hint="REVISION"
            )
        starting_rev, revision = revision.split(":", 2)

    @return_progressbar
    def upgrade(rev, _) -> Iterable[StampStep | RevisionStep]:
        from . import _patch_migrate_session

        with _patch_migrate_session():
            yield from script._upgrade_revs(revision, rev)

        _move_run_scripts(config, script, revision)

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
        revision: 目标迁移
        sql: 是否以 SQL 的形式输出迁移脚本
        tag: 一个任意的字符串, 可在自定义的 `env.py` 中通过 `alembic.EnvironmentContext.get_tag_argument` 获得
    """

    script = ScriptDirectory.from_config(config)
    starting_rev = None
    if ":" in revision:
        if not sql:
            raise click.BadParameter(
                "不允许在非 --sql 模式下使用迁移范围", param_hint="REVISION"
            )
        starting_rev, revision = revision.split(":", 2)
    elif sql:
        raise click.BadParameter(
            "--sql 模式下降级必须指定迁移范围 <fromrev>:<torev>", param_hint="REVISION"
        )

    @return_progressbar
    def downgrade(rev, _) -> Iterable[StampStep | RevisionStep]:
        from . import _patch_migrate_session

        with _patch_migrate_session():
            yield from script._downgrade_revs(revision, rev)

        _move_run_scripts(config, script, revision)

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


def sync(config: AlembicConfig, revision: str | None = None):
    """同步数据库模式 (仅用于开发).

    参数:
        config: `AlembicConfig` 对象
        revision: 目标迁移, 如果不提供则与当前模型同步
    """
    script = ScriptDirectory.from_config(config)

    revision_context = RevisionContext(
        config,
        script,
        dict(
            message=None,
            autogenerate=True,
            sql=False,
            head="head",
            splice=False,
            branch_label=None,
            version_path=None,
            rev_id=None,
            depends_on=None,
        ),
    )

    def retrieve_migrations(
        rev, context: MigrationContext
    ) -> Iterable[StampStep | RevisionStep]:
        assert context.connection

        metadata = MetaData() if revision else context.opts["target_metadata"]
        ops = cast(UpgradeOps, produce_migrations(context, metadata).upgrade_ops)

        if not (revision or ops.as_diffs()):
            return

        try:
            _run_ops(context, ops)
        except Exception:
            if revision:
                raise

            _run_ops(
                context,
                cast(UpgradeOps, produce_migrations(context, MetaData()).upgrade_ops),
            )
            metadata.create_all(context.connection)

        yield from script._stamp_revs("base", rev)

        if revision:
            yield from script._upgrade_revs(revision, "base")

        _move_run_scripts(config, script, revision or "base")

    with EnvironmentContext(
        config,
        script,
        fn=retrieve_migrations,
        as_sql=False,
        template_args=revision_context.template_args,
        revision_context=revision_context,
    ):
        script.run_env()


def _run_ops(context: MigrationContext, ops: UpgradeOps | DowngradeOps) -> None:
    with context.begin_transaction(True):
        exec(
            inspect.cleandoc(
                render_python_code(
                    ops,
                    render_as_batch=True,
                )
            ),
            {"sa": sqlalchemy, "op": alembic.op},
        )


def show(config: AlembicConfig, revs: str | Sequence[str] = "current") -> None:
    """显示迁移的信息.

    参数:
        config: `AlembicConfig` 对象
        revs: 目标迁移范围
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
    """显示迁移的历史.

    参数:
        config: `AlembicConfig` 对象
        rev_range: 迁移范围
        verbose: 是否显示详细信息
        indicate_current: 指示出当前迁移
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
        resolve_dependencies: 是否将依赖的迁移视作父迁移
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
    """显示当前的迁移.

    参数:
        config: `AlembicConfig` 对象
        verbose: 是否显示详细信息
    """

    script = ScriptDirectory.from_config(config)

    def display_version(
        rev, context: MigrationContext
    ) -> Iterable[StampStep | RevisionStep]:
        if verbose:
            config.print_stdout(
                "Current revision(s) for %s:",
                cast(Connection, context.connection).engine.url.render_as_string(),
            )
        for sc in cast("set[Script]", script.get_all_current(rev)):
            config.print_stdout(sc.cmd_format(verbose))

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
    """将数据库标记为特定的迁移版本, 不运行任何迁移.

    参数:
        config: `AlembicConfig` 对象
        revisions: 目标迁移
        sql: 是否以 SQL 的形式输出迁移脚本
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
                            "--sql 模式下标记操作仅支持一个起始迁移",
                            param_hint="REVISIONS",
                        )
            destination_revs.append(revision)
    else:
        destination_revs = revisions

    def do_stamp(rev, _) -> Iterable[StampStep | RevisionStep]:
        yield from script._stamp_revs(destination_revs, rev)
        _move_run_scripts(config, script, destination_revs)

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
    """使用 `$EDITOR` 编辑迁移脚本.

    参数:
        config: `AlembicConfig` 对象
        rev: 目标迁移
    """

    script = ScriptDirectory.from_config(config)

    if rev == "current":

        def edit_current(rev, _) -> Iterable[StampStep | RevisionStep]:
            if not rev:
                raise click.UsageError("当前没有迁移")

            for sc in cast("tuple[Script]", script.get_revisions(rev)):
                script_path = config.move_script(sc)
                open_in_editor(str(script_path))

            return ()

        with EnvironmentContext(config, script, fn=edit_current):
            script.run_env()
    else:
        revs = cast("tuple[Script, ...]", script.get_revisions(rev))

        if not revs:
            raise click.BadParameter(f'没有 "{rev}" 指示的迁移脚本')

        for sc in cast("tuple[Script]", revs):
            script_path = config.move_script(sc)
            open_in_editor(str(script_path))


def ensure_version(config: AlembicConfig, sql: bool = False) -> None:
    """创建版本表.

    参数:
        config: `AlembicConfig` 对象
        sql: 是否以 SQL 的形式输出迁移脚本
    """

    script = ScriptDirectory.from_config(config)

    def do_ensure_version(
        _, context: MigrationContext
    ) -> Iterable[StampStep | RevisionStep]:
        context._ensure_version_table()
        return ()

    with EnvironmentContext(
        config,
        script,
        fn=do_ensure_version,
        as_sql=sql,
    ):
        script.run_env()
