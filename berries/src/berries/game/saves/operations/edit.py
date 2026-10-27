"""Scope-aware queries and prepared single-save edits shared by copy and move."""

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from copy import copy, deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from berries.game.duration import Duration
from berries.game.levels import LevelSide, MapKey
from berries.game.saves.codecs import values as values_codec
from berries.game.saves.errors import BadProgressSave, ProgressConflictError
from berries.game.saves.files.communal_helper import CommunalHelperSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.main_save_data import MainSaveData
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress, campaign_name
from berries.game.saves.progress.map import MapProgress, PersonalBests
from berries.game.saves.progress.views import VanillaProgressView
from berries.map_entity_id import MapEntityID

from . import poem, preparation, sessions, summit_gems
from .poem import PoemID, PoemIDs
from .summit_gems import CustomSummitGemIDs

if TYPE_CHECKING:
    from berries.game.saves.files.collab_utils2 import CollabUtils2Save
    from berries.game.saves.files.mod_backup import ModBackupSave


type GoldenberryIDs = Mapping[MapKey, AbstractSet[MapEntityID]]

_EMPTY_IDS: frozenset[MapEntityID] = frozenset()


def require_empty(
    value: CampaignProgress | VanillaProgressView | LevelProgress | MapProgress,
) -> None:
    """Require empty domain progress and no opaque data that could hide history.

    Identity, fixed side structure and default protocol values are not progress.
    A stored berry-count offset is history even when the instance set is empty.
    """
    if isinstance(value, (CampaignProgress, VanillaProgressView)):
        value.require_known_levels()
        if value.total_strawberries or value.unlocked_levels or value.poem:
            raise ProgressConflictError(f'Target Campaign is not empty: {value.name!r}.')
        for level in value.levels.values():
            require_empty(level)
    elif isinstance(value, LevelProgress):
        if len(value.maps) != len(LevelSide):
            raise BadProgressSave(f'Expected three A/B/C entries for {value.sid!r}.')
        if value.cassette:
            raise ProgressConflictError(f'Target Level is not empty: {value.sid!r}.')
        for progress in value.maps:
            if progress is None:
                raise BadProgressSave(f'Missing Map progress for {value.sid!r}.')
            require_empty(progress)
    elif (
        value.time_played != Duration()
        or value.deaths != 0
        or value.completed
        or value.single_run_completed
        or value.full_clear
        or value.heart
        or value.strawberries
        or value.checkpoints
        or value.bests != PersonalBests()
        or value.berry_offset() != 0
    ):
        raise ProgressConflictError('Target Map progress is not empty.')


def campaign(save: MainSave, name: str) -> CampaignProgress | VanillaProgressView:
    return save.vanilla if name == 'Celeste' else save.mods.campaigns[name]


@dataclass
class PreparedEdit:
    save: MainSave
    progress: CampaignProgress
    time: Duration
    deaths: int
    goldenberries: int
    gems: summit_gems.PreparedGems | None = None
    session_changes: sessions.PreparedSessions | None = None

    def apply(self) -> None:
        self.apply_all(self)

    @staticmethod
    def apply_all(*edits: PreparedEdit) -> None:
        MainSaveData.install_campaigns(*((edit.save, edit.progress) for edit in edits))
        for edit in edits:
            edit.save.time_played, edit.save.deaths = edit.time, edit.deaths
            edit.save.total_goldenberries = edit.goldenberries
            if edit.gems is not None:
                edit.gems.apply()
            if edit.session_changes is not None:
                edit.session_changes.apply()


def totals(
    levels: tuple[LevelProgress, ...],
    sides: tuple[LevelSide, ...],
    goldenberry_ids: GoldenberryIDs,
) -> tuple[Duration, int, int, int]:
    time = Duration()
    deaths = berries = goldenberries = 0
    for level in levels:
        if len(level.maps) != len(LevelSide):
            raise BadProgressSave(f'Expected three A/B/C entries for {level.sid!r}.')
        for side in sides:
            progress = level.maps[side]
            if progress is None:
                raise BadProgressSave(f'Missing {side.name} progress for {level.sid!r}.')
            time += progress.time_played
            deaths += progress.deaths
            berries += len(progress.strawberries)
            goldenberries += len(
                progress.strawberries & goldenberry_ids.get((level.sid, side), _EMPTY_IDS)
            )
    return time, deaths, berries, goldenberries


def is_empty_scope(
    save: MainSave,
    name: str,
    sid: str | None,
    sides: tuple[LevelSide, ...],
    poems: set[str],
    *,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    communal: CommunalHelperSave | None = None,
    include_session: bool = True,
) -> bool:
    current = save.current_map
    if (
        include_session
        and current is not None
        and (
            (campaign_name(current[0]) == name if sid is None else current[0] == sid)
            and current[1] in sides
        )
    ):
        return False
    progress = save.vanilla if name == 'Celeste' else save.mods.campaigns.get(name)
    sids = (sid,) if sid is not None else () if progress is None else tuple(progress.levels)
    if not summit_gems.is_empty(
        save,
        summit_gems.scope_keys(
            {(sid, side) for sid in sids for side in sides},
            campaign=name if sid is None else None,
            ids=custom_summit_gem_ids,
        ),
        ids=custom_summit_gem_ids,
        communal=communal,
    ):
        return False
    if progress is None:
        return True
    if poems.intersection(progress.poem):
        return False
    try:
        if sid is None:
            require_empty(progress)
        elif (level := progress.levels.get(sid)) is not None:
            if len(sides) == len(LevelSide):
                require_empty(level)
            else:
                if len(level.maps) != len(LevelSide):
                    return False
                if LevelSide.A in sides and level.cassette:
                    return False
                selected = level.maps[sides[0]]
                if selected is None:
                    return False
                require_empty(selected)
    except ValueError:
        # Unknown or incomplete data is not proof of an empty record.
        return False
    return True


def prepare_clear(
    save: MainSave,
    name: str,
    sid: str | None,
    sides: tuple[LevelSide, ...],
    poems: set[str],
    goldenberry_ids: GoldenberryIDs,
    *,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    communal: CommunalHelperSave | None = None,
    collab: CollabUtils2Save | None = None,
    discard_collab_session: bool | Mapping[str, bool] | None = None,
    discard_current_session: bool = False,
    session_changes: sessions.PreparedSessions | None = None,
) -> PreparedEdit:
    donor = campaign(save, name)
    if sid is None:
        donor.require_known_levels()
    levels = tuple(donor.levels.values()) if sid is None else (donor.levels[sid],)
    gem_keys = summit_gems.scope_keys(
        {(level.sid, side) for level in levels for side in sides},
        campaign=name if sid is None else None,
        ids=custom_summit_gem_ids,
    )
    gems = summit_gems.prepare_clear(
        save,
        gem_keys,
        ids=custom_summit_gem_ids,
        communal=communal,
    )
    if session_changes is None:
        session_changes = sessions.prepare_clear(
            save,
            sessions.Scope(name, sid, sides),
            collab=collab,
            discard_current_session=discard_current_session,
            discard_collab_session=discard_collab_session,
        )
    time, deaths, berries, goldenberries = totals(levels, sides, goldenberry_ids)
    global_goldenberries = values_codec.parse_count(save.total_goldenberries - goldenberries)
    result = donor.snapshot() if isinstance(donor, VanillaProgressView) else copy(donor)
    result.levels = donor.levels.copy()
    result.poem = donor.poem.copy()
    for level in levels:
        reset = copy(level)
        reset.maps = level.maps.copy()
        for side in sides:
            progress = level.maps[side]
            if progress is None:
                raise BadProgressSave(f'Missing {side.name} progress for {level.sid!r}.')
            empty = deepcopy(progress)
            empty.reset()
            reset.maps[side] = empty
        if LevelSide.A in sides:
            reset.cassette = False
        kept = tuple(side for side in LevelSide if side not in sides)
        result.levels.replace(preparation.prepared_level(reset, keep_sides=kept))
    result.total_strawberries = values_codec.parse_count(donor.total_strawberries - berries)
    if sid is None:
        result.unlocked_levels = 0
        result.poem.clear()
    else:
        result.poem[:] = [value for value in result.poem if value not in poems]
    result = preparation.prepared_campaign(result)
    if sid is not None and not poems:
        result.poem = donor.poem
    global_time = save.time_played - time
    global_deaths = values_codec.parse_count(save.deaths - deaths)
    values_codec.validate_duration(global_time)
    return PreparedEdit(
        save, result, global_time, global_deaths, global_goldenberries, gems, session_changes
    )


def is_empty_map_progress(
    save: MainSave,
    sid: str,
    side: LevelSide,
    *,
    poem_id: PoemID,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    communal: CommunalHelperSave | None = None,
) -> bool:
    poems = poem.selected_ids(sid, {side: poem_id})
    return is_empty_scope(
        save,
        campaign_name(sid),
        sid,
        (side,),
        poems,
        custom_summit_gem_ids=custom_summit_gem_ids,
        communal=communal,
    )


def is_empty_level_progress(
    save: MainSave,
    sid: str,
    *,
    poem_ids: PoemIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    communal: CommunalHelperSave | None = None,
) -> bool:
    poems = poem.selected_ids(sid, poem.as_mapping(poem_ids))
    return is_empty_scope(
        save,
        campaign_name(sid),
        sid,
        tuple(LevelSide),
        poems,
        custom_summit_gem_ids=custom_summit_gem_ids,
        communal=communal,
    )


def is_empty_campaign_progress(
    save: MainSave,
    name: str,
    *,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    communal: CommunalHelperSave | None = None,
) -> bool:
    return is_empty_scope(
        save,
        name,
        None,
        tuple(LevelSide),
        set(),
        custom_summit_gem_ids=custom_summit_gem_ids,
        communal=communal,
    )


def clear_map_progress(
    save: MainSave,
    sid: str,
    side: LevelSide,
    *,
    poem_id: PoemID,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    communal: CommunalHelperSave | None = None,
    collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
) -> None:
    poems = poem.selected_ids(sid, {side: poem_id})
    prepare_clear(
        save,
        campaign_name(sid),
        sid,
        (side,),
        poems,
        goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        communal=communal,
        collab=collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
    ).apply()


def clear_level_progress(
    save: MainSave,
    sid: str,
    *,
    poem_ids: PoemIDs,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    communal: CommunalHelperSave | None = None,
    collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
) -> None:
    poems = poem.selected_ids(sid, poem.as_mapping(poem_ids))
    prepare_clear(
        save,
        campaign_name(sid),
        sid,
        tuple(LevelSide),
        poems,
        goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        communal=communal,
        collab=collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
    ).apply()


def clear_campaign_progress(
    save: MainSave,
    name: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    communal: CommunalHelperSave | None = None,
    collab: CollabUtils2Save | None = None,
    discard_collab_session: Mapping[str, bool] | None = None,
    discard_current_session: bool = False,
) -> None:
    prepare_clear(
        save,
        name,
        None,
        tuple(LevelSide),
        set(),
        goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        communal=communal,
        collab=collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
    ).apply()


def restore_mod_progress(main: MainSave, backup: ModBackupSave) -> None:
    """Apply Everest's recovery condition to main, leaving the explicit backup intact.

    This replaces the whole Mod progress, not individual missing maps. It
    does not model other game initialization or installed-map reconciliation.
    """
    if main.needs_mod_recovery:
        main.install_mod_progress(deepcopy(backup).mods)


def is_in_progress(
    main: MainSave,
    sid: str,
    side: LevelSide,
    *,
    collab: CollabUtils2Save | None = None,
) -> bool:
    """Combine current and optional retained identity; invalid requested data raises."""
    if main.current_map == (sid, side):
        return True
    pending = None if collab is None else collab.sessions.get(sid)
    if pending is None:
        return False
    session = pending.session
    return session.in_level and session.side is side


def correct_map_progress(
    save: MainSave,
    sid: str,
    side: LevelSide,
    *,
    time_played: Duration | None = None,
    deaths: int | None = None,
) -> tuple[Duration, int]:
    """Correct one Map and affected global counters after an atomic preflight.

    No backup, session, best or room statistic is inferred or modified. Omitted
    counters do not even require the corresponding old Map/global field.
    """
    maps = save.flat_levels[sid].maps
    progress = maps[side] if side < len(maps) else None
    if progress is None:
        raise BadProgressSave(f'Missing {side.name} progress for {sid!r}.')
    if time_played is None and deaths is None:
        return Duration(), 0
    bests = progress.bests
    if time_played is not None and any(
        value > Duration() and value > time_played for value in (bests.time, bests.full_clear_time)
    ):
        raise ProgressConflictError('Map time cannot be less than an existing personal best.')
    if deaths is not None and progress.single_run_completed and deaths < bests.deaths:
        raise ProgressConflictError('Map deaths cannot be less than the existing personal best.')
    session = save.session
    if session is not None and (session.sid, session.side) == (sid, side):
        session.validate_map_totals(time_played=time_played, deaths=deaths)
    time_delta = Duration()
    death_delta = 0
    global_time = None
    global_deaths = None
    if time_played is not None:
        values_codec.validate_duration(time_played)
        time_delta = time_played - progress.time_played
        if time_delta:
            global_time = save.time_played + time_delta
            values_codec.validate_duration(global_time)
    if deaths is not None:
        deaths = values_codec.parse_count(deaths)
        death_delta = deaths - progress.deaths
        if death_delta:
            global_deaths = values_codec.parse_count(save.deaths + death_delta)
    if time_delta:
        assert time_played is not None and global_time is not None
        progress.time_played = time_played
        save.time_played = global_time
    if death_delta:
        assert deaths is not None and global_deaths is not None
        progress.deaths = deaths
        save.deaths = global_deaths
    return time_delta, death_delta
