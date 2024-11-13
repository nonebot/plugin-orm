from __future__ import annotations

import sys
import json
import logging
from io import StringIO
from pathlib import Path
from functools import wraps
from itertools import repeat
from contextlib import suppress
from operator import methodcaller
from importlib.resources import files
from dataclasses import field, dataclass
from inspect import Parameter, Signature
from collections.abc import Callable, Iterable, Coroutine, Generator
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from typing_extensions import (
    TYPE_CHECKING,
    Any,
    TypeVar,
    Annotated,
    ParamSpec,
    get_args,
    get_origin,
)

import click
from nonebot.plugin import Plugin
from nonebot.params import Depends
from nonebot import logger, get_driver
from sqlalchemy.sql.selectable import ExecutableReturnsRows
from nonebot.typing import origin_is_union, origin_is_literal

if sys.version_info >= (3, 10):
    from importlib.metadata import packages_distributions
else:
    from importlib_metadata import packages_distributions


if TYPE_CHECKING:
    from . import async_scoped_session


_T = TypeVar("_T")
_P = ParamSpec("_P")


DependsInner = type(Depends())


class LoguruHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            level = logger.level(record.levelname).name
            if record.levelno <= logging.INFO:
                level = {"DEBUG": "TRACE", "INFO": "DEBUG"}.get(level, level)
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


class StreamToLogger(StringIO):
    """Use for startup migrate, AlembicConfig.print_stdout() only"""

    def __init__(self, level="INFO"):
        self._level = level

    def write(self, buffer: str):
        frame, depth = sys._getframe(3), 3
        while frame and frame.f_code.co_name != "print_stdout":
            frame = frame.f_back
            depth += 1

        for line in buffer.rstrip().splitlines():
            logger.opt(depth=depth + 1).log(self._level, line.rstrip())

        return len(buffer)

    def flush(self):
        pass


@dataclass(unsafe_hash=True)
class Option:
    stream: bool = True
    scalars: bool = False
    calls: tuple[methodcaller, ...] = field(default_factory=tuple)
    result: methodcaller | None = None


@dataclass
class Dependency:
    __signature__: Signature = field(init=False)

    statement: ExecutableReturnsRows
    option: Option

    def __post_init__(self) -> None:
        from . import async_scoped_session

        self.__signature__ = Signature(
            [
                Parameter(
                    "_session", Parameter.KEYWORD_ONLY, annotation=async_scoped_session
                ),
                *(
                    Parameter(name, Parameter.KEYWORD_ONLY, default=depends)
                    for name, depends in self.statement.compile().params.items()
                    if isinstance(depends, DependsInner)
                ),
            ]
        )

    async def __call__(self, *, _session: async_scoped_session, **params: Any) -> Any:
        if self.option.stream:
            result = await _session.stream(self.statement, params)
        else:
            result = await _session.execute(self.statement, params)

        if self.option.scalars:
            result = result.scalars()

        for call in self.option.calls:
            result = call(result)

        if call := self.option.result:
            result = call(result)

            if self.option.stream:
                result = await result

        return result

    def __hash__(self) -> int:
        return hash((self.statement, self.option))


def generic_issubclass(scls: Any, cls: Any) -> bool | list[Any]:
    if isinstance(cls, tuple):
        return _map_generic_issubclass(repeat(scls), cls)

    if scls is Any:
        return [cls]

    if cls is Any:
        return True

    with suppress(TypeError):
        return issubclass(scls, cls)

    scls_origin, scls_args = get_origin(scls) or scls, get_args(scls)
    cls_origin, cls_args = get_origin(cls) or cls, get_args(cls)

    if scls_origin is tuple and cls_origin is tuple:
        if len(scls_args) == 2 and scls_args[1] is Ellipsis:
            return generic_issubclass(scls_args[0], cls_args)

        if len(cls_args) == 2 and cls_args[1] is Ellipsis:
            return _map_generic_issubclass(
                scls_args, repeat(cls_args[0]), failfast=True
            )

    if scls_origin is Annotated:
        return generic_issubclass(scls_args[0], cls)
    if cls_origin is Annotated:
        return generic_issubclass(scls, cls_args[0])

    if origin_is_union(scls_origin):
        return _map_generic_issubclass(scls_args, repeat(cls), failfast=True)
    if origin_is_union(cls_origin):
        return generic_issubclass(scls, cls_args)

    if origin_is_literal(scls_origin) and origin_is_literal(cls_origin):
        return set(scls_args) <= set(cls_args)

    try:
        if not issubclass(scls_origin, cls_origin):
            return False
    except TypeError:
        return False

    if not cls_args:
        return True

    if len(scls_args) != len(cls_args):
        return False

    return _map_generic_issubclass(scls_args, cls_args, failfast=True)


def _map_generic_issubclass(
    scls: Iterable[Any], cls: Iterable[Any], *, failfast: bool = False
) -> bool | list[Any]:
    results = []
    for scls_arg, cls_arg in zip(scls, cls):
        if not (result := generic_issubclass(scls_arg, cls_arg)) and failfast:
            return False
        elif isinstance(result, list):
            results.extend(result)
        elif not isinstance(result, bool):
            results.append(result)

    return results or False


def return_progressbar(func: Callable[_P, Iterable[_T]]) -> Callable[_P, Iterable[_T]]:
    log_level = get_driver().config.log_level
    if isinstance(log_level, str):
        log_level = logger.level(log_level).no

    if log_level <= logger.level("INFO").no:
        return func

    @wraps(func)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Iterable[_T]:
        with click.progressbar(
            func(*args, **kwargs), label="运行迁移中", item_show_func=str
        ) as bar:
            yield from bar

    return wrapper


def get_parent_plugins(plugin: Plugin | None) -> Generator[Plugin, Any, None]:
    while plugin:
        yield plugin
        plugin = plugin.parent_plugin


pkgs = packages_distributions()


def is_editable(plugin: Plugin) -> bool:
    *_, plugin = get_parent_plugins(plugin)

    try:
        path = files(plugin.module)
    except TypeError:
        return False

    if not isinstance(path, Path) or "site-packages" in path.parts:
        return False

    dist: Distribution | None = None

    with suppress(PackageNotFoundError):
        dist = distribution(plugin.name.replace("_", "-"))

    if not dist and plugin.module.__file__:
        path = Path(plugin.module.__file__)
        for name in pkgs.get(plugin.module_name.split(".")[0], ()):
            dist = distribution(name)
            if path in (file.locate() for file in dist.files or ()):
                break
        else:
            dist = None

    if not dist:
        return True

    # https://github.com/pdm-project/pdm/blob/fee1e6bffd7de30315e2134e19f9a6f58e15867c/src/pdm/utils.py#L361-L374
    if getattr(dist, "link_file", None) is not None:
        return True

    direct_url = dist.read_text("direct_url.json")
    if not direct_url:
        return False

    direct_url_data = json.loads(direct_url)
    return direct_url_data.get("dir_info", {}).get("editable", False)


def get_subclasses(cls: type[_T]) -> Generator[type[_T], None, None]:
    yield from cls.__subclasses__()
    for subclass in cls.__subclasses__():
        yield from get_subclasses(subclass)


def coroutine(func: Callable[_P, _T]) -> Callable[_P, Coroutine[Any, Any, _T]]:
    @wraps(func)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        return func(*args, **kwargs)

    return wrapper
