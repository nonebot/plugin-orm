from __future__ import annotations

import sys
import json
import logging
from io import StringIO
from pathlib import Path
from functools import wraps
from itertools import repeat
from contextlib import suppress
from typing import Any, TypeVar
from operator import methodcaller
from typing_extensions import Annotated
from dataclasses import field, dataclass
from inspect import Parameter, Signature, isclass
from collections.abc import Callable, Iterable, Generator
from importlib.metadata import Distribution, PackageNotFoundError, distribution

import click
from nonebot.plugin import Plugin
from nonebot.params import Depends
from nonebot import logger, get_driver
from sqlalchemy.sql.selectable import ExecutableReturnsRows
from pydantic.typing import (
    get_args,
    is_union,
    get_origin,
    is_literal_type,
    all_literal_values,
)

if sys.version_info >= (3, 9):
    from importlib.resources import files
else:
    from importlib_resources import files

if sys.version_info >= (3, 10):
    from typing import ParamSpec
    from importlib.metadata import packages_distributions
else:
    from typing_extensions import ParamSpec
    from importlib_metadata import packages_distributions


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
        depth += 1

        for line in buffer.rstrip().splitlines():
            logger.opt(depth=depth).log(self._level, line.rstrip())

    def flush(self):
        pass


@dataclass
class Option:
    stream: bool = True
    scalars: bool = False
    result: methodcaller | None = None
    calls: list[methodcaller] = field(default_factory=list)


def compile_dependency(statement: ExecutableReturnsRows, option: Option) -> Any:
    from . import async_scoped_session

    async def __dependency(*, __session: async_scoped_session, **params: Any):
        if option.stream:
            result = await __session.stream(statement, params)
        else:
            result = await __session.execute(statement, params)

        for call in option.calls:
            result = call(result)

        if option.scalars:
            result = result.scalars()

        if call := option.result:
            result = call(result)

            if option.stream:
                result = await result

        return result

    __dependency.__signature__ = Signature(
        [
            Parameter(
                "__session", Parameter.KEYWORD_ONLY, annotation=async_scoped_session
            ),
            *(
                Parameter(name, Parameter.KEYWORD_ONLY, default=depends)
                for name, depends in statement.compile().params.items()
                if isinstance(depends, DependsInner)
            ),
        ]
    )

    return Depends(__dependency)


def generic_issubclass(scls: Any, cls: Any) -> Any:
    if cls is Any:
        return True

    if scls is Any:
        return cls

    if isclass(scls) and (isclass(cls) or isinstance(cls, tuple)):
        return issubclass(scls, cls)

    scls_origin, scls_args = get_origin(scls) or scls, get_args(scls)
    cls_origin, cls_args = get_origin(cls) or cls, get_args(cls)

    if scls_origin is tuple and cls_origin is tuple:
        if len(scls_args) == 2 and scls_args[1] is Ellipsis:
            return generic_issubclass(scls_args[0], cls_args)

        if len(cls_args) == 2 and cls_args[1] is Ellipsis:
            return all(map(generic_issubclass, scls_args, repeat(cls_args[0])))

    if scls_origin is Annotated:
        return generic_issubclass(scls_args[0], cls)
    if cls_origin is Annotated:
        return generic_issubclass(scls, cls_args[0])

    if is_union(scls_origin):
        return all(map(generic_issubclass, scls_args, repeat(cls)))
    if is_union(cls_origin):
        return generic_issubclass(scls, cls_args)

    if is_literal_type(scls) and is_literal_type(cls):
        return set(all_literal_values(scls)) <= set(all_literal_values(cls))

    try:
        if not issubclass(scls_origin, cls_origin):
            return False
    except TypeError:
        return False

    if len(scls_args) == 1 and len(cls_args) <= 1:
        return generic_issubclass(scls_args[0], (cls_args or (Any,))[0])

    return len(scls_args) == len(cls_args) and all(
        map(generic_issubclass, scls_args, cls_args)
    )


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

    path = files(plugin.module)
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


if sys.version_info >= (3, 10):
    from inspect import get_annotations as get_annotations  # nopycln: import
else:
    #  https://github.com/python/cpython/blob/63a7f7765c6e9c1c9b93b7692e828ecf7bbd3bb9/Lib/inspect.py#L66-L178
    import types
    import functools

    def get_annotations(obj, *, globals=None, locals=None, eval_str=False):
        # sourcery skip
        """Compute the annotations dict for an object.

        obj may be a callable, class, or module.
        Passing in an object of any other type raises TypeError.

        Returns a dict.  get_annotations() returns a new dict every time
        it's called; calling it twice on the same object will return two
        different but equivalent dicts.

        This function handles several details for you:

        * If eval_str is true, values of type str will
            be un-stringized using eval().  This is intended
            for use with stringized annotations
            ("from __future__ import annotations").
        * If obj doesn't have an annotations dict, returns an
            empty dict.  (Functions and methods always have an
            annotations dict; classes, modules, and other types of
            callables may not.)
        * Ignores inherited annotations on classes.  If a class
            doesn't have its own annotations dict, returns an empty dict.
        * All accesses to object members and dict values are done
            using getattr() and dict.get() for safety.
        * Always, always, always returns a freshly-created dict.

        eval_str controls whether or not values of type str are replaced
        with the result of calling eval() on those values:

        * If eval_str is true, eval() is called on values of type str.
        * If eval_str is false (the default), values of type str are unchanged.

        globals and locals are passed in to eval(); see the documentation
        for eval() for more information.  If either globals or locals is
        None, this function may replace that value with a context-specific
        default, contingent on type(obj):

        * If obj is a module, globals defaults to obj.__dict__.
        * If obj is a class, globals defaults to
            sys.modules[obj.__module__].__dict__ and locals
            defaults to the obj class namespace.
        * If obj is a callable, globals defaults to obj.__globals__,
            although if obj is a wrapped function (using
            functools.update_wrapper()) it is first unwrapped.
        """
        if isinstance(obj, type):
            # class
            obj_dict = getattr(obj, "__dict__", None)
            if obj_dict and hasattr(obj_dict, "get"):
                ann = obj_dict.get("__annotations__", None)
                if isinstance(ann, types.GetSetDescriptorType):
                    ann = None
            else:
                ann = None

            obj_globals = None
            module_name = getattr(obj, "__module__", None)
            if module_name:
                module = sys.modules.get(module_name, None)
                if module:
                    obj_globals = getattr(module, "__dict__", None)
            obj_locals = dict(vars(obj))
            unwrap = obj
        elif isinstance(obj, types.ModuleType):
            # module
            ann = getattr(obj, "__annotations__", None)
            obj_globals = getattr(obj, "__dict__")
            obj_locals = None
            unwrap = None
        elif callable(obj):
            # this includes types.Function, types.BuiltinFunctionType,
            # types.BuiltinMethodType, functools.partial, functools.singledispatch,
            # "class funclike" from Lib/test/test_inspect... on and on it goes.
            ann = getattr(obj, "__annotations__", None)
            obj_globals = getattr(obj, "__globals__", None)
            obj_locals = None
            unwrap = obj
        else:
            raise TypeError(f"{obj!r} is not a module, class, or callable.")

        if ann is None:
            return {}

        if not isinstance(ann, dict):
            raise ValueError(f"{obj!r}.__annotations__ is neither a dict nor None")

        if not ann:
            return {}

        if not eval_str:
            return dict(ann)

        if unwrap is not None:
            while True:
                if hasattr(unwrap, "__wrapped__"):
                    unwrap = unwrap.__wrapped__  # type: ignore[attr-defined]
                    continue
                if isinstance(unwrap, functools.partial):
                    unwrap = unwrap.func
                    continue
                break
            if hasattr(unwrap, "__globals__"):
                obj_globals = unwrap.__globals__  # type: ignore[attr-defined]

        if globals is None:
            globals = obj_globals
        if locals is None:
            locals = obj_locals

        return_value = {
            key: value if not isinstance(value, str) else eval(value, globals, locals)
            for key, value in ann.items()
        }
        return return_value
