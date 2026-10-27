"""Validated XML field groups."""

import math
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictStr,
    TypeAdapter,
    ValidationInfo,
)

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.values import (
    SaveBool,
    SaveCount,
    SaveDuration,
    SaveSingle,
    SaveTicks,
    validate_duration,
    validate_timer_duration,
)
from berries.map_entity_id import MapEntityID

from .model import Xml, XmlModel, XmlReadContext

# XML 1.0 Char: whitespace and valid scalar values, without control
# characters, surrogate code points or U+FFFE/U+FFFF.
type XmlText = Annotated[
    StrictStr,
    Field(pattern=r'\A[\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\x{10000}-\x{10FFFF}]*\z'),
]
_XML_TEXT_ADAPTER = TypeAdapter(XmlText)


def _timer_duration(value: Duration, info: ValidationInfo) -> Duration:
    context = info.context
    if isinstance(context, XmlReadContext) and context.strict:
        validate_timer_duration(value, path=f'{context.path}.{info.field_name}')
    return value


type XmlTimerDuration = Annotated[SaveDuration, AfterValidator(_timer_duration)]


def _finite_single(value: float, info: ValidationInfo) -> float:
    context = info.context
    if isinstance(context, XmlReadContext) and not context.strict:
        return value
    if not math.isfinite(value):
        raise ValueError('Expected a finite Single value.')
    return value


type XmlFiniteSingle = Annotated[SaveSingle, AfterValidator(_finite_single)]


def validate_entity_id(value: MapEntityID) -> None:
    """Require an entity key representable by the native save format."""
    if (
        not value.room
        or ':' in value.room
        or not isinstance(value.entity_id, int)
        or isinstance(value.entity_id, bool)
        or value.entity_id < 0
        or value.entity_id > 2**31 - 1
    ):
        raise ValueError(f'Invalid map entity ID: {value!r}')
    _ = _XML_TEXT_ADAPTER.validate_python(value.room)


def format_entity_id(value: MapEntityID) -> str:
    """Validate and encode a collected-entity key before modifying its XML target."""
    validate_entity_id(value)
    return str(value)


def _entity_id(value: str | MapEntityID) -> MapEntityID:
    return MapEntityID.parse(value) if isinstance(value, str) else value


type XmlEntityID = Annotated[MapEntityID, BeforeValidator(_entity_id)]


class XmlFields(XmlModel):
    """Immutable boundary snapshots, unlike editable save domain models."""

    model_config = ConfigDict(frozen=True)


class EntityItems(XmlFields):
    values: Annotated[list[XmlEntityID], Xml('EntityID/@Key', encoder=format_entity_id)] = Field(
        default_factory=list
    )


class StringItems(XmlFields):
    values: Annotated[list[XmlText], Xml('string')] = Field(default_factory=list)


class MapFlags(XmlFields):
    completed: Annotated[SaveBool, Xml('@Completed', strict_required=True)] = Field(
        default=False, alias='Completed'
    )
    single_run_completed: Annotated[SaveBool, Xml('@SingleRunCompleted', strict_required=True)] = (
        Field(default=False, alias='SingleRunCompleted')
    )
    full_clear: Annotated[SaveBool, Xml('@FullClear', strict_required=True)] = Field(
        default=False, alias='FullClear'
    )
    heart_collected: Annotated[SaveBool, Xml('@HeartGem', strict_required=True)] = Field(
        default=False, alias='HeartGem'
    )


class MapFields(MapFlags):
    time_played: Annotated[XmlTimerDuration, Xml('@TimePlayed', strict_required=True)] = Field(
        default_factory=Duration, alias='TimePlayed'
    )
    deaths: Annotated[SaveCount, Xml('@Deaths', strict_required=True)] = Field(
        default=0, alias='Deaths'
    )
    total_strawberries: Annotated[SaveCount, Xml('@TotalStrawberries', strict_required=True)] = (
        Field(default=0, alias='TotalStrawberries')
    )
    best_time: Annotated[XmlTimerDuration, Xml('@BestTime', strict_required=True)] = Field(
        default_factory=Duration, alias='BestTime'
    )
    best_full_clear_time: Annotated[
        XmlTimerDuration, Xml('@BestFullClearTime', strict_required=True)
    ] = Field(default_factory=Duration, alias='BestFullClearTime')
    best_deaths: Annotated[SaveCount, Xml('@BestDeaths', strict_required=True)] = Field(
        default=0, alias='BestDeaths'
    )
    best_dashes: Annotated[SaveCount, Xml('@BestDashes', strict_required=True)] = Field(
        default=0, alias='BestDashes'
    )
    strawberries: Annotated[
        list[XmlEntityID],
        Xml('Strawberries/EntityID/@Key', encoder=format_entity_id, strict_required=True),
    ] = Field(default_factory=list)
    checkpoints: Annotated[list[XmlText], Xml('Checkpoints/string', strict_required=True)] = Field(
        default_factory=list
    )

    def validate_output(self) -> None:
        """Check protocol constraints that field validation alone cannot express."""
        for value in (self.time_played, self.best_time, self.best_full_clear_time):
            validate_duration(value)
        for entity_id in self.strawberries:
            validate_entity_id(entity_id)


type SideName = Literal['Normal', 'BSide', 'CSide']

SIDE_NAMES = ('Normal', 'BSide', 'CSide')
SIDES = {name: LevelSide(i) for i, name in enumerate(SIDE_NAMES)}


def _parse_side(value: object) -> object:
    if isinstance(value, str):
        try:
            return SIDES[value]
        except KeyError:
            raise ValueError(f'Invalid Mode side name: {value!r}.') from None
    return value


type XmlSide = Annotated[LevelSide, BeforeValidator(_parse_side)]


class PersonalBestFields(XmlFields):
    time: SaveTicks = Field(0, alias='BestTime')
    full_clear_time: SaveTicks = Field(0, alias='BestFullClearTime')
    deaths: SaveCount = Field(0, alias='BestDeaths')
    dashes: SaveCount = Field(0, alias='BestDashes')


MAP_ATTRIBUTES = frozenset(
    field.alias for field in MapFields.model_fields.values() if field.alias is not None
)


class RespawnFields(XmlFields, elements=True):
    x: XmlFiniteSingle = Field(alias='X')
    y: XmlFiniteSingle = Field(alias='Y')
