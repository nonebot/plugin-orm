from __future__ import annotations

import sys
from itertools import repeat
from typing import Any, cast
from dataclasses import dataclass
from operator import methodcaller
from inspect import Parameter, isclass

from pydantic.fields import FieldInfo
from nonebot.dependencies import Param
from nonebot.params import Depends, DependParam
from sqlalchemy import Row, Result, ScalarResult, select
from sqlalchemy.sql.selectable import ExecutableReturnsRows
from sqlalchemy.ext.asyncio import AsyncResult, AsyncScalarResult

from .model import Model
from .utils import Option, Dependency, generic_issubclass

if sys.version_info >= (3, 10):
    from typing import Annotated, get_args, get_origin
    from collections.abc import Iterator, Sequence, AsyncIterator

    Tuple = tuple
    Type = type
else:
    from typing_extensions import Annotated, get_args, get_origin
    from typing import Type, Tuple, Iterator, Sequence, AsyncIterator

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
    use_cache: bool = True
    validate: bool | FieldInfo = False


def SQLDepends(
    dependency: ExecutableReturnsRows,
    *,
    use_cache: bool = True,
    validate: bool | FieldInfo = False,
) -> Any:
    return SQLDependsInner(dependency, use_cache, validate)


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
            statement = depends_inner.dependency
        elif all(map(isclass, models)) and all(
            map(issubclass, cast(Tuple[type, ...], models), repeat(Model))
        ):
            models = cast(Tuple[Type[Model], ...], models)
            # NOTE: statement is generated (see below)
            statement = select(*models).where(
                *(
                    getattr(model, name) == param.default
                    for model in models
                    for name, param in model.__signature__.parameters.items()
                )
            )
        else:
            return

        return super()._check_param(
            param.replace(
                default=Depends(
                    Dependency(statement, option),
                    use_cache=(
                        depends_inner.use_cache if depends_inner else False
                    ),  # NOTE: default use_cache=False as it is impossible to reuse a generated statement (see above)
                    validate=depends_inner.validate if depends_inner else False,
                )
            ),
            allow_types,
        )

    @classmethod
    def _check_parameterless(cls, *_) -> None:
        return
