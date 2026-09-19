"""Command-line entry point for pist."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from getpass import getpass
from pathlib import Path

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn

from pist.game.mods import DisabledMod, InstalledMod, ModScanner, ModScanReport
from pist.game.routes import EndersBlenderReader
from pist.game.saves import SaveReader
from pist.local_data import LocalDataStore
from pist.secrets import CredentialStore
from pist.settings import PistSettings, SettingsStore
from pist.sheet_report import field_coverage_report
from pist.smartsheet import InspectionReport, TencentSmartSheetClient, extract_file_id
from pist.ui.entities.audit import review_entity_audit
from pist.ui.mods.browser import browse_mods

INSPECT_DIR = Path('.pist/inspect')
MOD_REPORT_DIR = Path('.pist/mods')

type SettingValue = Path | str | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='pist')
    subcommands = parser.add_subparsers(dest='command', required=True)

    credentials = subcommands.add_parser(
        'credentials', help='Manage local application credentials.'
    )
    credentials_subcommands = credentials.add_subparsers(dest='credentials_command', required=True)
    credentials_subcommands.add_parser(
        'set', help='Store direct Tencent Docs credentials securely.'
    )

    settings = subcommands.add_parser('settings', help='Manage local presentation settings.')
    settings_subcommands = settings.add_subparsers(dest='settings_command', required=True)
    settings_subcommands.add_parser('show', help='Print current local settings.')
    dialog_languages = settings_subcommands.add_parser(
        'dialog-languages', help='Set Dialog language preference order.'
    )
    dialog_languages.add_argument(
        'languages', nargs='+', help='Language codes, for example zh-cn en.'
    )
    for name, help_text, value_name, value_type in (
        (
            'game-dir',
            'Set or clear the default Game installation directory.',
            'directory',
            Path,
        ),
        ('smartsheet-url', 'Set or clear the default Tencent Docs Smart Sheet URL.', 'url', str),
    ):
        setting = settings_subcommands.add_parser(name, help=help_text)
        setting_subcommands = setting.add_subparsers(dest='settings_action', required=True)
        set_value = setting_subcommands.add_parser('set', help=f'Set the default {value_name}.')
        set_value.add_argument(value_name, type=value_type)
        setting_subcommands.add_parser('clear', help=f'Clear the default {value_name}.')

    sheet = subcommands.add_parser('sheet', help='Read Tencent Docs Smart Sheet metadata.')
    sheet_subcommands = sheet.add_subparsers(dest='sheet_command', required=True)
    inspect = sheet_subcommands.add_parser(
        'inspect', help='Save raw sheet metadata and print a short summary.'
    )
    inspect.add_argument('source', nargs='?', help='Smart Sheet URL or file ID.')
    inspect.add_argument(
        '--record-limit', type=int, default=3, help='Sample records per sub-sheet.'
    )
    inspect.add_argument(
        '--output-dir',
        type=Path,
        default=INSPECT_DIR,
        help='Ignored directory for UTF-8 raw inspection JSON.',
    )
    report = sheet_subcommands.add_parser(
        'report', help='Create a field-coverage Markdown report from raw inspection JSON.'
    )
    report.add_argument('input_path', type=Path, help='Raw JSON created by pist sheet inspect.')
    report.add_argument(
        '--output-path', type=Path, help='Markdown output path; defaults beside input.'
    )

    record = subcommands.add_parser('record', help='Manage saved local map records.')
    record_subcommands = record.add_subparsers(dest='record_command', required=True)
    sync = record_subcommands.add_parser(
        'sync', help='Synchronize one saved local record to the main table.'
    )
    sync.add_argument(
        'record_id', type=int, help='Local record identifier shown after saving a record.'
    )
    mode = sync.add_mutually_exclusive_group(required=True)
    mode.add_argument('--add', dest='update', action='store_false', help='Add a new record.')
    mode.add_argument(
        '--update', dest='update', action='store_true', help='Update one matching record.'
    )
    sync.add_argument('--sheet-source', help='Smart Sheet URL or file ID; defaults to settings.')
    mods = subcommands.add_parser('mods', help='Inspect locally enabled Mods.')
    mods_subcommands = mods.add_subparsers(dest='mods_command', required=True)
    scan = mods_subcommands.add_parser('scan', help='Save an offline enabled-Mod inventory.')
    scan.add_argument('--game-dir', type=Path, help='Game installation directory.')
    scan.add_argument(
        '--output-dir',
        type=Path,
        default=MOD_REPORT_DIR,
        help='Ignored directory for UTF-8 Mod scan JSON.',
    )
    browse = mods_subcommands.add_parser('browse', help='Browse enabled Mods in a terminal UI.')
    browse.add_argument('--game-dir', type=Path, help='Game installation directory.')
    browse.add_argument(
        '--save-slot', type=int, default=0, help='Show native stats from this save slot.'
    )
    for command in (scan, browse):
        command.add_argument(
            '--whitelist',
            type=Path,
            help='Everest --whitelist file; relative paths resolve under Mods.',
        )
        command.add_argument(
            '--blacklist',
            dest='temporary_blacklist',
            type=Path,
            help='Everest --blacklist file; relative paths resolve under Mods.',
        )
        command.add_argument(
            '--whitelist-full-override',
            action='store_true',
            help='Match Everest WhitelistFullOverride behavior.',
        )

    entities = subcommands.add_parser('entities', help='Manage map entity classification rules.')
    entities_subcommands = entities.add_subparsers(dest='entities_command', required=True)
    audit = entities_subcommands.add_parser(
        'audit', help='Review one ignored entity audit report in a terminal UI.'
    )
    audit.add_argument(
        'input_path',
        type=Path,
        nargs='?',
        help='Optional JSON entity audit report; defaults to the latest import.',
    )
    audit.add_argument(
        '--game-dir', type=Path, help='Game installation directory for map progress sort.'
    )
    return parser


async def set_credentials(store: CredentialStore) -> None:
    client_id = input('Client ID: ').strip()
    access_token = getpass('Access Token (input hidden): ').strip()
    open_id = input('Open ID: ').strip()
    if not client_id or not access_token or not open_id:
        raise ValueError('Client ID, Access Token, and Open ID cannot be empty.')
    await store.save_credentials(client_id, access_token, open_id)
    print('Tencent Docs credentials were saved to Windows Credential Manager.')


def update_settings(store: SettingsStore, **changes: object) -> PistSettings:
    settings = PistSettings.model_validate({**store.load().model_dump(), **changes})
    store.save(settings)
    return settings


def set_dialog_languages(store: SettingsStore, languages: list[str]) -> None:
    preferences = tuple(lang.casefold() for lang in languages if lang.strip())
    if not preferences:
        raise ValueError('At least one Dialog language code is required.')
    update_settings(store, dialog_languages=preferences)
    print(f'Dialog language preference saved: {", ".join(preferences)}')


def setting_value(args: argparse.Namespace) -> SettingValue:
    return getattr(args, 'directory', None) or getattr(args, 'url', None)


def set_default_setting(store: SettingsStore, key: str, value: SettingValue) -> None:
    update_settings(store, **{key: value})
    action = 'cleared' if value is None else 'saved'
    print(f'Default {key} {action}.')


def default_game_dir(game_dir: Path | None, settings: PistSettings) -> Path:
    if game_dir is not None:
        return game_dir
    if settings.game_dir is not None:
        return settings.game_dir
    raise ValueError('Specify --game-dir or configure one with pist settings game-dir set.')


def default_sheet_source(source: str | None, settings: PistSettings) -> str:
    if source is not None:
        return source
    if settings.smartsheet_url is not None:
        return settings.smartsheet_url
    raise ValueError(
        'Specify a Smart Sheet URL or configure one with pist settings smartsheet-url set.'
    )


def inspection_summary(result: InspectionReport) -> dict[str, object]:
    summaries = [
        {
            'sheet_id': inspection.sheet.sheet_id,
            'title': inspection.sheet.title,
            'field_count': inspection.fields.total,
            'view_count': inspection.views.total,
            'record_count': inspection.records.total,
        }
        for inspection in result.sheets
    ]
    return {
        'file_id': result.file_id,
        'sheet_count': len(result.sheets),
        'sheets': summaries,
    }


async def inspect_sheet(
    store: CredentialStore,
    source: str,
    record_limit: int,
    output_dir: Path,
) -> None:
    if record_limit < 1 or record_limit > 100:
        raise ValueError('--record-limit must be between 1 and 100.')
    client = TencentSmartSheetClient(store)
    result = await client.inspect(extract_file_id(source), record_limit=record_limit)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%SZ')
    output_path = output_dir / f'inspect-{timestamp}.json'
    output_path.write_text(result.model_dump_json(indent=2), encoding='utf-8')
    print(f'Raw UTF-8 inspection saved to: {output_path}')
    print(json.dumps(inspection_summary(result), ensure_ascii=False, indent=2))


async def sync_saved_record(
    store: CredentialStore, source: str, record_id: int, *, update: bool
) -> None:
    """Synchronize one local record using an explicit add or update operation."""
    record = LocalDataStore().load_record(record_id)
    client = TencentSmartSheetClient(store)
    remote_record_id = await client.sync_record(extract_file_id(source), record, update=update)
    action = '更新' if update else '新增'
    print(f'已{action}表格记录：{remote_record_id}')


def create_sheet_report(input_path: Path, output_path: Path | None) -> Path:
    """Create a Markdown field-coverage report without accessing the network."""
    result = InspectionReport.model_validate_json(input_path.read_text(encoding='utf-8'))
    if output_path is None:
        output_path = input_path.with_name(f'{input_path.stem}-fields.md')
    output_path.write_text(field_coverage_report(result), encoding='utf-8')
    return output_path


def mod_scan_summary(result: ModScanReport) -> dict[str, object]:
    return {
        'mods_dir': result.mods_dir,
        'enabled_mod_count': len(result.mods),
        'disabled_candidate_count': len(result.disabled_filenames),
        'warning_count': len(result.warnings),
        'warnings': [warning.model_dump() for warning in result.warnings],
        'map_mod_count': sum(bool(mod.map_files) for mod in result.mods),
        'collab_mod_count': sum(mod.collab_id is not None for mod in result.mods),
        'sample': [
            {
                'metadata_name': mod.metadata_name,
                'filename': mod.filename,
                'collab_id': mod.collab_id,
                'map_file_count': len(mod.map_files),
                'localized_map_count': sum(bool(map_info.names) for map_info in mod.maps),
            }
            for mod in result.mods[:10]
        ],
    }


async def scan_mods(
    game_dir: Path,
    output_dir: Path,
    *,
    whitelist_path: Path | None,
    temporary_blacklist_path: Path | None,
    whitelist_full_override: bool,
) -> None:
    result = ModScanner(
        game_dir,
        whitelist_path=whitelist_path,
        temporary_blacklist_path=temporary_blacklist_path,
        whitelist_full_override=whitelist_full_override,
    ).scan()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%SZ')
    output_path = output_dir / f'mods-{timestamp}.json'
    output_path.write_text(result.model_dump_json(indent=2), encoding='utf-8')
    print(f'Raw UTF-8 Mod inventory saved to: {output_path}')
    print(json.dumps(mod_scan_summary(result), ensure_ascii=False, indent=2))


def latest_inspection_report() -> InspectionReport | None:
    """Load the newest valid local Sheet inspection, when one is available."""
    paths = sorted(INSPECT_DIR.glob('inspect-*.json'), reverse=True)
    if not paths:
        return None
    try:
        return InspectionReport.model_validate_json(paths[0].read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError(f'Invalid latest Sheet inspection: {paths[0]!r}') from error


async def browse_enabled_mods(
    game_dir: Path,
    save_slot_number: int,
    *,
    sheet_client: TencentSmartSheetClient,
    sheet_source: str | None,
    whitelist_path: Path | None,
    temporary_blacklist_path: Path | None,
    whitelist_full_override: bool,
) -> None:
    scanner = ModScanner(
        game_dir,
        whitelist_path=whitelist_path,
        temporary_blacklist_path=temporary_blacklist_path,
        whitelist_full_override=whitelist_full_override,
    )
    preparation = scanner.prepare()
    with Progress(
        TextColumn('[progress.description]{task.description}'),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn('{task.fields[filename]}'),
        transient=True,
    ) as startup_progress:
        total_task_id = startup_progress.add_task('准备 Mod 列表', total=2, filename='')
        scan_task_id = startup_progress.add_task(
            '正在扫描已启用 Mod', total=len(preparation.candidates), filename=''
        )
        scanned_mods: list[InstalledMod] = []
        for candidate in preparation.candidates:
            startup_progress.update(scan_task_id, filename=candidate.name)
            if mod := scanner.scan_mod(candidate):
                scanned_mods.append(mod)
            startup_progress.advance(scan_task_id)
        startup_progress.remove_task(scan_task_id)
        startup_progress.advance(total_task_id)

        scan_task_id = startup_progress.add_task(
            '正在读取已禁用 Mod', total=len(preparation.disabled_candidates), filename=''
        )
        disabled_mods: list[DisabledMod] = []
        for candidate in preparation.disabled_candidates:
            startup_progress.update(scan_task_id, filename=candidate.name)
            disabled_mods.append(scanner.scan_disabled_mod(candidate))
            startup_progress.advance(scan_task_id)
        startup_progress.remove_task(scan_task_id)
        startup_progress.advance(total_task_id)
    result = scanner.build_report(scanned_mods, disabled_mods)
    save_reader = SaveReader(game_dir)
    save_slot = save_reader.load(save_slot_number)
    await browse_mods(
        result,
        save_reader=save_reader,
        save_slot=save_slot,
        route_reader=EndersBlenderReader(game_dir),
        inspection_report=latest_inspection_report(),
        sheet_client=sheet_client if sheet_source is not None else None,
        sheet_source=sheet_source,
    )


async def async_main(args: argparse.Namespace) -> None:
    store = CredentialStore()
    settings_store = SettingsStore()
    if args.command == 'credentials' and args.credentials_command == 'set':
        await set_credentials(store)
    elif args.command == 'settings' and args.settings_command == 'dialog-languages':
        set_dialog_languages(settings_store, args.languages)
    elif args.command == 'settings' and args.settings_command == 'show':
        print(settings_store.load().model_dump_json(indent=2))
    elif args.command == 'settings' and args.settings_command in {'game-dir', 'smartsheet-url'}:
        key = args.settings_command.replace('-', '_')
        set_default_setting(
            settings_store,
            key,
            setting_value(args) if args.settings_action == 'set' else None,
        )
    elif args.command == 'sheet' and args.sheet_command == 'inspect':
        await inspect_sheet(
            store,
            default_sheet_source(args.source, settings_store.load()),
            args.record_limit,
            args.output_dir,
        )
    elif args.command == 'sheet' and args.sheet_command == 'report':
        output_path = create_sheet_report(args.input_path, args.output_path)
        print(f'Field-coverage report saved to: {output_path}')
    elif args.command == 'record' and args.record_command == 'sync':
        await sync_saved_record(
            store,
            default_sheet_source(args.sheet_source, settings_store.load()),
            args.record_id,
            update=args.update,
        )
    elif args.command == 'mods' and args.mods_command == 'scan':
        await scan_mods(
            default_game_dir(args.game_dir, settings_store.load()),
            args.output_dir,
            whitelist_path=args.whitelist,
            temporary_blacklist_path=args.temporary_blacklist,
            whitelist_full_override=args.whitelist_full_override,
        )
    elif args.command == 'mods' and args.mods_command == 'browse':
        settings = settings_store.load()
        await browse_enabled_mods(
            default_game_dir(args.game_dir, settings),
            args.save_slot,
            sheet_client=TencentSmartSheetClient(store),
            sheet_source=settings.smartsheet_url,
            whitelist_path=args.whitelist,
            temporary_blacklist_path=args.temporary_blacklist,
            whitelist_full_override=args.whitelist_full_override,
        )
    elif args.command == 'entities' and args.entities_command == 'audit':
        settings = settings_store.load()
        game_dir = args.game_dir or settings.game_dir
        await review_entity_audit(
            args.input_path,
            save_reader=SaveReader(game_dir) if game_dir is not None else None,
            game_dir=game_dir,
        )
    else:
        raise ValueError('Unknown command.')


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(async_main(args))
    except (RuntimeError, TypeError, ValueError) as error:
        print(f'Error: {error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
