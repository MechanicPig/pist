"""Editable room statistics, indexed by Ender's Blender's exact protocol keys."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Self

from yaml.nodes import MappingNode, Node, SequenceNode

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.yaml import nodes as yaml_nodes
from berries.game.saves.codecs.yaml.enders_blender import FIELDS, RoomFields, dictionary, scalar


def room_key(sid: str, side: LevelSide) -> str:
    return sid if side is LevelSide.A else f'{sid}_{side.name}'


@dataclass
class RoomStats:
    """Independent, possibly partial room dictionaries; no inferred RTA or ordering."""

    room_order: list[str] = field(default_factory=list)
    room_deaths: dict[str, int] = field(default_factory=dict)
    room_times: dict[str, Duration] = field(default_factory=dict)
    room_rta_times: dict[str, Duration] = field(default_factory=dict)
    room_strawberries: dict[str, int] = field(default_factory=dict)
    pause_flags: dict[str, bool] = field(default_factory=dict)
    _fields_present: frozenset[str] = field(
        default_factory=frozenset, init=False, repr=False, compare=False
    )

    def reset(self) -> None:
        """Reset all modeled room statistics without removing the enclosing record."""
        self.room_order.clear()
        self.room_deaths.clear()
        self.room_times.clear()
        self.room_rta_times.clear()
        self.room_strawberries.clear()
        self.pause_flags.clear()

    @classmethod
    def from_yaml(cls, fields: Mapping[str, Node], *, key: str, prefix: str) -> Self:
        raw: dict[str, object] = {}
        for name, node in fields.items():
            if name == 'room_order':
                if not isinstance(node, SequenceNode):
                    raise ValueError(f'Expected a room sequence at {key!r}.')
                raw[name] = [yaml_nodes.scalar(item) for item in node.value]
            else:
                raw[name] = {
                    room: yaml_nodes.scalar(value)
                    for room, value in yaml_nodes.mapping(node).items()
                }
        try:
            data = RoomFields.model_validate(raw)
        except ValueError as error:
            sources = ', '.join(f'{prefix}_{FIELDS[name]}' for name in raw)
            raise ValueError(f'Invalid Blender statistics {key!r} ({sources}): {error}') from error
        stats = cls(
            data.room_order,
            data.room_deaths,
            data.room_times,
            data.room_rta_times,
            data.room_strawberries,
            data.pause_flags,
        )
        stats._fields_present = frozenset(raw)
        return stats

    def to_yaml(self) -> dict[str, Node]:
        """Encode the modeled room fields, preserving explicit field presence."""
        self.validate_output()
        data = RoomFields(
            room_order=self.room_order,
            room_deaths=self.room_deaths,
            room_times=self.room_times,
            room_rta_times=self.room_rta_times,
            room_strawberries=self.room_strawberries,
            pause_flags=self.pause_flags,
        )
        values: dict[str, Node] = {
            'room_order': SequenceNode(
                'tag:yaml.org,2002:seq', [scalar(room) for room in data.room_order]
            ),
            'room_deaths': dictionary(dict(data.room_deaths)),
            'room_times': dictionary(dict(data.room_times)),
            'room_rta_times': dictionary(dict(data.room_rta_times)),
            'room_strawberries': dictionary(dict(data.room_strawberries)),
            'pause_flags': dictionary(dict(data.pause_flags)),
        }
        result = {
            name: node
            for name, node in values.items()
            if node.value
            or name in self._fields_present
            or (data.room_order and name in ('room_strawberries', 'pause_flags'))
        }
        if not self._fields_present and not any(node.value for node in values.values()):
            result['room_order'] = values['room_order']
        return result

    def validate_output(self) -> None:
        """Require the dictionaries directly indexed by Blender's room-order renderer."""
        rooms = set(self.room_order) - {''}
        if missing := rooms - self.room_deaths.keys():
            raise ValueError(f'Missing Blender room deaths: {sorted(missing)!r}.')
        if missing := rooms - self.room_times.keys():
            raise ValueError(f'Missing Blender room times: {sorted(missing)!r}.')


def read_records(entries: Mapping[str, Node], prefix: str) -> dict[str, RoomStats]:
    """Decode all parallel dictionaries into ordinary room-statistics values."""
    fields: dict[str, dict[str, Node]] = {}
    for name, suffix in FIELDS.items():
        node = entries.get(f'{prefix}_{suffix}')
        if node is None:
            continue
        for key, value in yaml_nodes.mapping(node).items():
            fields.setdefault(key, {})[name] = value
    return {
        key: RoomStats.from_yaml(fragments, key=key, prefix=prefix)
        for key, fragments in fields.items()
    }


def write_records(
    records: Mapping[str, RoomStats], prefix: str, present: frozenset[str]
) -> dict[str, MappingNode]:
    """Encode values without adding container semantics to the dictionaries."""
    fields: dict[str, list[tuple[Node, Node]]] = {name: [] for name in FIELDS}
    for key, stats in records.items():
        for name, node in stats.to_yaml().items():
            fields[name].append((scalar(key), node))
    return {
        f'{prefix}_{FIELDS[name]}': MappingNode(yaml_nodes.MAP_TAG, values)
        for name, values in fields.items()
        if values or f'{prefix}_{FIELDS[name]}' in present
    }
