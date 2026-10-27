"""Structured inventory, audio and counters used by sessions."""

from typing import Annotated

from pydantic import Field

from berries.game.saves.codecs.values import SaveBool, SaveCount, SaveInt
from berries.game.saves.codecs.xml.fields import XmlFiniteSingle, XmlText
from berries.game.saves.codecs.xml.model import Xml, XmlModel


class PlayerInventory(XmlModel, elements=True):
    xml_tag = 'Inventory'

    dashes: Annotated[SaveCount, Xml('Dashes')] = 0
    dream_dash: Annotated[SaveBool, Xml('DreamDash')] = False
    backpack: Annotated[SaveBool, Xml('Backpack')] = False
    no_refills: Annotated[SaveBool, Xml('NoRefills')] = False


class AudioParameter(XmlModel):
    xml_tag = 'MEP'

    key: Annotated[XmlText | None, Xml('@Key')] = None
    value: Annotated[XmlFiniteSingle, Xml('@Value')] = 0.0


class AudioTrackState(XmlModel):
    xml_tag = 'AudioTrackState'

    event: Annotated[XmlText | None, Xml('@Event')] = None
    parameters: Annotated[
        list[AudioParameter],
        Xml('Parameters/MEP'),
    ] = Field(default_factory=list)


class AudioState(XmlModel, elements=True):
    xml_tag = 'Audio'

    music: Annotated[
        AudioTrackState,
        Xml('Music'),
    ] = Field(default_factory=AudioTrackState)
    ambience: Annotated[
        AudioTrackState,
        Xml('Ambience'),
    ] = Field(default_factory=AudioTrackState)
    ambience_volume: Annotated[XmlFiniteSingle | None, Xml('AmbienceVolume', nillable=True)] = None


class SessionCounter(XmlModel):
    xml_tag = 'Counter'

    key: XmlText | None = None
    value: SaveInt = 0
