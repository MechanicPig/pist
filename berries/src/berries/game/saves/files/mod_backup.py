"""Everest's standalone Mod snapshot, not a generic file-backup facility."""

from typing import Self

from lxml import etree

from berries.game.saves.codecs.xml import tree as xml_tree
from berries.game.saves.conflicts import CampaignConflictResolver, LevelConflictResolver
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.unknown import UnknownFieldPolicy

from .save import Save


class ModBackupSave(ModSaveData, Save):
    """Adapt complete backup values to one-shot text and file I/O."""

    @classmethod
    def fromstring(
        cls,
        data: str | bytes,
        /,
        *,
        strict: bool = True,
        on_campaign_conflict: CampaignConflictResolver | None = None,
        on_level_conflict: LevelConflictResolver | None = None,
        unknown: UnknownFieldPolicy = 'error',
    ) -> Self:
        root = xml_tree.parse(data).getroot()
        if root.tag != 'ModSaveData':
            raise ValueError('Expected ModSaveData XML root.')
        return cls.from_xml(
            root,
            strict=strict,
            on_campaign_conflict=on_campaign_conflict,
            on_level_conflict=on_level_conflict,
            unknown=unknown,
        )

    def tostring(self) -> str:
        return etree.tostring(self.to_xml(), encoding='unicode')
