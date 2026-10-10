"""Opt-in tests that write synthetic rows only to an explicitly named test file."""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import ClientSession
from pydantic import Field

from berries.models import ExternalModel
from pist.credentials.store import CredentialStore
from pist.records.models import MapRecord
from pist.records.store import RecordStore, RecordSyncState
from pist.records.sync import RecordSyncConflict, RecordSyncService
from pist.smartsheet.client import REQUEST_TIMEOUT, TencentSmartSheetClient
from pist.smartsheet.models import JsonObject, MainSheetSnapshot, TencentApiResp

TEST_FILE_TITLE = 'Pist 同步测试（合成数据）'


class TestFileMetadata(ExternalModel):
    __test__ = False

    file_id: str = Field(validation_alias='ID')
    title: str
    type: str


class InterruptedConfirmationClient(TencentSmartSheetClient):
    """Fail only the first confirmation read after a successful real write."""

    def __init__(self) -> None:
        super().__init__(CredentialStore())
        self.interrupt_next_write = True
        self.interrupt_read = False

    async def write_record(
        self, snapshot: MainSheetSnapshot, values: JsonObject, *, record_id: str | None
    ) -> str:
        remote_id = await super().write_record(snapshot, values, record_id=record_id)
        if self.interrupt_next_write:
            self.interrupt_next_write = False
            self.interrupt_read = True
        return remote_id

    async def read_main_sheet(
        self, source: str, *, record_ids: tuple[str, ...] | None = None
    ) -> MainSheetSnapshot:
        if self.interrupt_read:
            self.interrupt_read = False
            raise RuntimeError('Synthetic confirmation-read failure')
        return await super().read_main_sheet(source, record_ids=record_ids)


def test_online_record_roundtrip_and_conflict(tmp_path: Path) -> None:
    source = os.environ.get('PIST_TEST_SHEET_URL')
    if not source:
        pytest.skip('Set PIST_TEST_SHEET_URL explicitly to enable remote writes to a test file.')

    async def run() -> None:
        credentials = await CredentialStore().load_credentials()
        headers = {
            'Access-Token': credentials.access_token,
            'Client-Id': credentials.client_id,
            'Open-Id': credentials.open_id,
        }
        client = TencentSmartSheetClient(CredentialStore())
        snapshot = await client.read_main_sheet(source)
        store = RecordStore(tmp_path / 'online-records.sqlite3')
        service = RecordSyncService(store, client, source)
        async with ClientSession(headers=headers, timeout=REQUEST_TIMEOUT) as session:
            async with session.get(
                f'https://docs.qq.com/openapi/drive/v2/files/{snapshot.file_id}/metadata'
            ) as response:
                response.raise_for_status()
                envelope = TencentApiResp.model_validate_json(await response.read())
            assert envelope.ret == 0, envelope.msg
            metadata = TestFileMetadata.model_validate(envelope.data)
            assert metadata.file_id == snapshot.file_id
            assert metadata.title == TEST_FILE_TITLE and metadata.type == 'smartsheet', (
                'Refusing to write: target is not the dedicated synthetic test file.'
            )
            assert not service.fields.issues(snapshot.fields)
            assert len(snapshot.fields.fields) == len(service.fields.fields)
            token = uuid4().hex
            record = MapRecord(
                created_at=datetime.now(UTC),
                mod_metadata_name=f'PistOnlineTest-{token}',
                map_name=f'合成地图-{token}',
                mod_name='合成 Mod',
                mod_url='https://gamebanana.com/mods/12345',
                video_url='https://www.bilibili.com/video/BV1xx411c7mD',
                authors=('测试选项A', '测试选项B'),
                tags=('测试选项A', '测试选项B'),
                mod_updated_at=datetime(2026, 10, 10, tzinfo=UTC).date(),
                time_played='0:01:23',
                deaths=7,
                n_strawberries=3,
                n_moonberries=1,
                cassette=True,
                heart='测试选项A',
                n_main_rooms=5,
                status='进行中',
                perceived_difficulty='测试选项A',
                perceived_difficulty_tier='测试选项A',
                rated_difficulty='测试选项B',
                rated_difficulty_tier='测试选项B',
                started_at=datetime(2026, 10, 9, tzinfo=UTC).date(),
                finished_at=datetime(2026, 10, 10, tzinfo=UTC).date(),
                save_load_usage='测试选项A',
                rating=8,
                notes='合成记录\n用于在线协议验证，不是真实游玩数据。',
            )
            retry_record = record.model_copy(
                update={'map_name': record.map_name + '-retry', 'notes': '确认失败后重试'}
            )
            try:
                result = await service.save(record, await service.check(record))
                assert result.synced, result.error
                saved = result.record
                assert saved.local_id is not None
                assert store.sync_state(saved.local_id) is RecordSyncState.SYNCED
                remote_id = store.sheet_links(saved.local_id)[0].record_id

                edited = saved.model_copy(
                    update={
                        'deaths': 0,
                        'n_strawberries': 0,
                        'n_moonberries': 0,
                        'cassette': False,
                        'rating': 0,
                        'tags': ('测试选项B',),
                        'notes': '已修改的合成记录',
                        'video_url': None,
                    }
                )
                result = await service.save(edited, await service.check(edited))
                assert result.synced, result.error
                assert store.sheet_links(saved.local_id)[0].record_id == remote_id

                optional_attributes = {
                    'mod_name',
                    'mod_url',
                    'video_url',
                    'mod_updated_at',
                    'time_played',
                    'deaths',
                    'n_strawberries',
                    'n_moonberries',
                    'cassette',
                    'heart',
                    'n_main_rooms',
                    'perceived_difficulty',
                    'perceived_difficulty_tier',
                    'rated_difficulty',
                    'rated_difficulty_tier',
                    'started_at',
                    'finished_at',
                    'save_load_usage',
                    'rating',
                    'notes',
                }
                cleared = edited.model_copy(
                    update={
                        **dict.fromkeys(optional_attributes),
                        'authors': (),
                        'tags': (),
                    }
                )
                result = await service.save(cleared, await service.check(cleared))
                assert result.synced, result.error

                check = await service.check(cleared)
                await client.write_record(
                    check.snapshot,
                    {'备注': [{'type': 'text', 'text': '远端独立修改'}]},
                    record_id=remote_id,
                )
                with pytest.raises(RecordSyncConflict) as error:
                    await service.save(cleared, check)
                assert {difference.title for difference in error.value.check.differences} == {
                    '备注'
                }
                assert store.load(saved.local_id) == cleared
                result = await service.save(cleared, error.value.check)
                assert result.synced, result.error

                interrupted = RecordSyncService(store, InterruptedConfirmationClient(), source)
                result = await interrupted.save(retry_record, await interrupted.check(retry_record))
                assert not result.synced and result.error == 'Synthetic confirmation-read failure'
                retry_record = result.record
                assert retry_record.local_id is not None
                assert store.sync_state(retry_record.local_id) is RecordSyncState.PENDING
                retry_id = store.sheet_links(retry_record.local_id)[0].record_id
                result = await service.save(retry_record, await service.check(retry_record))
                assert result.synced, result.error
                observed = await client.read_main_sheet(source)
                metadata_spec = next(
                    spec for spec in service.fields.fields if spec.title == 'Mod元数据名'
                )
                matches = [
                    row
                    for row in observed.records
                    if service.fields.decode(metadata_spec, row.values.get('Mod元数据名'))
                    == record.mod_metadata_name
                    and row.record_id != remote_id
                ]
                assert len(matches) == 1 and matches[0].record_id == retry_id
            finally:
                # Clean up only row IDs associated with this run's isolated local store.
                for local_id in (*store.matching_ids(record), *store.matching_ids(retry_record)):
                    local = store.load(local_id)
                    assert local.mod_metadata_name == record.mod_metadata_name
                    assert local.local_id is not None
                    for link in store.sheet_links(local.local_id):
                        assert (
                            link.file_id == snapshot.file_id and link.sheet_id == snapshot.sheet_id
                        )
                        async with session.post(
                            f'https://docs.qq.com/openapi/smartbook/v2/files/{link.file_id}/sheets/{link.sheet_id}',
                            json={'deleteRecords': {'recordIDs': [link.record_id]}},
                        ) as response:
                            response.raise_for_status()
                            cleanup_result = TencentApiResp.model_validate_json(
                                await response.read()
                            )
                        assert cleanup_result.ret == 0, cleanup_result.msg

    asyncio.run(run())
