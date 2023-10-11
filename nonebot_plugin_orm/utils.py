from __future__ import annotations

import sys
import json
import logging
from io import StringIO
from typing import TypeVar
from contextlib import suppress
from functools import wraps, lru_cache
from collections.abc import Callable, Iterable
from importlib.metadata import Distribution, PackageNotFoundError, distribution

import click
from nonebot.plugin import Plugin
from nonebot.params import Depends
from nonebot import logger, get_driver

if sys.version_info >= (3, 10):
    from typing import ParamSpec
    from importlib.metadata import packages_distributions
else:
    from typing_extensions import ParamSpec

    from importlib_metadata import packages_distributions


_T = TypeVar("_T")
_P = ParamSpec("_P")


DependsInner = type(Depends())


class _ReturnEq:
    def __eq__(self, __o: _T) -> _T:
        return __o


return_eq = _ReturnEq()


class StreamToLogger(StringIO):
    """Use for startup migrate only"""

    def __init__(self, level="INFO"):
        self._level = level

    def write(self, buffer):
        for line in buffer.rstrip().splitlines():
            # depth 0: this function
            # depth 1: click.echo()
            # depth 2: click.secho()
            logger.opt(depth=3).log(self._level, line.rstrip())

    def flush(self):
        pass


def return_progressbar(func: Callable[_P, Iterable[_T]]) -> Callable[_P, Iterable[_T]]:
    log_level = get_driver().config.log_level
    if isinstance(log_level, str):
        log_level = logging.getLevelName(log_level)

    if log_level <= logging.INFO:
        return func

    @wraps(func)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Iterable[_T]:
        with click.progressbar(
            func(*args, **kwargs), label="运行迁移中", item_show_func=str
        ) as bar:
            yield from bar

    return wrapper


_packages_distributions = lru_cache(None)(packages_distributions)


# https://github.com/pdm-project/pdm/blob/fee1e6bffd7de30315e2134e19f9a6f58e15867c/src/pdm/utils.py#L361-L374
def is_editable(plugin: Plugin) -> bool:
    """Check if the distribution is installed in editable mode"""
    while plugin.parent_plugin:
        plugin = plugin.parent_plugin

    dist: Distribution | None = None

    if plugin.metadata:
        with suppress(PackageNotFoundError):
            dist = distribution(plugin.metadata.name)

    if not dist:
        with suppress(KeyError, IndexError):
            dist = distribution(
                _packages_distributions()[plugin.module_name.split(".")[0]][0]
            )

    if not dist:
        return "site-packages" not in plugin.module.__path__[0]

    # https://github.com/pdm-project/pdm/blob/fee1e6bffd7de30315e2134e19f9a6f58e15867c/src/pdm/utils.py#L361-L374
    if getattr(dist, "link_file", None) is not None:
        return True

    direct_url = dist.read_text("direct_url.json")
    if not direct_url:
        return False

    direct_url_data = json.loads(direct_url)
    return direct_url_data.get("dir_info", {}).get("editable", False)


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
