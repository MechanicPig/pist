"""Slot locations and format selection, without eager companion loading."""

import os
from collections.abc import Iterator, Sequence
from io import BytesIO
from pathlib import Path
from typing import Self

import pytest

from berries.game.saves.directory import SavesDir
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.save import ModSave
from berries.game.saves.slot import SaveSlot
from test_support.save_docs import normal_save_xml


class TextSave(ModSave):
    mod_name = 'Example'

    def __init__(self) -> None:
        self.text = ''

    @classmethod
    def fromstring(cls, data: str | bytes, /) -> Self:
        result = cls()
        result.text = data.decode('utf-8') if isinstance(data, bytes) else data
        return result

    def tostring(self) -> str:
        return self.text


def test_directory_includes_holes_but_filters_only_on_request(tmp_path: Path) -> None:
    directory = SavesDir(tmp_path)
    assert len(directory.slots) == 3
    assert [slot.number for slot in directory.slots] == [0, 1, 2]
    for name in (
        '0.celeste',
        '2.celeste',
        'debug.celeste',
        '01.celeste',
        '-2.celeste',
        '١.celeste',
        '4-modsavedata.celeste',
        '5-modsave-Example.celeste',
        '6.celeste.bak',
    ):
        (tmp_path / name).write_bytes(b'corrupt is still an existing slot')
    assert len(directory.slots) == 3
    assert [slot.number for slot in directory.slots.iter(filter_empty=True)] == [0, 2]
    assert directory.slots[...].number == -1
    assert repr(directory.slots[...]) == 'SaveSlot(-1)'
    with pytest.raises(IndexError):
        directory.slots[100]
    assert len(directory.slots) == 3
    (tmp_path / '8.celeste').write_bytes(b'')
    assert len(directory.slots) == 3
    directory.slots.invalidate_cache()
    assert len(directory.slots) == 9
    assert [slot.number for slot in directory.slots.iter(filter_empty=True)] == [0, 2, 8]


def test_missing_directory_still_has_default_empty_positions(tmp_path: Path) -> None:
    directory = SavesDir(tmp_path / 'missing')
    assert len(directory.slots) == 3
    assert list(directory.slots.iter(filter_empty=True)) == []
    with pytest.raises(FileNotFoundError):
        directory.slots[0].main()


@pytest.mark.parametrize('number', (-2, -1))
def test_negative_numbers_are_not_positional_indexes(tmp_path: Path, number: int) -> None:
    with pytest.raises(IndexError):
        SavesDir(tmp_path).slots[number]


def test_bool_is_rejected_as_a_slot_number(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SaveSlot(tmp_path, True)
    with pytest.raises(ValueError):
        SavesDir(tmp_path).slots[True]


def test_slot_fields_are_readonly_and_do_not_open_files(tmp_path: Path) -> None:
    slot = SaveSlot(str(tmp_path), 6)
    assert slot.saves_dir == tmp_path
    assert slot.number == 6
    for name in ('number', 'saves_dir'):
        with pytest.raises(AttributeError):
            setattr(slot, name, None)


def test_formats_are_opened_independently_and_custom_mods_need_no_new_slot_fields(
    tmp_path: Path,
) -> None:
    directory = SavesDir(tmp_path)
    (tmp_path / '6.celeste').write_text(normal_save_xml('<SaveData/>'), encoding='utf-8')
    (tmp_path / '6-modsavedata.celeste').write_bytes(b'<ModSaveData/>')
    (tmp_path / '6-modsave-Example.celeste').write_bytes(b'original')
    slot = directory.slots[6]
    main = slot.main()
    backup = slot.backup()
    text = slot.mod(TextSave)
    assert main.needs_mod_recovery
    assert not backup.mods.campaigns
    assert text.text == 'original'
    text.text = 'memory only'
    assert (tmp_path / '6-modsave-Example.celeste').read_bytes() == b'original'
    text = slot.mod(TextSave)
    text.text = 'updated'
    text.write(tmp_path / '6-modsave-Example.celeste')
    assert (tmp_path / '6-modsave-Example.celeste').read_bytes() == b'updated'


def test_mod_forwards_positional_and_keyword_parser_arguments(tmp_path: Path) -> None:
    class EncodedSave(TextSave):
        def __init__(self, encoding: str, /, *, save_type: str) -> None:
            self.encoding = encoding
            self.label = save_type
            super().__init__()

        @classmethod
        def fromstring(
            cls, data: str | bytes, /, encoding: str = 'utf-8', *, save_type: str = ''
        ) -> Self:
            result = cls(encoding, save_type=save_type)
            result.text = data.decode(encoding) if isinstance(data, bytes) else data
            return result

    path = tmp_path / '6-modsave-Example.celeste'
    data = '你好'.encode('utf-16-le')
    path.write_bytes(data)
    save = SaveSlot(tmp_path, 6).mod(EncodedSave, 'utf-16-le', save_type='label')
    assert isinstance(save, EncodedSave)
    assert save.text == '你好' and save.encoding == 'utf-16-le' and save.label == 'label'
    assert path.read_bytes() == data


def test_mod_parser_errors_propagate_with_source_context(tmp_path: Path) -> None:
    error = ValueError('invalid option')

    class InvalidSave(TextSave):
        @classmethod
        def fromstring(cls, data: str | bytes, /, *, invalid: bool = False) -> Self:
            raise error

    path = tmp_path / '6-modsave-Example.celeste'
    path.write_bytes(b'input')
    with pytest.raises(ValueError) as caught:
        SaveSlot(tmp_path, 6).mod(InvalidSave, invalid=True)
    assert caught.value is error
    assert error.__notes__ == [f'While reading {path}.']
    with pytest.raises(TypeError):
        SaveSlot(tmp_path, 6).mod(TextSave, unknown=True)


def test_debug_slot_uses_platform_names(tmp_path: Path) -> None:
    (tmp_path / 'debug.celeste').write_text(normal_save_xml('<SaveData/>'), encoding='utf-8')
    (tmp_path / 'debug-modsavedata.celeste').write_bytes(b'<ModSaveData/>')
    (tmp_path / 'debug-modsave-Example.celeste').write_bytes(b'debug')
    slot = SavesDir(tmp_path).slots[...]
    main = slot.main()
    backup = slot.backup()
    mod = slot.mod(TextSave)
    assert main.needs_mod_recovery
    assert not backup.mods.campaigns
    assert mod.text == 'debug'


@pytest.mark.parametrize('name', ('', '.', '..', '../other', 'folder/file', '/absolute', 'a\x00b'))
def test_mod_names_cannot_escape_the_slot_directory(tmp_path: Path, name: str) -> None:
    class InvalidSave(TextSave):
        mod_name = name

    with pytest.raises(ValueError, match='single Mod name'):
        SavesDir(tmp_path).slots[0].mod(InvalidSave)


def test_broken_companions_are_not_read_until_explicitly_opened(tmp_path: Path) -> None:
    (tmp_path / '0.celeste').write_text(normal_save_xml('<SaveData/>'), encoding='utf-8')
    (tmp_path / '0-modsavedata.celeste').write_bytes(b'<broken')
    slot = SavesDir(tmp_path).slots[0]
    slot.main()
    with pytest.raises(ValueError) as caught:
        slot.backup()
    assert any('0-modsavedata.celeste' in note for note in caught.value.__notes__)


def test_orphan_companions_remain_explicitly_accessible(tmp_path: Path) -> None:
    (tmp_path / '1-modsavedata.celeste').write_bytes(b'<ModSaveData/>')
    (tmp_path / '1-modsave-Example.celeste').write_bytes(b'orphan')
    slot = SavesDir(tmp_path).slots[1]
    assert not slot
    assert list(SavesDir(tmp_path).slots.iter(filter_empty=True)) == []
    with pytest.raises(FileNotFoundError, match=r'1\.celeste'):
        slot.main()
    backup = slot.backup()
    mod = slot.mod(TextSave)
    assert not backup.mods.campaigns
    assert mod.text == 'orphan'


def test_lstat_access_failures_are_not_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.lstat

    def denied(path: Path) -> os.stat_result:
        if path.name == '0.celeste':
            raise PermissionError('Denied')
        return original(path)

    monkeypatch.setattr(Path, 'lstat', denied)
    with pytest.raises(PermissionError, match='Denied'):
        bool(SavesDir(tmp_path).slots[0])
    with pytest.raises(PermissionError, match='Denied'):
        list(SavesDir(tmp_path).slots.iter(filter_empty=True))


def test_directory_at_main_filename_is_not_a_save(tmp_path: Path) -> None:
    (tmp_path / '0.celeste').mkdir()
    with pytest.raises(ValueError, match='main save file'):
        bool(SavesDir(tmp_path).slots[0])


def test_repeated_opens_read_current_contents_without_changing_old_data(tmp_path: Path) -> None:
    path = tmp_path / '0.celeste'
    path.write_text(
        normal_save_xml('<SaveData><TotalDeaths>2</TotalDeaths></SaveData>'), encoding='utf-8'
    )
    slot = SavesDir(tmp_path).slots[0]
    first = slot.main()
    path.write_text(
        normal_save_xml('<SaveData><TotalDeaths>20</TotalDeaths></SaveData>'), encoding='utf-8'
    )
    second = slot.main()
    assert first.deaths == 2 and second.deaths == 20


def test_from_game_dir_and_pathlike_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('EVEREST_SAVEPATH', str(tmp_path / 'custom'))
    assert SavesDir.from_game_dir(tmp_path / 'game').path == tmp_path / 'custom' / 'Saves'
    assert SavesDir(str(tmp_path)).path == tmp_path
    assert SaveSlot(str(tmp_path), 0).saves_dir == tmp_path


def test_slot_view_is_readonly_and_supports_sequence_queries(tmp_path: Path) -> None:
    directory = SavesDir(tmp_path)
    slots = directory.slots
    assert isinstance(slots, Sequence)
    assert directory.slots is slots
    assert slots[1:3] == (SaveSlot(tmp_path, 1), SaveSlot(tmp_path, 2))
    assert slots[::-1] == tuple(reversed(slots))
    assert slots.index(SaveSlot(tmp_path, 2)) == 2
    assert slots.count(SaveSlot(tmp_path, 0)) == 1
    assert SaveSlot(tmp_path, 1) in slots and SaveSlot(tmp_path, -1) not in slots
    assert SaveSlot(tmp_path / 'other', 1) not in slots
    assert len({SaveSlot(tmp_path, 1), SaveSlot(tmp_path, 1)}) == 1
    for name in ('path', 'slots'):
        with pytest.raises(AttributeError):
            setattr(directory, name, None)
    with pytest.raises(TypeError):
        len(directory)  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError):
        slots[0] = SaveSlot(tmp_path, 0)  # pyright: ignore[reportIndexIssue]
    (tmp_path / '5.celeste').write_bytes(b'corrupt but occupied')
    assert len(slots) == 3
    slots.invalidate_cache()
    assert len(slots) == 6
    assert [slot.number for slot in reversed(slots)] == [5, 4, 3, 2, 1, 0]
    with pytest.raises(IndexError):
        slots[6]
    with pytest.raises(ValueError):
        slots[::0]


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('filter_empty', [False, True])
def test_slot_iteration_combines_direction_and_empty_filter(
    tmp_path: Path, reverse: bool, filter_empty: bool
) -> None:
    for name in ('0.celeste', '5.celeste', 'debug.celeste', '8-modsavedata.celeste'):
        (tmp_path / name).write_bytes(b'occupied')
    slots = SavesDir(tmp_path).slots
    expected = [0, 5] if filter_empty else list(range(6))
    if reverse:
        expected.reverse()
    assert [
        slot.number for slot in slots.iter(filter_empty=filter_empty, reverse=reverse)
    ] == expected
    if reverse and not filter_empty:
        assert list(slots.iter(reverse=True)) == list(reversed(slots))


def test_cached_range_does_not_scan_until_invalidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slots = SavesDir(tmp_path).slots
    assert len(slots) == 3
    (tmp_path / '8.celeste').write_bytes(b'occupied')

    def denied(path: Path) -> Iterator[Path]:
        raise PermissionError('Denied')

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'iterdir', denied)
        assert len(slots) == 3
        assert slots[2] == SaveSlot(tmp_path, 2)
        assert [slot.number for slot in slots] == [0, 1, 2]
        assert [slot.number for slot in reversed(slots)] == [2, 1, 0]
        assert len(slots[:]) == 3
        with pytest.raises(IndexError):
            slots[8]
        slots.invalidate_cache()
        slots.invalidate_cache()
        with pytest.raises(PermissionError):
            len(slots)
        with pytest.raises(PermissionError):
            len(slots)
    assert len(slots) == 9
    assert slots[8]
    (tmp_path / '8.celeste').unlink()
    assert len(slots) == 9
    assert not slots[8]
    assert list(slots.iter(filter_empty=True)) == []
    slots.invalidate_cache()
    assert len(slots) == 3


def test_failed_initial_discovery_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slots = SavesDir(tmp_path).slots
    slots.invalidate_cache()
    slots.invalidate_cache()

    def denied(path: Path) -> Iterator[Path]:
        raise PermissionError('Denied')

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'iterdir', denied)
        with pytest.raises(PermissionError):
            len(slots)
        slots.invalidate_cache()
        with pytest.raises(PermissionError):
            len(slots)
    (tmp_path / '4.celeste').write_bytes(b'occupied')
    assert len(slots) == 5


def test_find_empty_slot_searches_from_start_without_reserving(tmp_path: Path) -> None:
    directory = SavesDir(tmp_path)
    assert len(directory.slots) == 3
    for number in (0, 2, 3):
        (tmp_path / f'{number}.celeste').write_bytes(b'corrupt but occupied')
    (tmp_path / '1-modsavedata.celeste').write_bytes(b'companion only')
    assert directory.find_empty_slot(0) == SaveSlot(tmp_path, 1)
    assert directory.find_empty_slot() == SaveSlot(tmp_path, 1)
    assert directory.find_empty_slot(1) == SaveSlot(tmp_path, 1)
    assert directory.find_empty_slot(2) == SaveSlot(tmp_path, 4)
    assert directory.find_empty_slot(100) == SaveSlot(tmp_path, 100)
    assert directory.find_empty_slot(2) == directory.find_empty_slot(2)
    assert len(directory.slots) == 3
    assert not (tmp_path / '4.celeste').exists()
    assert not SavesDir(tmp_path / 'missing').find_empty_slot(6)
    assert SavesDir(tmp_path / 'missing').find_empty_slot() == SaveSlot(tmp_path / 'missing', 0)
    assert not (tmp_path / 'missing').exists()


@pytest.mark.parametrize('start', [-1, -2, True, 1.0])
def test_find_empty_slot_rejects_invalid_start(start: object, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SavesDir(tmp_path).find_empty_slot(start)  # pyright: ignore[reportArgumentType]


def test_find_empty_slot_does_not_coerce_text_start(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        SavesDir(tmp_path).find_empty_slot('1')  # pyright: ignore[reportArgumentType]


def test_find_empty_slot_does_not_hide_wrong_file_types_or_access_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = SavesDir(tmp_path)
    (tmp_path / '0.celeste').mkdir()
    with pytest.raises(ValueError, match='main save file'):
        directory.find_empty_slot(0)
    original = Path.lstat

    def denied(path: Path) -> os.stat_result:
        if path.name == '1.celeste':
            raise PermissionError('Denied')
        return original(path)

    monkeypatch.setattr(Path, 'lstat', denied)
    with pytest.raises(PermissionError):
        directory.find_empty_slot(1)


@pytest.mark.parametrize('backup', (False, True))
@pytest.mark.parametrize('recycled', (False, True))
def test_strict_load_checks_all_stored_partitions(
    tmp_path: Path, backup: bool, recycled: bool
) -> None:
    group = 'LevelSetRecycleBin' if recycled else 'LevelSets'
    root = 'ModSaveData' if backup else 'SaveData'
    path = tmp_path / ('0-modsavedata.celeste' if backup else '0.celeste')
    data = (
        f'<{root}><{group}><LevelSetStats Name="Wrong"><Areas>'
        '<AreaStats SID="Pack/Map"><Modes><AreaModeStats TimePlayed="0" Deaths="2"/>'
        '<AreaModeStats/><AreaModeStats/>'
        f'</Modes></AreaStats></Areas></LevelSetStats></{group}></{root}>'
    ).encode()
    data = normal_save_xml(data).encode()
    path.write_bytes(data)
    open_save = SavesDir(tmp_path).slots[0].backup if backup else SavesDir(tmp_path).slots[0].main
    with pytest.raises(BadProgressSave) as caught:
        open_save()
    message = '\n'.join((str(caught.value), *caught.value.__notes__))
    assert (
        str(path) in message and 'Pack/Map' in message and 'Wrong' in message and 'Pack' in message
    )
    save = open_save(strict=False)
    campaigns = save.mods.recycled_campaigns if recycled else save.mods.campaigns
    assert next(iter(campaigns['Wrong'].levels.values())).sid == 'Pack/Map'
    assert path.read_bytes() == data


def test_strict_vanilla_identity_does_not_invent_missing_sids() -> None:
    data = BytesIO(
        normal_save_xml(
            '<SaveData><Areas><AreaStats ID="999"/></Areas></SaveData>', complete=False
        ).encode()
    )
    with pytest.raises(BadProgressSave):
        MainSave.read(data)
    data.seek(0)
    assert not MainSave.read(data, strict=False).vanilla.levels
    with pytest.raises(BadProgressSave):
        MainSave.read(BytesIO(b'<SaveData><Areas><AreaStats SID="Other/Map"/></Areas></SaveData>'))


def test_relaxed_loading_does_not_waive_write_validation(tmp_path: Path) -> None:
    path = tmp_path / '0.celeste'
    data = b'<SaveData><Areas><AreaStats SID="Other/Map"/></Areas></SaveData>'
    path.write_bytes(data)
    save = MainSave.read(path, strict=False)
    with pytest.raises(BadProgressSave):
        save.write(path)
    assert path.read_bytes() == data
