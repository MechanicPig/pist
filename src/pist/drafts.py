"""Create and persist local first-playthrough record drafts."""

from collections.abc import Mapping
from datetime import UTC, datetime

from pist.game.saves import SaveSlot, sid_for_map_file
from pist.models import (
    DIALOG_LANGUAGES,
    GameBananaSubmission,
    InstalledMod,
    LocalMap,
    RecordDraft,
    localized_name,
)
from pist.sheet_report import MANUAL_DRAFT_FIELD_TITLES


def create_record_draft(
    mod: InstalledMod,
    map_info: LocalMap,
    *,
    save_slot: SaveSlot | None,
    gamebanana: GameBananaSubmission | None = None,
    authors: tuple[str, ...] = (),
    languages: tuple[str, ...] = DIALOG_LANGUAGES,
    table_values: Mapping[str, Mapping[str, int | bool | str]] | None = None,
    now: datetime | None = None,
) -> RecordDraft:
    """Build a draft from one locally selected Mod map."""
    stats = save_slot.get_map_stats(map_info) if save_slot is not None else None
    recorded_stats = stats if stats is not None and stats.is_recorded else None
    map_name = localized_name(map_info.names, languages) or map_info.fallback_name
    english_name = map_info.names.get('en') or map_info.fallback_name
    return RecordDraft(
        created_at=now or datetime.now(tz=UTC),
        mod_metadata_name=mod.metadata_name,
        mod_name=gamebanana.name if gamebanana is not None else None,
        mod_url=gamebanana.page_url if gamebanana is not None else None,
        mod_updated_at=gamebanana.latest_update_added_time if gamebanana is not None else None,
        credits=[credit.model_dump() for credit in gamebanana.credits]
        if gamebanana is not None
        else [],
        map_name=map_name,
        map_english_name=english_name if english_name != map_name else None,
        map_file=map_info.file_path,
        sid=sid_for_map_file(map_info.file_path),
        side=map_info.side or 'A',
        authors=authors,
        save_slot=save_slot.number if save_slot is not None else None,
        time_played=(
            recorded_stats.time_played.ingame_format('seconds')
            if recorded_stats is not None
            else None
        ),
        deaths=recorded_stats.deaths if recorded_stats is not None else None,
        completed=recorded_stats.completed if recorded_stats is not None else None,
        table_values={
            table: dict(values)
            for table, values in ({} if table_values is None else table_values).items()
        },
    )


def merge_saved_draft(current: RecordDraft, saved: RecordDraft) -> RecordDraft:
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
        raise ValueError('Cannot merge drafts for different Mods, maps, or save slots.')
    table_values: dict[str, dict[str, int | bool | str]] = {
        table: dict(values) for table, values in current.table_values.items()
    }
    for table, values in saved.table_values.items():
        retained = {
            title: value for title, value in values.items() if title in MANUAL_DRAFT_FIELD_TITLES
        }
        if retained:
            table_values.setdefault(table, {}).update(retained)
    return current.model_copy(
        update={
            'authors': saved.authors,
            'difficulty': saved.difficulty,
            'mod_updated_at': saved.mod_updated_at or current.mod_updated_at,
            'table_values': table_values,
        }
    )
