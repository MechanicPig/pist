"""Load explicit Campaign projections for Collab lobby sides."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated

import tomlkit
from pydantic import AfterValidator, ValidationError, field_validator, model_validator

from pist.game.content import ContentPath
from pist.game.levels import LevelSide
from pist.models import FrozenModel
from pist.paths import PIST_DIR, SHARED_DATA_DIR, SOURCE_ROOT
from pist.types import NonEmptyStr

SHARED_COLLAB_LOBBIES_PATH = SHARED_DATA_DIR / 'collab_lobbies.toml'
LOCAL_COLLAB_LOBBIES_PATH = PIST_DIR / 'collab_lobbies.toml'
SHARED_COLLAB_LOBBIES_WRITABLE = SOURCE_ROOT is not None


def _validate_collab_lobby_sid(lobby: str) -> str:
    if '\\' in lobby:
        raise ValueError('A Collab lobby SID must use forward slashes.')
    path = ContentPath(lobby)
    if len(path.parts) < 3 or path.parts[1] != '0-Lobbies':
        raise ValueError('A Collab lobby override requires an exact lobby SID.')
    return lobby


def _validate_campaign_ref(campaign: str) -> str:
    if '\\' in campaign:
        raise ValueError('A Campaign reference must use forward slashes.')
    path = ContentPath(campaign)
    if path.parts[0] == 'Maps':
        raise ValueError('Campaign references must omit the Maps prefix.')
    return campaign


type CollabLobbySID = Annotated[NonEmptyStr, AfterValidator(_validate_collab_lobby_sid)]
type CampaignRef = Annotated[NonEmptyStr, AfterValidator(_validate_campaign_ref)]


class CollabLobbyOverride(FrozenModel):
    """The complete Campaign projection for one concrete lobby side."""

    lobby: CollabLobbySID
    side: LevelSide
    campaigns: tuple[CampaignRef, ...]

    @field_validator('campaigns')
    @classmethod
    def validate_unique_campaigns(
        cls, campaigns: tuple[CampaignRef, ...]
    ) -> tuple[CampaignRef, ...]:
        if len(campaigns) != len(set(campaigns)):
            raise ValueError('A Collab lobby override cannot repeat a Campaign reference.')
        return campaigns


class CollabLobbyOverrides(FrozenModel):
    """Validated explicit projections keyed by exact lobby SID and Side."""

    lobbies: tuple[CollabLobbyOverride, ...] = ()

    @model_validator(mode='after')
    def validate_unique_lobbies(self) -> CollabLobbyOverrides:
        keys = [(override.lobby, override.side) for override in self.lobbies]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate Collab lobby override.')
        return self

    def campaigns_for(self, lobby: str, side: LevelSide) -> tuple[str, ...] | None:
        """Return an explicit projection, or ``None`` when journals should be inspected."""
        return next(
            (
                override.campaigns
                for override in self.lobbies
                if override.lobby == lobby and override.side is side
            ),
            None,
        )

    def with_override(
        self, lobby: str, side: LevelSide, campaigns: tuple[str, ...]
    ) -> CollabLobbyOverrides:
        """Return a copy with one lobby-side projection replaced or appended."""
        replacement = CollabLobbyOverride(lobby=lobby, side=side, campaigns=campaigns)
        key = (lobby, side)
        retained = tuple(
            override for override in self.lobbies if (override.lobby, override.side) != key
        )
        return CollabLobbyOverrides(lobbies=(*retained, replacement))

    def without_override(self, lobby: str, side: LevelSide) -> CollabLobbyOverrides:
        """Return a copy without one exact lobby-side projection."""
        return CollabLobbyOverrides(
            lobbies=tuple(
                override
                for override in self.lobbies
                if (override.lobby, override.side) != (lobby, side)
            )
        )


def load_collab_lobby_overrides(path: Path) -> CollabLobbyOverrides:
    """Load one validated Collab lobby override file."""
    try:
        with path.open('rb') as file:
            return CollabLobbyOverrides.model_validate(tomllib.load(file))
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValidationError, ValueError) as error:
        raise ValueError(f'Invalid Collab lobby overrides: {path!r}') from error


def load_collab_lobby_override_layers(
    shared_path: Path = SHARED_COLLAB_LOBBIES_PATH,
    local_path: Path = LOCAL_COLLAB_LOBBIES_PATH,
) -> CollabLobbyOverrides:
    """Load shared declarations with whole-entry local overrides."""
    shared = load_collab_lobby_overrides(shared_path)
    if not local_path.is_file():
        return shared
    local = load_collab_lobby_overrides(local_path)
    local_keys = {(override.lobby, override.side) for override in local.lobbies}
    return CollabLobbyOverrides(
        lobbies=(
            *local.lobbies,
            *(
                override
                for override in shared.lobbies
                if (override.lobby, override.side) not in local_keys
            ),
        )
    )


class CollabLobbyOverrideStore:
    """Persist local projections and, from source checkouts, shared defaults."""

    def __init__(
        self,
        shared_path: Path = SHARED_COLLAB_LOBBIES_PATH,
        local_path: Path = LOCAL_COLLAB_LOBBIES_PATH,
        *,
        can_write_shared: bool = False,
    ) -> None:
        self._shared_path = shared_path
        self._can_write_shared = can_write_shared
        self.local_path = local_path

    @property
    def can_write_shared(self) -> bool:
        """Return whether this process may update the source-controlled shared layer."""
        return self._can_write_shared

    def load(self) -> CollabLobbyOverrides:
        """Load the current merged shared and local projections."""
        return load_collab_lobby_override_layers(self._shared_path, self.local_path)

    def has_local_override(self, lobby: str, side: LevelSide) -> bool:
        """Return whether the local layer explicitly defines this lobby side."""
        return self._load_local().campaigns_for(lobby, side) is not None

    def has_shared_override(self, lobby: str, side: LevelSide) -> bool:
        """Return whether the shared layer explicitly defines this lobby side."""
        return load_collab_lobby_overrides(self._shared_path).campaigns_for(lobby, side) is not None

    def save_local(
        self, lobby: str, side: LevelSide, campaigns: tuple[str, ...]
    ) -> CollabLobbyOverrides:
        """Save one complete local projection and return the merged rules."""
        local = self._load_local().with_override(lobby, side, campaigns)
        self._write(self.local_path, local)
        return self.load()

    def save_shared(
        self, lobby: str, side: LevelSide, campaigns: tuple[str, ...]
    ) -> CollabLobbyOverrides:
        """Save one shared projection and remove its now-redundant local override."""
        if not self._can_write_shared:
            raise ValueError('安装包模式不能修改共享配置。')
        shared = load_collab_lobby_overrides(self._shared_path).with_override(
            lobby, side, campaigns
        )
        self._write(self._shared_path, shared)
        self._write(
            self.local_path,
            self._load_local().without_override(lobby, side),
        )
        return self.load()

    def remove_local(self, lobby: str, side: LevelSide) -> CollabLobbyOverrides:
        """Remove one local projection so the shared or automatic behavior applies."""
        self._write(self.local_path, self._load_local().without_override(lobby, side))
        return self.load()

    def remove_shared(self, lobby: str, side: LevelSide) -> CollabLobbyOverrides:
        """Remove one shared projection without changing the local layer."""
        if not self._can_write_shared:
            raise ValueError('安装包模式不能修改共享配置。')
        shared = load_collab_lobby_overrides(self._shared_path).without_override(lobby, side)
        self._write(self._shared_path, shared)
        return self.load()

    def _load_local(self) -> CollabLobbyOverrides:
        return (
            load_collab_lobby_overrides(self.local_path)
            if self.local_path.is_file()
            else CollabLobbyOverrides()
        )

    @staticmethod
    def _write(path: Path, overrides: CollabLobbyOverrides) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = overrides.model_dump(mode='json')
        path.write_text(tomlkit.dumps(data), encoding='utf-8')


DEFAULT_COLLAB_LOBBY_OVERRIDES = load_collab_lobby_overrides(SHARED_COLLAB_LOBBIES_PATH)
