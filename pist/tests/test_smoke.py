import pytest
from pydantic import ValidationError

from pist import __doc__
from pist.__main__ import build_parser
from pist.credentials.store import TencentDocsCredentials
from pist.smartsheet.client import extract_file_id
from pist.smartsheet.models import FieldsResult, TencentApiResp


def test_pkg_loads() -> None:
    assert __doc__


def test_entity_audit_is_a_pist_subcommand() -> None:
    args = build_parser().parse_args(
        ['entities', 'audit', 'report.json', '--game-dir', 'C:/Celeste']
    )

    assert args.command == 'entities'
    assert args.entities_command == 'audit'
    assert args.input_path.name == 'report.json'
    assert args.game_dir.name == 'Celeste'


def test_map_browse_defaults_to_save_slot_zero() -> None:
    parser = build_parser()
    args = parser.parse_args(['maps', 'browse'])

    assert args.save_slot == 0
    with pytest.raises(SystemExit):
        parser.parse_args(['mods', 'browse'])


def test_saved_record_sync_uses_association_instead_of_a_write_mode() -> None:
    parser = build_parser()

    args = parser.parse_args(['records', 'sync', '42'])

    assert args.record_id == 42
    for flag in ('--add', '--update'):
        with pytest.raises(SystemExit):
            parser.parse_args(['records', 'sync', '42', flag])


def test_records_browse_requires_no_game_or_save_configuration() -> None:
    args = build_parser().parse_args(['records', 'browse'])
    assert args.command == 'records'
    assert args.records_command == 'browse'
    assert not hasattr(args, 'game_dir')


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
