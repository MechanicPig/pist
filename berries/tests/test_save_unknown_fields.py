"""Unknown-field policies are independent of value validation and warning filters."""

import warnings
from collections.abc import Callable
from io import BytesIO

import pytest
import yaml
from lxml import etree

from berries.game.saves.codecs.xml import tree
from berries.game.saves.codecs.yaml import nodes as yaml_nodes
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.communal_helper import CommunalHelperSave
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.progress.level import LevelProgress
from berries.game.saves.progress.session import Session
from berries.game.saves.unknown import (
    UnknownFieldPolicy,
    UnknownSaveFieldError,
    UnknownSaveFieldWarning,
)


@pytest.mark.parametrize('save_type', [CollabUtils2Save, CommunalHelperSave, EndersBlenderSave])
def test_unknown_fields_default_to_error_and_warn_explicitly_drops_them(
    save_type: type[CollabUtils2Save | CommunalHelperSave | EndersBlenderSave],
) -> None:
    path = f'{save_type.__name__}.Future'
    data = 'Future: !extension {text: "01", other: [true, 2]}\n'
    with pytest.raises(UnknownSaveFieldError) as caught:
        save_type.fromstring(data)
    assert caught.value.path == path
    with pytest.warns(UnknownSaveFieldWarning) as recorded:
        save = save_type.fromstring(data, unknown='warn')
    assert len(recorded) == 1
    assert isinstance(recorded[0].message, UnknownSaveFieldWarning)
    assert recorded[0].message.path == path
    assert 'Future' not in yaml.safe_load(save.tostring())


def test_warning_filters_can_ignore_or_escalate_without_an_ignore_policy() -> None:
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter('ignore', UnknownSaveFieldWarning)
        save = CollabUtils2Save.fromstring('Future: 1', unknown='warn')
    assert not recorded and 'Future' not in yaml.safe_load(save.tostring())
    with warnings.catch_warnings():
        warnings.simplefilter('error', UnknownSaveFieldWarning)
        with pytest.raises(UnknownSaveFieldWarning):
            CollabUtils2Save.fromstring('Future: 1', unknown='warn')


@pytest.mark.parametrize('unknown', ['error', 'warn'])
@pytest.mark.parametrize('data', ['RevealMap: invalid', 'VisitedLobbyPositions: {Map: []}'])
def test_unknown_policy_does_not_relax_known_value_validation(
    unknown: UnknownFieldPolicy, data: str
) -> None:
    with pytest.raises(ValueError):
        CollabUtils2Save.fromstring(data, unknown=unknown)


def test_invalid_policy_is_rejected_without_closing_the_stream() -> None:
    stream = BytesIO(b'{}')
    with pytest.raises(ValueError):
        CollabUtils2Save.read(stream, unknown='ignore')  # pyright: ignore[reportArgumentType]
    assert not stream.closed


def test_known_mod_payloads_are_opaque_strings_not_unknown_fields() -> None:
    data = {
        'ModSessionsPerLevel': {'Map': {'Helper': 'Future: !payload [01, true]'}},
        'ModSessionsPerLevelBinary': {'Map': {'Helper': 'AAEC'}},
        'VisitedLobbyPositions': {'Lobby': 'AAE='},
    }
    save = CollabUtils2Save.fromstring(yaml.safe_dump(data))
    restored = yaml.safe_load(save.tostring())
    for field, value in data.items():
        assert restored[field] == value


@pytest.mark.parametrize(
    ('load', 'data'),
    [
        (CollabUtils2Save.fromstring, 'SpeedBerryPBs: {Map: 170000}'),
        (CommunalHelperSave.fromstring, 'SummitGems: [Pack/Map/room/1]'),
        (EndersBlenderSave.fromstring, 'mapDict_roomStat_firstClear_death: {Map: {room: 2}}'),
    ],
)
def test_yaml_output_does_not_reparse_retained_input(
    load: Callable[[str], CollabUtils2Save | CommunalHelperSave | EndersBlenderSave],
    data: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save = load(data)

    def unexpected_parse(data: str | bytes) -> None:
        raise AssertionError('Output must use domain values, not parse saved YAML fragments.')

    with monkeypatch.context() as patch:
        patch.setattr(yaml_nodes, 'compose', unexpected_parse)
        encoded = save.tostring()
        assert save.tostring() == encoded
    assert load(encoded).tostring() == encoded


@pytest.mark.parametrize('field', ['VisitedLobbyPositions', 'ModSessionsPerLevel'])
@pytest.mark.parametrize('value', ['null', '~', ''])
def test_yaml_string_payloads_reject_null(field: str, value: str) -> None:
    payload = f'Map: {value}' if field == 'VisitedLobbyPositions' else f'Map: {{Helper: {value}}}'
    with pytest.raises(ValueError, match='non-null'):
        CollabUtils2Save.fromstring(f'{field}: {{{payload}}}')


@pytest.mark.parametrize('value', ['null', '~', ''])
def test_quoted_yaml_strings_remain_strings(value: str) -> None:
    save = CollabUtils2Save.fromstring(f'VisitedLobbyPositions: {{Map: "{value}"}}')
    assert save.visited_lobby_positions == {'Map': value}
    assert CollabUtils2Save.fromstring(save.tostring()).visited_lobby_positions == {'Map': value}


def _load_nil_unknown(kind: str, unknown: UnknownFieldPolicy) -> None:
    nil = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"'
    extra = 'Future="1"><Future/>'
    if kind == 'SummitGems':
        root = etree.fromstring(MainSave().tostring())
        root.append(etree.fromstring(f'<SummitGems {nil} {extra}</SummitGems>'))
        MainSave.fromstring(etree.tostring(root), unknown=unknown)
    elif kind == 'RespawnPoint':
        Session.from_xml(
            tree.parse(
                '<Session Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
                f'<Area SID="Pack/Map"/><RespawnPoint {nil} {extra}</RespawnPoint></Session>'
            ).getroot(),
            unknown=unknown,
            path='Save',
        )
    else:
        contents = (
            f'<Modes {nil} {extra}</Modes>'
            if kind == 'Modes'
            else f'<Modes><AreaModeStats {nil} {extra}</AreaModeStats></Modes>'
        )
        LevelProgress.from_xml(
            etree.fromstring(f'<AreaStats SID="Pack/Map">{contents}</AreaStats>'),
            strict=False,
            unknown=unknown,
            path='Save',
        )


@pytest.mark.parametrize(
    ('kind', 'path'),
    [
        ('SummitGems', 'SaveData.SummitGems'),
        ('Modes', 'Save.Modes'),
        ('AreaModeStats', 'Save.Modes[0]'),
        ('RespawnPoint', 'Save.RespawnPoint'),
    ],
)
def test_nil_nodes_still_enforce_unknown_field_policy(kind: str, path: str) -> None:
    with pytest.raises(UnknownSaveFieldError) as caught:
        _load_nil_unknown(kind, 'error')
    assert caught.value.path == f'{path}.@Future'
    with pytest.warns(UnknownSaveFieldWarning) as recorded:
        _load_nil_unknown(kind, 'warn')
    assert [
        item.message.path for item in recorded if isinstance(item.message, UnknownSaveFieldWarning)
    ] == [f'{path}.@Future', f'{path}.Future']
