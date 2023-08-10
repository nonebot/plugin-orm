from __future__ import annotations

from functools import wraps
from abc import ABCMeta, abstractmethod
from inspect import Parameter, Signature
from typing import Any, Union, Generic, TypeVar
from collections.abc import Callable, Sequence, Coroutine
from typing_extensions import Self, Unpack, TypeVarTuple, override

import sqlalchemy as sa
from sqlalchemy import Row
from nonebot.params import Depends
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql import ColumnExpressionArgument
from sqlalchemy.ext.asyncio import AsyncResult, AsyncSession, AsyncScalarResult

from .utils import _return_eq
from .model import DependsInner
from . import get_engine, get_session

__all__ = (
    "one",
    "all_",
    "first",
    "select",
    "scalars",
    "scalar_all",
    "scalar_one",
    "one_or_none",
    "scalar_first",
    "scalar_one_or_none",
)

_T = TypeVar("_T")
_Ts = TypeVarTuple("_Ts")


class SelectBase(Generic[_T], metaclass=ABCMeta):
    __signature__: Signature

    @abstractmethod
    async def __call__(self, *, session: AsyncSession, **kwargs: Any) -> Any:
        raise NotImplementedError

    def all(self) -> Callable[..., Coroutine[Any, Any, Sequence[_T]]]:
        @wraps(self)
        async def all(**kwargs: Any) -> Sequence[_T]:
            return await (await self(**kwargs)).all()

        return all

    def first(self) -> Callable[..., Coroutine[Any, Any, _T | None]]:
        @wraps(self)
        async def first(**kwargs: Any) -> _T:
            return await (await self(**kwargs)).first()

        return first

    def one_or_none(self) -> Callable[..., Coroutine[Any, Any, _T | None]]:
        @wraps(self)
        async def one_or_none(**kwargs: Any) -> _T | None:
            return await (await self(**kwargs)).one_or_none()

        return one_or_none

    def one(self) -> Callable[..., Coroutine[Any, Any, _T]]:
        @wraps(self)
        async def one(**kwargs: Any) -> _T:
            return await (await self(**kwargs)).one()

        return one


class Select(sa.Select["tuple[Unpack[_Ts]]"], SelectBase[Row["tuple[Unpack[_Ts]]"]]):
    inherit_cache = True

    _final: Self

    @property
    @override
    def __signature__(self) -> Signature:
        try:
            self._final = self.filter_by(__signature__=_return_eq)
        except InvalidRequestError:
            self._final = self

        compiled = SQLCompiler(get_engine().dialect, self._final)
        parameters = [
            Parameter("session", Parameter.KEYWORD_ONLY, default=Depends(get_session)),
            *(
                Parameter(name, Parameter.KEYWORD_ONLY, default=depends)
                for name, depends in compiled.params.items()
                if isinstance(depends, DependsInner)
            ),
        ]

        return Signature(parameters)

    @override
    async def __call__(
        self, *, session: AsyncSession, **kwargs: Any
    ) -> AsyncResult[tuple[Unpack[_Ts]]]:
        return await session.stream(self._final, kwargs)

    def where(
        self,
        sig_or_clause: Signature | ColumnExpressionArgument[bool] | None = None,
        *whereclause: ColumnExpressionArgument[bool],
    ) -> Self:
        if sig_or_clause is None:
            return self

        if not isinstance(sig_or_clause, Signature):
            return super().where(sig_or_clause, *whereclause)

        if whereclause:
            raise ValueError(
                "Cannot specify other where clauses when first argument is a Signature"
            )

        return self.filter_by(
            **{name: param.default for name, param in sig_or_clause.parameters.items()}
        )

    @override
    def as_scalar(self) -> ScalarSelect[Union[Unpack[_Ts]]]:
        super().as_scalar()
        return ScalarSelect(self)


def select(*entities: Any) -> Select[Unpack[tuple[Any, ...]]]:
    return Select(*entities)


def all_(
    *entities: Any,
) -> Callable[..., Coroutine[Any, Any, Sequence[Row[tuple[Any]]]]]:
    return select(*entities).all()


def first(*entities: Any) -> Callable[..., Coroutine[Any, Any, Row[tuple[Any]] | None]]:
    return select(*entities).first()


def one_or_none(
    *entities: Any,
) -> Callable[..., Coroutine[Any, Any, Row[tuple[Any]] | None]]:
    return select(*entities).one_or_none()


def one(*entities: Any) -> Callable[..., Coroutine[Any, Any, Row[tuple[Any]]]]:
    return select(*entities).one()


class ScalarSelect(sa.ScalarSelect[_T], SelectBase[_T]):
    element: Select[_T]

    @property
    def __signature__(self) -> Signature:
        return self.element.__signature__

    @override
    async def __call__(
        self, *, session: AsyncSession, **kwargs: Any
    ) -> AsyncScalarResult[_T]:
        return (await self.element(session=session, **kwargs)).scalars()


def scalars(entity: type[_T]) -> ScalarSelect[_T]:
    return ScalarSelect(Select(entity))


def scalar_all(entity: type[_T]) -> Callable[..., Coroutine[Any, Any, Sequence[_T]]]:
    return scalars(entity).all()


def scalar_first(entity: type[_T]) -> Callable[..., Coroutine[Any, Any, _T | None]]:
    return scalars(entity).first()


def scalar_one(entity: type[_T]) -> Callable[..., Coroutine[Any, Any, _T]]:
    return scalars(entity).one()


def scalar_one_or_none(
    entity: type[_T],
) -> Callable[..., Coroutine[Any, Any, _T | None]]:
    return scalars(entity).one_or_none()
