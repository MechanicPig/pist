"""Main-save text adaptation and Mod-edit history coordination."""

from copy import deepcopy
from typing import Self

from lxml import etree
from pydantic import ConfigDict

from berries.game.saves.codecs.xml import tree as xml_tree
from berries.game.saves.conflicts import CampaignConflictResolver, LevelConflictResolver
from berries.game.saves.main_save_data import MainSaveData
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.unknown import UnknownFieldPolicy
from berries.model_behavior import PrivateAttr

from .save import Save


class MainSave(MainSaveData, Save):
    """A complete main-save model with explicit, one-shot text/file I/O."""

    model_config = ConfigDict(extra='forbid', validate_by_name=True)

    _mods_baseline: dict[str, object] | None = PrivateAttr(default=None, compare=False)

    def model_post_init(self, context: object, /) -> None:
        super().model_post_init(context)
        self._mods_baseline = None if self.has_modded_save_data else deepcopy(self._mod_state())

    def _mod_state(self) -> dict[str, object]:
        return dict(self.iter_comparable_items(model=ModSaveData))

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
        if root.tag != 'SaveData':
            raise ValueError('Expected SaveData XML root.')
        return cls.from_xml(
            root,
            strict=strict,
            on_campaign_conflict=on_campaign_conflict,
            on_level_conflict=on_level_conflict,
            unknown=unknown,
        )

    def _modded_save_data_for_output(self) -> bool:
        return super()._modded_save_data_for_output() or (
            self._mods_baseline is not None and self._mod_state() != self._mods_baseline
        )

    def tostring(self) -> str:
        """Serialize complete values without committing to a file or stream."""
        return etree.tostring(self.to_xml(), encoding='unicode')
