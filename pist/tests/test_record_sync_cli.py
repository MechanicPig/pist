import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from sheet_factory import FakeSheet, make_sheet_fields

from pist import __main__ as cli
from pist.credentials.store import CredentialStore
from pist.records.lock import record_writer_lock
from pist.records.models import MapRecord
from pist.records.store import RecordSheetLink, RecordStore, RecordSyncState
from pist.smartsheet.encoding import encode_record_values

NOW = datetime(2026, 10, 10, tzinfo=UTC)


@pytest.fixture
def context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[RecordStore, FakeSheet, MapRecord]:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    monkeypatch.setattr(cli, 'RecordStore', lambda: store)
    monkeypatch.setattr(cli, 'TencentSmartSheetClient', lambda _: client)
    monkeypatch.setattr(cli, 'record_writer_lock', lambda: record_writer_lock(tmp_path))
    saved = store.load(
        store.save(
            MapRecord(
                created_at=NOW,
                mod_metadata_name='Example',
                map_name='Map',
                time_played='0:01:00',
                status='进行中',
                notes='A',
            )
        )
    )
    return store, client, saved


def associate(store: RecordStore, client: FakeSheet, saved: MapRecord) -> None:
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


@pytest.mark.parametrize('linked', (False, True))
def test_cli_uses_exact_association_or_new_row_and_verifies(
    context: tuple[RecordStore, FakeSheet, MapRecord], linked: bool
) -> None:
    store, client, saved = context
    assert saved.local_id is not None
    if linked:
        associate(store, client, saved)
        client.rows['same-name-other-row'] = deepcopy(client.rows['target'])
        store.save(saved.model_copy(update={'notes': 'B'}))
    asyncio.run(cli.sync_saved_record(Mock(spec=CredentialStore), 'source', saved.local_id))
    assert [target for target, _ in client.writes] == (['target'] if linked else [None])
    assert store.sync_state(saved.local_id) is RecordSyncState.SYNCED
    link = store.sheet_links(saved.local_id)[0]
    assert link.confirmed_values == client.rows[link.record_id]
    assert store.sheet_add_attempts(saved.local_id) == ()


@pytest.mark.parametrize('failure', ('conflict', 'schema', 'offline'))
def test_cli_refuses_unreviewed_remote_changes_or_read_failure_without_writing(
    context: tuple[RecordStore, FakeSheet, MapRecord], failure: str
) -> None:
    store, client, saved = context
    assert saved.local_id is not None
    if failure == 'conflict':
        associate(store, client, saved)
        client.rows['target']['备注'] = [{'text': 'remote change'}]
    elif failure == 'schema':
        client.fields = make_sheet_fields(('地图名', 1))
    else:
        client.read_error = True
    with pytest.raises((ValueError, OSError)):
        asyncio.run(cli.sync_saved_record(Mock(spec=CredentialStore), 'source', saved.local_id))
    assert store.load(saved.local_id) == saved
    assert client.writes == []


def test_cli_failed_add_keeps_pending_and_cannot_blindly_retry(
    context: tuple[RecordStore, FakeSheet, MapRecord],
) -> None:
    store, client, saved = context
    assert saved.local_id is not None
    client.write_error = True
    with pytest.raises(RuntimeError, match='待同步'):
        asyncio.run(cli.sync_saved_record(Mock(spec=CredentialStore), 'source', saved.local_id))
    assert store.sync_state(saved.local_id) is RecordSyncState.PENDING
    client.write_error = False
    with pytest.raises(ValueError, match='不能再次新增'):
        asyncio.run(cli.sync_saved_record(Mock(spec=CredentialStore), 'source', saved.local_id))
    assert len(client.writes) == 1
