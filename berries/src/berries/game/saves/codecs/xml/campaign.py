"""Encode campaign values and bound vanilla fields without intermediate models."""

from typing import TYPE_CHECKING, Annotated

from lxml import etree
from pydantic import Field

from berries.game.saves.codecs import values

from .fields import XmlFields, XmlText
from .model import Xml

if TYPE_CHECKING:
    from berries.game.saves.progress.campaign import CampaignProgress
    from berries.game.saves.progress.views import VanillaProgressView


class CampaignFields(XmlFields, elements=True):
    """Validated metadata shared by domain preparation and XML output."""

    name: Annotated[XmlText, Xml('@Name')]
    unlocked_levels: values.SaveCount = Field(alias='UnlockedAreas')
    total_strawberries: values.SaveCount = Field(alias='TotalStrawberries')
    poem: Annotated[list[XmlText], Xml('Poem/string')]


def metadata_fields(progress: CampaignProgress | VanillaProgressView) -> CampaignFields:
    """Project representable metadata without copying or decoding any Levels."""
    progress.require_known_levels()
    return CampaignFields(
        name=progress.name,
        UnlockedAreas=progress.unlocked_levels,
        TotalStrawberries=progress.total_strawberries,
        poem=progress.poem,
    )


def encode_progress(progress: CampaignProgress | VanillaProgressView) -> etree.Element:
    """Encode complete known campaign fields, rejecting unresolved identities."""
    fields = metadata_fields(progress)
    root = fields.to_xml('LevelSetStats')
    areas = etree.SubElement(root, 'Areas')
    for level in progress.levels.values():
        progress.levels.validate_member(level)
        areas.append(level.to_xml())
    return root
