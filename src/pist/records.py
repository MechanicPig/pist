"""Build and merge locally maintained first-playthrough map records."""

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import Field, NonNegativeInt

from pist.entities.classification import MapEntityStats
from pist.game import dialog, levels, saves
from pist.gamebanana import GameBananaCredit, GameBananaSubmission
from pist.models import FrozenModel
from pist.types import CellValue, RecordValues

MANUAL_RECORD_FIELD_TITLES = frozenset(
    {
        '体感难度',
        '难度子阶',
        '标注难度',
        '标注难度子阶',
        '起始日期',
        '结束日期',
        '状态',
        'SL使用',
        '评分',
        '备注',
    }
)


class MapRecordProgress(StrEnum):
    """The player-facing completion state inferred for one record draft."""

    COMPLETED_ALL_COLLECTIBLES = 'completed_all_collectibles'
    COMPLETED_MISSING_COLLECTIBLES = 'completed_missing_collectibles'
    PARTIALLY_COMPLETED = 'partially_completed'
    IN_PROGRESS = 'in_progress'
    RAN_BEFORE = 'ran_before'
    NOT_STARTED = 'not_started'

    @property
    def label(self) -> str:
        """Return the concise Chinese label used by the record editor."""
        return MAP_RECORD_PROGRESS_LABELS[self]


MAP_RECORD_PROGRESS_LABELS: Final[Mapping[MapRecordProgress, str]] = {
    MapRecordProgress.COMPLETED_ALL_COLLECTIBLES: '通关（全收集或无收集品）',
    MapRecordProgress.COMPLETED_MISSING_COLLECTIBLES: '通关（未全收集）',
    MapRecordProgress.PARTIALLY_COMPLETED: '部分通关',
    MapRecordProgress.IN_PROGRESS: '进行中',
    MapRecordProgress.RAN_BEFORE: '已跑比',
    MapRecordProgress.NOT_STARTED: '未开始',
}


def map_record_progress(
    stats: saves.MapStats | None,
    entity_stats: MapEntityStats,
    *,
    is_in_progress: bool,
) -> MapRecordProgress:
    """Infer a draft's completion state from native saves and configured entities."""
    if stats is None or not stats.is_recorded:
        return MapRecordProgress.NOT_STARTED
    if stats.single_run_completed:
        strawberries = entity_stats.count('strawberry') + entity_stats.count('moonberry')
        all_collected = (
            len(stats.collected_strawberries) >= strawberries
            and (not entity_stats.has_stat_kind('cassette') or stats.cassette_collected)
            and (not entity_stats.has_stat_kind('heart') or stats.heart_collected)
        )
        return (
            MapRecordProgress.COMPLETED_ALL_COLLECTIBLES
            if all_collected
            else MapRecordProgress.COMPLETED_MISSING_COLLECTIBLES
        )
    if stats.completed:
        return MapRecordProgress.PARTIALLY_COMPLETED
    return MapRecordProgress.IN_PROGRESS if is_in_progress else MapRecordProgress.RAN_BEFORE


class MapRecord(FrozenModel):
    """One locally maintained map record, optionally synchronized to Smart Sheet."""

    created_at: datetime
    mod_metadata_name: str
    mod_name: str | None = None
    mod_url: str | None = None
    mod_updated_at: datetime | None = None
    credits: tuple[GameBananaCredit, ...] = ()
    map_name: str
    map_english_name: str | None = None
    map_file: str
    sid: str
    side: Literal['A', 'B', 'C']
    authors: tuple[str, ...] = ()
    difficulty: str | None = None
    save_slot: NonNegativeInt
    time_played: str | None = None
    deaths: NonNegativeInt | None = None
    completed: bool | None = None
    record_values: RecordValues = Field(default_factory=dict)


def create_map_record(
    level: levels.Level,
    side: levels.LevelSide,
    *,
    save_slot: saves.SaveSlot,
    gamebanana: GameBananaSubmission | None = None,
    authors: tuple[str, ...] = (),
    languages: tuple[str, ...] = dialog.DIALOG_LANGUAGES,
    dialogs: Mapping[str, Mapping[str, str]] | None = None,
    record_values: Mapping[str, Mapping[str, CellValue]] | None = None,
    now: datetime | None = None,
) -> MapRecord:
    """Build a local record from one selected Mod map."""
    loaded_map = level.maps_by_side[side]
    if not isinstance(loaded_map, levels.LoadedModMap):
        raise TypeError('Cannot create a local record for a vanilla map.')
    mod = loaded_map.mod
    map_info = loaded_map.map_info
    stats = save_slot.get_map_stats(level, side)
    recorded_stats = stats if stats is not None and stats.is_recorded else None
    current_dialogs = {} if dialogs is None else dialogs
    names = levels.map_names(level, side, current_dialogs)
    fallback_name = levels.map_fallback_name(level, side)
    map_name = dialog.localized_name(names, languages) or fallback_name
    english_name = names.get('en') or fallback_name
    return MapRecord(
        created_at=now or datetime.now(tz=UTC),
        mod_metadata_name=mod.metadata_name,
        mod_name=gamebanana.name if gamebanana is not None else None,
        mod_url=gamebanana.page_url if gamebanana is not None else None,
        mod_updated_at=gamebanana.latest_update_added_time if gamebanana is not None else None,
        credits=gamebanana.credits if gamebanana is not None else (),
        map_name=map_name,
        map_english_name=english_name if english_name != map_name else None,
        map_file=map_info.file_path.as_posix(),
        sid=saves.sid_for_level(level),
        side=side.value,
        authors=authors,
        save_slot=save_slot.number,
        time_played=(
            recorded_stats.time_played.ingame_format('seconds')
            if recorded_stats is not None
            else None
        ),
        deaths=recorded_stats.deaths if recorded_stats is not None else None,
        completed=recorded_stats.completed if recorded_stats is not None else None,
        record_values={table: dict(values) for table, values in record_values.items()}
        if record_values is not None
        else {},
    )


def merge_saved_record(current: MapRecord, saved: MapRecord) -> MapRecord:
    """Reuse human-entered values while retaining freshly read map and save data."""
    if (
        current.mod_metadata_name,
        current.map_file,
        current.save_slot,
    ) != (
        saved.mod_metadata_name,
        saved.map_file,
        saved.save_slot,
    ):
        raise ValueError('Cannot merge records for different Mods, maps, or save slots.')

    record_values = {table: dict(values) for table, values in current.record_values.items()}
    for table, values in saved.record_values.items():
        retained = {
            title: value for title, value in values.items() if title in MANUAL_RECORD_FIELD_TITLES
        }
        if retained:
            record_values.setdefault(table, {}).update(retained)

    return current.model_copy(
        update={
            'authors': saved.authors,
            'difficulty': saved.difficulty,
            'mod_updated_at': saved.mod_updated_at or current.mod_updated_at,
            'record_values': record_values,
        }
    )
