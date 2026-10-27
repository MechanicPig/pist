"""Original-game assist settings and their XML representation."""

from typing import Annotated, Literal

from berries.game.saves.codecs.values import SaveBool, SaveInt
from berries.game.saves.codecs.xml.model import Xml, XmlModel


class Assists(XmlModel, elements=True):
    xml_tag = 'Assists'

    game_speed: Annotated[SaveInt, Xml('GameSpeed', missing=0)] = 10
    invincible: Annotated[SaveBool, Xml('Invincible')] = False
    dash_mode: Annotated[Literal['Normal', 'Two', 'Infinite'], Xml('DashMode')] = 'Normal'
    dash_assist: Annotated[SaveBool, Xml('DashAssist')] = False
    infinite_stamina: Annotated[SaveBool, Xml('InfiniteStamina')] = False
    mirror_mode: Annotated[SaveBool, Xml('MirrorMode')] = False
    three_sixty_dashing: Annotated[SaveBool, Xml('ThreeSixtyDashing')] = False
    invisible_motion: Annotated[SaveBool, Xml('InvisibleMotion')] = False
    no_grabbing: Annotated[SaveBool, Xml('NoGrabbing')] = False
    low_friction: Annotated[SaveBool, Xml('LowFriction')] = False
    super_dashing: Annotated[SaveBool, Xml('SuperDashing')] = False
    hiccups: Annotated[SaveBool, Xml('Hiccups')] = False
    play_as_badeline: Annotated[SaveBool, Xml('PlayAsBadeline')] = False
