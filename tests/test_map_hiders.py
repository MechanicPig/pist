"""Tests for data-declared helper-Mod map hiding rules."""

from pathlib import Path

import pytest

from pist.game.content import ContentPath
from pist.game.map_hiders import (
    CHRONIA_HELPER,
    HELPER_TEST_MAP_HIDER,
    SCUG_HELPER,
    MapHiderRules,
)

HELPER_MAP = ContentPath('Maps/Helper/Test.bin')
SCUG_MAP = ContentPath('Maps/ScugHelper/ScugHelperTest/Example.bin')


def test_chronia_uses_builtin_targets_without_saved_configuration(tmp_path: Path) -> None:
    resolution = MapHiderRules(tmp_path).resolve((CHRONIA_HELPER,))

    assert resolution.diagnostics == ()
    assert resolution.hides(source='AltSidesHelper', map_file=HELPER_MAP)
    assert resolution.hides(source='altsideshelper', map_file=HELPER_MAP)


def test_chronia_uses_configured_targets_and_preserves_yaml_scalars(tmp_path: Path) -> None:
    path = tmp_path / 'Saves' / CHRONIA_HELPER / 'MapHider.yaml'
    path.parent.mkdir(parents=True)
    path.write_text('HelperMapsToHide:\n- on\n- 123\n', encoding='utf-8')

    resolution = MapHiderRules(tmp_path).resolve((CHRONIA_HELPER,))

    assert resolution.hides(source='ON', map_file=HELPER_MAP)
    assert resolution.hides(source='123', map_file=HELPER_MAP)
    assert not resolution.hides(source='AltSidesHelper', map_file=HELPER_MAP)


def test_invalid_chronia_configuration_does_not_hide_and_reports_once(tmp_path: Path) -> None:
    path = tmp_path / 'Saves' / CHRONIA_HELPER / 'MapHider.yaml'
    path.parent.mkdir(parents=True)
    path.write_text('HelperMapsToHide: [', encoding='utf-8')

    resolution = MapHiderRules(tmp_path).resolve((CHRONIA_HELPER,))

    assert resolution.rules == ()
    assert len(resolution.diagnostics) == 1
    assert 'ChroniaHelper' in resolution.diagnostics[0]


def test_chronia_rejects_a_non_list_target_value(tmp_path: Path) -> None:
    path = tmp_path / 'Saves' / CHRONIA_HELPER / 'MapHider.yaml'
    path.parent.mkdir(parents=True)
    path.write_text('HelperMapsToHide: ScugHelper\n', encoding='utf-8')

    resolution = MapHiderRules(tmp_path).resolve((CHRONIA_HELPER,))

    assert resolution.rules == ()
    assert len(resolution.diagnostics) == 1


def test_unknown_rule_does_not_cancel_other_confirmed_hiders(tmp_path: Path) -> None:
    path = tmp_path / 'Saves' / CHRONIA_HELPER / 'MapHider.yaml'
    path.parent.mkdir(parents=True)
    path.write_text('HelperMapsToHide: [', encoding='utf-8')

    resolution = MapHiderRules(tmp_path).resolve((CHRONIA_HELPER, HELPER_TEST_MAP_HIDER))

    assert resolution.hides(source='AltSidesHelper', map_file=HELPER_MAP)
    assert len(resolution.diagnostics) == 1


def test_inactive_hider_does_not_read_stale_configuration(tmp_path: Path) -> None:
    path = tmp_path / 'Saves' / CHRONIA_HELPER / 'MapHider.yaml'
    path.parent.mkdir(parents=True)
    path.write_text('HelperMapsToHide: [', encoding='utf-8')

    resolution = MapHiderRules(tmp_path).resolve((HELPER_TEST_MAP_HIDER,))

    assert resolution.diagnostics == ()
    assert resolution.hides(source='AltSidesHelper', map_file=HELPER_MAP)


def test_hider_activation_requires_exact_metadata_name(tmp_path: Path) -> None:
    resolution = MapHiderRules(tmp_path).resolve(('chroniahelper',))

    assert resolution.rules == ()
    assert not resolution.hides(source='AltSidesHelper', map_file=HELPER_MAP)


def test_scug_uses_its_default_enabled_setting_when_file_is_missing(tmp_path: Path) -> None:
    resolution = MapHiderRules(tmp_path).resolve((SCUG_HELPER,))

    assert resolution.hides(
        source='Anything',
        map_file=SCUG_MAP,
    )


def test_scug_falls_back_to_its_legacy_setting_location(tmp_path: Path) -> None:
    path = tmp_path / 'Everest' / 'ModSettings-OBSOLETE' / 'ScugHelper.yaml'
    path.parent.mkdir(parents=True)
    path.write_text('HideShowcaseMapsInMapSelect: false\n', encoding='utf-8')

    resolution = MapHiderRules(tmp_path).resolve((SCUG_HELPER,))

    assert not resolution.hides(
        source='Anything',
        map_file=SCUG_MAP,
    )


@pytest.mark.parametrize(
    ('content', 'hidden'),
    [
        ('HideShowcaseMapsInMapSelect: false\n', False),
        ('HideShowcaseMapsInMapSelect: "false"\n', False),
        ('HideShowcaseMapsInMapSelect:\n', False),
        ('HideShowcaseMapsInMapSelect: true\n', True),
    ],
)
def test_scug_uses_everest_bool_setting_semantics(
    tmp_path: Path, content: str, *, hidden: bool
) -> None:
    path = tmp_path / 'Saves' / 'modsettings-ScugHelper.celeste'
    path.parent.mkdir()
    path.write_text(content, encoding='utf-8')

    resolution = MapHiderRules(tmp_path).resolve((SCUG_HELPER,))

    assert (
        resolution.hides(
            source='Anything',
            map_file=SCUG_MAP,
        )
        is hidden
    )


def test_scug_invalid_setting_is_unknown_not_a_forced_display(tmp_path: Path) -> None:
    path = tmp_path / 'Saves' / 'modsettings-ScugHelper.celeste'
    path.parent.mkdir()
    path.write_text('HideShowcaseMapsInMapSelect: perhaps\n', encoding='utf-8')

    resolution = MapHiderRules(tmp_path).resolve((SCUG_HELPER, HELPER_TEST_MAP_HIDER))

    assert not resolution.hides(
        source='Anything',
        map_file=SCUG_MAP,
    )
    assert resolution.hides(source='AltSidesHelper', map_file=HELPER_MAP)
    assert len(resolution.diagnostics) == 1


def test_scug_rejects_a_non_mapping_settings_document(tmp_path: Path) -> None:
    path = tmp_path / 'Saves' / 'modsettings-ScugHelper.celeste'
    path.parent.mkdir()
    path.write_text('- HideShowcaseMapsInMapSelect\n', encoding='utf-8')

    resolution = MapHiderRules(tmp_path).resolve((SCUG_HELPER,))

    assert resolution.rules == ()
    assert len(resolution.diagnostics) == 1


def test_unknown_reader_name_is_a_rule_definition_error(tmp_path: Path) -> None:
    rules = tmp_path / 'map_hiders.toml'
    rules.write_text(
        """[[rules]]
id = "bad"
hider = "Example"
reader = "not_a_reader"
target = "source"
match = "exact"
""",
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='Unknown map hider reader'):
        MapHiderRules(tmp_path, rules_path=rules)
