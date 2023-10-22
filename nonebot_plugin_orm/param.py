from __future__ import annotations

import sys
from typing import Any, Union
from inspect import Parameter, isclass
from typing_extensions import Annotated
from collections.abc import Iterator, Sequence, AsyncIterator

from nonebot.matcher import Matcher
from nonebot.dependencies import Param
from pydantic.typing import get_args, get_origin
from nonebot.params import DependParam, DefaultParam
from sqlalchemy import Row, Result, ScalarResult, select
from sqlalchemy.ext.asyncio import AsyncResult, AsyncScalarResult

from .model import Model
from .utils import Option, toclass, methodcall, compile_dependency

if sys.version_info >= (3, 10):
    from types import NoneType, UnionType
else:
    NoneType = type(None)
    UnionType = None


def parse_model_annotation(
    anno: Any,
) -> tuple[tuple[type[Model], ...], Option] | tuple[None, None]:
    if isclass(anno) and issubclass(anno, Model):
        return (anno,), Option(scalars=True, result=methodcall("one_or_none"))

    origin, args = get_origin(anno), get_args(anno)

    if not (origin and args):
        return (None, None)

    if origin is Annotated:
        return parse_model_annotation(args[0])

    if origin in (UnionType, Union) and len(args) == 2:
        if args[0] is NoneType:
            return parse_model_annotation(args[1])
        elif args[1] is NoneType:
            return parse_model_annotation(args[0])

    if not isclass(origin):
        return (None, None)

    if origin is Row:
        origin, args = tuple, get_args(args[0])

    if origin is tuple and all(issubclass(arg, Model) for arg in map(toclass, args)):
        return args, Option(result=methodcall("one_or_none"))

    models, option = parse_model_annotation(args[0])
    if not (models and option):
        return (None, None)

    if option.result == methodcall("all"):
        if issubclass(Iterator, origin):
            return models, Option(False, option.scalars, methodcall("partitions"))

        if issubclass(AsyncIterator, origin):
            return models, Option(True, option.scalars, methodcall("partitions"))

    if option.result != methodcall("one_or_none"):
        return (None, None)

    if (
        (not option.scalars and origin is Result)
        or (option.scalars and origin is ScalarResult)
        or issubclass(Iterator, origin)
    ):
        return models, Option(False, option.scalars)

    if (
        (not option.scalars and origin is AsyncResult)
        or (option.scalars and origin is AsyncScalarResult)
        or issubclass(AsyncIterator, origin)
    ):
        return models, Option(scalars=option.scalars)

    if issubclass(Sequence, origin):
        return models, Option(True, option.scalars, methodcall("all"))

    return (None, None)


class ModelParam(DependParam):
    @classmethod
    def _check_param(
        cls, param: Parameter, allow_types: tuple[type[Param], ...]
    ) -> Param | None:
        models, option = parse_model_annotation(param.annotation)

        if not (models and option):
            return

        stat = select(*models).where(
            *(
                getattr(model, name) == param.default
                for model in models
                for name, param in model.__signature__.parameters.items()
            )
        )

        return super()._check_param(
            param.replace(default=compile_dependency(stat, option)), allow_types
        )

    @classmethod
    def _check_parameterless(
        cls, value: Any, allow_types: tuple[type[Param], ...]
    ) -> Param | None:
        return


Matcher.HANDLER_PARAM_TYPES = Matcher.HANDLER_PARAM_TYPES[:-1] + (
    ModelParam,
    DefaultParam,
)
