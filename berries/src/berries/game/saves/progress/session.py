"""Complete original-game and Everest Session state, without retained XML."""

from collections.abc import Iterator
from copy import deepcopy
from functools import partial
from typing import Annotated, Literal

from lxml import etree
from pydantic import Field, InstanceOf, computed_field

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.values import SaveBool, SaveCount, SaveInt, SaveSingle
from berries.game.saves.codecs.xml import fields as xml_fields
from berries.game.saves.codecs.xml import session as session_codec
from berries.game.saves.codecs.xml import summit_gems as gems_codec
from berries.game.saves.codecs.xml.model import Xml, XmlModel, XmlReadContext
from berries.game.saves.errors import BadProgressSave, ProgressConflictError
from berries.game.vanilla import VANILLA_SIDS
from berries.model_behavior import FieldModel, PrivateAttr

from .level import LevelProgress
from .session_state import AudioState, PlayerInventory, SessionCounter


class Session(XmlModel, FieldModel):
    """Complete session values with stable SID and side identity.

    Parsing supplies defaults from the serializer constructor, not values inferred
    from installed maps. Writing requires the OldStats history to be present and
    valid. No XML tree, fragment or lexical baseline is retained. The structured
    baseline exists only for coordinating the main save's two session branches.
    """

    xml_tag = 'Session'
    xml_nillable = False

    _baseline: dict[str, object] = PrivateAttr(compare=False)
    _level_id: Annotated[SaveInt | None, Xml('Area/@ID')] = PrivateAttr(
        default=None, alias='level_id'
    )
    _sid: Annotated[xml_fields.XmlText | None, Field(min_length=1), Xml('Area/@SID')] = PrivateAttr(
        default=None, alias='sid'
    )
    side: Annotated[
        xml_fields.XmlSide,
        Field(frozen=True),
        Xml('Area/@Mode', encoder=lambda side: xml_fields.SIDE_NAMES[side], missing=LevelSide.A),
    ]
    in_level: Annotated[SaveBool, Xml('@InArea')] = True
    room: Annotated[xml_fields.XmlText, Xml('@Level')]
    time_played: Annotated[xml_fields.XmlTimerDuration, Xml('@Time')]
    deaths: Annotated[SaveCount, Xml('@Deaths')]
    room_deaths: Annotated[SaveCount, Xml('@DeathsInCurrentLevel')]
    first_room: Annotated[SaveBool, Xml('@FirstLevel')] = True
    dark_room_alpha: Annotated[xml_fields.XmlFiniteSingle, Xml('@DarkRoomAlpha')] = 0.75
    started_from_beginning: Annotated[SaveBool, Xml('@StartedFromBeginning')] = False
    dashes: Annotated[SaveCount, Xml('@Dashes')] = 0
    dashes_at_room_start: Annotated[SaveCount, Xml('@DashesAtLevelStart')] = 0
    start_checkpoint: Annotated[xml_fields.XmlText | None, Xml('@StartCheckpoint')] = None
    cassette: Annotated[SaveBool, Xml('@Cassette')] = False
    heart: Annotated[SaveBool, Xml('@HeartGem')] = False
    dreaming: Annotated[SaveBool, Xml('@Dreaming')] = False
    color_grade: Annotated[xml_fields.XmlText | None, Xml('@ColorGrade')] = None
    lighting_alpha_add: Annotated[xml_fields.XmlFiniteSingle, Xml('@LightingAlphaAdd')] = 0.0
    bloom_base_add: Annotated[xml_fields.XmlFiniteSingle, Xml('@BloomBaseAdd')] = 0.0
    core_mode: Annotated[Literal['None', 'Hot', 'Cold'], Xml('@CoreMode')] = 'None'
    grabbed_golden: Annotated[SaveBool, Xml('@GrabbedGolden')] = False
    hit_checkpoint: Annotated[SaveBool, Xml('@HitCheckpoint')] = False
    unlocked_c_side: Annotated[SaveBool, Xml('UnlockedCSide')] = False
    furthest_seen_room: Annotated[xml_fields.XmlText | None, Xml('FurthestSeenLevel')] = None
    beat_best_time: Annotated[SaveBool, Xml('BeatBestTime')] = False
    restarted_from_golden: Annotated[SaveBool, Xml('RestartedFromGolden')] = False
    respawn: Annotated[
        tuple[xml_fields.XmlFiniteSingle, xml_fields.XmlFiniteSingle] | None,
        Xml(
            'RespawnPoint',
            nillable=True,
            decoder=session_codec.read_respawn,
            element_encoder=session_codec.write_respawn,
        ),
    ] = None
    audio: Annotated[AudioState, Xml('Audio')] = Field(default_factory=AudioState)
    inventory: Annotated[PlayerInventory, Xml('Inventory')] = Field(default_factory=PlayerInventory)
    old_stats: Annotated[
        InstanceOf[LevelProgress] | None,
        Xml(
            'OldStats',
            decoder=partial(LevelProgress.from_xml, vanilla=True),
            element_encoder=LevelProgress.to_xml,
        ),
    ] = None
    flags: Annotated[set[xml_fields.XmlText], Xml('Flags/string')] = Field(default_factory=set)
    room_flags: Annotated[set[xml_fields.XmlText], Xml('LevelFlags/string')] = Field(
        default_factory=set
    )
    strawberries: Annotated[
        set[xml_fields.XmlEntityID],
        Xml('Strawberries/EntityID/@Key', encoder=xml_fields.format_entity_id),
    ] = Field(default_factory=set)
    do_not_load: Annotated[
        set[xml_fields.XmlEntityID],
        Xml('DoNotLoad/EntityID/@Key', encoder=xml_fields.format_entity_id),
    ] = Field(default_factory=set)
    keys: Annotated[
        set[xml_fields.XmlEntityID],
        Xml('Keys/EntityID/@Key', encoder=xml_fields.format_entity_id),
    ] = Field(default_factory=set)
    counters: Annotated[
        list[SessionCounter],
        Xml('Counters/Counter'),
    ] = Field(default_factory=list)
    summit_gems: Annotated[
        list[bool],
        Xml(
            'SummitGems',
            many=False,
            nil_as_missing=True,
            decoder=gems_codec.from_xml,
            element_encoder=gems_codec.to_xml,
        ),
    ] = Field(default_factory=lambda: [False] * gems_codec.N_GEMS)
    sliders: Annotated[
        dict[xml_fields.XmlText, SaveSingle],
        Xml(
            'Sliders',
            decoder=session_codec.read_sliders,
            element_encoder=session_codec.write_sliders,
        ),
    ] = Field(default_factory=dict)

    def model_post_init(self, context: object, /) -> None:
        if self._sid is None:
            if isinstance(context, XmlReadContext) and context.tag != 'CurrentSession':
                raise ValueError('Missing session Area/SID.')
            if self._level_id is None:
                raise ValueError('Missing original-game session Area/ID or session Area/SID.')
            if not 0 <= self._level_id < len(VANILLA_SIDS):
                raise ValueError('Unknown original-game current session identity.')
            self._sid = VANILLA_SIDS[self._level_id]
        if self._level_id is None:
            self._level_id = 0
        if (
            isinstance(context, XmlReadContext)
            and context.strict
            and context.tag in ('CurrentSession', 'CurrentSession_Safe')
            and (
                self.old_stats is None
                or len(self.old_stats.maps) != len(LevelSide)
                or any(value is None for value in self.old_stats.maps)
            )
        ):
            raise ValueError('Session requires OldStats with three non-null Modes.')
        self._validate_old_stats_identity()
        self._baseline = deepcopy(dict(self.iter_comparable_items()))

    @computed_field
    @property
    def sid(self) -> str:
        assert self._sid is not None
        return self._sid

    @computed_field
    @property
    def level_id(self) -> int:
        if self._level_id is None:
            raise ValueError('Session numeric ID cannot be None after initialization.')
        return self._level_id

    @level_id.setter
    def level_id(self, value: int) -> None:
        self._level_id = value

    def _validate_old_stats_identity(self) -> None:
        if self.old_stats is not None and self.old_stats.sid != self.sid:
            raise ValueError(
                f'OldStats SID {self.old_stats.sid!r} differs from session SID {self.sid!r}.'
            )

    def iter_changed_fields(self) -> Iterator[str]:
        """Yield fields whose current values differ from the initial baseline."""
        for name, value in self.iter_comparable_items():
            if value != self._baseline[name]:
                yield name

    def __eq__(self, other: object) -> bool:
        """Compare complete modeled state independently of its edit baseline."""
        if not isinstance(other, Session):
            return NotImplemented
        if self is other:
            return True
        return dict(self.iter_comparable_items()) == dict(other.iter_comparable_items())

    def validate_map_totals(self, *, time_played: Duration | None, deaths: int | None) -> None:
        old = self.old_stats
        if old is None or len(old.maps) != len(LevelSide) or old.maps[self.side] is None:
            raise ProgressConflictError('Session requires OldStats with three non-null Modes.')
        history = old.maps[self.side]
        assert history is not None
        if time_played is not None and time_played < history.time_played + self.time_played:
            raise ProgressConflictError('Map time cannot be less than OldStats plus session time.')
        if deaths is not None and deaths < history.deaths + self.deaths:
            raise ProgressConflictError(
                'Map deaths cannot be less than OldStats plus session deaths.'
            )

    def _write_xml(self, root: etree.Element, *, exclude_unset: bool = False) -> None:
        self._validate_old_stats_identity()
        if (
            self.old_stats is None
            or len(self.old_stats.maps) != len(LevelSide)
            or any(value is None for value in self.old_stats.maps)
        ):
            raise BadProgressSave(
                'Session requires OldStats with three non-null Modes before writing.'
            )
        gems_codec.validate(self.summit_gems, nullable=False)
        super()._write_xml(root, exclude_unset=exclude_unset)

    def tostring(self) -> str:
        return etree.tostring(self.to_xml(), encoding='unicode')
