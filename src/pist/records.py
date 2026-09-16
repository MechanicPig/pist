"""Build and merge locally maintained first-playthrough map records."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pist.game.dialog import DIALOG_LANGUAGES, localized_name
from pist.game.mods import InstalledMod, LocalMap
from pist.game.saves import SaveSlot, sid_for_map_file
from pist.gamebanana import GameBananaCredit, GameBananaSubmission
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


class MapRecord(BaseModel):
    """One locally maintained map record, optionally synchronized to Smart Sheet."""

    model_config = ConfigDict(extra='forbid', frozen=True)

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
    save_slot: int
    time_played: str | None = None
    deaths: int | None = None
    completed: bool | None = None
    record_values: RecordValues = Field(default_factory=dict)


def create_map_record(
    mod: InstalledMod,
    map_info: LocalMap,
    *,
    save_slot: SaveSlot,
    gamebanana: GameBananaSubmission | None = None,
    authors: tuple[str, ...] = (),
    languages: tuple[str, ...] = DIALOG_LANGUAGES,
    record_values: Mapping[str, Mapping[str, CellValue]] | None = None,
    now: datetime | None = None,
) -> MapRecord:
    """Build a local record from one selected Mod map."""
    stats = save_slot.get_map_stats(map_info)
    recorded_stats = stats if stats is not None and stats.is_recorded else None
    map_name = localized_name(map_info.names, languages) or map_info.fallback_name
    english_name = map_info.names.get('en') or map_info.fallback_name
    return MapRecord(
        created_at=now or datetime.now(tz=UTC),
        mod_metadata_name=mod.metadata_name,
        mod_name=gamebanana.name if gamebanana is not None else None,
        mod_url=gamebanana.page_url if gamebanana is not None else None,
        mod_updated_at=gamebanana.latest_update_added_time if gamebanana is not None else None,
        credits=gamebanana.credits if gamebanana is not None else (),
        map_name=map_name,
        map_english_name=english_name if english_name != map_name else None,
        map_file=map_info.file_path,
        sid=sid_for_map_file(map_info.file_path),
        side=map_info.side or 'A',
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
