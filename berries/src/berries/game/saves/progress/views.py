"""Bound projections of root save fields, without independent progress state."""

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from berries.game.levels import MapKey
from berries.game.saves.errors import UnsupportedProgressError

from .campaign import CampaignProgress
from .level import LevelProgress, UnresolvedLevelProgress
from .map_ref import MapRef
from .members import CampaignMembers, LevelIndex, LevelMembers
from .session import Session

if TYPE_CHECKING:
    from berries.game.saves.main_save_data import MainSaveData
    from berries.game.saves.mod_save_data import ModSaveData


class VanillaProgressView:
    """Edit the main root's original-game fields through a campaign-shaped view."""

    def __init__(self, owner: MainSaveData) -> None:
        self._owner = owner

    @property
    def name(self) -> str:
        return 'Celeste'

    @property
    def key(self) -> str:
        return self.name

    @property
    def levels(self) -> LevelMembers:
        return self._owner.levels

    @levels.setter
    def levels(self, values: Iterable[LevelProgress] | Mapping[str, LevelProgress]) -> None:
        self._owner.replace_vanilla_levels(values)

    @property
    def unlocked_levels(self) -> int:
        return self._owner.unlocked_levels

    @unlocked_levels.setter
    def unlocked_levels(self, value: int) -> None:
        self._owner.unlocked_levels = value

    @property
    def total_strawberries(self) -> int:
        return self._owner.total_strawberries

    @total_strawberries.setter
    def total_strawberries(self, value: int) -> None:
        self._owner.total_strawberries = value

    @property
    def poem(self) -> list[str]:
        return self._owner.poem

    @poem.setter
    def poem(self, value: list[str]) -> None:
        self._owner.poem = value

    @property
    def unresolved_levels(self) -> list[UnresolvedLevelProgress]:
        return self._owner.unresolved_levels

    def snapshot(self) -> CampaignProgress:
        """Create a detached campaign shell, sharing its progress values."""
        result = CampaignProgress(
            self.name,
            self.levels,
            unlocked_levels=self.unlocked_levels,
            total_strawberries=self.total_strawberries,
        )
        result.poem = self.poem
        result.unresolved_levels = self.unresolved_levels
        return result

    def require_known_levels(self) -> None:
        if self.unresolved_levels:
            raise UnsupportedProgressError(
                'Resolve all stored Level identities before moving an entire Campaign.'
            )

    def reset(self) -> None:
        self.unlocked_levels = 0
        self.total_strawberries = 0
        self.poem.clear()
        for level in self.levels.values():
            level.reset()
        for level in self.unresolved_levels:
            level.reset()


class ModsProgressView:
    """Mod fields and partition indexes bound to a main or backup save root."""

    def __init__(self, owner: ModSaveData, flat_levels: LevelIndex) -> None:
        self._owner = owner
        self._flat_levels = flat_levels

    @property
    def campaigns(self) -> CampaignMembers:
        return self._owner.campaigns

    @property
    def recycled_campaigns(self) -> CampaignMembers:
        return self._owner.recycled_campaigns

    @property
    def last_map_ref(self) -> MapRef | None:
        return self._owner.safe_last_map_ref

    @last_map_ref.setter
    def last_map_ref(self, value: MapRef | None) -> None:
        self._owner.safe_last_map_ref = value

    @property
    def last_map(self) -> MapKey | None:
        return None if self.last_map_ref is None else self.last_map_ref.resolve_key()

    @last_map.setter
    def last_map(self, value: MapKey | None) -> None:
        if value is None:
            self.last_map_ref = None
            return
        sid, side = value
        if not sid:
            raise ValueError('Last map SID must not be empty.')
        level_id = 0 if self.last_map_ref is None else self.last_map_ref.level_id
        self.last_map_ref = MapRef(level_id=level_id, side=side, level_sid=sid)

    @property
    def session(self) -> Session | None:
        return self._owner.safe_session

    @session.setter
    def session(self, value: Session | None) -> None:
        self._owner.safe_session = value

    @property
    def vanilla_entry(self) -> CampaignProgress | None:
        return self._owner.vanilla_entry

    @vanilla_entry.setter
    def vanilla_entry(self, value: CampaignProgress | None) -> None:
        self._owner.vanilla_entry = value

    @property
    def flat_levels(self) -> LevelIndex:
        return self._flat_levels

    @property
    def recycled_flat_levels(self) -> LevelIndex:
        return self._owner.recycled_flat_levels

    @property
    def has_session_data(self) -> bool:
        return self.session is not None

    @property
    def current_map(self) -> MapKey | None:
        value = self.session
        return None if value is None or not value.in_level else (value.sid, value.side)

    def partition_size(self, *, recycled: bool = False) -> int:
        return (
            len(self.recycled_campaigns)
            if recycled
            else (len(self.campaigns) + (self.vanilla_entry is not None))
        )
