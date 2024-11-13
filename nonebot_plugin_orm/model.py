from __future__ import annotations

from inspect import Parameter, Signature
from typing_extensions import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Annotated,
    get_args,
    get_origin,
    get_annotations,
)

from sqlalchemy import Table, MetaData
from nonebot import get_plugin_by_module_name
from sqlalchemy.orm import Mapped, DeclarativeBase

from .utils import DependsInner

__all__ = ("Model",)


_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Model(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)

    if TYPE_CHECKING:
        __bind_key__: ClassVar[str]
        __signature__: ClassVar[Signature]
        __table__: ClassVar[Table]

    def __init_subclass__(cls, **kwargs) -> None:
        _setup_di(cls)
        _setup_tablename(cls)

        super().__init_subclass__(**kwargs)

        if not hasattr(cls, "__table__"):
            return

        _setup_bind(cls)


def _setup_di(cls: type[Model]) -> None:
    """Get signature for NoneBot's dependency injection,
    and set annotations for SQLAlchemy declarative class.
    """
    parameters: list[Parameter] = []

    annotations: dict[str, Any] = {}
    for base in reversed(cls.__mro__):
        annotations.update(get_annotations(base, eval_str=True))

    for name, type_annotation in annotations.items():
        # Check if the attribute is both a dependent and a mapped column
        depends_inner = None
        if get_origin(type_annotation) is Annotated:
            (type_annotation, *extra_args) = get_args(type_annotation)
            depends_inner = next(
                (x for x in extra_args if isinstance(x, DependsInner)), None
            )

        if get_origin(type_annotation) is not Mapped:
            continue

        default = getattr(cls, name, Signature.empty)

        depends_inner = default if isinstance(default, DependsInner) else depends_inner
        if depends_inner is None:
            continue

        # Set parameter for NoneBot dependency injection
        parameters.append(
            Parameter(
                name,
                Parameter.KEYWORD_ONLY,
                default=depends_inner,
                annotation=get_args(type_annotation)[0],
            )
        )

        # Set annotation for SQLAlchemy declarative class
        cls.__annotations__[name] = type_annotation
        if default is not Signature.empty and not isinstance(default, Mapped):
            delattr(cls, name)

    cls.__signature__ = Signature(parameters)


def _setup_tablename(cls: type[Model]) -> None:
    for attr in ("__abstract__", "__tablename__", "__table__"):
        if getattr(cls, attr, None):
            return

    cls.__tablename__ = cls.__name__.lower()

    if plugin := get_plugin_by_module_name(cls.__module__):
        cls.__tablename__ = f"{plugin.name.replace('-', '_')}_{cls.__tablename__}"


def _setup_bind(cls: type[Model]) -> None:
    bind_key: str | None = getattr(cls, "__bind_key__", None)

    if bind_key is None:
        if plugin := get_plugin_by_module_name(cls.__module__):
            bind_key = plugin.name
        else:
            bind_key = ""

    cls.__table__.info["bind_key"] = bind_key
