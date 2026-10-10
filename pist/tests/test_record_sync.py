import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sheet_factory import FakeSheet
from sheet_factory import make_sheet_fields as fields

from pist.records.models import MapRecord
from pist.records.store import RecordSheetLink, RecordStore, RecordSyncState
from pist.records.sync import RecordAddRecoveryRequired, RecordSyncConflict, RecordSyncService
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.models import JsonObject, MainSheetSnapshot

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def uncertain_add(
    store: RecordStore, client: FakeSheet, record: MapRecord
) -> tuple[RecordSyncService, MapRecord]:
    service = RecordSyncService(store, client, 'source')
    client.write_error = True
    result = asyncio.run(service.save(record, asyncio.run(service.check(record))))
    client.write_error = False
    return service, result.record


def test_recovery_uses_submitted_identity_and_excludes_bound_rows(
    tmp_path: Path, record: MapRecord
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    service, saved = uncertain_add(store, client, record)
    client.rows = {
        id_: encode_record_values(record, client.fields) for id_ in ('first', 'second', 'bound')
    }
    other = store.save(record)
    store.link_sheet_record(
        RecordSheetLink(local_id=other, file_id='file', sheet_id='main', record_id='bound')
    )
    renamed = saved.model_copy(update={'map_name': 'Renamed', 'mod_metadata_name': 'RenamedMod'})
    store.save(renamed)
    with pytest.raises(RecordAddRecoveryRequired) as error:
        asyncio.run(service.check(renamed))
    recovery = error.value.recovery
    assert [row.record_id for row in recovery.candidates] == ['first', 'second']
    asyncio.run(service.resolve_add(recovery, record_id='second'))
    assert saved.local_id is not None
    assert store.sheet_add_attempts(saved.local_id) == ()
    assert store.sheet_links(saved.local_id)[0].confirmed_values is None
    check = asyncio.run(service.check(renamed))
    assert check.remote is not None and check.remote.record_id == 'second'
    assert check.differences  # Binding does not acknowledge the row or overwrite anything.
    assert len(client.writes) == 1


@pytest.mark.parametrize('change', ('contents', 'deleted', 'bound', 'wrong-id', 'read-failure'))
def test_recovery_revalidates_selected_candidate_before_binding(
    tmp_path: Path, record: MapRecord, change: str
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    service, saved = uncertain_add(store, client, record)
    client.rows['candidate'] = encode_record_values(record, client.fields)
    with pytest.raises(RecordAddRecoveryRequired) as error:
        asyncio.run(service.check(saved))
    target = 'candidate'
    if change == 'contents':
        client.rows[target]['死亡数'] = 99
    elif change == 'deleted':
        client.rows.clear()
    elif change == 'bound':
        other = store.save(record)
        store.link_sheet_record(
            RecordSheetLink(local_id=other, file_id='file', sheet_id='main', record_id=target)
        )
    elif change == 'wrong-id':
        target = 'not-reviewed'
    else:
        client.read_error = True
    with pytest.raises((ValueError, OSError)):
        asyncio.run(service.resolve_add(error.value.recovery, record_id=target))
    assert saved.local_id is not None
    assert store.sheet_links(saved.local_id) == ()
    assert len(store.sheet_add_attempts(saved.local_id)) == 1
    assert len(client.writes) == 1


@pytest.mark.parametrize('candidate_appears', (False, True))
def test_confirmed_no_row_retry_rechecks_remote_and_does_not_write_itself(
    tmp_path: Path, record: MapRecord, candidate_appears: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    service, saved = uncertain_add(store, client, record)
    with pytest.raises(RecordAddRecoveryRequired) as error:
        asyncio.run(service.check(saved))
    assert error.value.recovery.candidates == ()
    assert saved.local_id is not None
    if candidate_appears:
        client.rows['late'] = encode_record_values(record, client.fields)
        with pytest.raises(ValueError, match='候选'):
            asyncio.run(service.resolve_add(error.value.recovery, record_id=None))
        assert store.sheet_add_attempts(saved.local_id)
    else:
        asyncio.run(service.resolve_add(error.value.recovery, record_id=None))
        assert store.sheet_add_attempts(saved.local_id) == ()
        assert len(client.writes) == 1
        check = asyncio.run(service.check(saved))
        assert asyncio.run(service.save(saved, check)).synced
        assert len(client.writes) == 2


def test_recovery_refuses_retry_with_candidates_and_another_sheet(
    tmp_path: Path, record: MapRecord
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    service, saved = uncertain_add(store, client, record)
    client.rows['candidate'] = encode_record_values(record, client.fields)
    with pytest.raises(RecordAddRecoveryRequired) as error:
        asyncio.run(service.check(saved))
    with pytest.raises(ValueError, match='候选'):
        asyncio.run(service.resolve_add(error.value.recovery, record_id=None))
    recovery = error.value.recovery
    wrong = recovery.attempt.model_copy(update={'sheet_id': 'different'})
    with pytest.raises(ValueError, match='其他表格'):
        service._add_recovery((wrong,), recovery.snapshot)


@pytest.fixture
def record() -> MapRecord:
    return MapRecord(
        created_at=NOW,
        map_name='Map',
        mod_metadata_name='Example',
        map_file='Maps/Map.bin',
        save_slot=0,
        time_played='0:01:00',
        status='进行中',
        notes='A',
    )


def linked_record(store: RecordStore, client: FakeSheet, record: MapRecord) -> MapRecord:
    saved = store.load(store.save(record))
    assert saved.local_id is not None
    values = encode_record_values(saved, client.fields)
    client.rows['target'] = deepcopy(values)
    store.confirm_sheet_record(
        RecordSheetLink(
            local_id=saved.local_id, file_id='file', sheet_id='main', record_id='target'
        ),
        values,
        submitted=saved,
        confirmed_at=NOW,
    )
    return saved


def test_offline_edits_sync_latest_contents_using_original_baseline(
    tmp_path: Path, record: MapRecord
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    original = linked_record(store, client, record)
    current = original.model_copy(update={'status': '进行中', 'notes': 'C'})
    store.save(current)
    service = RecordSyncService(store, client, 'source')

    async def run() -> None:
        check = await service.check(current)
        assert check.differences == ()
        result = await service.save(current, check)
        assert result.synced and result.error is None
        assert current.local_id is not None
        assert store.sync_state(current.local_id) is RecordSyncState.SYNCED
        assert client.writes[0][0] == 'target'
        assert store.sheet_links(current.local_id)[0].confirmed_values == client.rows['target']

    asyncio.run(run())

    assert client.read_selections == [('target',)] * 3


@pytest.mark.parametrize('reviewed', (False, True))
def test_converged_dates_refresh_confirmation_without_writing(
    tmp_path: Path, record: MapRecord, reviewed: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    client.fields.fields.extend(fields(('起始日期', 4), ('结束日期', 4)).fields)
    original = record.model_copy(
        update={'started_at': date(2026, 5, 29), 'finished_at': date(2026, 5, 29)}
    )
    saved = linked_record(store, client, original)
    service = RecordSyncService(store, client, 'source')
    expected = asyncio.run(service.check(saved)) if reviewed else None
    current = saved.model_copy(
        update={'started_at': date(2026, 5, 28), 'finished_at': date(2026, 5, 28)}
    )
    store.save(current)
    client.rows['target'] = encode_record_values(current, client.fields)

    async def run() -> None:
        check = await service.check(current, expected=expected)
        assert check.differences == ()
        assert current.local_id is not None
        # A read alone must not advance the old confirmation.
        assert store.sheet_links(current.local_id)[0].confirmed_values != client.rows['target']
        result = await service.save(current, check)
        assert result.synced
        assert store.sheet_links(current.local_id)[0].confirmed_values == client.rows['target']
        assert store.sync_state(current.local_id) is RecordSyncState.SYNCED

    asyncio.run(run())
    assert client.writes == []


def test_converged_field_does_not_hide_another_conflict(tmp_path: Path, record: MapRecord) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    saved = linked_record(store, client, record)
    current = saved.model_copy(update={'notes': 'Same', 'deaths': 4})
    client.rows['target']['备注'] = [{'text': 'Same'}]
    client.rows['target']['死亡数'] = 9
    service = RecordSyncService(store, client, 'source')
    check = asyncio.run(service.check(current))
    assert [diff.title for diff in check.differences] == ['死亡数']
    assert client.writes == []


def test_partial_observation_preserves_other_rows(tmp_path: Path, record: MapRecord) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    saved = linked_record(store, client, record)
    other = store.load(store.save(record.model_copy(update={'map_name': 'Other'})))
    assert other.local_id is not None
    client.rows['other'] = encode_record_values(other, client.fields)
    store.link_sheet_record(
        RecordSheetLink(local_id=other.local_id, file_id='file', sheet_id='main', record_id='other')
    )
    service = RecordSyncService(store, client, 'source')
    asyncio.run(service.read_snapshot())
    before = store.sheet_snapshots(other.local_id)
    client.rows['other']['备注'] = [{'text': 'Not queried'}]
    asyncio.run(service.check(saved))
    assert client.read_selections == [None, ('target',)]
    assert store.sheet_snapshots(other.local_id) == before


def test_remote_change_during_editing_requires_renewed_consent_before_saving(
    tmp_path: Path, record: MapRecord
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    saved = linked_record(store, client, record)
    service = RecordSyncService(store, client, 'source')

    async def run() -> None:
        check = await service.check(saved)
        client.rows['target']['备注'] = [{'text': 'D'}]
        edited = saved.model_copy(update={'deaths': 4})
        with pytest.raises(RecordSyncConflict) as error:
            await service.save(edited, check)
        changed = error.value.check
        assert [diff.title for diff in changed.differences] == ['备注']
        assert saved.local_id is not None and store.load(saved.local_id) == saved
        assert client.writes == []
        adopted = service.use_remote(edited, changed)
        assert adopted.notes == 'D'
        result = await service.save(edited, changed)
        assert result.synced

    asyncio.run(run())


@pytest.mark.parametrize('verification', (False, True))
def test_failed_write_or_readback_keeps_local_pending_and_baseline_unchanged(
    tmp_path: Path, record: MapRecord, verification: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    saved = linked_record(store, client, record)
    service = RecordSyncService(store, client, 'source')

    async def run() -> None:
        check = await service.check(saved)
        client.write_error = not verification
        client.verify_error = verification
        result = await service.save(saved.model_copy(update={'deaths': 9}), check)
        assert not result.synced and result.error is not None
        assert saved.local_id is not None
        assert store.load(saved.local_id).deaths == 9
        assert store.sync_state(saved.local_id) is RecordSyncState.PENDING
        assert (
            store.sheet_links(saved.local_id)[0].confirmed_values == check.remote.values
            if check.remote is not None
            else False
        )
        assert len(client.writes) == 1

    asyncio.run(run())


def test_new_row_keeps_returned_association_even_if_verification_fails(
    tmp_path: Path, record: MapRecord
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    service = RecordSyncService(store, client, 'source')

    async def run() -> None:
        check = await service.check(record)
        client.verify_error = True
        result = await service.save(record, check)
        assert not result.synced and result.record.local_id is not None
        assert store.sheet_links(result.record.local_id)[0].record_id == 'created'
        assert store.sheet_add_attempts(result.record.local_id) == ()
        client.verify_error = False
        reviewed = await service.check(result.record)
        assert (await service.save(result.record, reviewed)).synced
        assert [id_ for id_, _ in client.writes] == [
            None
        ]  # A successful read confirms without rewriting.

    asyncio.run(run())


def test_missing_associated_row_never_becomes_a_new_row(tmp_path: Path, record: MapRecord) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    saved = linked_record(store, client, record)
    client.rows.clear()
    service = RecordSyncService(store, client, 'source')
    with pytest.raises(ValueError, match='删除'):
        asyncio.run(service.check(saved))
    assert client.writes == []


def test_unlinked_saved_record_cannot_blindly_retry_a_possible_add(
    tmp_path: Path, record: MapRecord
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    saved = store.load(store.save(record))
    client.rows['unknown'] = encode_record_values(record, client.fields)
    service = RecordSyncService(store, client, 'source')
    with pytest.raises(ValueError, match='不能自动重试新增'):
        asyncio.run(service.check(saved))
    # Explicitly requesting a new experience is still permitted.
    assert asyncio.run(service.check(record)).remote is None
    assert client.writes == []


def test_schema_mismatch_and_read_failure_do_not_save_or_write(
    tmp_path: Path, record: MapRecord
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    service = RecordSyncService(store, client, 'source')
    client.fields = fields(('地图名', 1))

    async def run() -> None:
        check = await service.check(record)
        assert check.issues
        with pytest.raises(ValueError, match='必需字段'):
            await service.save(record, check)
        client.read_error = True
        with pytest.raises(OSError, match='offline'):
            await service.check(record)

    asyncio.run(run())
    assert client.writes == []
    assert store.matching_ids(record) == ()


def test_video_only_remote_change_is_a_conflict(tmp_path: Path, record: MapRecord) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    saved = linked_record(store, client, record)
    client.rows['target']['地图名'] = [{'text': 'Map', 'link': 'https://example.com/video'}]
    check = asyncio.run(RecordSyncService(store, client, 'source').check(saved))
    assert [diff.title for diff in check.differences] == ['地图名']


@pytest.mark.parametrize('created_before_failure', (False, True))
def test_uncertain_add_survives_restart_and_rename_without_another_write(
    tmp_path: Path, record: MapRecord, created_before_failure: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')

    class LostResponseSheet(FakeSheet):
        async def write_record(
            self, snapshot: MainSheetSnapshot, values: JsonObject, *, record_id: str | None
        ) -> str:
            # The intent must be committed before the request can reach the remote boundary.
            assert len(RecordStore(store.path).sheet_add_attempts(1)) == 1
            if created_before_failure:
                await super().write_record(snapshot, values, record_id=record_id)
            else:
                self.writes.append((record_id, deepcopy(values)))
            raise OSError('response lost')

    client = LostResponseSheet()
    service = RecordSyncService(store, client, 'source')
    checked = asyncio.run(service.check(record))
    failed = asyncio.run(service.save(record, checked))
    assert not failed.synced and failed.record.local_id is not None
    assert store.sheet_links(failed.record.local_id) == ()
    assert len(store.sheet_add_attempts(failed.record.local_id)) == 1
    renamed = failed.record.model_copy(update={'map_name': 'Renamed', 'notes': 'offline change'})
    store.save(renamed)
    reopened = RecordStore(store.path)
    with pytest.raises(ValueError, match='不能再次新增'):
        asyncio.run(RecordSyncService(reopened, client, 'source').check(renamed))
    assert len(client.writes) == 1
    assert reopened.load(failed.record.local_id).notes == 'offline change'
    if created_before_failure:
        # Explicitly associating the checked row resolves the uncertainty without an add.
        reopened.link_sheet_record(
            RecordSheetLink(
                local_id=failed.record.local_id,
                file_id='file',
                sheet_id='main',
                record_id='created',
            )
        )
        assert reopened.sheet_add_attempts(failed.record.local_id) == ()
        assert (
            asyncio.run(RecordSyncService(reopened, client, 'source').check(renamed)).remote
            is not None
        )
