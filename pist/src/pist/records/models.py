"""Build and merge locally maintained first-playthrough map records."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final

from pydantic import Field, HttpUrl, NonNegativeInt, PositiveInt, field_validator

from berries.game import dialog, levels, mods, saves
from berries.gamebanana import GameBananaCredit, GameBananaSubmission
from berries.models import FrozenModel
from pist.entity_stats import MapEntityStats
from pist.types import CellValue

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
        all_collected = (
            entity_stats.all_instances_collected('strawberry', stats.collected_strawberries)
            and entity_stats.all_instances_collected('moonberry', stats.collected_strawberries)
            and (not entity_stats.has_stat('cassette') or stats.cassette_collected)
            and (not entity_stats.has_stat('heart') or stats.heart_collected)
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
    local_id: PositiveInt | None = None
    record_number: PositiveInt | None = None
    mod_metadata_name: str | None = None
    mod_name: str | None = None
    mod_url: str | None = None
    mod_updated_at: date | None = None
    credits: tuple[GameBananaCredit, ...] = ()
    map_name: str
    video_url: str | None = None
    map_english_name: str | None = None
    map_file: str | None = None
    sid: str | None = None
    side: levels.LevelSide | None = None
    authors: tuple[str, ...] = ()
    difficulty: str | None = None
    save_slot: NonNegativeInt | None = None
    time_played: str | None = None
    deaths: NonNegativeInt | None = None
    completed: bool | None = None
    n_strawberries: NonNegativeInt | None = None
    n_moonberries: NonNegativeInt | None = None
    cassette: bool = False
    heart: str | None = None
    n_main_rooms: PositiveInt | None = None
    status: str | None = None
    tags: tuple[str, ...] = ()
    perceived_difficulty: str | None = None
    perceived_difficulty_tier: str | None = None
    rated_difficulty: str | None = None
    rated_difficulty_tier: str | None = None
    started_at: date | None = None
    finished_at: date | None = None
    save_load_usage: str | None = None
    rating: int | None = Field(default=None, ge=0, le=10)
    notes: str | None = None

    @field_validator('video_url', 'mod_url')
    @classmethod
    def validate_http_url(cls, value: str | None) -> str | None:
        """Accept an optional HTTP(S) link without rewriting its spelling."""
        if value is not None:
            HttpUrl(value)
        return value


def create_map_record(
    level: levels.Level,
    side: levels.LevelSide,
    *,
    mod: mods.InstalledMod | None,
    save_slot: saves.SaveSlot,
    gamebanana: GameBananaSubmission | None = None,
    authors: tuple[str, ...] = (),
    languages: tuple[str, ...] = dialog.DIALOG_LANGUAGES,
    dialogs: Mapping[str, Mapping[str, str]] | None = None,
    statistics: Mapping[str, CellValue | date] | None = None,
    now: datetime | None = None,
) -> MapRecord:
    """Build a local record from one selected Mod map."""
    if mod is None:
        raise TypeError('Cannot create a local record for a vanilla map.')
    map_info = level[side].map_info
    stats = save_slot.get_map_stats(level, side)
    recorded_stats = stats if stats is not None and stats.is_recorded else None
    current_dialogs = {} if dialogs is None else dialogs
    names = level.localized_names(side, current_dialogs)
    fallback_name = level.fallback_name(side)
    map_name = dialog.localized_name(names, languages) or fallback_name
    english_name = names.get('en') or fallback_name
    record = MapRecord(
        created_at=now or datetime.now(tz=UTC),
        mod_metadata_name=mod.metadata_name,
        mod_name=gamebanana.name if gamebanana is not None else None,
        mod_url=gamebanana.page_url if gamebanana is not None else None,
        mod_updated_at=gamebanana.latest_update_added_time.astimezone().date()
        if gamebanana is not None
        else None,
        credits=gamebanana.credits if gamebanana is not None else (),
        map_name=map_name,
        map_english_name=english_name if english_name != map_name else None,
        map_file=map_info.file_path.as_posix(),
        sid=level.sid,
        side=side,
        authors=authors,
        save_slot=save_slot.number,
        time_played=(
            recorded_stats.time_played.ingame_format('seconds')
            if recorded_stats is not None
            else None
        ),
        deaths=recorded_stats.deaths if recorded_stats is not None else None,
        completed=recorded_stats.completed if recorded_stats is not None else None,
    )
    data = record.model_dump()
    if statistics is not None:
        data.update(statistics)
    return MapRecord.model_validate(data)


def merge_saved_record(current: MapRecord, saved: MapRecord) -> MapRecord:
    """Reuse human-entered values while retaining freshly read map and save data."""
    if current.mod_metadata_name != saved.mod_metadata_name or (
        current.map_file != saved.map_file
        if current.map_file is not None and saved.map_file is not None
        else current.map_name != saved.map_name
    ):
        raise ValueError('Cannot merge records for different Mods or maps.')

    return current.model_copy(
        update={
            'local_id': saved.local_id,
            'record_number': saved.record_number,
            'created_at': saved.created_at,
            'map_name': saved.map_name,
            'mod_name': saved.mod_name,
            'mod_url': saved.mod_url,
            'video_url': saved.video_url,
            'authors': saved.authors,
            'difficulty': saved.difficulty,
            'mod_updated_at': saved.mod_updated_at,
            **{
                name: getattr(saved, name)
                for name in (
                    'status',
                    'tags',
                    'perceived_difficulty',
                    'perceived_difficulty_tier',
                    'rated_difficulty',
                    'rated_difficulty_tier',
                    'started_at',
                    'finished_at',
                    'save_load_usage',
                    'rating',
                    'notes',
                )
            },
        }
    )
