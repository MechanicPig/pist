"""Primitive identifiers shared by map data and native save records."""

from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True, slots=True)
class MapEntityID:
    """The stable ``room:id`` identity of one entity in a map version."""

    room: str
    entity_id: int

    @classmethod
    def parse(cls, value: str) -> Self:
        """Parse Celeste's ``room:id`` serialization."""
        room, separator, entity_id = value.rpartition(':')
        if not separator or not room or not entity_id.isdecimal():
            raise ValueError(f'Invalid map entity ID: {value!r}')
        return cls(room, int(entity_id))

    def __str__(self) -> str:
        return f'{self.room}:{self.entity_id}'
