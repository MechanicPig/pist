"""External scan models and value types for the entity audit domain."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import AliasChoices, Field
from typing_extensions import Sentinel as sentinel

from pist.game.binmap import AttrValue
from pist.game.map_source import MapSource
from pist.models import ExternalModel, FrozenModel

from ..rules import EntityRuleConditions

LOCATION_ATTR_NAMES = frozenset({'id', 'x', 'y', 'width', 'height', 'originX', 'originY'})
META_ATTR_PREFIX = '@meta.'
UNKNOWN = sentinel('UNKNOWN')

type DefaultValue = AttrValue | None | UNKNOWN


class EntityAuditStatus(StrEnum):
    """The human review state of one entity ID."""

    UNKNOWN = 'unknown'
    IGNORED = 'ignored'
    ENTITY_CANDIDATE = 'entity_candidate'


class AttrAuditStatus(StrEnum):
    """How one attribute of one entity ID relates to entity classification."""

    UNKNOWN = 'unknown'
    AFFECTS_KIND = 'affects_kind'
    DOES_NOT_AFFECT_KIND = 'does_not_affect_kind'
    LIKELY_NOT_AFFECT_KIND = 'likely_not_affect_kind'
    UNSURE_DEFAULT = 'unsure_default'
    AFFECTS_BEHAVIOR = 'affects_behavior'


CLASSIFICATION_IRRELEVANT_STATUSES = (
    AttrAuditStatus.DOES_NOT_AFFECT_KIND,
    AttrAuditStatus.LIKELY_NOT_AFFECT_KIND,
    AttrAuditStatus.AFFECTS_BEHAVIOR,
)
CLASSIFICATION_IRRELEVANT_STATUS_PLACEHOLDERS = ', '.join(
    '?' for _ in CLASSIFICATION_IRRELEVANT_STATUSES
)


class ObservationQuestion(StrEnum):
    """A game-behavior question that can establish an entity kind."""

    ENTITY_CLASSIFICATION = 'entity_classification'
    PAUSE_MENU_COUNT = 'pause_menu_count'
    DEBUG_MAP_COLOR = 'debug_map_color'
    TOTAL_STRAWBERRY_COUNT = 'total_strawberry_count'


class ObservationStatus(StrEnum):
    """The confidence state of one recorded game observation."""

    UNKNOWN = 'unknown'
    CONFIRMED = 'confirmed'
    NOT_COLLECTIBLE = 'not_collectible'
    CONFLICT = 'conflict'


class AuditSource(ExternalModel):
    """The package source of one scanned entity occurrence."""

    scope: MapSource
    map_file: str
    map_name: str
    mod_name: str | None = None
    mod_file: str | None = None
    package: str | None = None
    meta: dict[str, AttrValue] = Field(default_factory=dict)


class AuditOccurrence(ExternalModel):
    """One raw entity occurrence emitted by the maintenance scan."""

    source: AuditSource
    room: str
    entity_id: int | None = None
    attrs: dict[str, AttrValue]


class AuditGroup(ExternalModel):
    """The scan's grouped occurrences for one entity ID."""

    entity_name: str
    occurrences: tuple[AuditOccurrence, ...]


class AuditReport(ExternalModel):
    """The only report fields that the knowledge importer needs."""

    entities: tuple[AuditGroup, ...] = Field(validation_alias=AliasChoices('entities', 'unmatched'))


class VariantKey(FrozenModel):
    """The normalized storage representation of one audited entity variant."""

    attrs: dict[str, AttrValue]
    meta: dict[str, AttrValue]


@dataclass(frozen=True, slots=True)
class EntityAuditSummary:
    """Compact entity-level data for the audit TUI's first level."""

    entity_name: str
    occurrence_count: int
    variant_count: int | None
    status: EntityAuditStatus
    map_files: tuple[str, ...]
    unreviewed_variant_count: int = 0


@dataclass(frozen=True, slots=True)
class AttrAuditSummary:
    """A per-entity attribute's present and missing raw values."""

    name: str
    value_counts: tuple[tuple[AttrValue | None, int], ...]
    status: AttrAuditStatus
    reason: str
    evidence: str | None
    default_value: DefaultValue


@dataclass(frozen=True, slots=True)
class RawEntityOccurrence:
    """One complete raw entity record, preserving every explicit attribute."""

    entity_name: str
    attrs: dict[str, AttrValue]
    source: AuditSource
    room: str
    entity_id: int | None


@dataclass(frozen=True, slots=True)
class AuditMapOccurrences:
    """One entity's aggregated room counts in a map source."""

    source: AuditSource
    rooms: tuple[tuple[str, int], ...]

    @property
    def count(self) -> int:
        return sum(count for _, count in self.rooms)


@dataclass(frozen=True, slots=True)
class VariantObservation:
    """One behavior observation for an entity's raw semantic attribute combination."""

    question: ObservationQuestion
    status: ObservationStatus
    kind: str | None
    reason: str
    evidence: str | None


@dataclass(frozen=True, slots=True)
class EntityKindConfirmation:
    """A deliberate classification that applies to every variant of one entity."""

    kind: str
    reason: str
    evidence: str | None


@dataclass(frozen=True, slots=True)
class EntityVariant:
    """One raw semantic attribute variant and its saved behavior observations."""

    attrs: dict[str, AttrValue]
    meta: dict[str, AttrValue]
    occurrence_count: int
    observations: tuple[VariantObservation, ...]


@dataclass(frozen=True, slots=True)
class EntityVariantSignature:
    """The classification-relevant portion of one raw entity variant."""

    attrs: tuple[tuple[str, AttrValue], ...]
    meta: tuple[tuple[str, AttrValue], ...]


@dataclass(frozen=True, slots=True)
class EntityAuditDetail:
    """The second-level review data for one entity ID."""

    entity_name: str
    status: EntityAuditStatus
    reason: str
    evidence: str | None
    kind_confirmation: EntityKindConfirmation | None
    attr_summaries: tuple[AttrAuditSummary, ...]
    variants: tuple[EntityVariant, ...]


@dataclass(frozen=True, slots=True)
class RuleCandidate:
    """One condition inferred from confirmed behavior observations."""

    kind: str | None
    when: dict[str, AttrValue]
    meta: dict[str, AttrValue]
    variant_count: int
    fallback: bool = False
    provisional: bool = False
    missing: tuple[str, ...] = ()
    missing_meta: tuple[str, ...] = ()

    @property
    def exclude(self) -> bool:
        """Return whether this candidate is a terminal non-collectible outcome."""
        return self.kind is None

    @property
    def conditions(self) -> EntityRuleConditions:
        """Return this candidate's complete value and presence conditions."""
        return EntityRuleConditions(
            tuple(sorted(self.when.items())),
            tuple(sorted(self.meta.items())),
            tuple(sorted(self.missing)),
            tuple(sorted(self.missing_meta)),
        )
