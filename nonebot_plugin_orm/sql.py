from __future__ import annotations

from functools import wraps
from abc import ABCMeta, abstractmethod
from inspect import Parameter, Signature
from typing_extensions import Unpack, Annotated, TypeVarTuple, override
from typing import (
    Any,
    Type,
    Tuple,
    Union,
    Generic,
    TypeVar,
    Callable,
    Optional,
    Sequence,
    Coroutine,
)

import sqlalchemy as sa
from nonebot.params import Depends
from sqlalchemy.orm import QueryableAttribute
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import Row, Result, ScalarResult

from .model import Model
from . import get_session

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
_T0 = TypeVar("_T0", bound=Union[Type[Model], QueryableAttribute])
_Ts = TypeVarTuple("_Ts")


class SelectBase(Generic[_T], metaclass=ABCMeta):
    __signature__: Signature

    @abstractmethod
    def __call__(
        self, *, session: Annotated[AsyncSession, Depends(get_session)], **kwargs: Any
    ) -> Coroutine[Any, Any, Any]:
        raise NotImplementedError

    def all(self) -> Callable[..., Coroutine[Any, Any, Sequence[_T]]]:
        @wraps(self)
        async def all(**kwargs) -> Sequence[_T]:
            return (await self(**kwargs)).all()

        return all

    def first(self) -> Callable[..., Coroutine[Any, Any, Optional[_T]]]:
        @wraps(self)
        async def first(**kwargs) -> _T:
            return (await self(**kwargs)).first()

        return first

    def one_or_none(self) -> Callable[..., Coroutine[Any, Any, Optional[_T]]]:
        @wraps(self)
        async def one_or_none(**kwargs) -> Optional[_T]:
            return (await self(**kwargs)).one_or_none()

        return one_or_none

    def one(self) -> Callable[..., Coroutine[Any, Any, _T]]:
        @wraps(self)
        async def one(**kwargs) -> _T:
            return (await self(**kwargs)).one()

        return one


class Select(
    SelectBase[Row[Tuple[_T0, Unpack[_Ts]]]], sa.Select[Tuple[_T0, Unpack[_Ts]]]
):
    inherit_cache = True

    def __init__(self, entity: _T0, *entities: Unpack[_Ts]):
        model = entity.class_ if isinstance(entity, QueryableAttribute) else entity
        parameters: list[Parameter] = [
            Parameter(
                "session",
                Parameter.KEYWORD_ONLY,
                default=Depends(get_session),
                annotation=AsyncSession,
            ),
            *model.__signature__.parameters.values(),
        ]
        self.__signature__ = Signature(parameters)  # type: ignore
        super().__init__(entity, *entities)

    @override
    async def __call__(
        self, *, session: Annotated[AsyncSession, Depends(get_session)], **kwargs: Any
    ) -> Result[Tuple[_T0, Unpack[_Ts]]]:
        return await session.execute(self.filter_by(**kwargs))  # type: ignore

    @override
    def as_scalar(self) -> ScalarSelect[_T0]:
        return ScalarSelect(self)  # type: ignore


def select(entity: _T0, *entities: Unpack[_Ts]) -> Select[_T0, Unpack[_Ts]]:
    return Select(entity, *entities)


def all_(
    entity: _T0, *entities: Unpack[_Ts]
) -> Callable[..., Coroutine[Any, Any, Sequence[Row[Tuple[_T0, Unpack[_Ts]]]]]]:
    return select(entity, *entities).all()


def first(
    entity: _T0, *entities: Unpack[_Ts]
) -> Callable[..., Coroutine[Any, Any, Optional[Row[Tuple[_T0, Unpack[_Ts]]]]]]:
    return select(entity, *entities).first()


def one_or_none(
    entity: _T0, *entities: Unpack[_Ts]
) -> Callable[..., Coroutine[Any, Any, Optional[Row[Tuple[_T0, Unpack[_Ts]]]]]]:
    return select(entity, *entities).one_or_none()


def one(
    entity: _T0, *entities: Unpack[_Ts]
) -> Callable[..., Coroutine[Any, Any, Row[Tuple[_T0, Unpack[_Ts]]]]]:
    return select(entity, *entities).one()


class ScalarSelect(SelectBase[_T0], sa.ScalarSelect[_T0]):
    element: Select[_T0]

    def __init__(self, element: Select[_T0, Unpack[Tuple[Any, ...]]]) -> None:
        self.__signature__ = element.__signature__
        super().__init__(element)

    @override
    async def __call__(
        self, *, session: Annotated[AsyncSession, Depends(get_session)], **kwargs: Any
    ) -> ScalarResult[_T0]:
        return (await self.element(session=session, **kwargs)).scalars()


def scalars(entity: _T0) -> ScalarSelect[_T0]:
    return ScalarSelect(Select(entity))


def scalar_all(entity: _T0) -> Callable[..., Coroutine[Any, Any, Sequence[_T0]]]:
    return scalars(entity).all()


def scalar_first(entity: _T0) -> Callable[..., Coroutine[Any, Any, Optional[_T0]]]:
    return scalars(entity).first()


def scalar_one(entity: _T0) -> Callable[..., Coroutine[Any, Any, _T0]]:
    return scalars(entity).one()


def scalar_one_or_none(
    entity: _T0,
) -> Callable[..., Coroutine[Any, Any, Optional[_T0]]]:
    return scalars(entity).one_or_none()
