"""Opaque slot groups use native file operations on synthetic directories only."""

import errno
import os
import shutil
from pathlib import Path
from typing import Never

import pytest

from berries.game.levels import LevelSide
from berries.game.saves import (
    SaveSlot,
    clear_mod_sessions,
    copy_mod_sessions,
    mod_session_path,
    move_mod_sessions,
)
from berries.game.saves.files.main import MainSave
from berries.game.saves.operations import move_map_progress
from berries.game.saves.paths import save_slot_path
from berries.game.saves.progress import CampaignProgress, LevelProgress
from test_support.save_docs import parsed_session


def _group(slot: SaveSlot, values: dict[str, bytes]) -> None:
    slot.saves_dir.mkdir(parents=True, exist_ok=True)
    for name, data in values.items():
        mod_session_path(slot.saves_dir, slot.number, name).write_bytes(data)


def _read(slot: SaveSlot) -> dict[str, bytes]:
    return {name: path.read_bytes() for name, path in slot.mod_session_paths().items()}


@pytest.mark.parametrize('number, stem', [(0, '0'), (6, '6'), (-1, 'debug')])
def test_discovery_is_slot_specific_nonrecursive_and_does_not_decode(
    tmp_path: Path,
    number: int,
    stem: str,
) -> None:
    slot = SaveSlot(tmp_path, number)
    values = {'CommunalHelper': b'\xff\x00\xfe', 'Other-Mod.2': b'', '01': b'001'}
    _group(slot, values)
    noise = (
        f'{stem}.celeste',
        f'{stem}-modsave-CommunalHelper.celeste',
        f'{stem}-modsavedata.celeste',
        f'{stem}-modsession-CommunalHelper.celeste.bak',
        f'{stem}-modsession-CommunalHelper.celeste.tmp',
        f'{stem}-modsession-.celeste',
        '01-modsession-CommunalHelper.celeste',
        '16-modsession-CommunalHelper.celeste',
    )
    for name in noise:
        (tmp_path / name).write_bytes(b'not a Mod session')
    directory = tmp_path / f'{stem}-modsession-Directory.celeste'
    directory.mkdir()
    (directory / f'{stem}-modsession-Nested.celeste').write_bytes(b'nested')
    paths = slot.mod_session_paths()
    assert paths == {
        name: tmp_path / f'{stem}-modsession-{name}.celeste' for name in sorted(values)
    }
    assert _read(slot) == values
    assert mod_session_path(tmp_path, number, 'Other-Mod.2') == paths['Other-Mod.2']
    paths.clear()
    assert len(slot.mod_session_paths()) == 3


def test_discovery_does_not_require_a_main_file_or_load_any_document(tmp_path: Path) -> None:
    slot = SaveSlot(tmp_path, 8)
    assert slot.mod_session_paths() == {}
    _group(slot, {'Opaque': b'not YAML or XML'})
    assert not slot
    assert _read(slot) == {'Opaque': b'not YAML or XML'}


@pytest.mark.parametrize('move', (False, True))
@pytest.mark.parametrize('separate_directories', (False, True))
@pytest.mark.parametrize('debug', (False, True))
def test_group_replacement_preserves_bytes_and_only_removes_target_leftovers(
    tmp_path: Path,
    move: bool,
    separate_directories: bool,
    debug: bool,
) -> None:
    source = SaveSlot(tmp_path / 'source', -1 if debug else 1)
    target = SaveSlot(
        tmp_path / 'target' if separate_directories else source.saves_dir,
        source.number if separate_directories else 6,
    )
    donor = {'A': b'\x00\xffopaque', 'B': b'', 'Mod.with-hyphen': b'\xef\xbb\xbftext\r\n'}
    _group(source, donor)
    _group(target, {'A': b'old', 'TargetOnly': b'stale'})
    untouched = {
        source.saves_dir / f'{source.number}-modsave-A.celeste': b'persistent',
        source.saves_dir / '2-modsession-A.celeste': b'another slot',
        target.saves_dir / '6-modsession-Backup.celeste.bak': b'backup',
        target.saves_dir / '6.celeste': b'main',
        target.saves_dir / '6-modsave-CollabUtils2.celeste': b'collab',
    }
    for path, data in untouched.items():
        path.write_bytes(data)
    action = move_mod_sessions if move else copy_mod_sessions
    action(source, target)
    assert _read(target) == donor
    assert _read(source) == ({} if move else donor)
    for path, data in untouched.items():
        assert path.read_bytes() == data


@pytest.mark.parametrize('move', (False, True))
def test_empty_source_group_clears_target_but_missing_source_directory_does_not(
    tmp_path: Path,
    move: bool,
) -> None:
    source, target = SaveSlot(tmp_path / 'source', 0), SaveSlot(tmp_path / 'target', 6)
    _group(target, {'Old': b'data'})
    action = move_mod_sessions if move else copy_mod_sessions
    with pytest.raises(FileNotFoundError):
        action(source, target)
    assert _read(target) == {'Old': b'data'}
    source.saves_dir.mkdir()
    action(source, target)
    assert _read(target) == {}


@pytest.mark.parametrize('move', (False, True))
def test_transfer_creates_target_directory_without_a_main_save(tmp_path: Path, move: bool) -> None:
    source = SaveSlot(tmp_path / 'source', 0)
    target = SaveSlot(tmp_path / 'new' / 'Saves', 6)
    _group(source, {'M': b'raw'})
    action = move_mod_sessions if move else copy_mod_sessions
    action(source, target)
    assert _read(target) == {'M': b'raw'} and not target


def test_clear_only_removes_the_selected_group(tmp_path: Path) -> None:
    selected, other, debug = SaveSlot(tmp_path, 6), SaveSlot(tmp_path, 16), SaveSlot(tmp_path, -1)
    for slot in (selected, other, debug):
        _group(slot, {'Mod': b'data'})
    main = tmp_path / '6.celeste'
    main.write_bytes(b'main')
    clear_mod_sessions(selected)
    assert _read(selected) == {}
    assert _read(other) == _read(debug) == {'Mod': b'data'}
    assert main.read_bytes() == b'main'
    clear_mod_sessions(selected)


@pytest.mark.parametrize('move', (False, True))
def test_same_slot_and_relative_alias_are_rejected_without_changes(
    tmp_path: Path,
    move: bool,
) -> None:
    source = SaveSlot(tmp_path, 0)
    _group(source, {'M': b'keep'})
    (tmp_path / 'child').mkdir()
    target = SaveSlot(tmp_path / 'child' / '..', 0)
    action = move_mod_sessions if move else copy_mod_sessions
    with pytest.raises(ValueError, match='must be distinct'):
        action(source, target)
    assert _read(source) == {'M': b'keep'}


@pytest.mark.parametrize('move', (False, True))
def test_destination_directory_cannot_redirect_a_move_inside_it(
    tmp_path: Path,
    move: bool,
) -> None:
    source, target = SaveSlot(tmp_path, 0), SaveSlot(tmp_path, 6)
    _group(source, {'M': b'keep'})
    collision = mod_session_path(target.saves_dir, target.number, 'M')
    collision.mkdir()
    action = move_mod_sessions if move else copy_mod_sessions
    with pytest.raises(IsADirectoryError):
        action(source, target)
    assert _read(source) == {'M': b'keep'} and not list(collision.iterdir())


@pytest.mark.parametrize('move', (False, True))
def test_native_error_keeps_completed_operations_and_does_not_retry_or_roll_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    move: bool,
) -> None:
    source, target = SaveSlot(tmp_path / 'source', 0), SaveSlot(tmp_path / 'target', 6)
    _group(source, {'A': b'new A', 'B': b'new B'})
    _group(target, {'A': b'old A', 'B': b'old B', 'Stale': b'old leftover'})
    original = shutil.move if move else shutil.copyfile
    attempts: list[str] = []

    def fail_second(source: Path, destination: Path) -> None:
        attempts.append(source.name)
        if source.name == '0-modsession-B.celeste':
            raise PermissionError('synthetic I/O failure')
        original(source, destination)

    monkeypatch.setattr(shutil, 'move' if move else 'copyfile', fail_second)
    action = move_mod_sessions if move else copy_mod_sessions
    with pytest.raises(PermissionError, match='synthetic I/O failure'):
        action(source, target)
    assert attempts == ['0-modsession-A.celeste', '0-modsession-B.celeste']
    assert _read(target) == {'A': b'new A', 'B': b'old B', 'Stale': b'old leftover'}
    assert _read(source) == ({'B': b'new B'} if move else {'A': b'new A', 'B': b'new B'})


@pytest.mark.parametrize('move', (False, True))
def test_cleanup_error_is_not_hidden_or_rolled_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    move: bool,
) -> None:
    source, target = SaveSlot(tmp_path / 'source', 0), SaveSlot(tmp_path / 'target', 6)
    _group(source, {'A': b'new'})
    _group(target, {'Stale': b'old'})
    original = Path.unlink

    def refuse(path: Path, *, missing_ok: bool = False) -> None:
        if path.name == '6-modsession-Stale.celeste':
            raise PermissionError('synthetic cleanup failure')
        original(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, 'unlink', refuse)
    action = move_mod_sessions if move else copy_mod_sessions
    with pytest.raises(PermissionError, match='synthetic cleanup failure'):
        action(source, target)
    assert _read(target) == {'A': b'new', 'Stale': b'old'}
    assert _read(source) == ({} if move else {'A': b'new'})


@pytest.mark.parametrize('name', ('', '.', '..', '../outside', 'nested/Mod', 'bad\0name'))
def test_locator_rejects_names_that_are_not_one_component(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        mod_session_path(tmp_path, 0, name)


def test_windows_case_alias_is_not_deleted_as_a_target_only_module(tmp_path: Path) -> None:
    if os.name != 'nt':
        pytest.skip('Windows filename case matching.')
    source, target = SaveSlot(tmp_path, 0), SaveSlot(tmp_path, 6)
    _group(source, {'MyMod': b'new'})
    old = tmp_path / '6-MODSESSION-mymod.CELESTE'
    old.write_bytes(b'old')
    copy_mod_sessions(source, target)
    assert old.read_bytes() == b'new'
    assert len(target.mod_session_paths()) == 1


def test_native_move_cross_device_fallback_preserves_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target = SaveSlot(tmp_path / 'source', 0), SaveSlot(tmp_path / 'target', 6)
    _group(source, {'M': b'\xff\x00no decoding'})
    _group(target, {'Stale': b'old'})

    def cross_device(_source: Path, _destination: Path) -> Never:
        raise OSError(errno.EXDEV, 'synthetic cross-device rename')

    monkeypatch.setattr(os, 'rename', cross_device)
    move_mod_sessions(source, target)
    assert _read(source) == {}
    assert _read(target) == {'M': b'\xff\x00no decoding'}


def test_directory_symlink_alias_cannot_move_a_group_to_itself(tmp_path: Path) -> None:
    source = SaveSlot(tmp_path / 'source', 0)
    _group(source, {'M': b'keep'})
    alias = tmp_path / 'alias'
    try:
        alias.symlink_to(source.saves_dir, target_is_directory=True)
    except OSError:
        pytest.skip('Creating directory symlinks is unavailable.')
    with pytest.raises(ValueError, match='must be distinct'):
        move_mod_sessions(source, SaveSlot(alias, 0))
    assert _read(source) == {'M': b'keep'}


@pytest.mark.parametrize(
    'source_matches, target_matches', [(True, False), (False, True), (False, False)]
)
def test_caller_coordinates_only_the_selected_active_session_and_writes_documents_explicitly(
    tmp_path: Path,
    source_matches: bool,
    target_matches: bool,
) -> None:
    source_slot, target_slot = SaveSlot(tmp_path, 0), SaveSlot(tmp_path, 6)
    _group(source_slot, {'AnyMod': b'opaque source'})
    _group(target_slot, {'OldMod': b'opaque target'})
    source, target = MainSave(), MainSave()
    sid, side = 'Pack/Map', LevelSide.A
    for save in (source, target):
        save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress(sid)]))
    source.session = parsed_session(sid if source_matches else 'Other/Map', side)
    target.session = parsed_session(sid if target_matches else 'Elsewhere/Map', side)
    source_path = save_slot_path(tmp_path, source_slot.number)
    target_path = save_slot_path(tmp_path, target_slot.number)
    source.write(source_path)
    target.write(target_path)
    source, target = source_slot.main(), target_slot.main()
    original_documents = source_path.read_bytes(), target_path.read_bytes()
    source_current, target_current = source.current_map, target.current_map
    move_map_progress(
        source,
        target,
        sid,
        side,
        mode='merge',
        poem_id=None,
        goldenberry_ids={},
        discard_current_session=True,
    )
    if source_current == (sid, side):
        move_mod_sessions(source_slot, target_slot)
    elif target_current == (sid, side):
        clear_mod_sessions(target_slot)
    assert (source_path.read_bytes(), target_path.read_bytes()) == original_documents
    assert _read(source_slot) == ({} if source_matches else {'AnyMod': b'opaque source'})
    assert _read(target_slot) == (
        {'AnyMod': b'opaque source'}
        if source_matches
        else {}
        if target_matches
        else {'OldMod': b'opaque target'}
    )
    source.write(source_path)
    target.write(target_path)
    assert source_slot.main().current_map == source.current_map
    assert target_slot.main().current_map == target.current_map
