from __future__ import annotations

import sys
from collections import defaultdict
from typing import TYPE_CHECKING, Any
from inspect import Parameter, Signature

from nonebot import get_plugin_by_module_name
from sqlalchemy.orm import Mapped, DeclarativeBase, declared_attr

from .utils import DependsInner, get_annotations

if sys.version_info >= (3, 9):
    from typing import Annotated, get_args, get_origin  # nopycln: import
else:
    from typing_extensions import Annotated, get_args, get_origin  # nopycln: import


__all__ = ("Model",)


_models: dict[str | None, list[Model]] = defaultdict(list)


class Model(DeclarativeBase):
    if TYPE_CHECKING:
        __bind_key__: str
        __signature__: Signature

    def __init_subclass__(cls) -> None:
        parameters: list[Parameter] = []

        annotations: dict[str, Any] = {}
        for base in reversed(cls.__mro__):
            annotations.update(get_annotations(base, eval_str=True))

        for name, type_annotation in annotations.items():
            # Check if the attribute is both a dependent and a mapped column
            depends_inner = None
            if get_origin(type_annotation) is Annotated:
                type_annotation, *extra_args = get_args(type_annotation)
                depends_inner = next(
                    (x for x in extra_args if isinstance(x, DependsInner)), None
                )

            if get_origin(type_annotation) is not Mapped:
                continue

            default = getattr(cls, name, Signature.empty)

            depends_inner = (
                default if isinstance(default, DependsInner) else depends_inner
            )
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

        super().__init_subclass__()

    @declared_attr.directive
    def __tablename__(cls) -> str:
        if plugin := get_plugin_by_module_name(cls.__module__):
            prefix = plugin.name.replace("-", "_") + "_"
            bind_key = plugin.name
        else:
            prefix = ""
            bind_key = None
        bind_key = getattr(cls, "__bind_key__", bind_key)
        _models[bind_key].append(cls)

        return prefix + cls.__name__.lower()
