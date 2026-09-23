"""Pydantic models for Everest's ``everest.yaml`` manifest protocol."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self

from pydantic import ConfigDict, Field, GetCoreSchemaHandler, TypeAdapter
from pydantic_core import core_schema

from pist.models import ExternalModel

_VERSION_PATTERN = re.compile(r'\d+(?:\.\d+){1,3}')
_MAX_VERSION_COMPONENT = 2_147_483_647


@dataclass(frozen=True, slots=True)
class Version:
    """One numeric manifest version interpreted with Everest's ``System.Version`` rules."""

    raw: str
    numbers: tuple[int, int, int, int]

    @classmethod
    def parse(cls, value: str) -> Self:
        """Parse the numeric prefix that Everest passes to ``System.Version``."""
        numeric_prefix = value.split('-', maxsplit=1)[0]
        if _VERSION_PATTERN.fullmatch(numeric_prefix) is None:
            raise ValueError('must contain two to four dot-separated decimal integer components')
        parts = tuple(int(part) for part in numeric_prefix.split('.'))
        if any(part > _MAX_VERSION_COMPONENT for part in parts):
            raise ValueError('components must not exceed System.Int32.MaxValue')
        match parts:
            case major, minor:
                numbers = major, minor, -1, -1
            case major, minor, build:
                numbers = major, minor, build, -1
            case major, minor, build, revision:
                numbers = major, minor, build, revision
            case _:
                raise AssertionError('Version pattern must contain two to four components.')
        return cls(value, numbers)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: object, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Validate YAML scalar text and preserve its original display form."""
        return core_schema.no_info_plain_validator_function(
            cls._from_pydantic_value,
            json_schema_input_schema=core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                str,
                return_schema=core_schema.str_schema(),
            ),
        )

    @classmethod
    def _from_pydantic_value(cls, value: object) -> Self:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError('must be a version string')  # noqa: TRY004
        return cls.parse(value)

    @property
    def major(self) -> int:
        return self.numbers[0]

    @property
    def minor(self) -> int:
        return self.numbers[1]

    def is_compatible_with(self, required: Version) -> bool:
        """Return whether this installed version satisfies one Everest dependency."""
        if self.major == 0 and self.minor == 0:
            return True
        return self.major == required.major and self.numbers[1:] >= required.numbers[1:]

    def __str__(self) -> str:
        return self.raw


DEFAULT_VERSION = Version.parse('1.0')


class Dependency(ExternalModel):
    """One required or optional Everest Mod dependency."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(validation_alias='Name')
    version: Version | None = Field(default=None, validation_alias='Version')


class EverestModMetadata(ExternalModel):
    """One Mod metadata entry declared in an Everest manifest."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(validation_alias='Name')
    version: Version | None = Field(default=None, validation_alias='Version')
    dependencies: list[Dependency] = Field(default_factory=list, validation_alias='Dependencies')
    optional_dependencies: list[Dependency] = Field(
        default_factory=list, validation_alias='OptionalDependencies'
    )
    dll: str | None = Field(default=None, validation_alias='DLL')


type Manifest = tuple[EverestModMetadata, ...]

MANIFEST_ADAPTER = TypeAdapter(Manifest)
