"""Codec for Blender's parallel dictionaries; domain records own edited data."""

from typing import Annotated

from pydantic import BeforeValidator, Field, StrictStr, TypeAdapter
from yaml.nodes import MappingNode, ScalarNode

from berries.game.duration import Duration
from berries.game.saves.codecs import values as values_codec
from berries.models import FrozenExternalModel
from berries.types import DecimalInt

from . import nodes as yaml_nodes

FIELDS = {
    'room_order': 'roomOrder',
    'room_deaths': 'death',
    'room_times': 'timer',
    'room_rta_times': 'rtatimer',
    'room_strawberries': 'strawberries',
    'pause_flags': 'pauseType',
}


def _pause_flag(value: object) -> bool:
    """Retain Blender YAML's case-insensitive bool spellings, unlike XML."""
    return values_codec.parse_bool(value.lower() if isinstance(value, str) else value)


type _PauseFlag = Annotated[bool, BeforeValidator(_pause_flag)]


class RoomFields(FrozenExternalModel):
    room_order: list[StrictStr] = Field(default_factory=list)
    room_deaths: dict[StrictStr, values_codec.SaveCount] = Field(default_factory=dict)
    room_times: dict[StrictStr, values_codec.SaveDuration] = Field(default_factory=dict)
    room_rta_times: dict[StrictStr, values_codec.SaveDuration] = Field(default_factory=dict)
    room_strawberries: dict[StrictStr, values_codec.SaveCount] = Field(default_factory=dict)
    pause_flags: dict[StrictStr, _PauseFlag] = Field(default_factory=dict)


def scalar(value: str | int | bool | Duration) -> ScalarNode:
    if isinstance(value, Duration):
        return ScalarNode('tag:yaml.org,2002:int', values_codec.format_duration(value))
    if isinstance(value, bool):
        return ScalarNode('tag:yaml.org,2002:bool', str(value).lower())
    if isinstance(value, int):
        return ScalarNode('tag:yaml.org,2002:int', str(value))
    return ScalarNode(yaml_nodes.STR_TAG, value)


def dictionary(values: dict[str, str | int | bool | Duration]) -> MappingNode:
    return MappingNode(
        yaml_nodes.MAP_TAG, [(scalar(key), scalar(value)) for key, value in values.items()]
    )


_NAMES_ADAPTER = TypeAdapter(dict[StrictStr, dict[StrictStr, StrictStr]])
_COLORS_ADAPTER = TypeAdapter(dict[StrictStr, dict[StrictStr, DecimalInt]])


def parse_room_names(value: object) -> dict[str, dict[str, str]]:
    return _NAMES_ADAPTER.validate_python(value)


def parse_room_colors(value: object) -> dict[str, dict[str, int]]:
    return _COLORS_ADAPTER.validate_python(value)
