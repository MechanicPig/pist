"""Shared Pydantic model bases for recurring boundary-data semantics."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """Closed, immutable model for trusted Pist configuration and value data."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True)


class ExternalModel(BaseModel):
    """Tolerant model for data supplied by external files, APIs, or reports."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')


class FrozenExternalModel(ExternalModel):
    """Immutable snapshot of tolerant external data."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class StrictModel(BaseModel):
    """Closed model that rejects coercion for browser and other live input."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
