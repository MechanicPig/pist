"""Ender's Blender's complete declared save fields as editable domain data."""

from typing import Self

from yaml.nodes import MappingNode, Node

from berries.game.saves import rooms
from berries.game.saves.codecs import values as values_codec
from berries.game.saves.codecs.yaml import enders_blender as blender_codec
from berries.game.saves.codecs.yaml import nodes as yaml_nodes
from berries.game.saves.errors import UnsupportedProgressError
from berries.game.saves.unknown import UnknownFieldPolicy

from .yaml_doc import YamlDoc

_NAMES = 'mapDict_roomStatCustomNameDict'
_COLORS = 'mapDict_roomStat_colorIndex'
_PREFIXES = ('mapDict_roomStat_firstClear', 'mapDict_roomStat_latestSession')


def _nested_values(node: Node) -> dict[str, dict[str, str]]:
    return {
        key: {room: yaml_nodes.scalar(value) for room, value in yaml_nodes.mapping(rooms).items()}
        for key, rooms in yaml_nodes.mapping(node).items()
    }


class EndersBlenderSave(YamlDoc):
    """First-clear and last-saved records, not a first-clear-only reader."""

    mod_name = 'EndersBlender'
    fields = frozenset(
        {f'{prefix}_{suffix}' for prefix in _PREFIXES for suffix in blender_codec.FIELDS.values()}
        | {_NAMES, _COLORS}
    )

    def __init__(self) -> None:
        super().__init__()
        self.first_clear: dict[str, rooms.RoomStats] = {}
        self.last_saved: dict[str, rooms.RoomStats] = {}
        self.room_names: dict[str, dict[str, str]] = {}
        self.room_colors: dict[str, dict[str, int]] = {}
        self._present: frozenset[str] = frozenset()
        self._order: tuple[str, ...] = ()

    @classmethod
    def fromstring(
        cls,
        data: str | bytes,
        /,
        *,
        strict: bool = True,
        unknown: UnknownFieldPolicy = 'error',
    ) -> Self:
        result = super().fromstring(data, unknown=unknown)
        if strict:
            result._validate_timers()  # noqa: SLF001
        return result

    def _validate_timers(self) -> None:
        for prefix, records in zip(_PREFIXES, (self.first_clear, self.last_saved), strict=True):
            for key, stats in records.items():
                for room, duration in stats.room_times.items():
                    values_codec.validate_timer_duration(
                        duration, path=f'{prefix}_timer[{key!r}][{room!r}]'
                    )

    def _read_root(self, root: MappingNode, *, unknown: UnknownFieldPolicy = 'error') -> None:
        entries = yaml_nodes.mapping(root)
        self.first_clear = rooms.read_records(entries, _PREFIXES[0])
        self.last_saved = rooms.read_records(entries, _PREFIXES[1])
        self.room_names = blender_codec.parse_room_names(
            {} if _NAMES not in entries else _nested_values(entries[_NAMES])
        )
        self.room_colors = blender_codec.parse_room_colors(
            {} if _COLORS not in entries else _nested_values(entries[_COLORS])
        )
        self._present = frozenset(entries)
        self._order = tuple(entries)

    def validate_transfer(self, keys: set[str]) -> None:
        """Reject transfers whose affected values retain unsupported YAML tags."""
        for path in self._tags:
            if not path or (path[0][1] in self.fields and (len(path) == 1 or path[1][1] in keys)):
                raise UnsupportedProgressError('Cannot safely transfer tagged Blender data.')

    def tostring(self) -> str:
        entries: dict[str, Node] = {}
        for prefix, records in zip(_PREFIXES, (self.first_clear, self.last_saved), strict=True):
            entries.update(rooms.write_records(records, prefix, self._present))
        names = blender_codec.parse_room_names(self.room_names)
        colors = blender_codec.parse_room_colors(self.room_colors)
        for key in dict.fromkeys((*self.first_clear.keys(), *self.last_saved.keys())):
            names.setdefault(key, {})  # pyright: ignore[reportUnusedCallResult]
        for i, prefix in enumerate(_PREFIXES):
            order = entries.get(f'{prefix}_roomOrder')
            if order is None:
                continue
            # First-clear needs a nonempty order; last-saved renders even an empty
            # order when the key exists, then indexes pauseType after its row loop.
            visible = [
                key for key, node in yaml_nodes.mapping(order).items() if i == 1 or node.value
            ]
            if not visible:
                continue
            field = f'{prefix}_pauseType'
            pauses = entries.get(field)
            if pauses is None:
                pauses = entries[field] = MappingNode(yaml_nodes.MAP_TAG, [])
            assert isinstance(pauses, MappingNode)
            present = yaml_nodes.mapping(pauses)
            for key in visible:
                colors.setdefault(key, {})  # pyright: ignore[reportUnusedCallResult]
                if key not in present:
                    pauses.value.append(
                        (blender_codec.scalar(key), MappingNode(yaml_nodes.MAP_TAG, []))
                    )
        for field, values in ((_NAMES, names), (_COLORS, colors)):
            if field in self._present or values:
                entries[field] = MappingNode(
                    yaml_nodes.MAP_TAG,
                    [
                        (blender_codec.scalar(k), blender_codec.dictionary(dict(v)))
                        for k, v in values.items()
                    ],
                )
        ordered = dict.fromkeys((*self._order, *entries))
        root = MappingNode(
            yaml_nodes.MAP_TAG,
            [(blender_codec.scalar(key), entries[key]) for key in ordered if key in entries],
        )
        return self._dump(root)
