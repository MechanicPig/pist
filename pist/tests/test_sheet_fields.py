from datetime import UTC, date, datetime

import pytest
from pydantic import JsonValue, ValidationError
from sheet_factory import make_sheet_fields as fields

from pist.records.models import MapRecord
from pist.smartsheet.encoding import MapNameCell, encode_record_values
from pist.smartsheet.fields import SheetFields, SheetFieldSpec, load_sheet_fields
from pist.smartsheet.models import FieldType, JsonObject


@pytest.mark.parametrize('type_id', (1, 2, 3, 4, 8, 9, 17))
def test_field_config_converts_protocol_ids_to_enum_and_serializes_as_numbers(type_id: int) -> None:
    payload = {'title': 'Example', 'type': type_id, 'required': False, 'writable': True}
    spec = SheetFieldSpec.model_validate(payload)
    assert isinstance(spec.type, FieldType)
    assert spec.model_dump(mode='json') == payload


@pytest.mark.parametrize('writable', (False, True))
def test_field_config_rejects_formula_even_when_read_only(writable: bool) -> None:
    with pytest.raises(ValidationError, match='formula columns'):
        SheetFieldSpec.model_validate({'title': 'Formula', 'type': 19, 'writable': writable})


def test_field_config_rejects_unknown_protocol_type() -> None:
    with pytest.raises(ValidationError):
        SheetFieldSpec.model_validate({'title': 'Unknown', 'type': 999})


def test_date_field_discards_time_and_subseconds() -> None:
    spec = SheetFieldSpec.model_validate({'title': 'Date', 'type': 4})
    assert (
        SheetFields.decode(spec, 946684800123)
        == datetime.fromtimestamp(946684800.123, UTC).astimezone().date().isoformat()
    )


@pytest.mark.parametrize('hour', (0, 12, 16, 23))
def test_three_date_columns_compare_only_the_calendar_date(hour: int) -> None:
    contract = load_sheet_fields()
    schema = fields(('起始日期', 4), ('结束日期', 4), ('更新时间', 4))
    record = MapRecord(
        created_at=datetime.now(UTC),
        map_name='Map',
        started_at=date(2026, 10, 10),
        finished_at=date(2026, 10, 10),
        mod_updated_at=date(2026, 10, 10),
    )
    remote: JsonObject = {
        column.field_title: int(datetime(2026, 10, 10, hour).astimezone().timestamp() * 1000)
        for column in schema.fields
    }
    encoded = encode_record_values(record, schema)
    assert contract.comparable(encoded, schema) == contract.comparable(remote, schema)
    adopted = contract.apply_remote(record, remote, schema)
    assert adopted == record


@pytest.mark.parametrize('raw', (None, [], '', False))
def test_blank_checkbox_decodes_and_applies_as_unchecked(
    raw: JsonValue,
) -> None:
    contract = load_sheet_fields()
    schema = fields(('磁带', 3))
    spec = next(spec for spec in contract.fields if spec.title == '磁带')
    assert contract.decode(spec, raw) is False
    assert contract.comparable({'磁带': raw}, schema) == {'磁带': False}
    record = MapRecord(created_at=datetime.now(UTC), map_name='Map')
    assert record.cassette is False
    assert encode_record_values(record, schema) == {'磁带': False}
    assert (
        contract.apply_remote(
            record.model_copy(update={'cassette': True}), {'磁带': raw}, schema
        ).cassette
        is False
    )


@pytest.mark.parametrize('url', (None, 'https://www.bilibili.com/video/example'))
def test_map_name_cell_round_trip(url: str | None) -> None:
    cell = MapNameCell(name='Map', video_url=url)
    assert MapNameCell.from_value(cell.to_value()) == cell
    record = MapRecord(created_at=datetime(2026, 10, 10, tzinfo=UTC), map_name='Map', video_url=url)
    assert encode_record_values(record, fields(('地图名', 1)))['地图名'] == cell.to_value()


def test_map_name_cell_preserves_split_text_and_rejects_ambiguous_links() -> None:
    assert MapNameCell.from_value(
        [
            {'text': 'First '},
            {'text': 'Map', 'link': 'https://example.com/video'},
        ]
    ) == MapNameCell(name='First Map', video_url='https://example.com/video')
    with pytest.raises(ValueError, match='多个'):
        MapNameCell.from_value(
            [
                {'text': 'A', 'link': 'https://example.com/a'},
                {'text': 'B', 'link': 'https://example.com/b'},
            ]
        )


def test_schema_contract_requires_core_fields_and_rejects_type_changes() -> None:
    contract = load_sheet_fields()
    required = fields(('地图名', 1), ('Mod元数据名', 1), ('状态', 17), ('其他列', 2))
    assert contract.issues(required) == ('尚未支持的表格字段：其他列',)
    assert contract.issues(fields(('地图名', 1))) == (
        '缺少必需字段：Mod元数据名',
        '缺少必需字段：状态',
    )
    assert contract.issues(
        fields(('地图名', 1), ('Mod元数据名', 1), ('状态', 17), ('备注', 2))
    ) == ('字段类型不符：备注（要求 1）',)
    assert contract.issues(
        fields(('地图名', 1), ('地图名', 1), ('Mod元数据名', 1), ('状态', 17))
    ) == ('字段名称重复：地图名',)
    assert [
        field.field_title
        for field in contract.writable_fields(
            fields(('地图名', 1), ('标签', 9), ('其他列', 2))
        ).fields
    ] == ['地图名', '标签']


def test_comparison_includes_recording_links_but_ignores_decoration() -> None:
    contract = load_sheet_fields()
    schema = fields(('地图名', 1), ('作者', 9))
    before = contract.comparable(
        {
            '地图名': [{'text': 'Map', 'link': 'https://example.com/a', 'bold': True}],
            '作者': [{'text': 'Alice'}, {'text': 'Bob'}],
            '其他字段': 1,
        },
        schema,
    )
    assert before == contract.comparable(
        {
            '地图名': [{'type': 'url', 'text': 'Map', 'link': 'https://example.com/a'}],
            '作者': [{'text': 'Bob'}, {'text': 'Alice'}],
            '其他字段': 2,
        },
        schema,
    )
    assert before != contract.comparable(
        {
            '地图名': [{'text': 'Map', 'link': 'https://example.com/b'}],
        },
        schema,
    )


def test_using_remote_values_replaces_public_fields_and_retains_private_identity() -> None:
    record = MapRecord(
        created_at=datetime(2026, 10, 10, tzinfo=UTC),
        local_id=3,
        record_number=7,
        map_name='Local',
        video_url='https://example.com/old',
        map_file='Maps/Map.bin',
        save_slot=4,
        notes='old',
    )
    contract = load_sheet_fields()
    schema = fields(('地图名', 1), ('备注', 1), ('死亡数', 2))
    result = contract.apply_remote(
        record,
        {
            '地图名': [{'text': 'Remote', 'link': 'https://example.com/new'}],
            '死亡数': 0,
        },
        schema,
    )
    assert result.map_name == 'Remote' and result.video_url == 'https://example.com/new'
    assert (result.local_id, result.record_number, result.map_file, result.save_slot) == (
        3,
        7,
        'Maps/Map.bin',
        4,
    )
    assert result.deaths == 0
    assert result.notes is None
    assert contract.apply_remote(result, {'地图名': [{'text': 'Remote'}]}, schema).video_url is None


def test_contract_rejects_duplicate_supported_titles() -> None:
    with pytest.raises(ValueError, match='unique'):
        SheetFields(
            fields=(
                SheetFieldSpec(title='地图名', type=FieldType.TEXT),
                SheetFieldSpec(title='地图名', type=FieldType.TEXT),
            )
        )
