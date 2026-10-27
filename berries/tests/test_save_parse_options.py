"""Read policies belong to a single parse, not an editable save's lifetime."""

import gc
import weakref
from io import StringIO
from typing import assert_type

import pytest
from lxml import etree
from pydantic import ValidationError

from berries.game.saves.conflicts import CampaignConflictContext
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.communal_helper import CommunalHelperSave
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.files.save import Save
from berries.game.saves.progress.campaign import CampaignProgress
from test_support.save_docs import normal_save_xml


@pytest.mark.parametrize(
    ('save_type', 'source'),
    [
        (MainSave, normal_save_xml('<SaveData/>')),
        (ModBackupSave, '<ModSaveData/>'),
        (CollabUtils2Save, '{}'),
        (CommunalHelperSave, '{}'),
        (EndersBlenderSave, '{}'),
    ],
)
def test_parser_options_are_not_retained(save_type: type[Save], source: str) -> None:
    save = save_type.fromstring(source)
    for name in ('_strict', '_unknown', '_on_campaign_conflict', '_on_level_conflict'):
        assert not hasattr(save, name)
    assert save_type.read(StringIO(source)).tostring() == save.tostring()


def test_root_element_entry_has_the_same_strict_checks_as_text_entry() -> None:
    with pytest.raises(ValueError, match='Missing'):
        MainSave.from_xml(etree.Element('SaveData'))
    source = normal_save_xml('<SaveData><Name>Player</Name></SaveData>')
    assert MainSave.from_xml(etree.fromstring(source)) == MainSave.fromstring(source)
    assert ModBackupSave.from_xml(etree.Element('ModSaveData')).tostring() == (
        ModBackupSave.fromstring('<ModSaveData/>').tostring()
    )
    with pytest.raises(ValueError, match='root'):
        MainSave.fromstring('<Other/>', strict=False)


def test_read_preserves_concrete_return_type_and_parser_signature() -> None:
    main = assert_type(MainSave.read(StringIO('<SaveData/>'), strict=False), MainSave)
    backup = assert_type(ModBackupSave.read(StringIO('<ModSaveData/>')), ModBackupSave)
    blender = assert_type(EndersBlenderSave.read(StringIO('{}'), strict=False), EndersBlenderSave)
    assert main.name == 'Madeline' and not backup.mods.campaigns and not blender.first_clear


def test_callback_is_released_after_resolving_a_conflict() -> None:
    class Resolver:
        def __call__(
            self, old: CampaignConflictContext, new: CampaignConflictContext
        ) -> CampaignProgress:
            return new.progress

    resolver = Resolver()
    reference = weakref.ref(resolver)
    save = ModBackupSave.fromstring(
        '<ModSaveData><LevelSets><LevelSetStats Name="Pack"/>'
        '<LevelSetStats Name="Pack"/></LevelSets></ModSaveData>',
        strict=False,
        on_campaign_conflict=resolver,
    )
    del resolver
    gc.collect()
    assert reference() is None
    assert list(save.mods.campaigns) == ['Pack']


def test_read_reports_unknown_parser_keywords_instead_of_ignoring_them() -> None:
    with pytest.raises(TypeError):
        MainSave.read(StringIO('<SaveData/>'), strcit=False)  # pyright: ignore[reportCallIssue]


def test_blank_construction_does_not_accept_parse_options() -> None:
    with pytest.raises(ValidationError):
        MainSave(strict=False)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        ModBackupSave(strict=False)  # pyright: ignore[reportCallIssue]
