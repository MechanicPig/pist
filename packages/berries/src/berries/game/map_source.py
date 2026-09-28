"""Identify the package that provides a map."""

from enum import StrEnum


class MapSource(StrEnum):
    """The broad source category of one Celeste map."""

    VANILLA = 'vanilla'
    MOD = 'mod'
