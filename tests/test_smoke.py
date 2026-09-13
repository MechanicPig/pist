import pytest
from pydantic import ValidationError

from pist import __doc__
from pist.__main__ import build_parser
from pist.models import TencentApiResp, TencentDocsCredentials, parse_json_object
from pist.smartsheet import extract_file_id


def test_package_loads() -> None:
    assert __doc__


def test_mod_browse_defaults_to_save_slot_zero() -> None:
    args = build_parser().parse_args(['mods', 'browse'])

    assert args.save_slot == 0


def test_saved_draft_submit_requires_an_explicit_write_mode() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(['draft', 'submit', '1'])

    args = parser.parse_args(['draft', 'submit', '42', '--update'])

    assert args.draft_id == 42
    assert args.update is True


def test_entity_rules_can_write_to_the_shared_library_with_legacy_alias() -> None:
    args = build_parser().parse_args(['entities', 'rules', '--shared'])

    assert args.shared
    assert args.entities_command == 'rules'


def test_extract_smart_sheet_file_id_from_url() -> None:
    assert (
        extract_file_id('https://docs.qq.com/smartsheet/DV05RU0tncXp6T1dG?tab=table')
        == 'DV05RU0tncXp6T1dG'
    )


def test_response_envelope_validates_json_data() -> None:
    resp = TencentApiResp.model_validate({'ret': 0, 'data': {'name': 'value'}})
    assert resp.data == {'name': 'value'}


def test_dynamic_operation_result_must_be_an_object() -> None:
    with pytest.raises(TypeError, match='getViews operation result'):
        parse_json_object(['not', 'an object'], context='getViews operation result')


def test_credentials_are_serialized_and_validated_by_pydantic() -> None:
    credentials = TencentDocsCredentials(client_id='id', access_token='token', open_id='open-id')
    assert TencentDocsCredentials.model_validate_json(credentials.model_dump_json()) == credentials
    with pytest.raises(ValidationError):
        TencentDocsCredentials.model_validate_json('{"client_id":"id"}')
