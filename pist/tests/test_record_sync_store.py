import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pist.records.models import MapRecord
from pist.records.store import (
    RecordSheetAddAttempt,
    RecordSheetLink,
    RecordStorageError,
    RecordStore,
    RecordSyncState,
)

NOW = datetime(2026, 10, 10, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> RecordStore:
    return RecordStore(tmp_path / 'records.sqlite3')


@pytest.fixture
def record() -> MapRecord:
    return MapRecord(
        created_at=NOW,
        mod_metadata_name='Example',
        map_name='Map',
        map_file='Maps/Example/Map.bin',
        save_slot=0,
        time_played='0:01:00',
        status='进行中',
        notes='A',
    )


def test_offline_edits_preserve_confirmed_remote_baseline(
    store: RecordStore, record: MapRecord
) -> None:
    initial = store.load(store.save(record))
    assert initial.local_id is not None
    link = RecordSheetLink(
        local_id=initial.local_id, file_id='file', sheet_id='sheet', record_id='row'
    )
    store.confirm_sheet_record(link, {'note': 'A'}, submitted=initial, confirmed_at=NOW)
    assert store.sync_state(initial.local_id) is RecordSyncState.SYNCED
    for note in ('B', 'C'):
        edited = initial.model_copy(update={'status': '进行中', 'notes': note})
        store.save(edited)
        assert store.sync_state(initial.local_id) is RecordSyncState.PENDING
        assert store.sheet_links(initial.local_id)[0].confirmed_values == {'note': 'A'}
    current = store.load(initial.local_id)
    assert current.notes == 'C'
    assert current.record_number == initial.record_number
    store.confirm_sheet_record(link, {'note': 'C'}, submitted=current, confirmed_at=NOW)
    assert store.sheet_links(initial.local_id)[0].confirmed_values == {'note': 'C'}
    assert store.sync_state(initial.local_id) is RecordSyncState.SYNCED


def test_confirming_older_submission_preserves_newer_pending_edit(
    store: RecordStore, record: MapRecord
) -> None:
    submitted = store.load(store.save(record))
    assert submitted.local_id is not None
    current = submitted.model_copy(update={'deaths': 10})
    store.save(current)
    store.confirm_sheet_record(
        RecordSheetLink(
            local_id=submitted.local_id, file_id='file', sheet_id='sheet', record_id='row'
        ),
        {'deaths': 0},
        submitted=submitted,
        confirmed_at=NOW,
    )
    assert store.load(submitted.local_id) == current
    assert store.sync_state(submitted.local_id) is RecordSyncState.PENDING
    assert store.sheet_links(submitted.local_id)[0].confirmed_values == {'deaths': 0}


def test_acknowledgement_cannot_change_remote_identity(
    store: RecordStore, record: MapRecord
) -> None:
    saved = store.load(store.save(record))
    assert saved.local_id is not None
    link = RecordSheetLink(
        local_id=saved.local_id, file_id='file', sheet_id='sheet', record_id='row'
    )
    store.link_sheet_record(link)
    with pytest.raises(ValueError, match='association'):
        store.confirm_sheet_record(
            link.model_copy(update={'record_id': 'other'}), {}, submitted=saved, confirmed_at=NOW
        )
    assert store.sheet_links(saved.local_id) == (link,)
    assert store.sync_state(saved.local_id) is RecordSyncState.PENDING
    with pytest.raises(ValueError, match='submitted record'):
        store.confirm_sheet_record(
            link, {}, submitted=saved.model_copy(update={'local_id': 999}), confirmed_at=NOW
        )


def test_record_store_schema_has_no_edit_draft_table(store: RecordStore) -> None:
    with store.connect() as conn:
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE name = 'record_drafts'").fetchone()
            is None
        )


def test_formal_numbers_are_not_recycled_after_deleting_latest(
    store: RecordStore, record: MapRecord
) -> None:
    first = store.load(store.save(record))
    second = store.load(store.save(record))
    with store.connect() as conn:
        conn.execute('DELETE FROM records WHERE id = ?', (second.local_id,))
    reopened = RecordStore(store.path)
    assert reopened.load(reopened.save(record)).record_number == 3
    assert first.local_id is not None
    with pytest.raises(ValueError, match='未开始'):
        reopened.save(first.model_copy(update={'status': '未开始'}))
    assert reopened.load(first.local_id) == first


def test_failed_number_allocation_rolls_back_counter(store: RecordStore, record: MapRecord) -> None:
    first = store.load(store.save(record))
    with pytest.raises(RecordStorageError) as caught:
        store.save(record.model_copy(update={'record_number': first.record_number}))
    assert isinstance(caught.value.__cause__, sqlite3.IntegrityError)
    assert store.load(store.save(record)).record_number == 2


def test_locked_database_raises_record_error_and_does_not_save(
    store: RecordStore, record: MapRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, 'connect', lambda path: connect(path, timeout=0))
    with connect(store.path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(RecordStorageError) as caught:
            store.save(record)
        assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
    assert store.matching_ids(record) == ()


def test_failed_remote_link_preserves_add_attempt_atomically(
    store: RecordStore, record: MapRecord
) -> None:
    first = store.save(record)
    second = store.save(record)
    link = RecordSheetLink(local_id=first, file_id='file', sheet_id='sheet', record_id='row')
    store.link_sheet_record(link)
    attempt = RecordSheetAddAttempt(
        local_id=second,
        file_id='file',
        sheet_id='sheet',
        started_at=NOW,
        mod_metadata_name=record.mod_metadata_name or '',
        map_name=record.map_name,
    )
    store.begin_sheet_add(attempt)
    with pytest.raises(RecordStorageError):
        store.link_sheet_record(link.model_copy(update={'local_id': second}))
    assert store.sheet_links(second) == ()
    assert store.sheet_add_attempts(second) == (attempt,)
    with pytest.raises(ValueError, match='不能再次新增'):
        store.begin_sheet_add(attempt.model_copy(update={'sheet_id': 'other'}))
    store.link_sheet_record(link.model_copy(update={'local_id': second, 'record_id': 'other-row'}))
    assert store.sheet_add_attempts(second) == ()
