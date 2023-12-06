from __future__ import annotations

import sys
from itertools import repeat
from typing import Any, cast
from dataclasses import dataclass
from operator import methodcaller
from inspect import Parameter, isclass

from pydantic.fields import FieldInfo
from nonebot.dependencies import Param
from nonebot.params import DependParam
from pydantic.typing import get_args, get_origin
from sqlalchemy import Row, Result, ScalarResult, select
from sqlalchemy.sql.selectable import ExecutableReturnsRows
from sqlalchemy.ext.asyncio import AsyncResult, AsyncScalarResult

from .model import Model
from .utils import Option, compile_dependency, generic_issubclass

if sys.version_info >= (3, 9):
    from typing import Annotated
    from collections.abc import Iterator, Sequence, AsyncIterator

    Tuple = tuple
else:
    from typing_extensions import Annotated
    from typing import Tuple, Iterator, Sequence, AsyncIterator

__all__ = (
    "SQLDepends",
    "ORMParam",
)


PATTERNS = {
    AsyncIterator[Sequence[Row[Tuple[Any, ...]]]]: Option(
        True,
        False,
        methodcaller("partitions"),
    ),
    AsyncIterator[Sequence[Tuple[Any, ...]]]: Option(
        True,
        False,
        methodcaller("partitions"),
    ),
    AsyncIterator[Sequence[Any]]: Option(
        True,
        True,
        methodcaller("partitions"),
    ),
    Iterator[Sequence[Row[Tuple[Any, ...]]]]: Option(
        False,
        False,
        methodcaller("partitions"),
    ),
    Iterator[Sequence[Tuple[Any, ...]]]: Option(
        False,
        False,
        methodcaller("partitions"),
    ),
    Iterator[Sequence[Any]]: Option(
        False,
        True,
        methodcaller("partitions"),
    ),
    AsyncResult[Tuple[Any, ...]]: Option(
        True,
        False,
    ),
    AsyncScalarResult[Any]: Option(
        True,
        True,
    ),
    Result[Tuple[Any, ...]]: Option(
        False,
        False,
    ),
    ScalarResult[Any]: Option(
        False,
        True,
    ),
    AsyncIterator[Row[Tuple[Any, ...]]]: Option(
        True,
        False,
    ),
    Iterator[Row[Tuple[Any, ...]]]: Option(
        False,
        False,
    ),
    Sequence[Row[Tuple[Any, ...]]]: Option(
        True,
        False,
        methodcaller("all"),
    ),
    Sequence[Tuple[Any, ...]]: Option(
        True,
        False,
        methodcaller("all"),
    ),
    Sequence[Any]: Option(
        True,
        True,
        methodcaller("all"),
    ),
    Tuple[Any, ...]: Option(
        True,
        False,
        methodcaller("one_or_none"),
    ),
    Any: Option(
        True,
        True,
        methodcaller("one_or_none"),
    ),
}


@dataclass
class SQLDependsInner:
    dependency: ExecutableReturnsRows

    if sys.version_info >= (3, 10):
        from dataclasses import KW_ONLY

        _: KW_ONLY

    use_cache: bool = True
    validate: bool | FieldInfo = False


def SQLDepends(
    dependency: ExecutableReturnsRows,
    *,
    use_cache: bool = True,
    validate: bool | FieldInfo = False,
) -> Any:
    return SQLDependsInner(dependency, use_cache=use_cache, validate=validate)


class ORMParam(DependParam):
    @classmethod
    def _check_param(
        cls, param: Parameter, allow_types: tuple[type[Param], ...]
    ) -> Param | None:
        type_annotation, depends_inner = param.annotation, None
        if get_origin(param.annotation) is Annotated:
            type_annotation, *extra_args = get_args(param.annotation)
            depends_inner = next(
                (x for x in reversed(extra_args) if isinstance(x, SQLDependsInner)),
                None,
            )

        if isinstance(param.default, SQLDependsInner):
            depends_inner = param.default

        for pattern, option in PATTERNS.items():
            if models := generic_issubclass(pattern, type_annotation):
                break
        else:
            models, option = None, Option()

        if not isinstance(models, tuple):
            models = (models,)

        if depends_inner is not None:
            dependency = compile_dependency(depends_inner.dependency, option)
        elif all(map(isclass, models)) and all(map(issubclass, models, repeat(Model))):
            models = cast(Tuple[Model, ...], models)
            dependency = compile_dependency(
                select(*models).where(
                    *(
                        getattr(model, name) == param.default
                        for model in models
                        for name, param in model.__signature__.parameters.items()
                    )
                ),
                option,
            )
        else:
            return

        return super()._check_param(param.replace(default=dependency), allow_types)

    @classmethod
    def _check_parameterless(cls, *_) -> Param | None:
        return
