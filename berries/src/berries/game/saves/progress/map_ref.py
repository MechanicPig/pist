"""Stored map references in save objects."""

from typing import Annotated

from pydantic import ConfigDict

from berries.game.levels import LevelSide, MapKey
from berries.game.saves.codecs.values import SaveInt
from berries.game.saves.codecs.xml import fields as xml_fields
from berries.game.saves.codecs.xml.model import Xml, XmlModel
from berries.game.vanilla import VANILLA_SIDS


class MapRef(XmlModel):
    """A stored Level reference plus side, encoded as the game's AreaKey."""

    model_config = ConfigDict(frozen=True, validate_by_name=True)
    xml_tag = 'Area'

    level_id: Annotated[SaveInt, Xml('@ID')] = 0
    side: Annotated[
        xml_fields.XmlSide,
        Xml('@Mode', encoder=lambda side: xml_fields.SIDE_NAMES[side]),
    ] = LevelSide.A
    level_sid: Annotated[xml_fields.XmlText | None, Xml('@SID')] = None

    def resolve_key(self, *, vanilla: bool = False) -> MapKey | None:
        if self.level_sid:
            return self.level_sid, self.side
        if vanilla and 0 <= self.level_id < len(VANILLA_SIDS):
            return VANILLA_SIDS[self.level_id], self.side
        return None
