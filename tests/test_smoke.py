import pytest
from pydantic import ValidationError

from pist import __doc__
from pist.__main__ import build_parser
from pist.secrets import TencentDocsCredentials
from pist.smartsheet import FieldsResult, TencentApiResp, extract_file_id


def test_package_loads() -> None:
    assert __doc__


def test_map_browse_defaults_to_save_slot_zero() -> None:
    parser = build_parser()
    args = parser.parse_args(['maps', 'browse'])

    assert args.save_slot == 0
    with pytest.raises(SystemExit):
        parser.parse_args(['mods', 'browse'])


def test_saved_record_sync_requires_an_explicit_write_mode() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(['record', 'sync', '1'])

    args = parser.parse_args(['record', 'sync', '42', '--update'])

    assert args.record_id == 42
    assert args.update is True


def test_extract_smart_sheet_file_id_from_url() -> None:
    assert (
        extract_file_id('https://docs.qq.com/smartsheet/DV05RU0tncXp6T1dG?tab=table')
        == 'DV05RU0tncXp6T1dG'
    )


def test_response_envelope_validates_json_data() -> None:
    resp = TencentApiResp.model_validate({'ret': 0, 'data': {'name': 'value'}})
    assert resp.data == {'name': 'value'}


def test_operation_result_must_match_its_model() -> None:
    with pytest.raises(ValidationError):
        FieldsResult.model_validate(['not', 'an object'])


def test_credentials_are_serialized_and_validated_by_pydantic() -> None:
    credentials = TencentDocsCredentials(client_id='id', access_token='token', open_id='open-id')
    assert TencentDocsCredentials.model_validate_json(credentials.model_dump_json()) == credentials
    with pytest.raises(ValidationError):
        TencentDocsCredentials.model_validate_json('{"client_id":"id"}')
