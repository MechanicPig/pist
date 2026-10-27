import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from berries.game.campaigns import CampaignCatalog
from berries.game.maps import ModMap
from pist import __doc__
from pist import __main__ as cli
from pist.__main__ import build_parser
from pist.credentials.store import TencentDocsCredentials
from pist.settings import SettingsStore
from pist.smartsheet.client import TencentSmartSheetClient, extract_file_id
from pist.smartsheet.models import FieldsResult, TencentApiResp
from test_support.save_docs import normal_save_xml


def test_map_browse_cli_injects_completed_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod_dir = tmp_path / 'Mods' / 'Example'
    maps_dir = mod_dir / 'Maps' / 'Example'
    maps_dir.mkdir(parents=True)
    (mod_dir / 'everest.yaml').write_text('- Name: Example\n  Version: 1.0.0\n', encoding='utf-8')
    (maps_dir / 'Map.bin').touch()
    dialog_dir = tmp_path / 'Content' / 'Dialog'
    dialog_dir.mkdir(parents=True)
    (dialog_dir / 'English.txt').write_bytes(b'\xff')
    saves_dir = tmp_path / 'Saves'
    saves_dir.mkdir()
    (saves_dir / '0.celeste').write_text(normal_save_xml('<SaveData/>'), encoding='utf-8')
    captured: list[CampaignCatalog] = []
    captured_diagnostics: list[tuple[str, ...]] = []

    async def capture_catalog(
        campaign_catalog: CampaignCatalog, *, diagnostics: tuple[str, ...], **kwargs: object
    ) -> None:
        captured.append(campaign_catalog)
        captured_diagnostics.append(diagnostics)

    monkeypatch.setattr(cli, 'browse_maps', capture_catalog)
    monkeypatch.setattr(cli, 'latest_inspection_report', lambda: None)
    asyncio.run(
        cli.browse_game_maps(
            tmp_path,
            0,
            settings_store=SettingsStore(tmp_path / 'settings.json'),
            sheet_client=Mock(spec=TencentSmartSheetClient),
            sheet_source=None,
            whitelist_path=None,
            temporary_blacklist_path=None,
            whitelist_full_override=False,
        )
    )
    assert len(captured) == 1
    assert type(captured[0]) is CampaignCatalog
    assert len(captured_diagnostics) == 1
    assert len(captured_diagnostics[0]) == 1
    assert 'English.txt' in captured_diagnostics[0][0]
    assert [camp.ref for camp in captured[0].campaigns] == ['Example']
    loaded_map = next(captured[0].campaigns[0].iter_maps())
    assert isinstance(loaded_map, ModMap)
    assert loaded_map.mod.path == mod_dir


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
