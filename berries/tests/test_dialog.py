import warnings
from collections.abc import Generator
from pathlib import Path
from zipfile import ZipFile

import pytest

from berries.game import dialog, vanilla
from berries.game.content import ContentPath, DirContentEntry, ZipContentEntry
from berries.game.dialog import (
    dialog_key_for_campaign_dir,
    dialog_key_for_map_file,
    parse_dialog,
)
from berries.game.localization import default_campaign_name, default_map_name
from berries.game.maps import campaign_dir_for_map_file, split_map_side_suffix


def test_merge_dialogs_preserves_sources_and_applies_case_insensitive_overrides() -> None:
    base = {'en': {'TITLE': 'Base', 'base_only': 'Kept'}}
    first = {'en': {'title': 'First'}, 'ja': {'TITLE': 'Japanese'}}
    last = {'en': {'Title': 'Last'}}
    merged = dialog.merge_dialogs(iter((first, last)), base_dialogs=base)
    assert merged['en']['TITLE'] == 'Last'
    assert merged['en']['base_only'] == 'Kept'
    assert merged['ja']['title'] == 'Japanese'
    merged['en']['base_only'] = 'Changed'
    assert base['en']['base_only'] == 'Kept'
    assert first['en']['title'] == 'First'
    assert last['en']['Title'] == 'Last'
    assert dialog.merge_dialogs(()) == {}


def test_read_dialogs_merges_selected_files_without_cross_file_insertion(tmp_path: Path) -> None:
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    ignored = tmp_path / 'ignored'
    first.mkdir()
    second.mkdir()
    ignored.mkdir()
    (first / 'English.txt').write_text('BASE=First\nTITLE={+BASE}\n', encoding='utf-8')
    (second / 'english.txt').write_text('BASE=Second\nOTHER={+TITLE}\n', encoding='utf-8')
    (ignored / 'English.TXT').write_text('BASE=Ignored\n', encoding='utf-8')
    (second / 'Unknown.txt').write_text('BASE=Ignored\n', encoding='utf-8')
    (second / 'Japanese.txt').mkdir()
    with DirContentEntry(tmp_path) as root:
        result = dialog.merge_dialogs(
            dialog.read_dialogs(root / name) for name in ('first', 'second', 'ignored')
        )
    assert dict(result['en']) == {'base': 'Second', 'title': 'First', 'other': '[XXX]'}


def test_read_dialogs_raises_by_default_and_reports_errors_to_callback(tmp_path: Path) -> None:
    (tmp_path / 'English.txt').write_bytes(bytes((255,)))
    with DirContentEntry(tmp_path) as root:
        with pytest.raises(dialog.DialogReadError) as raised:
            dialog.read_dialogs(root)
        error = raised.value
        assert error.root == tmp_path
        assert error.file_path.as_posix() == 'English.txt'
        assert isinstance(error.cause, UnicodeDecodeError)
        assert error.__cause__ is error.cause
        failures: list[dialog.DialogReadError] = []
        assert dialog.read_dialogs(root, on_error=failures.append) == {}
        assert len(failures) == 1

        def abort(error: dialog.DialogReadError) -> None:
            raise error

        with pytest.raises(dialog.DialogReadError):
            dialog.read_dialogs(root, on_error=abort)


def test_vanilla_dialogs_ignore_unknown_files_and_report_decoding_errors(tmp_path: Path) -> None:
    assert vanilla.load_dialogs(tmp_path) == {}
    directory = tmp_path / 'Content' / 'Dialog'
    directory.mkdir(parents=True)
    (directory / 'Unknown.txt').write_text('TITLE=Ignored\n', encoding='utf-8')
    (directory / 'English.txt').write_bytes(bytes((255,)))
    with pytest.raises(dialog.DialogReadError):
        vanilla.load_dialogs(tmp_path)
    failures: list[dialog.DialogReadError] = []
    assert vanilla.load_dialogs(tmp_path, on_error=failures.append) == {}
    assert len(failures) == 1


@pytest.mark.parametrize('archive', (False, True))
def test_dialog_failures_preserve_successful_files_and_override_order(
    tmp_path: Path, archive: bool
) -> None:
    files = {
        'first/English.txt': b'TITLE=First\nKEPT=Value\n',
        'bad/english.txt': b'TITLE=\xff',
        'bad/French.txt': b'TITLE={+TITLE}\n',
        'last/English.txt': b'TITLE=Last\n',
        'last/Japanese.txt': b'TITLE=Japanese\n',
    }
    if archive:
        source = tmp_path / 'example.zip'
        with ZipFile(source, 'w') as zip_file:
            for path, data in files.items():
                zip_file.writestr(path, data)
        root = ZipContentEntry(source)
    else:
        source = tmp_path
        for path, data in files.items():
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        root = DirContentEntry(source)
    failures: list[dialog.DialogReadError] = []
    with root:
        result = dialog.merge_dialogs(
            dialog.read_dialogs(root / name, on_error=failures.append)
            for name in ('first', 'bad', 'last')
        )
    assert dict(result['en']) == {'title': 'Last', 'kept': 'Value'}
    assert dict(result['ja']) == {'title': 'Japanese'}
    assert 'fr' not in result
    assert {failure.file_path.as_posix() for failure in failures} == {
        'bad/english.txt',
        'bad/French.txt',
    }
    assert all(failure.root == source for failure in failures)
    if archive:
        source.unlink()


def test_dialog_encoding_is_explicit(tmp_path: Path) -> None:
    (tmp_path / 'English.txt').write_bytes(b'TITLE=Caf\xe9\n')
    with DirContentEntry(tmp_path) as root:
        result = dialog.read_dialogs(root, encoding='cp1252')
        assert result['en']['TITLE'] == 'Café'
        with pytest.raises(LookupError):
            dialog.read_dialogs(root, encoding='invalid-codec')


@pytest.mark.parametrize('failure', (RuntimeError, ValueError))
def test_dialog_callback_preserves_other_warnings_and_does_not_hide_programming_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    for name in ('English.txt', 'French.txt', 'Japanese.txt'):
        (tmp_path / name).touch()

    def read(entry: DirContentEntry) -> bytes:
        if entry.name == 'English.txt':
            raise PermissionError('Denied')
        if entry.name == 'French.txt':
            warnings.warn('Unrelated', UserWarning, stacklevel=2)
            return b'TITLE=French'
        raise failure('Programming error')

    monkeypatch.setattr(DirContentEntry, 'read_bytes', read)
    failures: list[dialog.DialogReadError] = []
    with (
        DirContentEntry(tmp_path) as root,
        pytest.warns(UserWarning, match='Unrelated'),
        pytest.raises(failure, match='Programming error'),
    ):
        dialog.read_dialogs(root, on_error=failures.append)
    assert len(failures) == 1
    assert isinstance(failures[0].cause, PermissionError)


@pytest.mark.parametrize('archive', (False, True))
@pytest.mark.parametrize('dirname', ('Dialog', 'dialog', 'Custom'))
def test_dialog_strict_checks_directory_name_without_changing_error_policy(
    tmp_path: Path, archive: bool, dirname: str
) -> None:
    if archive:
        source = tmp_path / 'example.zip'
        with ZipFile(source, 'w') as zip_file:
            zip_file.writestr(f'{dirname}/English.txt', b'\xff')
        root = ZipContentEntry(source)
    else:
        directory = tmp_path / dirname
        directory.mkdir()
        (directory / 'English.txt').write_bytes(b'\xff')
        root = DirContentEntry(tmp_path)
    with root:
        directory_entry = root / dirname
        failures: list[dialog.DialogReadError] = []
        assert dialog.read_dialogs(directory_entry, on_error=failures.append) == {}
        assert len(failures) == 1
        if dirname != 'Dialog':
            with pytest.raises(ValueError, match='Expected a directory named'):
                dialog.read_dialogs(directory_entry, strict=True)
        else:
            assert dialog.read_dialogs(directory_entry, strict=True, on_error=failures.append) == {}
        with pytest.raises(dialog.DialogReadError):
            dialog.read_dialogs(directory_entry)


def test_dialog_missing_directory_and_file_argument(tmp_path: Path) -> None:
    root = DirContentEntry(tmp_path)
    assert dialog.read_dialogs(root / 'Dialog', strict=True) == {}
    (tmp_path / 'Dialog').write_text('TITLE=Not a directory', encoding='utf-8')
    with pytest.raises(NotADirectoryError):
        dialog.read_dialogs(root / 'Dialog', strict=True)


def test_dialog_directory_enumeration_failure_preserves_already_read_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / 'English.txt').write_text('TITLE=Kept', encoding='utf-8')

    def entries(entry: DirContentEntry) -> Generator[DirContentEntry]:
        yield entry / 'English.txt'
        raise PermissionError('Cannot finish listing directory')

    monkeypatch.setattr(DirContentEntry, 'iterdir', entries)
    failures: list[dialog.DialogReadError] = []
    result = dialog.read_dialogs(DirContentEntry(tmp_path), on_error=failures.append)
    assert result['en']['TITLE'] == 'Kept'
    assert len(failures) == 1
    error = failures[0]
    assert error.root == tmp_path
    assert not error.file_path.parts
    assert isinstance(error.cause, PermissionError)
    with pytest.raises(dialog.DialogReadError):
        dialog.read_dialogs(DirContentEntry(tmp_path))


def test_dialog_parser_handles_comments_continuations_and_insertions() -> None:
    parsed = parse_dialog(
        '# ignored\nBASE=First\nSecond\nTITLE={+BASE} {#f00}Name{n}\n[portrait]Shown\n'
    )
    assert parsed['BASE'] == 'First\nSecond'
    assert parsed['base'] == 'First\nSecond'
    assert parsed['TITLE'] == 'First\nSecond Name\nShown'


def test_map_path_becomes_dialog_key() -> None:
    assert dialog_key_for_map_file(ContentPath('Maps/Author/Pack/Map.bin')) == 'Author_Pack_Map'
    assert (
        dialog_key_for_map_file(ContentPath('Maps/Author/Pack Name/Map-Name.bin'))
        == 'Author_Pack_Name_Map_Name'
    )
    assert split_map_side_suffix(ContentPath('Maps/Author/Pack/Map-B.bin')) == (
        ContentPath('Maps/Author/Pack/Map.bin'),
        'B',
    )
    assert split_map_side_suffix(ContentPath('Maps/Author/Pack/Map.bin')) == (
        ContentPath('Maps/Author/Pack/Map.bin'),
        None,
    )
    assert default_map_name(ContentPath('Maps/Author/Pack Name/Map.bin')) == 'Author_Pack_Name_Map'
    assert (
        default_map_name(ContentPath('Maps/Ferret/MicroMountain/map.bin'))
        == 'Ferret_Micro Mountain_map'
    )
    assert default_map_name(ContentPath('Maps/Ezel/7CC.bin')) == 'Ezel_7CC'
    assert default_map_name(ContentPath('Maps/RootMap.bin')) == 'Root Map'
    assert campaign_dir_for_map_file(ContentPath('Maps/Author/Pack/Map.bin')) == ContentPath(
        'Maps/Author/Pack'
    )
    info = ContentPath('Maps/Author/Pack/Map.bin')
    assert info.parent == ContentPath('Maps/Author/Pack')
    assert dialog_key_for_campaign_dir(ContentPath('Maps')) == ''
    assert dialog_key_for_campaign_dir(ContentPath('Maps/Author/Pack Name')) == 'Author_Pack_Name'


def test_dialog_key_uses_everests_exact_key_conversion() -> None:
    assert dialog.to_dialog_key('Author/Pack-Name+Map Name') == 'Author_Pack_Name_Map_Name'
    assert dialog.to_dialog_key('Author/Pack.Name\\地图') == 'Author_Pack.Name\\地图'
    assert dialog.to_dialog_key('') == ''
    assert (
        dialog_key_for_map_file(ContentPath('Maps/Author/Pack.Name/Map+Name.bin'))
        == 'Author_Pack.Name_Map_Name'
    )
    assert dialog_key_for_campaign_dir(ContentPath('Maps/Author/Pack.Name')) == 'Author_Pack.Name'


@pytest.mark.parametrize(
    'map_file',
    ('maps/Author/Pack/Map.bin', 'Maps/Author/Pack/Map.BIN'),
)
def test_dialog_map_path_requires_everests_exact_virtual_path_spelling(map_file: str) -> None:
    with pytest.raises(ValueError, match='Not a map file path'):
        dialog_key_for_map_file(ContentPath(map_file))


def test_dialog_map_side_suffix_is_case_sensitive() -> None:
    assert split_map_side_suffix(ContentPath('Maps/Author/Pack/Map-b.bin')) == (
        ContentPath('Maps/Author/Pack/Map-b.bin'),
        None,
    )


def test_campaign_dir_rejects_invalid_map_file() -> None:
    with pytest.raises(ValueError, match='Not a map file path'):
        campaign_dir_for_map_file(ContentPath('Content/Maps/Author/Pack/Map.bin'))
    with pytest.raises(ValueError, match='Not a campaign directory'):
        default_campaign_name(ContentPath('Content/Maps/Author/Pack'))
