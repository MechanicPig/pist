"""Coordinated progress membership and eagerly maintained SID indexes."""

from collections.abc import Iterable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, overload
from weakref import WeakValueDictionary

from typing_extensions import Sentinel as sentinel

from berries.containers import KeyedDict
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.partition import ProgressPartition

from .level import LevelProgress, campaign_name

if TYPE_CHECKING:
    from .campaign import CampaignProgress
    from .views import VanillaProgressView


type CampaignOwner = CampaignProgress | VanillaProgressView


_MISSING = sentinel('_MISSING')


def _progress_key(progress: CampaignProgress | LevelProgress) -> str:
    return progress.key


class LevelMembers(Mapping[str, LevelProgress]):
    """SID-keyed levels; new writes must belong to the enclosing campaign."""

    def __init__(self, name: str, values: Iterable[LevelProgress] | LevelMembers = ()) -> None:
        self._name = name
        self._indexes: WeakValueDictionary[int, LevelIndexState] = WeakValueDictionary()
        if isinstance(values, LevelMembers) and values.name == name:
            # Preserve retained lenient input when copying the same membership.
            self._data = values._data.copy()  # noqa: SLF001
        else:
            self._data = KeyedDict(
                values.values() if isinstance(values, LevelMembers) else values, key=_progress_key
            )
            self.validate_ownership()

    @classmethod
    def _from_decoded(cls, name: str, values: Iterable[LevelProgress]) -> Self:
        """Assemble checked decoder input, retaining lenient ownership errors.

        This is not a structural replacement: new installations must validate
        ownership again before attaching the decoded members to an existing root.
        """
        result = cls(name)
        result._data.update(values)
        return result

    def validate_member(self, value: LevelProgress) -> None:
        """Require a newly installed SID to belong to this campaign."""
        if campaign_name(value.sid) != self.name:
            raise BadProgressSave(f'SID {value.sid!r} does not belong to {self.name!r}.')

    def validate_ownership(self) -> None:
        """Check all retained members before a structural installation or write."""
        for value in self.values():
            self.validate_member(value)

    def _prepare_replacement(
        self, campaign: CampaignOwner, replacement: LevelMembers
    ) -> tuple[IndexDelta, ...]:
        """Preflight all related indexes before replacing this campaign's levels."""
        return tuple(
            index.prepare_rebind(campaign, replacement) for index in self._indexes.values()
        )

    @property
    def name(self) -> str:
        return self._name

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __getitem__(self, sid: str) -> LevelProgress:
        return self._data[sid]

    def __contains__(self, sid: object) -> bool:
        return sid in self._data

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LevelMembers):
            return tuple(self) == tuple(other) and self._data == other._data
        return super().__eq__(other)

    def _install(self, values: dict[str, LevelProgress], removed: tuple[str, ...] = ()) -> None:
        updates = [
            index.prepare_level_changes({id(self): (self, values, removed)})
            for index in self._indexes.values()
        ]
        for sid in removed:
            del self._data[sid]
        self._data.update(values.values())
        for update in updates:
            update.apply()

    def add(self, value: LevelProgress, /) -> None:
        self.validate_member(value)
        if not self._indexes:
            self._data.add(value)
            return
        self._install({value.sid: value})

    def adddefault(self, value: LevelProgress, /) -> LevelProgress:
        if (old := self.get(value.sid)) is not None:
            return old
        self.add(value)
        return value

    def replace(self, value: LevelProgress, /) -> None:
        if value.sid not in self:
            raise KeyError(value.sid)
        self.add(value)

    def update(self, *others: Iterable[LevelProgress] | Mapping[str, LevelProgress]) -> None:
        incoming: dict[str, LevelProgress] = {}
        for other in others:
            for level in other.values() if isinstance(other, Mapping) else other:
                self.validate_member(level)
                incoming[level.sid] = level
        if not self._indexes:
            self._data.update(incoming.values())
            return
        self._install(incoming)

    def __delitem__(self, sid: str) -> None:
        if not self._indexes:
            del self._data[sid]
            return
        if sid not in self:
            raise KeyError(sid)
        self._install({}, (sid,))

    @overload
    def pop(self, sid: str, /) -> LevelProgress: ...
    @overload
    def pop[D](self, sid: str, /, default: D) -> LevelProgress | D: ...
    def pop[D](self, sid: str, /, default: D | _MISSING = _MISSING) -> LevelProgress | D:
        if not self._indexes:
            return self._data.pop(sid) if default is _MISSING else self._data.pop(sid, default)
        if sid not in self:
            if default is _MISSING:
                raise KeyError(sid)
            return default
        value = self[sid]
        del self[sid]
        return value

    def popitem(self) -> tuple[str, LevelProgress]:
        if not self._indexes:
            return self._data.popitem()
        if not self:
            raise KeyError('popitem from empty progress members')
        sid = next(reversed(self._data))
        return sid, self.pop(sid)

    def clear(self) -> None:
        if self._indexes:
            self._install({}, tuple(self))
        else:
            self._data.clear()

    def __copy__(self) -> Self:
        return type(self)(self.name, self)

    def copy(self) -> Self:
        return self.__copy__()

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        result = type(self)(self.name)
        memo[id(self)] = result
        result._data = deepcopy(self._data, memo)  # noqa: SLF001
        return result


class CampaignMembers(Mapping[str, 'CampaignProgress']):
    """One Mod partition's campaign names and coordinated member registration."""

    def __init__(
        self,
        values: Iterable[CampaignProgress] | CampaignMembers = (),
        *,
        partition: ProgressPartition = ProgressPartition.MODS,
    ) -> None:
        self._partition = partition
        self._indexes: WeakValueDictionary[int, LevelIndexState] = WeakValueDictionary()
        if isinstance(values, CampaignMembers):
            self._data = values._data.copy()  # noqa: SLF001
        else:
            self._data = KeyedDict(values, key=_progress_key)
            self.validate_ownership()

    @classmethod
    def _from_decoded(
        cls,
        values: Iterable[CampaignProgress],
        *,
        partition: ProgressPartition = ProgressPartition.MODS,
    ) -> Self:
        """Assemble decoder input after identities and conflicts were checked."""
        result = cls(partition=partition)
        result._data.update(values)
        return result

    def validate_member(self, value: CampaignProgress) -> None:
        """Reject an active vanilla placeholder and invalid Level ownership."""
        if value.name == 'Celeste' and self._partition is not ProgressPartition.RECYCLED:
            raise BadProgressSave('Celeste progress belongs to MainSave.vanilla.')
        value.levels.validate_ownership()

    def validate_ownership(self) -> None:
        """Validate this partition without requiring another partition's repair."""
        for value in self.values():
            self.validate_member(value)

    @property
    def partition(self) -> ProgressPartition:
        return self._partition

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __getitem__(self, name: str) -> CampaignProgress:
        return self._data[name]

    def __contains__(self, name: object) -> bool:
        return name in self._data

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CampaignMembers):
            return tuple(self) == tuple(other) and self._data == other._data
        return super().__eq__(other)

    def _install(self, values: dict[str, CampaignProgress], removed: tuple[str, ...] = ()) -> None:
        updates = [
            index.prepare_campaign_change(self, values, removed) for index in self._indexes.values()
        ]
        for name in removed:
            del self._data[name]
        self._data.update(values.values())
        for update in updates:
            update.apply()

    @staticmethod
    def update_together(*changes: tuple[CampaignMembers, CampaignProgress]) -> None:
        """Preflight all memberships and related indexes before any installation."""
        groups: dict[int, tuple[CampaignMembers, dict[str, CampaignProgress]]] = {}
        for members, campaign in changes:
            members.validate_member(campaign)
            if id(members) not in groups:
                groups[id(members)] = members, {}
            groups[id(members)][1][campaign.name] = campaign
        indexes = {
            key: index
            for members, _ in groups.values()
            for key, index in members._indexes.items()  # noqa: SLF001
        }
        updates = [index.prepare_campaign_changes(groups.values()) for index in indexes.values()]
        for members, values in groups.values():
            members._data.update(values.values())  # noqa: SLF001
        for update in updates:
            update.apply()

    def add(self, value: CampaignProgress, /) -> None:
        self.validate_member(value)
        if not self._indexes:
            self._data.add(value)
            return
        self._install({value.name: value})

    def adddefault(self, value: CampaignProgress, /) -> CampaignProgress:
        if (old := self.get(value.name)) is not None:
            return old
        self.add(value)
        return value

    def replace(self, value: CampaignProgress, /) -> None:
        if value.name not in self:
            raise KeyError(value.name)
        self.add(value)

    def update(self, *others: Iterable[CampaignProgress] | Mapping[str, CampaignProgress]) -> None:
        incoming: dict[str, CampaignProgress] = {}
        for other in others:
            for campaign in other.values() if isinstance(other, Mapping) else other:
                self.validate_member(campaign)
                incoming[campaign.name] = campaign
        if not self._indexes:
            self._data.update(incoming.values())
            return
        self._install(incoming)

    def __delitem__(self, name: str) -> None:
        if not self._indexes:
            del self._data[name]
            return
        if name not in self:
            raise KeyError(name)
        self._install({}, (name,))

    @overload
    def pop(self, name: str, /) -> CampaignProgress: ...
    @overload
    def pop[D](self, name: str, /, default: D) -> CampaignProgress | D: ...
    def pop[D](self, name: str, /, default: D | _MISSING = _MISSING) -> CampaignProgress | D:
        if not self._indexes:
            return self._data.pop(name) if default is _MISSING else self._data.pop(name, default)
        if name not in self:
            if default is _MISSING:
                raise KeyError(name)
            return default
        value = self[name]
        del self[name]
        return value

    def popitem(self) -> tuple[str, CampaignProgress]:
        if not self._indexes:
            return self._data.popitem()
        if not self:
            raise KeyError('popitem from empty progress members')
        name = next(reversed(self._data))
        return name, self.pop(name)

    def clear(self) -> None:
        if self._indexes:
            self._install({}, tuple(self))
        else:
            self._data.clear()

    def __copy__(self) -> Self:
        return type(self)(self, partition=self._partition)

    def copy(self) -> Self:
        return self.__copy__()

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        result = type(self)(partition=self._partition)
        memo[id(self)] = result
        result._data = deepcopy(self._data, memo)  # noqa: SLF001
        return result


@dataclass
class MembershipUpdate:
    index: LevelIndexState
    levels: dict[str, LevelProgress]
    owners: dict[str, CampaignOwner]
    campaign_members: list[CampaignMembers]
    level_members: dict[int, tuple[LevelMembers, CampaignOwner]]
    sources: tuple[CampaignMembers | CampaignOwner, ...]

    def apply(self) -> None:
        self.index.install(self)


type LevelChanges = Mapping[int, tuple[LevelMembers, Mapping[str, LevelProgress], tuple[str, ...]]]


@dataclass
class IndexDelta:
    """A preflighted change, retaining only affected entries and registrations."""

    index: LevelIndexState
    removed: set[str]
    levels: dict[str, LevelProgress]
    owners: dict[str, CampaignOwner]
    detached: dict[int, tuple[LevelMembers, CampaignOwner]]
    attached: dict[int, tuple[LevelMembers, CampaignOwner]]

    def apply(self) -> None:
        self.index.install_delta(self)


class LevelIndexState(Mapping[str, LevelProgress]):
    """An eager, editable SID index; writes are routed through stored campaigns."""

    def __init__(self, *sources: CampaignMembers | CampaignOwner) -> None:
        self._data: KeyedDict[str, LevelProgress] = KeyedDict(key=_progress_key)
        self._sources = sources
        self._owners: dict[str, CampaignOwner] = {}
        self._campaign_members: list[CampaignMembers] = []
        self._level_members: dict[int, tuple[LevelMembers, CampaignOwner]] = {}
        self.prepare().apply()
        self.view = LevelIndex(self)

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __getitem__(self, sid: str) -> LevelProgress:
        return self._data[sid]

    def __contains__(self, sid: object) -> bool:
        return sid in self._data

    def prepare(
        self,
        *,
        sources: tuple[CampaignMembers | CampaignOwner, ...] | None = None,
    ) -> MembershipUpdate:
        """Stage a complete partition for loading or root replacement."""
        return self._prepare(self._sources if sources is None else sources)

    def prepare_campaign_change(
        self,
        members: CampaignMembers,
        values: Mapping[str, CampaignOwner],
        removed: tuple[str, ...],
    ) -> IndexDelta:
        detached: dict[int, tuple[LevelMembers, CampaignOwner]] = {}
        for name in dict.fromkeys((*removed, *values)):
            if (old := members.get(name)) is not None:
                detached[id(old.levels)] = old.levels, old
        attached = {id(value.levels): (value.levels, value) for value in values.values()}
        return self._prepare_members_change(detached, attached)

    def prepare_campaign_changes(
        self, changes: Iterable[tuple[CampaignMembers, Mapping[str, CampaignOwner]]]
    ) -> IndexDelta:
        detached: dict[int, tuple[LevelMembers, CampaignOwner]] = {}
        attached: dict[int, tuple[LevelMembers, CampaignOwner]] = {}
        for members, values in changes:
            if not any(source is members for source in self._campaign_members):
                continue
            for name, campaign in values.items():
                if (old := members.get(name)) is not None:
                    detached[id(old.levels)] = old.levels, old
                attached[id(campaign.levels)] = campaign.levels, campaign
        return self._prepare_members_change(detached, attached)

    def prepare_level_changes(self, changes: LevelChanges) -> IndexDelta:
        """Preflight all affected parents together before a flat batch write."""
        removed: set[str] = set()
        levels: dict[str, LevelProgress] = {}
        owners: dict[str, CampaignOwner] = {}
        for key, (_, incoming, deleted) in changes.items():
            if (binding := self._level_members.get(key)) is None:
                continue
            members, campaign = binding
            removed.update(deleted)
            for sid, level in incoming.items():
                if sid in levels:
                    raise BadProgressSave(f'Duplicate progress SID {sid!r}.')
                levels[sid] = level
                owners[sid] = campaign
                if sid in self._data and self._owners[sid].levels is not members:
                    raise BadProgressSave(f'Duplicate progress SID {sid!r}.')
        return IndexDelta(self, removed, levels, owners, {}, {})

    def _prepare_members_change(
        self,
        detached: dict[int, tuple[LevelMembers, CampaignOwner]],
        attached: dict[int, tuple[LevelMembers, CampaignOwner]],
    ) -> IndexDelta:
        removed = {sid for members, _ in detached.values() for sid in members}
        levels: dict[str, LevelProgress] = {}
        owners: dict[str, CampaignOwner] = {}
        for members, campaign in attached.values():
            for sid, level in members.items():
                if sid in levels or (sid in self._data and sid not in removed):
                    raise BadProgressSave(f'Duplicate progress SID {sid!r}.')
                levels[sid] = level
                owners[sid] = campaign
        return IndexDelta(self, removed, levels, owners, detached, attached)

    def _prepare(
        self,
        sources: tuple[CampaignMembers | CampaignOwner, ...],
    ) -> MembershipUpdate:
        levels: dict[str, LevelProgress] = {}
        owners: dict[str, CampaignOwner] = {}
        campaign_members: list[CampaignMembers] = []
        level_members: dict[int, tuple[LevelMembers, CampaignOwner]] = {}
        for source in sources:
            if isinstance(source, CampaignMembers):
                campaign_members.append(source)
                campaigns = source.values()
            else:
                campaigns = (source,)
            for campaign in campaigns:
                members = campaign.levels
                level_members[id(members)] = members, campaign
                for level in members.values():
                    if level.sid in levels:
                        raise BadProgressSave(f'Duplicate progress SID {level.sid!r}.')
                    levels[level.sid] = level
                    owners[level.sid] = campaign
        return MembershipUpdate(self, levels, owners, campaign_members, level_members, sources)

    def prepare_rebind(self, campaign: CampaignOwner, replacement: LevelMembers) -> IndexDelta:
        old = campaign.levels
        binding = self._level_members.get(id(old))
        # A shallow campaign copy can share members without belonging to this index.
        if binding is None or binding[1] is not campaign:
            return IndexDelta(self, set(), {}, {}, {}, {})
        return self._prepare_members_change(
            {id(old): (old, campaign)}, {id(replacement): (replacement, campaign)}
        )

    def configure(self, *sources: CampaignMembers | CampaignOwner) -> None:
        self.prepare(sources=sources).apply()

    def install(self, update: MembershipUpdate) -> None:
        for campaigns in self._campaign_members:
            campaigns._indexes.pop(id(self), None)  # noqa: SLF001 # pyright: ignore[reportUnusedCallResult]
        for levels, _ in self._level_members.values():
            levels._indexes.pop(id(self), None)  # noqa: SLF001 # pyright: ignore[reportUnusedCallResult]
        self._data.clear()
        self._data.update(update.levels.values())
        self._owners = update.owners
        self._campaign_members = update.campaign_members
        self._level_members = update.level_members
        self._sources = update.sources
        for campaigns in self._campaign_members:
            campaigns._indexes[id(self)] = self  # noqa: SLF001
        for levels, _ in self._level_members.values():
            levels._indexes[id(self)] = self  # noqa: SLF001

    def install_delta(self, update: IndexDelta) -> None:
        for sid in update.removed:
            if sid not in update.levels:
                del self._data[sid]
                del self._owners[sid]
        self._data.update(update.levels.values())
        self._owners.update(update.owners)
        for key, (members, _) in update.detached.items():
            del self._level_members[key]
            members._indexes.pop(id(self), None)  # noqa: SLF001 # pyright: ignore[reportUnusedCallResult]
        self._level_members.update(update.attached)
        for members, _ in update.attached.values():
            members._indexes[id(self)] = self  # noqa: SLF001

    def _parent(self, sid: str) -> CampaignOwner:
        name = campaign_name(sid)
        for source in self._sources:
            if isinstance(source, CampaignMembers):
                campaign = source.get(name)
                if campaign is not None:
                    return campaign
            elif source.name == name:
                return source
        raise KeyError(name)

    def add(self, value: LevelProgress, /) -> None:
        self._parent(value.sid).levels.add(value)

    def adddefault(self, value: LevelProgress, /) -> LevelProgress:
        if (old := self.get(value.sid)) is not None:
            return old
        self.add(value)
        return value

    def replace(self, value: LevelProgress, /) -> None:
        if value.sid not in self:
            raise KeyError(value.sid)
        self.add(value)

    def update(self, *others: Iterable[LevelProgress] | Mapping[str, LevelProgress]) -> None:
        # Validate every parent before any write; a valid SID has exactly one parent.
        groups: dict[int, tuple[LevelMembers, dict[str, LevelProgress], tuple[str, ...]]] = {}
        for other in others:
            for value in other.values() if isinstance(other, Mapping) else other:
                members = self._parent(value.sid).levels
                members.validate_member(value)
                if id(members) not in groups:
                    groups[id(members)] = members, {}, ()
                groups[id(members)][1][value.sid] = value
        self._install_groups(groups)

    @staticmethod
    def _install_groups(
        groups: Mapping[int, tuple[LevelMembers, dict[str, LevelProgress], tuple[str, ...]]],
    ) -> None:
        indexes = {
            key: index
            for members, _, _ in groups.values()
            for key, index in members._indexes.items()  # noqa: SLF001
        }
        updates = [index.prepare_level_changes(groups) for index in indexes.values()]
        for members, values, removed in groups.values():
            for sid in removed:
                del members._data[sid]  # noqa: SLF001
            members._data.update(values.values())  # noqa: SLF001
        for update in updates:
            update.apply()

    def __delitem__(self, sid: str) -> None:
        owner = self._owners[sid]
        del owner.levels[sid]

    @overload
    def pop(self, sid: str, /) -> LevelProgress: ...
    @overload
    def pop[D](self, sid: str, /, default: D) -> LevelProgress | D: ...
    def pop[D](self, sid: str, /, default: D | _MISSING = _MISSING) -> LevelProgress | D:
        if sid not in self:
            if default is _MISSING:
                raise KeyError(sid)
            return default
        value = self[sid]
        del self[sid]
        return value

    def popitem(self) -> tuple[str, LevelProgress]:
        if not self:
            raise KeyError('popitem from empty progress index')
        sid = next(reversed(self._data))
        return sid, self.pop(sid)

    def clear(self) -> None:
        groups: dict[int, tuple[LevelMembers, dict[str, LevelProgress], tuple[str, ...]]] = {}
        for source in self._sources:
            campaigns = source.values() if isinstance(source, CampaignMembers) else (source,)
            for campaign in campaigns:
                groups[id(campaign.levels)] = campaign.levels, {}, tuple(campaign.levels)
        self._install_groups(groups)

    def __copy__(self) -> Self:
        raise TypeError('Copy campaign members, not a derived SID index.')

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        result = object.__new__(type(self))
        memo[id(self)] = result
        type(self).__init__(result, *deepcopy(self._sources, memo))
        return result


class LevelIndex(Mapping[str, LevelProgress]):
    """A partition's SID mapping obtained from a save root, not constructed by users."""

    def __init__(self, state: LevelIndexState) -> None:
        self._state = state

    def __len__(self) -> int:
        return len(self._state)

    def __iter__(self) -> Iterator[str]:
        return iter(self._state)

    def __getitem__(self, sid: str) -> LevelProgress:
        return self._state[sid]

    def __contains__(self, sid: object) -> bool:
        return sid in self._state

    def add(self, value: LevelProgress, /) -> None:
        self._state.add(value)

    def adddefault(self, value: LevelProgress, /) -> LevelProgress:
        return self._state.adddefault(value)

    def replace(self, value: LevelProgress, /) -> None:
        self._state.replace(value)

    def update(self, *others: Iterable[LevelProgress] | Mapping[str, LevelProgress]) -> None:
        self._state.update(*others)

    def __delitem__(self, sid: str) -> None:
        del self._state[sid]

    @overload
    def pop(self, sid: str, /) -> LevelProgress: ...
    @overload
    def pop[D](self, sid: str, /, default: D) -> LevelProgress | D: ...
    def pop[D](self, sid: str, /, default: D | _MISSING = _MISSING) -> LevelProgress | D:
        return self._state.pop(sid) if default is _MISSING else self._state.pop(sid, default)

    def popitem(self) -> tuple[str, LevelProgress]:
        return self._state.popitem()

    def clear(self) -> None:
        self._state.clear()

    def __copy__(self) -> Self:
        raise TypeError('Use dict(index) for a detached SID lookup snapshot.')

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        raise TypeError('Deep-copy domain members, not a root-bound SID index.')
