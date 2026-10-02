"""User-confirmed routes through concrete map files."""

from pydantic import Field, PositiveInt, model_validator

from berries.models import FrozenModel


class MapRoute(FrozenModel):
    """The user-confirmed main rooms for one concrete map file."""

    map_file: str
    rooms: tuple[str, ...]
    room_counts: dict[str, PositiveInt] = Field(default_factory=dict)
    excluded_entities: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode='after')
    def validate_rooms(self) -> MapRoute:
        if len(set(self.rooms)) != len(self.rooms):
            raise ValueError('Map route must not contain duplicate rooms.')
        if not self.room_counts.keys() <= set(self.rooms):
            raise ValueError('Map route room counts must refer to selected rooms.')
        return self

    @property
    def room_count(self) -> int:
        """Return the weighted number of user-confirmed main rooms."""
        return sum(self.room_counts.get(room, 1) for room in self.rooms)
