"""Ordered campaign progress and campaign collections."""

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Self

from lxml import etree

from berries.game.saves.codecs import values as values_codec
from berries.game.saves.codecs.xml import campaign as campaign_codec
from berries.game.saves.codecs.xml import schema
from berries.game.saves.codecs.xml import tree as xml_tree
from berries.game.saves.codecs.xml import vanilla as vanilla_codec
from berries.game.saves.errors import (
    BadProgressSave,
    UnresolvedLevelIdentityError,
    UnsupportedProgressError,
)
from berries.game.saves.partition import ProgressPartition
from berries.game.saves.unknown import UnknownFieldPolicy

from .level import LevelProgress, UnresolvedLevelProgress, campaign_name
from .members import LevelMembers

if TYPE_CHECKING:
    from berries.game.saves.conflicts import LevelConflictResolver


class CampaignProgress:
    """Campaign metadata and an explicit ordered Level progress collection."""

    def __init__(
        self,
        name: str,
        levels: Iterable[LevelProgress] | LevelMembers = (),
        *,
        unlocked_levels: int = 0,
        total_strawberries: int = 0,
        poem: Iterable[str] = (),
    ) -> None:
        self._name = name
        self.unlocked_levels = unlocked_levels
        self.total_strawberries = total_strawberries
        self.poem = list(poem)
        self.unresolved_levels: list[UnresolvedLevelProgress] = []

        self._levels = LevelMembers(name, levels)

    @property
    def levels(self) -> LevelMembers:
        return self._levels

    @levels.setter
    def levels(self, values: Iterable[LevelProgress] | Mapping[str, LevelProgress]) -> None:
        replacement = (
            values
            if isinstance(values, LevelMembers) and values.name == self.name
            else LevelMembers(self.name, values.values() if isinstance(values, Mapping) else values)
        )
        replacement.validate_ownership()
        updates = self.levels._prepare_replacement(self, replacement)  # noqa: SLF001
        self._levels = replacement
        for update in updates:
            update.apply()

    @property
    def name(self) -> str:
        return self._name

    @property
    def key(self) -> str:
        """Return the immutable campaign identity used by progress collections."""
        return self.name

    def reset(self) -> None:
        """Reset modeled campaign progress, preserving levels and save totals."""
        self.unlocked_levels = 0
        self.total_strawberries = 0
        self.poem.clear()
        for level in self.levels.values():
            level.reset()
        for unresolved in self.unresolved_levels:
            unresolved.reset()

    @classmethod
    def from_xml(
        cls,
        root: etree.Element,
        *,
        strict: bool = True,
        on_conflict: LevelConflictResolver | None = None,
        partition: ProgressPartition = ProgressPartition.MODS,
        unknown: UnknownFieldPolicy = 'error',
        path: str = 'LevelSetStats',
    ) -> Self:
        """Decode a complete LevelSetStats element using its stored Name."""
        schema.check(
            root,
            attributes={'Name'},
            children={'Areas', 'Poem', 'UnlockedAreas', 'TotalStrawberries'},
            unknown=unknown,
            path=path,
        )
        return cls._from_xml_fields(
            root,
            root.get('Name', ''),
            strict=strict,
            on_conflict=on_conflict,
            partition=partition,
            unknown=unknown,
            path=path,
        )

    @classmethod
    def from_main_xml(
        cls,
        root: etree.Element,
        *,
        strict: bool = True,
        on_conflict: LevelConflictResolver | None = None,
        unknown: UnknownFieldPolicy = 'error',
        path: str = 'SaveData',
    ) -> Self:
        """Read vanilla progress fields directly from the main-save root.

        Only Areas, Poem, UnlockedAreas and TotalStrawberries are consumed and
        validated here. The main-save decoder validates the surrounding schema.
        The input tree is neither copied nor modified.
        """
        result = cls._from_xml_fields(
            root,
            'Celeste',
            strict=strict,
            on_conflict=on_conflict,
            partition=ProgressPartition.VANILLA,
            unknown=unknown,
            path=path,
        )
        if strict:
            vanilla_codec.validate_progress_input(root, result, path=path)
        return result

    @classmethod
    def _from_xml_fields(
        cls,
        root: etree.Element,
        name: str,
        *,
        strict: bool,
        on_conflict: LevelConflictResolver | None,
        partition: ProgressPartition,
        unknown: UnknownFieldPolicy,
        path: str,
    ) -> Self:
        """Decode shared campaign fields without changing the input tree."""
        from berries.game.saves.conflicts import resolve_level

        unlocked = xml_tree.child(root, 'UnlockedAreas')
        total = xml_tree.child(root, 'TotalStrawberries')
        for field, node in (('UnlockedAreas', unlocked), ('TotalStrawberries', total)):
            if node is not None:
                schema.check(node, unknown=unknown, path=f'{path}.{field}')
        campaign = cls(
            name,
            unlocked_levels=0
            if unlocked is None
            else values_codec.parse_count(xml_tree.text(unlocked)),
            total_strawberries=0
            if total is None
            else values_codec.parse_count(xml_tree.text(total)),
            poem=schema.strings(xml_tree.child(root, 'Poem'), unknown=unknown, path=f'{path}.Poem'),
        )
        areas = xml_tree.child(root, 'Areas')
        loaded: dict[str, LevelProgress] = {}
        if areas is not None:
            for i, area in enumerate(
                schema.members(areas, 'AreaStats', unknown=unknown, path=f'{path}.Areas')
            ):
                area_path = f'{path}.Areas[{i}]'
                try:
                    level = LevelProgress.from_xml(
                        area,
                        vanilla=partition is ProgressPartition.VANILLA,
                        strict=strict,
                        unknown=unknown,
                        path=area_path,
                    )
                except UnresolvedLevelIdentityError:
                    if strict:
                        raise
                    campaign.unresolved_levels.append(
                        UnresolvedLevelProgress.from_xml(
                            area,
                            strict=False,
                            unknown=unknown,
                            path=area_path,
                        )
                    )
                    continue
                sid = level.sid
                if strict and campaign_name(sid) != name:
                    raise BadProgressSave(
                        f'Invalid progress: SID {sid!r} is stored in '
                        f'campaign {name!r}, expected {campaign_name(sid)!r}.'
                    )
                previous = loaded.get(sid)
                if previous is not None:
                    level = resolve_level(
                        previous,
                        level,
                        on_conflict=on_conflict,
                        strict=strict,
                        partition=partition,
                        campaign=campaign,
                    )
                loaded[sid] = level
        campaign._levels = LevelMembers._from_decoded(name, loaded.values())  # noqa: SLF001
        return campaign

    def to_xml(self) -> etree.Element:
        """Encode the complete known schema, requiring unresolved members to be repaired."""
        return campaign_codec.encode_progress(self)

    def tostring(self) -> bytes:
        return etree.tostring(self.to_xml(), encoding='utf-8')

    def __eq__(self, other: object) -> bool:
        """Compare stored values and member order, excluding index associations."""
        if not isinstance(other, CampaignProgress) or type(self) is not type(other):
            return NotImplemented
        if self is other:
            return True
        return (
            self.name == other.name
            and self.unlocked_levels == other.unlocked_levels
            and self.total_strawberries == other.total_strawberries
            and self.poem == other.poem
            and self.unresolved_levels == other.unresolved_levels
            and tuple(self.levels) == tuple(other.levels)
            and all(level == other.levels[sid] for sid, level in self.levels.items())
        )

    def require_known_levels(self) -> None:
        """An entire history cannot be transferred while some Level identities are unknown."""
        if self.unresolved_levels:
            raise UnsupportedProgressError(
                'Resolve all stored Level identities before moving an entire Campaign.'
            )
