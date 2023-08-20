from __future__ import annotations

import sys
from functools import wraps
from contextlib import suppress
from abc import ABCMeta, abstractmethod
from typing import Any, Union, Generic, TypeVar
from inspect import Parameter, Signature, signature
from collections.abc import Callable, Sequence, Coroutine

import sqlalchemy as sa
from nonebot.params import Depends
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy import Row, ColumnExpressionArgument
from sqlalchemy.engine.default import DefaultDialect
from sqlalchemy.sql.elements import SQLCoreOperations
from sqlalchemy.exc import NoResultFound, InvalidRequestError
from sqlalchemy.sql.roles import ExpressionElementRole, TypedColumnsClauseRole
from sqlalchemy.ext.asyncio import (
    AsyncResult,
    AsyncSession,
    AsyncScalarResult,
    async_scoped_session,
)

from . import get_scoped_session
from .utils import DependsInner, return_eq

if sys.version_info >= (3, 12):
    from typing import Self, Unpack, TypeVarTuple, override  # nopycln: import
else:
    from typing_extensions import (  # nopycln: import
        Self,
        Unpack,
        TypeVarTuple,
        override,
    )

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
    "one_or_create",
    "scalar_one_or_none",
)

_T = TypeVar("_T")
_Ts = TypeVarTuple("_Ts")
_TypedColumnClauseArgument = Union[
    TypedColumnsClauseRole[_T],
    SQLCoreOperations[_T],
    ExpressionElementRole[_T],
    "type[_T]",
]

_default_dialect = DefaultDialect()


class SelectBase(Generic[_T], metaclass=ABCMeta):
    __signature__: Signature

    @abstractmethod
    async def __call__(
        self, *, session: async_scoped_session[AsyncSession], **kwargs: Any
    ) -> Any:
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
            self._final = self.filter_by(__signature__=return_eq)
        except InvalidRequestError:
            self._final = self

        compiled = SQLCompiler(_default_dialect, self._final)
        parameters = [
            Parameter(
                "__session__",
                Parameter.KEYWORD_ONLY,
                default=Depends(get_scoped_session),
            ),
            *(
                Parameter(name, Parameter.KEYWORD_ONLY, default=depends)
                for name, depends in compiled.params.items()
                if isinstance(depends, DependsInner)
            ),
        ]

        return Signature(parameters)

    @override
    async def __call__(
        self, *, __session__: async_scoped_session[AsyncSession], **kwargs: Any
    ) -> AsyncResult[tuple[Unpack[_Ts]]]:
        return await __session__.stream(self._final, kwargs)

    @override
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
    @override
    def __signature__(self) -> Signature:
        return self.element.__signature__

    @override
    async def __call__(
        self, *, __session__: async_scoped_session[AsyncSession], **kwargs: Any
    ) -> AsyncScalarResult[_T]:
        return (await self.element(__session__=__session__, **kwargs)).scalars()


def scalars(entity: _TypedColumnClauseArgument[_T]) -> ScalarSelect[_T]:
    return ScalarSelect(Select(entity))


def scalar_all(
    entity: _TypedColumnClauseArgument[_T],
) -> Callable[..., Coroutine[Any, Any, Sequence[_T]]]:
    return scalars(entity).all()


def scalar_first(
    entity: _TypedColumnClauseArgument[_T],
) -> Callable[..., Coroutine[Any, Any, _T | None]]:
    return scalars(entity).first()


def scalar_one(
    entity: _TypedColumnClauseArgument[_T],
) -> Callable[..., Coroutine[Any, Any, _T]]:
    return scalars(entity).one()


def scalar_one_or_none(
    entity: _TypedColumnClauseArgument[_T],
) -> Callable[..., Coroutine[Any, Any, _T | None]]:
    return scalars(entity).one_or_none()


def one_or_create(
    entity: type[_T], defaults: dict[str, Any] | None = None, **criterions: Any
) -> Callable[..., Coroutine[Any, Any, _T]]:
    defaults = defaults or {}
    parameters = dict(signature(entity).parameters.items())
    for name, value in criterions.items():
        if isinstance(value, DependsInner):
            parameters[name] = Parameter(name, Parameter.KEYWORD_ONLY, default=value)
            del criterions[name]
        else:
            with suppress(KeyError):
                del parameters[name]

    async def _one_or_create(
        __session__: async_scoped_session[AsyncSession], **kwargs: Any
    ) -> _T:
        try:
            return await (
                await __session__.stream_scalars(
                    select(entity).filter_by(**criterions, **kwargs)
                )
            ).one()
        except NoResultFound:
            instance = entity(**defaults, **criterions, **kwargs)
            __session__.add(instance)
            return instance

    _one_or_create.__signature__ = Signature(
        (
            Parameter(
                "__session__",
                Parameter.KEYWORD_ONLY,
                default=Depends(get_scoped_session),
            ),
            *parameters.values(),
        )
    )
    return _one_or_create
