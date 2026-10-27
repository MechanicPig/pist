"""Complete AreaStats values and exact, optionally unresolved Level identities."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Self

from lxml import etree
from pydantic import StrictBool, TypeAdapter

from berries.game.levels import LevelSide
from berries.game.saves.codecs import values as values_codec
from berries.game.saves.codecs.xml import fields as xml_fields
from berries.game.saves.codecs.xml import schema, tree
from berries.game.saves.errors import BadProgressSave, UnresolvedLevelIdentityError
from berries.game.saves.unknown import UnknownFieldPolicy
from berries.game.vanilla import VANILLA_SIDS

from .map import MapProgress

_BOOL = TypeAdapter(StrictBool)
_ID = TypeAdapter(values_codec.SaveInt)
_SID = TypeAdapter(xml_fields.XmlText)


def campaign_name(sid: str) -> str:
    """Return Everest's exact LevelSet prefix without path normalization."""
    if not sid:
        raise ValueError('A progress SID must not be empty.')
    return sid.rpartition('/')[0]


def _read_area(
    root: etree.Element, *, strict: bool, unknown: UnknownFieldPolicy, path: str
) -> tuple[int, bool, list[MapProgress | None]]:
    schema.check(
        root, attributes={'ID', 'SID', 'Cassette'}, children={'Modes'}, unknown=unknown, path=path
    )
    level_id = _ID.validate_python(root.get('ID', '0'))
    cassette = values_codec.parse_bool(root.get('Cassette', 'false'))
    modes = tree.child(root, 'Modes')
    if strict and modes is None:
        raise BadProgressSave(f'Missing Modes at {path!r}; expected three entries.')
    if strict and modes is not None and tree.is_nil(modes):
        raise BadProgressSave(f'Nil Modes at {path!r}; expected three entries.')
    if modes is None or tree.is_nil(modes):
        # A missing/nil non-nullable Modes array keeps the constructor defaults.
        if modes is not None:
            schema.check(modes, children={'AreaModeStats'}, unknown=unknown, path=f'{path}.Modes')
        return level_id, cassette, [MapProgress() for _ in LevelSide]
    nodes = tuple(schema.members(modes, 'AreaModeStats', unknown=unknown, path=f'{path}.Modes'))
    if strict and (len(nodes) != len(LevelSide) or any(tree.is_nil(node) for node in nodes)):
        raise BadProgressSave(f'Expected three non-null A/B/C entries at {path!r}.')
    maps: list[MapProgress | None] = []
    for i, node in enumerate(nodes):
        if tree.is_nil(node):
            schema.check(
                node,
                attributes=xml_fields.MAP_ATTRIBUTES,
                children={'Strawberries', 'Checkpoints'},
                unknown=unknown,
                path=f'{path}.Modes[{i}]',
            )
            maps.append(None)
        else:
            maps.append(
                MapProgress.from_xml(
                    node, strict=strict, unknown=unknown, path=f'{path}.Modes[{i}]'
                )
            )
    return level_id, cassette, maps


def _validate_maps(sid: str, maps: list[MapProgress | None]) -> None:
    if len(maps) != len(LevelSide):
        raise BadProgressSave(f'Expected three A/B/C entries for {sid!r}; got {len(maps)}.')
    for i, progress in enumerate(maps):
        if progress is None:
            raise BadProgressSave(f'Missing {LevelSide(i).name} progress for {sid!r}.')


def _element(
    sid: str, level_id: int, cassette: bool, maps: list[MapProgress | None]
) -> etree.Element:
    _validate_maps(sid, maps)
    root = etree.Element(
        'AreaStats',
        ID=str(_ID.validate_python(level_id)),
        Cassette=str(_BOOL.validate_python(cassette)).lower(),
        SID=sid,
    )
    modes = etree.SubElement(root, 'Modes')
    for progress in maps:
        assert progress is not None
        modes.append(progress.to_xml())
    return root


class LevelProgress:
    """One resolved SID with its stored numeric ID, cassette and positional maps."""

    def __init__(
        self,
        sid: str,
        maps: Iterable[MapProgress | None] | None = None,
        *,
        cassette: bool = False,
        id: int = 0,
    ) -> None:
        if not sid:
            raise ValueError('A progress SID must not be empty.')
        self._sid = sid
        self.id = id
        self.cassette = cassette
        self.maps: list[MapProgress | None] = list(
            (MapProgress() for _ in LevelSide) if maps is None else maps
        )

    @property
    def sid(self) -> str:
        return self._sid

    @property
    def key(self) -> str:
        return self.sid

    def reset(self) -> None:
        self.cassette = False
        for progress in self.maps:
            if progress is not None:
                progress.reset()

    @classmethod
    def from_xml(
        cls,
        root: etree.Element,
        *,
        vanilla: bool = False,
        strict: bool = True,
        unknown: UnknownFieldPolicy = 'error',
        path: str = 'AreaStats',
    ) -> Self:
        sid = root.get('SID')
        if sid is None and vanilla:
            level_id = root.get('ID')
            if level_id is None:
                raise ValueError('Missing original-game AreaStats/ID.')
            parsed_id = values_codec.parse_count(level_id)
            sid = VANILLA_SIDS[parsed_id] if parsed_id < len(VANILLA_SIDS) else None
        if not sid:
            raise UnresolvedLevelIdentityError(f'Unresolved Level identity at {path!r}.')
        level_id, cassette, maps = _read_area(root, strict=strict, unknown=unknown, path=path)
        return cls(sid, maps, cassette=cassette, id=level_id)

    def to_xml(self) -> etree.Element:
        return _element(self.sid, self.id, self.cassette, self.maps)

    def validate_output(self) -> None:
        """Check output structure and values without encoding or replacing Maps."""
        _validate_maps(self.sid, self.maps)
        _ = _SID.validate_python(self.sid)
        _ = _ID.validate_python(self.id)
        _ = _BOOL.validate_python(self.cassette)
        for progress in self.maps:
            assert progress is not None
            progress.validate_output()

    def tostring(self) -> bytes:
        return etree.tostring(self.to_xml(), encoding='utf-8')

    def __eq__(self, other: object) -> bool:
        """Compare identity fields and ordered map values."""
        if not isinstance(other, LevelProgress) or type(self) is not type(other):
            return NotImplemented
        return (self.sid, self.id, self.cassette, self.maps) == (
            other.sid,
            other.id,
            other.cassette,
            other.maps,
        )


@dataclass
class UnresolvedLevelProgress:
    """Inspectable AreaStats with no resolvable SID, excluded from SID indexes.

    Repair by constructing a resolved Level using resolve(), adding it to its
    correct Campaign, and removing this entry from unresolved_levels. No XML
    fragment or synthetic SID is used to hide an unresolved numeric identity.
    """

    id: int = 0
    cassette: bool = False
    maps: list[MapProgress | None] = field(
        default_factory=lambda: [MapProgress() for _ in LevelSide]
    )

    def reset(self) -> None:
        """Reset known progress, retaining the unresolved identity and Modes structure."""
        self.cassette = False
        for progress in self.maps:
            if progress is not None:
                progress.reset()

    @classmethod
    def from_xml(
        cls,
        root: etree.Element,
        *,
        strict: bool = False,
        unknown: UnknownFieldPolicy = 'error',
        path: str = 'AreaStats',
    ) -> Self:
        level_id, cassette, maps = _read_area(root, strict=strict, unknown=unknown, path=path)
        return cls(level_id, cassette, maps)

    def resolve(self, sid: str) -> LevelProgress:
        """Return resolved progress sharing the existing map values, without removing this entry."""
        return LevelProgress(sid, self.maps, cassette=self.cassette, id=self.id)
