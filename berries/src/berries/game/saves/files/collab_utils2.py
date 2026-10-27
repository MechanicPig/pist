"""Collab progress with editable values and independently lazy session XML."""

from operator import attrgetter

from pydantic import StrictBool, StrictStr, TypeAdapter
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from berries.containers import KeyedDict
from berries.game.duration import Duration
from berries.game.saves.codecs import values as values_codec
from berries.game.saves.codecs.yaml import nodes as yaml_nodes
from berries.game.saves.session_input import SessionInput
from berries.game.saves.unknown import UnknownFieldPolicy

from .yaml_doc import YamlDoc

_SETS = {
    'OpenedMiniHeartDoors': 'opened_mini_heart_doors',
    'CombinedRainbowBerries': 'combined_rainbow_berries',
    'CompletedWarpPedestalSIDs': 'completed_warp_pedestals',
}
_MOD_SESSIONS = {
    'ModSessionsPerLevel': 'mod_sessions',
    'ModSessionsPerLevelBinary': 'mod_sessions_binary',
}
_BOOLS = {
    'SpeedberryOptionMessageShown': 'speedberry_option_message_shown',
    'RevealMap': 'reveal_map',
    'PauseVisitingPoints': 'pause_visiting_points',
    'ShowVisitedPoints': 'show_visited_points',
}
_PBS = TypeAdapter(dict[StrictStr, values_codec.SaveDuration])
_TECH = TypeAdapter(dict[StrictStr, set[StrictStr]])
_STRINGS = TypeAdapter(set[StrictStr])
_PAYLOADS = TypeAdapter(dict[StrictStr, dict[StrictStr, StrictStr]])
_POSITIONS = TypeAdapter(dict[StrictStr, StrictStr])
_BOOL = TypeAdapter(StrictBool)
_SESSION_KEY = attrgetter('sid')


def _strings(node: Node) -> list[str]:
    if not isinstance(node, SequenceNode):
        raise ValueError('Expected a YAML sequence.')  # noqa: TRY004 - invalid file shape
    return [yaml_nodes.scalar(value) for value in node.value]


def _scalar(value: str) -> ScalarNode:
    return ScalarNode(yaml_nodes.STR_TAG, value)


def _sequence(values: set[str]) -> SequenceNode:
    return SequenceNode('tag:yaml.org,2002:seq', [_scalar(value) for value in sorted(values)])


def _mapping(values: dict[str, Node]) -> MappingNode:
    return MappingNode(yaml_nodes.MAP_TAG, [(_scalar(key), value) for key, value in values.items()])


def _read_set(entries: dict[str, Node], field: str) -> set[str]:
    return _STRINGS.validate_python([] if field not in entries else _strings(entries[field]))


def _read_bool(entries: dict[str, Node], field: str) -> bool:
    return (
        False
        if field not in entries
        else values_codec.parse_bool(yaml_nodes.scalar(entries[field]).lower())
    )


def _read_payloads(entries: dict[str, Node], field: str) -> dict[str, dict[str, str]]:
    return _PAYLOADS.validate_python(
        {}
        if field not in entries
        else {
            sid: {
                name: yaml_nodes.scalar(payload)
                for name, payload in yaml_nodes.mapping(value).items()
            }
            for sid, value in yaml_nodes.mapping(entries[field]).items()
        }
    )


class CollabUtils2Save(YamlDoc):
    mod_name = 'CollabUtils2'
    fields = frozenset(
        (
            *_SETS,
            *_MOD_SESSIONS,
            *_BOOLS,
            'SessionsPerLevel',
            'SpeedBerryPBs',
            'LearnedTech',
            'VisitedLobbyPositions',
        )
    )

    def __init__(self) -> None:
        super().__init__()
        self.speedberry_option_message_shown = False
        self.reveal_map = False
        self.pause_visiting_points = False
        self.show_visited_points = False
        self.visited_lobby_positions: dict[str, str] = {}
        self.sessions: KeyedDict[str, SessionInput] = KeyedDict(key=_SESSION_KEY)
        self.mod_sessions: dict[str, dict[str, str]] = {}
        self.mod_sessions_binary: dict[str, dict[str, str]] = {}
        self.speed_berry_pbs: dict[str, Duration] = {}
        self.opened_mini_heart_doors: set[str] = set()
        self.combined_rainbow_berries: set[str] = set()
        self.completed_warp_pedestals: set[str] = set()
        self.learned_tech: dict[str, set[str]] = {}
        self._present: frozenset[str] = frozenset()
        self._order: tuple[str, ...] = ()

    def _read_root(self, root: MappingNode, *, unknown: UnknownFieldPolicy = 'error') -> None:
        entries = yaml_nodes.mapping(root)
        self.speedberry_option_message_shown = _read_bool(entries, 'SpeedberryOptionMessageShown')
        self.reveal_map = _read_bool(entries, 'RevealMap')
        self.pause_visiting_points = _read_bool(entries, 'PauseVisitingPoints')
        self.show_visited_points = _read_bool(entries, 'ShowVisitedPoints')
        self.visited_lobby_positions = _POSITIONS.validate_python(
            {}
            if 'VisitedLobbyPositions' not in entries
            else {
                sid: yaml_nodes.scalar(value)
                for sid, value in yaml_nodes.mapping(entries['VisitedLobbyPositions']).items()
            }
        )
        sessions = entries.get('SessionsPerLevel')
        self.sessions = KeyedDict(
            ()
            if sessions is None
            else (
                SessionInput(sid, yaml_nodes.scalar(node), unknown=unknown)
                for sid, node in yaml_nodes.mapping(sessions).items()
            ),
            key=_SESSION_KEY,
        )
        self.mod_sessions = _read_payloads(entries, 'ModSessionsPerLevel')
        self.mod_sessions_binary = _read_payloads(entries, 'ModSessionsPerLevelBinary')
        self.speed_berry_pbs = _PBS.validate_python(
            {}
            if 'SpeedBerryPBs' not in entries
            else {
                sid: yaml_nodes.scalar(value)
                for sid, value in yaml_nodes.mapping(entries['SpeedBerryPBs']).items()
            }
        )
        self.opened_mini_heart_doors = _read_set(entries, 'OpenedMiniHeartDoors')
        self.combined_rainbow_berries = _read_set(entries, 'CombinedRainbowBerries')
        self.completed_warp_pedestals = _read_set(entries, 'CompletedWarpPedestalSIDs')
        self.learned_tech = _TECH.validate_python(
            {}
            if 'LearnedTech' not in entries
            else {
                collab: _strings(value)
                for collab, value in yaml_nodes.mapping(entries['LearnedTech']).items()
            }
        )
        self._present = frozenset(entries)
        self._order = tuple(entries)

    def tostring(self) -> str:
        entries: dict[str, Node] = {}
        sessions: dict[str, Node] = {}
        for sid, value in self.sessions.items():
            sessions[sid] = _scalar(value.tostring())
        values: dict[str, Node] = {'SessionsPerLevel': _mapping(sessions)}
        for field, attr in _BOOLS.items():
            value = _BOOL.validate_python(getattr(self, attr))
            if field in self._present or value:
                entries[field] = ScalarNode('tag:yaml.org,2002:bool', str(value).lower())
        positions = _POSITIONS.validate_python(self.visited_lobby_positions)
        values['VisitedLobbyPositions'] = _mapping(
            {sid: _scalar(value) for sid, value in positions.items()}
        )
        for field, attr in _MOD_SESSIONS.items():
            payloads = _PAYLOADS.validate_python(getattr(self, attr))
            values[field] = _mapping(
                {
                    sid: _mapping({name: _scalar(payload) for name, payload in data.items()})
                    for sid, data in payloads.items()
                }
            )
        pbs = _PBS.validate_python(self.speed_berry_pbs)
        values['SpeedBerryPBs'] = _mapping(
            {sid: _scalar(values_codec.format_duration(value)) for sid, value in pbs.items()}
        )
        for field, attr in _SETS.items():
            values[field] = _sequence(_STRINGS.validate_python(getattr(self, attr)))
        tech = _TECH.validate_python(self.learned_tech)
        values['LearnedTech'] = _mapping(
            {collab: _sequence(value) for collab, value in tech.items()}
        )
        for field, value in values.items():
            if field in self._present or value.value:
                entries[field] = value
        ordered = dict.fromkeys((*self._order, *entries))
        root = MappingNode(
            yaml_nodes.MAP_TAG, [(_scalar(key), entries[key]) for key in ordered if key in entries]
        )
        return self._dump(root)
