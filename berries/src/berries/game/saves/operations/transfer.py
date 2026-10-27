"""Preflighted in-memory copying, moving and merging of save progress."""

from collections.abc import Iterable, Mapping
from copy import copy, deepcopy
from typing import Literal

from berries.game.duration import Duration
from berries.game.levels import LevelSide, MapKey
from berries.game.saves.codecs import values as values_codec
from berries.game.saves.errors import (
    BadProgressSave,
    ProgressConflictError,
    UnsupportedProgressError,
)
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.communal_helper import CommunalHelperSave
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress, campaign_name
from berries.game.saves.progress.map import MapProgress, PersonalBests
from berries.game.saves.progress.members import LevelMembers
from berries.game.saves.progress.views import VanillaProgressView
from berries.game.saves.rooms import room_key
from berries.game.vanilla import VANILLA_SIDS

from . import blender as blender_transfer
from . import edit, poem, preparation, sessions, summit_gems
from .edit import GoldenberryIDs
from .poem import PoemID, PoemIDs
from .summit_gems import CustomSummitGemIDs

type TransferMode = Literal['overwrite', 'merge', 'exclusive']


def _better_time(a: Duration, b: Duration) -> Duration:
    if a <= Duration():
        return b
    return a if b <= Duration() else min(a, b)


def _better_count(a: int, b: int, source: MapProgress, target: MapProgress) -> int:
    if not source.single_run_completed:
        return b
    return a if not target.single_run_completed else min(a, b)


def _merged_map(source: MapProgress, target: MapProgress) -> MapProgress:
    # Preserve target historical counters, but construct the merged
    # collections and records rather than cloning values that will be replaced.
    result = copy(target)
    result.time_played += source.time_played
    result.deaths += source.deaths
    result.completed |= source.completed
    result.single_run_completed |= source.single_run_completed
    result.full_clear |= source.full_clear
    result.heart |= source.heart
    result.strawberries = target.strawberries | source.strawberries
    result.checkpoints = target.checkpoints | source.checkpoints
    a, b = source.bests, target.bests
    result.bests = PersonalBests(
        _better_time(a.time, b.time),
        _better_time(a.full_clear_time, b.full_clear_time),
        _better_count(a.deaths, b.deaths, source, target),
        _better_count(a.dashes, b.dashes, source, target),
    )
    return result


def _state_ids(
    campaign: CampaignProgress | VanillaProgressView,
    levels: Iterable[LevelProgress],
    sides: tuple[LevelSide, ...],
    *,
    poem: bool,
) -> set[int]:
    result = {id(campaign), id(campaign.levels)}
    if poem:
        result.add(id(campaign.poem))
    for level in levels:
        if len(level.maps) != len(LevelSide):
            raise BadProgressSave(f'Expected three A/B/C entries for {level.sid!r}.')
        result.add(id(level))
        for side in sides:
            progress = level.maps[side]
            if progress is None:
                raise BadProgressSave(f'Missing {side.name} progress for {level.sid!r}.')
            result.update(
                map(
                    id,
                    (
                        progress,
                        progress.bests,
                        progress.strawberries,
                        progress.checkpoints,
                    ),
                )
            )
    return result


def _keys(levels: Iterable[LevelProgress], sides: Iterable[LevelSide]) -> set[MapKey]:
    return {(level.sid, side) for level in levels for side in sides}


def _poems(
    source: CampaignProgress | VanillaProgressView,
    target: CampaignProgress,
    ids: set[str],
    mode: TransferMode,
) -> None:
    for value in source.poem:
        if value in ids and value not in target.poem:
            target.poem.append(value)
    if mode == 'overwrite':
        target.poem[:] = [
            value for value in target.poem if value not in ids or value in source.poem
        ]


def transfer(
    source: MainSave,
    target: MainSave,
    name: str,
    sid: str | None,
    sides: tuple[LevelSide, ...],
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool | Mapping[str, bool] | None = None,
    discard_current_session: bool = False,
    mode: TransferMode,
    remove_source: bool,
    poem_ids: Mapping[LevelSide, PoemID],
    source_blender: EndersBlenderSave | None,
    target_blender: EndersBlenderSave | None,
    unlock_requirements: Mapping[str, int] | None,
) -> None:
    if mode not in ('overwrite', 'merge', 'exclusive'):
        raise ValueError(f'Unknown progress transfer mode: {mode!r}.')
    replace = mode != 'merge'
    if source is target:
        raise ProgressConflictError('Source and target saves must be independent.')
    if (source_blender is None) != (target_blender is None):
        raise ValueError('Both Blender files must be supplied together.')
    if source_blender is not None and source_blender is target_blender:
        raise ProgressConflictError('Source and target Blender files must be independent.')
    selected_poems = set() if sid is None else poem.selected_ids(sid, poem_ids)
    donor = edit.campaign(source, name)
    previous = target.vanilla if name == 'Celeste' else target.mods.campaigns.get(name)
    if sid is None:
        donor.require_known_levels()
        if previous is not None and replace:
            previous.require_known_levels()
    if sid is not None and previous is None:
        raise KeyError(name)
    levels = tuple(donor.levels.values()) if sid is None else (donor.levels[sid],)
    if len(sides) == 1 and mode != 'exclusive':
        assert sid is not None
        if previous is None or previous.levels.get(sid) is None:
            raise KeyError(sid)
    replaced = (
        ()
        if previous is None
        else tuple(previous.levels.values())
        if sid is None and replace
        else tuple(old for level in levels if (old := previous.levels.get(level.sid)) is not None)
    )
    if name != 'Celeste' and (
        source.mods is target.mods or source.mods.campaigns is target.mods.campaigns
    ):
        raise ProgressConflictError('Source and target share the modified campaign collection.')
    if previous is not None and (
        _state_ids(donor, levels, sides, poem=sid is None or bool(selected_poems))
        & _state_ids(previous, replaced, sides, poem=sid is None or bool(selected_poems))
    ):
        raise ProgressConflictError('Source and target progress overlap.')
    if mode == 'exclusive' and not edit.is_empty_scope(
        target, name, sid, sides, selected_poems, include_session=False
    ):
        raise ProgressConflictError('Selected target progress is not empty.')
    source_keys = _keys(levels, sides)
    target_keys = source_keys | _keys(replaced, sides)
    source_gem_keys = summit_gems.scope_keys(
        source_keys,
        campaign=name if sid is None else None,
        ids=custom_summit_gem_ids,
    )
    target_gem_keys = summit_gems.scope_keys(
        target_keys,
        campaign=name if sid is None else None,
        ids=custom_summit_gem_ids,
    )
    session_changes = sessions.prepare_transfer(
        source,
        target,
        sessions.Scope(name, sid, sides),
        source_collab=source_collab,
        target_collab=target_collab,
        remove_source=remove_source,
        discard_current_session=discard_current_session,
        discard_collab_session=discard_collab_session,
    )
    gems = summit_gems.prepare_target(
        source,
        target,
        source_gem_keys,
        target_gem_keys,
        ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        overwrite=replace,
        exclusive=mode == 'exclusive',
    )
    clearing = (
        edit.prepare_clear(
            source,
            name,
            sid,
            sides,
            selected_poems,
            goldenberry_ids,
            custom_summit_gem_ids=custom_summit_gem_ids,
            communal=source_communal,
            session_changes=session_changes,
        )
        if remove_source
        else None
    )
    if sid is None and (previous is None or replace):
        merged = donor.snapshot() if isinstance(donor, VanillaProgressView) else copy(donor)
        merged.levels = LevelMembers(name)
        merged.poem = list(donor.poem)
    elif previous is None:
        merged = CampaignProgress(name)
    else:
        merged = (
            previous.snapshot() if isinstance(previous, VanillaProgressView) else copy(previous)
        )
        merged.levels = previous.levels.copy()
        merged.poem = list(previous.poem)
    time, deaths, _, _ = edit.totals(levels, sides, goldenberry_ids)
    old_time, old_deaths, old_berries, old_goldenberries = (
        edit.totals(replaced, sides, goldenberry_ids) if replace else (Duration(), 0, 0, 0)
    )
    added = golden_added = 0
    for level in levels:
        before = None if previous is None else previous.levels.get(level.sid)
        if len(sides) == len(LevelSide) and (replace or before is None):
            result = copy(level)
            result.maps = level.maps.copy()
        elif before is not None:
            result = copy(before)
            result.maps = before.maps.copy()
        else:
            result = LevelProgress(level.sid)
        origins: dict[LevelSide, MapProgress | None] = {}
        for side in sides:
            progress = level.maps[side]
            existing = None if before is None else before.maps[side]
            if progress is None:
                raise BadProgressSave(f'Missing {side.name} progress for {level.sid!r}.')
            if before is not None and existing is None:
                raise BadProgressSave(f'Missing {side.name} progress for {before.sid!r}.')
            new_berries = (
                progress.strawberries
                if replace or existing is None
                else progress.strawberries - existing.strawberries
            )
            added += len(new_berries)
            golden_added += len(new_berries & goldenberry_ids.get((level.sid, side), frozenset()))
            if existing is not None and mode == 'merge':
                result.maps[side] = _merged_map(progress, existing)
            else:
                result.maps[side] = deepcopy(progress)
                origins[side] = existing
        if LevelSide.A in sides:
            # Cassette is stored on Level, but belongs to A-side progress.
            result.cassette = (
                level.cassette if replace or before is None else before.cassette or level.cassette
            )
        kept = tuple(side for side in LevelSide if side not in sides)
        result = preparation.prepared_level(result, counter_origins=origins, keep_sides=kept)
        if merged.levels.get(level.sid) is not None:
            merged.levels.replace(result)
        else:
            merged.levels.add(result)
    merged.total_strawberries = values_codec.parse_count(
        (0 if previous is None else previous.total_strawberries) + added - old_berries
    )
    if sid is None:
        merged.unlocked_levels = (
            donor.unlocked_levels
            if replace or previous is None
            else max(previous.unlocked_levels, donor.unlocked_levels)
        )
        if mode == 'merge' and previous is not None:
            merged.poem.extend(value for value in donor.poem if value not in merged.poem)
    else:
        _poems(donor, merged, selected_poems, 'overwrite' if replace else 'merge')
    for level in levels:
        if not any(
            progress is not None and progress.completed
            for side in sides
            for progress in (level.maps[side],)
        ):
            continue
        if name == 'Celeste' and level.sid in VANILLA_SIDS:
            required = min(VANILLA_SIDS.index(level.sid) + 1, len(VANILLA_SIDS) - 1)
        elif unlock_requirements is not None and level.sid in unlock_requirements:
            required = values_codec.parse_count(unlock_requirements[level.sid])
        else:
            raise UnsupportedProgressError(
                f'Missing completion unlock requirement for {level.sid!r}.'
            )
        merged.unlocked_levels = max(merged.unlocked_levels, required)
    merged = preparation.prepared_campaign(merged)
    if sid is not None and not selected_poems and previous is not None:
        merged.poem = previous.poem
    target_time = target.time_played + time - old_time
    target_deaths = values_codec.parse_count(target.deaths + deaths - old_deaths)
    target_goldenberries = values_codec.parse_count(
        target.total_goldenberries + golden_added - old_goldenberries
    )
    values_codec.validate_duration(target_time)
    blender = None
    if source_blender is not None:
        assert target_blender is not None
        protocol_keys = {room_key(*key) for key in source_keys}
        target_protocol_keys = {room_key(*key) for key in target_keys}
        _check_protocol_keys(source, target, protocol_keys | target_protocol_keys)
        blender = blender_transfer.prepare(
            source_blender,
            target_blender,
            protocol_keys,
            target_keys=target_protocol_keys,
            overwrite=mode == 'overwrite',
        )
    # Only independent prepared values are installed after every preflight passed.
    # These are collection replacements: references held before the move stay
    # usable but detached; existing flat views resolve the newly installed state.
    receiving = edit.PreparedEdit(target, merged, target_time, target_deaths, target_goldenberries)
    edit.PreparedEdit.apply_all(*(() if clearing is None else (clearing,)), receiving)
    gems.apply()
    if clearing is None:
        session_changes.apply()
    if blender is not None:
        assert source_blender is not None and target_blender is not None
        blender_transfer.apply(source_blender, target_blender, blender, remove_source=remove_source)


def _check_protocol_keys(source: MainSave, target: MainSave, keys: set[str]) -> None:
    identities: dict[str, MapKey] = {}
    for save in (source, target):
        for campaign in (
            save.vanilla,
            *save.mods.campaigns.values(),
            *save.mods.recycled_campaigns.values(),
        ):
            for level in campaign.levels.values():
                for side in LevelSide:
                    key = room_key(level.sid, side)
                    if key not in keys:
                        continue
                    identity = level.sid, side
                    if key in identities and identities[key] != identity:
                        raise ProgressConflictError(f'Ambiguous Blender protocol key: {key!r}.')
                    identities[key] = identity


def copy_map_progress(
    source: MainSave,
    target: MainSave,
    sid: str,
    side: LevelSide,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
    poem_id: PoemID,
    mode: TransferMode,
    source_blender: EndersBlenderSave | None = None,
    target_blender: EndersBlenderSave | None = None,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Copy selected map progress without changing source progress or totals."""
    if not isinstance(side, LevelSide):
        raise TypeError('Expected a LevelSide.')
    transfer(
        source,
        target,
        campaign_name(sid),
        sid,
        (side,),
        mode=mode,
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        remove_source=False,
        poem_ids={side: poem_id},
        source_blender=source_blender,
        target_blender=target_blender,
        unlock_requirements=unlock_requirements,
    )


def merge_map_progress(
    source: MainSave,
    target: MainSave,
    sid: str,
    side: LevelSide,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
    poem_id: PoemID,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Accumulate map progress and target totals; leave the source unchanged."""
    copy_map_progress(
        source,
        target,
        sid,
        side,
        mode='merge',
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        unlock_requirements=unlock_requirements,
        poem_id=poem_id,
    )


def overwrite_map_progress(
    source: MainSave,
    target: MainSave,
    sid: str,
    side: LevelSide,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
    poem_id: PoemID,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Replace map progress and target totals; leave the source unchanged."""
    copy_map_progress(
        source,
        target,
        sid,
        side,
        mode='overwrite',
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        unlock_requirements=unlock_requirements,
        poem_id=poem_id,
    )


def copy_level_progress(
    source: MainSave,
    target: MainSave,
    sid: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
    poem_ids: PoemIDs,
    mode: TransferMode,
    source_blender: EndersBlenderSave | None = None,
    target_blender: EndersBlenderSave | None = None,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Copy selected level progress without changing source progress or totals."""
    transfer(
        source,
        target,
        campaign_name(sid),
        sid,
        tuple(LevelSide),
        mode=mode,
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        remove_source=False,
        poem_ids=poem.as_mapping(poem_ids),
        source_blender=source_blender,
        target_blender=target_blender,
        unlock_requirements=unlock_requirements,
    )


def merge_level_progress(
    source: MainSave,
    target: MainSave,
    sid: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
    poem_ids: PoemIDs,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Accumulate level progress and target totals; leave the source unchanged."""
    copy_level_progress(
        source,
        target,
        sid,
        mode='merge',
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        unlock_requirements=unlock_requirements,
        poem_ids=poem_ids,
    )


def overwrite_level_progress(
    source: MainSave,
    target: MainSave,
    sid: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
    poem_ids: PoemIDs,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Replace level progress and target totals; leave the source unchanged."""
    copy_level_progress(
        source,
        target,
        sid,
        mode='overwrite',
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        unlock_requirements=unlock_requirements,
        poem_ids=poem_ids,
    )


def copy_campaign_progress(
    source: MainSave,
    target: MainSave,
    name: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: Mapping[str, bool] | None = None,
    discard_current_session: bool = False,
    mode: TransferMode,
    source_blender: EndersBlenderSave | None = None,
    target_blender: EndersBlenderSave | None = None,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Copy selected campaign progress without changing source progress or totals."""
    transfer(
        source,
        target,
        name,
        None,
        tuple(LevelSide),
        mode=mode,
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        remove_source=False,
        poem_ids={},
        source_blender=source_blender,
        target_blender=target_blender,
        unlock_requirements=unlock_requirements,
    )


def merge_campaign_progress(
    source: MainSave,
    target: MainSave,
    name: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: Mapping[str, bool] | None = None,
    discard_current_session: bool = False,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Accumulate campaign progress and target totals; leave the source unchanged."""
    copy_campaign_progress(
        source,
        target,
        name,
        mode='merge',
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        unlock_requirements=unlock_requirements,
    )


def overwrite_campaign_progress(
    source: MainSave,
    target: MainSave,
    name: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: Mapping[str, bool] | None = None,
    discard_current_session: bool = False,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Replace campaign progress and target totals; leave the source unchanged."""
    copy_campaign_progress(
        source,
        target,
        name,
        mode='overwrite',
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        unlock_requirements=unlock_requirements,
    )


def move_map_progress(
    source: MainSave,
    target: MainSave,
    sid: str,
    side: LevelSide,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
    poem_id: PoemID,
    mode: TransferMode,
    source_blender: EndersBlenderSave | None = None,
    target_blender: EndersBlenderSave | None = None,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Move one side including A cassette; exclusive checks only the selected progress."""
    if not isinstance(side, LevelSide):
        raise TypeError('Expected a LevelSide.')
    transfer(
        source,
        target,
        campaign_name(sid),
        sid,
        (side,),
        mode=mode,
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        remove_source=True,
        poem_ids={side: poem_id},
        source_blender=source_blender,
        target_blender=target_blender,
        unlock_requirements=unlock_requirements,
    )


def move_level_progress(
    source: MainSave,
    target: MainSave,
    sid: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
    poem_ids: Mapping[LevelSide, PoemID] | tuple[PoemID, PoemID, PoemID],
    mode: TransferMode,
    source_blender: EndersBlenderSave | None = None,
    target_blender: EndersBlenderSave | None = None,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Move all fixed sides; tuples specify A/B/C, missing mapping keys mean None."""
    selected = poem.as_mapping(poem_ids)
    transfer(
        source,
        target,
        campaign_name(sid),
        sid,
        tuple(LevelSide),
        mode=mode,
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        remove_source=True,
        poem_ids=selected,
        source_blender=source_blender,
        target_blender=target_blender,
        unlock_requirements=unlock_requirements,
    )


def move_campaign_progress(
    source: MainSave,
    target: MainSave,
    name: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: Mapping[str, bool] | None = None,
    discard_current_session: bool = False,
    mode: TransferMode,
    source_blender: EndersBlenderSave | None = None,
    target_blender: EndersBlenderSave | None = None,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None:
    """Move an active Campaign, including its complete poem list."""
    transfer(
        source,
        target,
        name,
        None,
        tuple(LevelSide),
        mode=mode,
        goldenberry_ids=goldenberry_ids,
        custom_summit_gem_ids=custom_summit_gem_ids,
        source_communal=source_communal,
        target_communal=target_communal,
        source_collab=source_collab,
        target_collab=target_collab,
        discard_collab_session=discard_collab_session,
        discard_current_session=discard_current_session,
        remove_source=True,
        poem_ids={},
        source_blender=source_blender,
        target_blender=target_blender,
        unlock_requirements=unlock_requirements,
    )
