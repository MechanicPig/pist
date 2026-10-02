"""Read Ender's Blender first-clear route data from Celeste saves."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

import yaml
from pydantic import Field, ValidationError

from berries.game import levels, maps, saves
from berries.game.duration import Duration
from berries.models import ExternalModel
from berries.types import NonNegativeDecimalInt

ENDERS_BLENDER_MOD_NAME = 'EndersBlender'
ENDERS_BLENDER_ROOM_ORDER_KEY = 'mapDict_roomStat_firstClear_roomOrder'
ENDERS_BLENDER_ROOM_DEATH_KEY = 'mapDict_roomStat_firstClear_death'
ENDERS_BLENDER_ROOM_TIMER_KEY = 'mapDict_roomStat_firstClear_timer'
ENDERS_BLENDER_FIELD_LABELS = MappingProxyType(
    {
        ENDERS_BLENDER_ROOM_ORDER_KEY: 'room orders',
        ENDERS_BLENDER_ROOM_DEATH_KEY: 'room deaths',
        ENDERS_BLENDER_ROOM_TIMER_KEY: 'room timers',
    }
)


@dataclass(frozen=True, slots=True)
class FirstClearStats:
    """A view of one map's first-clear records, without layout filtering."""

    room_order: tuple[str, ...]
    room_deaths: Mapping[str, int]
    room_times: Mapping[str, Duration]


@dataclass(frozen=True, slots=True)
class EndersBlenderSave:
    """First-clear room data indexed by map SID."""

    number: int
    first_clear_room_orders: dict[str, tuple[str, ...]]
    first_clear_room_deaths: dict[str, dict[str, int]] = field(default_factory=dict)
    first_clear_room_times: dict[str, dict[str, Duration]] = field(default_factory=dict)

    def first_clear_stats(self, level: levels.Level, side: levels.LevelSide) -> FirstClearStats:
        """Return the records for a final Level identity and Side."""
        return self._first_clear_stats(_map_save_key(level, side))

    def first_clear_stats_for_file(self, map_info: maps.MapInfo) -> FirstClearStats:
        """Return records using a raw map's inferred identity when no Level is available."""
        return self._first_clear_stats(_map_file_save_key(map_info))

    def _first_clear_stats(self, key: str) -> FirstClearStats:
        return FirstClearStats(
            self.first_clear_room_orders.get(key, ()),
            self.first_clear_room_deaths.get(key, {}),
            self.first_clear_room_times.get(key, {}),
        )

    def first_clear_room_order(
        self, level: levels.Level, side: levels.LevelSide
    ) -> tuple[str, ...]:
        """Return the recorded first-clear sequence for one map, if available."""
        return self.first_clear_room_orders.get(_map_save_key(level, side), ())

    def first_clear_room_time(
        self, level: levels.Level, side: levels.LevelSide, room: str
    ) -> Duration | None:
        """Return one room's recorded first-clear time, if available."""
        return self.first_clear_room_times.get(_map_save_key(level, side), {}).get(room)

    def first_clear_room_death(
        self, level: levels.Level, side: levels.LevelSide, room: str
    ) -> int | None:
        """Return one room's recorded first-clear death count, if available."""
        return self.first_clear_room_deaths.get(_map_save_key(level, side), {}).get(room)

    def first_clear_room_time_for_file(self, map_info: maps.MapInfo, room: str) -> Duration | None:
        """Return time for a raw preview map whose Level context is unavailable."""
        return self.first_clear_room_times.get(_map_file_save_key(map_info), {}).get(room)

    def first_clear_room_order_for_file(self, map_info: maps.MapInfo) -> tuple[str, ...]:
        """Return room order for a raw preview map whose Level context is unavailable."""
        return self.first_clear_room_orders.get(_map_file_save_key(map_info), ())

    def first_clear_room_death_for_file(self, map_info: maps.MapInfo, room: str) -> int | None:
        """Return deaths for a raw preview map whose Level context is unavailable."""
        return self.first_clear_room_deaths.get(_map_file_save_key(map_info), {}).get(room)


class _EndersBlenderData(ExternalModel):
    """The subset of Ender's Blender YAML that Pist consumes.

    ``yaml.BaseLoader`` deliberately preserves scalar values as strings. The
    nested field annotations validate their decimal encoding and convert them.
    """

    room_orders: dict[str, tuple[str, ...]] = Field(
        default_factory=dict,
        validation_alias=ENDERS_BLENDER_ROOM_ORDER_KEY,
    )
    room_deaths: dict[str, dict[str, NonNegativeDecimalInt]] = Field(
        default_factory=dict,
        validation_alias=ENDERS_BLENDER_ROOM_DEATH_KEY,
    )
    room_timers: dict[str, dict[str, Duration]] = Field(
        default_factory=dict,
        validation_alias=ENDERS_BLENDER_ROOM_TIMER_KEY,
    )


class EndersBlenderReader:
    """Read Ender's Blender YAML saves without modifying the game files."""

    def __init__(self, game_dir: Path) -> None:
        self._saves_dir = saves.settings_dir(game_dir)

    def available_numbers(self) -> list[int]:
        """Return save slots that have an Ender's Blender persistent save."""
        if not self._saves_dir.is_dir():
            return []
        prefix = '-modsave-EndersBlender'
        return sorted(
            int(number)
            for path in self._saves_dir.glob(f'*{prefix}{saves.SAVE_EXT}')
            if (number := path.name.removesuffix(f'{prefix}{saves.SAVE_EXT}')).isdigit()
        )

    def load(self, number: int) -> EndersBlenderSave:
        """Load first-clear room orders for one save slot."""
        if number < 0:
            raise ValueError('Save slot number must be non-negative.')
        path = self._path(number)
        try:
            data = yaml.load(path.read_bytes(), Loader=yaml.BaseLoader)
        except (OSError, yaml.YAMLError) as error:
            raise ValueError(f'Invalid Ender’s Blender save file: {path!r}') from error
        try:
            saved_data = _EndersBlenderData.model_validate(data)
        except ValidationError as error:
            raise _validation_error(path, error) from error
        return EndersBlenderSave(
            number,
            saved_data.room_orders,
            saved_data.room_deaths,
            saved_data.room_timers,
        )

    def _path(self, number: int) -> Path:
        return saves.mod_save_path(self._saves_dir, number, ENDERS_BLENDER_MOD_NAME)


def _validation_error(path: Path, error: ValidationError) -> ValueError:
    detail = error.errors(include_url=False)[0]
    location = detail['loc']
    field = location[0] if location else None
    label = (
        ENDERS_BLENDER_FIELD_LABELS.get(field, 'save data')
        if isinstance(field, str)
        else 'save data'
    )
    nested_path = ''.join(f'[{part!r}]' for part in location[1:])
    suffix = f' at {nested_path}' if nested_path else ''
    return ValueError(f'Invalid Ender’s Blender {label}{suffix}: {detail["msg"]} in {path!r}')


def _map_save_key(level: levels.Level, side: levels.LevelSide) -> str:
    return f'{level.sid}_{side.value}' if side is not levels.LevelSide.A else level.sid


def _map_file_save_key(map_info: maps.MapInfo) -> str:
    base_file, local_side = maps.split_map_side_suffix(map_info.file_path)
    local_sid = levels.local_sid_for_map_file(base_file)
    return f'{local_sid}_{local_side}' if local_side is not None else local_sid
