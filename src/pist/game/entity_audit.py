"""SQLite-backed knowledge collected while auditing unknown map entities."""

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError
from typing_extensions import Sentinel as sentinel

from .binmap import AttrValue
from .entities import EntityRule, EntityRuleLayer, EntityRulesForId

LOCAL_AUDIT_DB_PATH = Path('.pist/collectible-audit.sqlite3')
LOCATION_ATTR_NAMES = frozenset({'id', 'x', 'y', 'width', 'height', 'originX', 'originY'})
META_ATTRIBUTE_PREFIX = '@meta.'
_UNKNOWN = sentinel('_UNKNOWN')

type DefaultValue = AttrValue | None | _UNKNOWN


class EntityAuditStatus(StrEnum):
    """The human review state of one entity ID."""

    UNKNOWN = 'unknown'
    IGNORED = 'ignored'
    ENTITY_CANDIDATE = 'entity_candidate'


class AttributeAuditStatus(StrEnum):
    """How one attribute of one entity ID relates to entity classification."""

    UNKNOWN = 'unknown'
    AFFECTS_KIND = 'affects_kind'
    DOES_NOT_AFFECT_KIND = 'does_not_affect_kind'
    LIKELY_NOT_AFFECT_KIND = 'likely_not_affect_kind'
    UNSURE_DEFAULT = 'unsure_default'
    AFFECTS_BEHAVIOR = 'affects_behavior'


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


class AuditSource(BaseModel):
    """The package source of one scanned entity occurrence."""

    model_config = ConfigDict(extra='ignore')

    scope: str
    map_file: str
    map_name: str
    mod_name: str | None = None
    mod_file: str | None = None
    package: str | None = None
    meta: dict[str, AttrValue] = Field(default_factory=dict)


class AuditOccurrence(BaseModel):
    """One raw entity occurrence emitted by the maintenance scan."""

    model_config = ConfigDict(extra='ignore')

    source: AuditSource
    room: str
    entity_id: int | None = None
    attrs: dict[str, AttrValue]


class AuditGroup(BaseModel):
    """The scan's grouped occurrences for one entity ID."""

    model_config = ConfigDict(extra='ignore')

    entity_name: str
    occurrences: tuple[AuditOccurrence, ...]


class AuditReport(BaseModel):
    """The only report fields that the knowledge importer needs."""

    model_config = ConfigDict(extra='ignore')

    entities: tuple[AuditGroup, ...] = Field(
        validation_alias=AliasChoices('entities', 'unmatched')
    )


@dataclass(frozen=True, slots=True)
class EntityAuditSummary:
    """Compact entity-level data for the audit TUI's first level."""

    entity_name: str
    occurrence_count: int
    variant_count: int
    status: EntityAuditStatus
    map_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AttributeAuditSummary:
    """A per-entity attribute's present and missing raw values."""

    name: str
    value_counts: tuple[tuple[AttrValue | None, int], ...]
    status: AttributeAuditStatus
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


def occurrences_for_variants(
    occurrences: Iterable[RawEntityOccurrence], variants: Iterable[EntityVariant]
) -> tuple[RawEntityOccurrence, ...]:
    """Return raw occurrences represented by the given semantic attribute variants."""
    keys = {_variant_key(variant.attrs, variant.meta) for variant in variants}
    return tuple(
        occurrence
        for occurrence in occurrences
        if _variant_key(_semantic_attrs(occurrence.attrs), occurrence.source.meta) in keys
    )


@dataclass(frozen=True, slots=True)
class EntityAuditDetail:
    """The second-level review data for one entity ID."""

    entity_name: str
    status: EntityAuditStatus
    reason: str
    evidence: str | None
    kind_confirmation: EntityKindConfirmation | None
    legacy_kind_confirmation: EntityKindConfirmation | None
    attributes: tuple[AttributeAuditSummary, ...]
    variants: tuple[EntityVariant, ...]
    occurrences: tuple[RawEntityOccurrence, ...]


@dataclass(frozen=True, slots=True)
class RuleCandidate:
    """One condition inferred from confirmed behavior observations."""

    kind: str | None
    when: dict[str, AttrValue]
    meta: dict[str, AttrValue]
    variant_count: int
    fallback: bool = False
    provisional: bool = False

    @property
    def exclude(self) -> bool:
        """Return whether this candidate is a terminal non-collectible outcome."""
        return self.kind is None


class EntityAuditStore:
    """Store immutable raw scans and mutable entity knowledge in SQLite."""

    def __init__(self, path: Path = LOCAL_AUDIT_DB_PATH) -> None:
        self.path = path
        self._initialize()

    def import_report(self, path: Path) -> int:
        """Import one report's raw entity occurrences, preserving older reports."""
        data = path.read_bytes()
        try:
            report = AuditReport.model_validate_json(data)
        except ValidationError as error:
            raise ValueError(f'Invalid entity audit report: {path!r}') from error
        digest = hashlib.sha256(data).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                'SELECT id FROM audit_reports WHERE digest = ?', (digest,)
            ).fetchone()
            if row is not None:
                return int(row['id'])
            cursor = connection.execute(
                'INSERT INTO audit_reports(path, digest) VALUES (?, ?)', (str(path), digest)
            )
            if cursor.lastrowid is None:
                raise RuntimeError('SQLite did not return an audit report ID.')
            report_id = cursor.lastrowid
            for group in report.entities:
                connection.execute(
                    'INSERT OR IGNORE INTO entity_knowledge(entity_name) VALUES (?)',
                    (group.entity_name,),
                )
                for occurrence in group.occurrences:
                    source = occurrence.source
                    attrs_json = _json(occurrence.attrs)
                    connection.execute(
                        """
                        INSERT INTO raw_entity_occurrences(
                            report_id, entity_name, attrs_json, scope, map_file, map_name,
                            mod_name, mod_file, package, meta_json, room, entity_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            report_id,
                            group.entity_name,
                            attrs_json,
                            source.scope,
                            source.map_file,
                            source.map_name,
                            source.mod_name,
                            source.mod_file,
                            source.package,
                            _json(source.meta),
                            occurrence.room,
                            occurrence.entity_id,
                        ),
                    )
        return report_id

    def entity_summaries(self, report_id: int | None = None) -> tuple[EntityAuditSummary, ...]:
        """Return entity-level counts for one imported report, newest by default."""
        with self._connect() as connection:
            report_id = self._report_id(connection, report_id)
            if report_id is None:
                return ()
            rows = connection.execute(
                """
                SELECT raw.entity_name, COUNT(*) AS occurrence_count,
                    COUNT(DISTINCT raw.attrs_json) AS variant_count, knowledge.status
                FROM raw_entity_occurrences AS raw
                JOIN entity_knowledge AS knowledge ON knowledge.entity_name = raw.entity_name
                WHERE raw.report_id = ?
                GROUP BY raw.entity_name
                ORDER BY occurrence_count DESC, raw.entity_name COLLATE NOCASE
                """,
                (report_id,),
            )
            map_files: dict[str, set[str]] = defaultdict(set)
            for row in connection.execute(
                """
                SELECT entity_name, map_file FROM raw_entity_occurrences
                WHERE report_id = ?
                """,
                (report_id,),
            ):
                map_files[row['entity_name']].add(row['map_file'])
            return tuple(
                EntityAuditSummary(
                    row['entity_name'],
                    int(row['occurrence_count']),
                    int(row['variant_count']),
                    EntityAuditStatus(row['status']),
                    tuple(sorted(map_files[row['entity_name']], key=str.casefold)),
                )
                for row in rows
            )

    def latest_report_id(self) -> int | None:
        """Return the most recently imported raw report, if one exists."""
        with self._connect() as connection:
            return self._report_id(connection, None)

    def entity_detail(self, entity_name: str, report_id: int | None = None) -> EntityAuditDetail:
        """Return raw occurrences and attribute knowledge for one entity ID."""
        with self._connect() as connection:
            report_id = self._report_id(connection, report_id)
            row = connection.execute(
                'SELECT status, reason, evidence FROM entity_knowledge WHERE entity_name = ?',
                (entity_name,),
            ).fetchone()
            if row is None:
                raise ValueError(f'Unknown audited entity: {entity_name!r}')
            occurrences = (
                () if report_id is None else self._occurrences(connection, report_id, entity_name)
            )
            attributes = self._attributes(connection, entity_name, occurrences)
            variants = self._variants(connection, entity_name, occurrences)
            confirmation = self._entity_kind_confirmation(connection, entity_name)
            return EntityAuditDetail(
                entity_name,
                EntityAuditStatus(row['status']),
                row['reason'],
                row['evidence'],
                confirmation,
                None
                if confirmation is not None
                else self._legacy_entity_kind_confirmation(connection, entity_name),
                attributes,
                variants,
                occurrences,
            )

    def save_entity_knowledge(
        self,
        entity_name: str,
        status: EntityAuditStatus,
        *,
        reason: str = '',
        evidence: str | None = None,
    ) -> None:
        """Update the deliberate entity-level review outcome."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO entity_knowledge(entity_name, status, reason, evidence)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_name) DO UPDATE SET
                    status = excluded.status, reason = excluded.reason, evidence = excluded.evidence
                """,
                (entity_name, status, reason, evidence),
            )

    def save_attribute_knowledge(
        self,
        entity_name: str,
        name: str,
        status: AttributeAuditStatus,
        *,
        reason: str = '',
        evidence: str | None = None,
        default_value: DefaultValue = _UNKNOWN,
    ) -> None:
        """Update knowledge scoped to exactly one entity ID and attribute name."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO attribute_knowledge(
                    entity_name, name, status, reason, evidence, default_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_name, name) DO UPDATE SET
                    status = excluded.status, reason = excluded.reason, evidence = excluded.evidence,
                    default_json = excluded.default_json
                """,
                (
                    entity_name,
                    name,
                    status,
                    reason,
                    evidence,
                    None if default_value is _UNKNOWN else _value_json(default_value),
                ),
            )

    def save_observation(
        self,
        entity_name: str,
        attrs: dict[str, AttrValue],
        question: ObservationQuestion,
        status: ObservationStatus,
        *,
        kind: str | None = None,
        meta: dict[str, AttrValue] | None = None,
        reason: str = '',
        evidence: str | None = None,
    ) -> None:
        """Save one manual behavior observation for an exact raw attribute variant."""
        _validate_observation_kind(status, kind)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO variant_observations(
                    entity_name, attrs_json, question, status, kind, reason, evidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_name, attrs_json, question) DO UPDATE SET
                    status = excluded.status, kind = excluded.kind, reason = excluded.reason,
                    evidence = excluded.evidence
                """,
                (entity_name, _variant_key(attrs, meta), question, status, kind, reason, evidence),
            )

    def confirm_entity_kind(
        self,
        entity_name: str,
        report_id: int,
        kind: str,
        *,
        reason: str = '',
        evidence: str | None = None,
    ) -> int:
        """Apply one deliberate classification to every raw variant in a report.

        This records independent observations rather than merging missing attributes
        with explicit defaults. It is appropriate only when the reviewer has
        confirmed that no present attribute changes the collectible kind.
        """
        if not kind:
            raise ValueError('An entity classification must identify a collectible kind.')
        detail = self.entity_detail(entity_name, report_id)
        self.save_group_observation(
            entity_name,
            detail.variants,
            ObservationQuestion.ENTITY_CLASSIFICATION,
            ObservationStatus.CONFIRMED,
            kind=kind,
            reason=reason,
            evidence=evidence,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO entity_kind_confirmations(entity_name, kind, reason, evidence)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_name) DO UPDATE SET
                    kind = excluded.kind, reason = excluded.reason, evidence = excluded.evidence
                """,
                (entity_name, kind, reason, evidence),
            )
            connection.execute(
                'DELETE FROM entity_kind_confirmation_variants WHERE entity_name = ?',
                (entity_name,),
            )
            connection.executemany(
                """
                INSERT INTO entity_kind_confirmation_variants(entity_name, attrs_json)
                VALUES (?, ?)
                """,
                (
                    (entity_name, _variant_key(variant.attrs, variant.meta))
                    for variant in detail.variants
                ),
            )
        return len(detail.variants)

    def revoke_entity_kind(self, entity_name: str) -> int:
        """Withdraw a whole-entity confirmation without removing later individual review."""
        with self._connect() as connection:
            confirmation = self._entity_kind_confirmation(connection, entity_name)
            if confirmation is None:
                return 0
            keys = tuple(
                (row['attrs_json'],)
                for row in connection.execute(
                    """
                    SELECT attrs_json FROM entity_kind_confirmation_variants
                    WHERE entity_name = ?
                    """,
                    (entity_name,),
                )
            )
            removed = 0
            for candidate_keys in keys:
                for key in candidate_keys:
                    cursor = connection.execute(
                        """
                        DELETE FROM variant_observations
                        WHERE entity_name = ? AND attrs_json = ? AND question = ?
                            AND status = ? AND kind = ? AND reason = ?
                            AND (evidence = ? OR (evidence IS NULL AND ? IS NULL))
                        """,
                        (
                            entity_name,
                            key,
                            ObservationQuestion.ENTITY_CLASSIFICATION,
                            ObservationStatus.CONFIRMED,
                            confirmation.kind,
                            confirmation.reason,
                            confirmation.evidence,
                            confirmation.evidence,
                        ),
                    )
                    removed += cursor.rowcount
            connection.execute(
                'DELETE FROM entity_kind_confirmation_variants WHERE entity_name = ?',
                (entity_name,),
            )
            connection.execute(
                'DELETE FROM entity_kind_confirmations WHERE entity_name = ?',
                (entity_name,),
            )
        return removed

    def rename_kind(self, old_name: str, new_name: str) -> None:
        """Move persisted audit conclusions to a renamed shared kind ID."""
        if old_name == new_name:
            return
        with self._connect() as connection:
            connection.execute(
                'UPDATE variant_observations SET kind = ? WHERE kind = ?',
                (new_name, old_name),
            )
            connection.execute(
                'UPDATE entity_kind_confirmations SET kind = ? WHERE kind = ?',
                (new_name, old_name),
            )

    def save_group_observation(
        self,
        entity_name: str,
        variants: Iterable[EntityVariant],
        question: ObservationQuestion,
        status: ObservationStatus,
        *,
        kind: str | None = None,
        reason: str = '',
        evidence: str | None = None,
    ) -> None:
        """Save one observation for each raw variant belonging to a review group."""
        _validate_observation_kind(status, kind)
        with self._connect() as connection:
            for variant in variants:
                connection.execute(
                    """
                    INSERT INTO variant_observations(
                        entity_name, attrs_json, question, status, kind, reason, evidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entity_name, attrs_json, question) DO UPDATE SET
                        status = excluded.status, kind = excluded.kind, reason = excluded.reason,
                        evidence = excluded.evidence
                    """,
                    (
                        entity_name,
                        _variant_key(variant.attrs, variant.meta),
                        question,
                        status,
                        kind,
                        reason,
                        evidence,
                    ),
                )

    def rule_candidates(
        self, entity_name: str, report_id: int | None = None
    ) -> tuple[RuleCandidate, ...]:
        """Suggest only conditions that distinguish confirmed kinds in known raw variants."""
        return self.rule_candidates_for_detail(self.entity_detail(entity_name, report_id))

    @staticmethod
    def rule_candidates_for_detail(detail: EntityAuditDetail) -> tuple[RuleCandidate, ...]:
        """Suggest candidates from a detail payload already read for the audit UI."""
        if detail.kind_confirmation is not None:
            return (
                RuleCandidate(
                    detail.kind_confirmation.kind,
                    {},
                    {},
                    len(detail.variants),
                ),
            )
        provisional = any(
            attribute.status is AttributeAuditStatus.LIKELY_NOT_AFFECT_KIND
            for attribute in detail.attributes
        )
        affecting_names = {
            attribute.name
            for attribute in detail.attributes
            if attribute.status is AttributeAuditStatus.AFFECTS_KIND
        }
        classifications: list[tuple[dict[str, AttrValue], dict[str, AttrValue], str | None]] = []
        for variant in detail.variants:
            if any(
                observation.status is ObservationStatus.CONFLICT
                for observation in variant.observations
            ):
                continue
            outcomes = {
                observation.kind if observation.status is ObservationStatus.CONFIRMED else None
                for observation in variant.observations
                if observation.status
                in {ObservationStatus.CONFIRMED, ObservationStatus.NOT_COLLECTIBLE}
            }
            if len(outcomes) == 1:
                classifications.append((variant.attrs, variant.meta, outcomes.pop()))
        if len(classifications) != len(detail.variants):
            return ()
        candidates: dict[
            tuple[str | None, str, str], tuple[dict[str, AttrValue], dict[str, AttrValue], int]
        ] = {}
        for attrs, meta, kind in classifications:
            conditions = _minimal_condition(attrs, meta, kind, classifications, affecting_names)
            if conditions is None:
                continue
            when, meta_when = conditions
            key = kind, _json(when), _json(meta_when)
            previous = candidates.get(key)
            candidates[key] = (
                when,
                meta_when,
                1 if previous is None else previous[2] + 1,
            )
        rule_candidates = tuple(
            RuleCandidate(kind, when, meta_when, variant_count, provisional=provisional)
            for (kind, _, _), (when, meta_when, variant_count) in sorted(
                candidates.items(),
                key=lambda item: (
                    len(item[1][0]) + len(item[1][1]),
                    item[0][0] is None,
                    item[0],
                ),
            )
        )
        return (
            *rule_candidates,
            *_default_fallback_candidates(
                detail, classifications, rule_candidates, provisional=provisional
            ),
        )

    def generated_rule_layer(self, report_id: int | None = None) -> EntityRuleLayer:
        """Build the reproducible rule layer supported by current audit knowledge.

        Only entities deliberately retained as candidates participate.  Each included
        entity must have a complete set of terminal variant observations, as enforced
        by :meth:`rule_candidates`; incomplete review remains absent from the layer.
        """
        entities: dict[str, EntityRulesForId] = {}
        for entity_name in self._candidate_entity_names(report_id):
            candidates = self.rule_candidates(entity_name, report_id)
            if not candidates:
                continue
            rules = tuple(
                EntityRule(kind=candidate.kind, when=candidate.when, meta=candidate.meta)
                for candidate in candidates
            )
            entities[entity_name] = EntityRulesForId(rules=rules)
        return EntityRuleLayer(entities=entities)

    def _candidate_entity_names(self, report_id: int | None) -> tuple[str, ...]:
        """Return current-report candidates without aggregating every raw occurrence.

        ``entity_summaries`` intentionally computes map and variant counts for the
        navigation UI.  Rule refresh needs neither, and this indexed query instead
        probes the current report once per reviewed entity.  Its candidate-only count
        retains the old generated-file order: most frequently seen entities first.
        """
        with self._connect() as connection:
            report_id = self._report_id(connection, report_id)
            if report_id is None:
                return ()
            rows = connection.execute(
                """
                SELECT entity_name
                FROM entity_knowledge
                WHERE status = ?
                    AND EXISTS (
                        SELECT 1
                        FROM raw_entity_occurrences AS raw
                        WHERE raw.report_id = ? AND raw.entity_name = entity_knowledge.entity_name
                    )
                ORDER BY (
                    SELECT COUNT(*)
                    FROM raw_entity_occurrences AS raw
                    WHERE raw.report_id = ? AND raw.entity_name = entity_knowledge.entity_name
                ) DESC, entity_name COLLATE NOCASE
                """,
                (EntityAuditStatus.ENTITY_CANDIDATE, report_id, report_id),
            )
            return tuple(row['entity_name'] for row in rows)

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_reports (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL,
                    digest TEXT NOT NULL UNIQUE,
                    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS entity_knowledge (
                    entity_name TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    reason TEXT NOT NULL DEFAULT '',
                    evidence TEXT
                );
                CREATE TABLE IF NOT EXISTS attribute_knowledge (
                    entity_name TEXT NOT NULL REFERENCES entity_knowledge(entity_name),
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    reason TEXT NOT NULL DEFAULT '',
                    evidence TEXT,
                    default_json TEXT,
                    PRIMARY KEY (entity_name, name)
                );
                CREATE TABLE IF NOT EXISTS raw_entity_occurrences (
                    id INTEGER PRIMARY KEY,
                    report_id INTEGER NOT NULL REFERENCES audit_reports(id),
                    entity_name TEXT NOT NULL REFERENCES entity_knowledge(entity_name),
                    attrs_json TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    map_file TEXT NOT NULL,
                    map_name TEXT NOT NULL,
                    mod_name TEXT,
                    mod_file TEXT,
                    package TEXT,
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    room TEXT NOT NULL,
                    entity_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS raw_entity_occurrences_report_entity
                    ON raw_entity_occurrences(report_id, entity_name);
                CREATE TABLE IF NOT EXISTS variant_observations (
                    entity_name TEXT NOT NULL REFERENCES entity_knowledge(entity_name),
                    attrs_json TEXT NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    kind TEXT,
                    reason TEXT NOT NULL DEFAULT '',
                    evidence TEXT,
                    PRIMARY KEY (entity_name, attrs_json, question)
                );
                CREATE TABLE IF NOT EXISTS entity_kind_confirmations (
                    entity_name TEXT PRIMARY KEY REFERENCES entity_knowledge(entity_name),
                    kind TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    evidence TEXT
                );
                CREATE TABLE IF NOT EXISTS entity_kind_confirmation_variants (
                    entity_name TEXT NOT NULL REFERENCES entity_kind_confirmations(entity_name),
                    attrs_json TEXT NOT NULL,
                    PRIMARY KEY (entity_name, attrs_json)
                );
                """
            )
            columns = {
                row['name']
                for row in connection.execute('PRAGMA table_info(raw_entity_occurrences)')
            }
            if 'meta_json' not in columns:
                connection.execute(
                    "ALTER TABLE raw_entity_occurrences ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'"
                )
            attribute_columns = {
                row['name'] for row in connection.execute('PRAGMA table_info(attribute_knowledge)')
            }
            if 'default_json' not in attribute_columns:
                connection.execute('ALTER TABLE attribute_knowledge ADD COLUMN default_json TEXT')
            connection.execute(
                "UPDATE entity_knowledge SET status = 'entity_candidate' "
                "WHERE status = 'collectible_candidate'"
            )
            connection.execute(
                "UPDATE entity_knowledge SET status = 'entity_candidate' "
                "WHERE status = 'rule_complete'"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        return connection

    @staticmethod
    def _report_id(connection: sqlite3.Connection, report_id: int | None) -> int | None:
        if report_id is not None:
            return report_id
        row = connection.execute('SELECT id FROM audit_reports ORDER BY id DESC LIMIT 1').fetchone()
        return None if row is None else int(row['id'])

    @staticmethod
    def _occurrences(
        connection: sqlite3.Connection, report_id: int, entity_name: str
    ) -> tuple[RawEntityOccurrence, ...]:
        rows = connection.execute(
            """
            SELECT attrs_json, scope, map_file, map_name, mod_name, mod_file, package, meta_json,
                room, entity_id
            FROM raw_entity_occurrences
            WHERE report_id = ? AND entity_name = ?
            ORDER BY map_name COLLATE NOCASE, map_file COLLATE NOCASE, room COLLATE NOCASE
            """,
            (report_id, entity_name),
        )
        return tuple(
            RawEntityOccurrence(
                entity_name,
                _attrs(row['attrs_json']),
                AuditSource(
                    scope=row['scope'],
                    map_file=row['map_file'],
                    map_name=row['map_name'],
                    mod_name=row['mod_name'],
                    mod_file=row['mod_file'],
                    package=row['package'],
                    meta=_attrs(row['meta_json']),
                ),
                row['room'],
                row['entity_id'],
            )
            for row in rows
        )

    @staticmethod
    def _attributes(
        connection: sqlite3.Connection,
        entity_name: str,
        occurrences: Iterable[RawEntityOccurrence],
    ) -> tuple[AttributeAuditSummary, ...]:
        counts: dict[str, Counter[AttrValue | None]] = defaultdict(Counter)
        total_occurrence_count = 0
        for total_occurrence_count, occurrence in enumerate(occurrences, start=1):
            attrs: dict[str, AttrValue] = {
                name: value
                for name, value in occurrence.attrs.items()
                if name not in LOCATION_ATTR_NAMES
            }
            attrs.update(
                (f'{META_ATTRIBUTE_PREFIX}{name}', value)
                for name, value in occurrence.source.meta.items()
            )
            for name, values in counts.items():
                if name not in attrs:
                    values[None] += 1
            for name, value in attrs.items():
                if name not in counts and total_occurrence_count > 1:
                    counts[name][None] = total_occurrence_count - 1
                counts[name][value] += 1
        knowledge_rows = {
            row['name']: row
            for row in connection.execute(
                """
                SELECT name, status, reason, evidence, default_json
                FROM attribute_knowledge WHERE entity_name = ?
                """,
                (entity_name,),
            )
        }
        return tuple(
            AttributeAuditSummary(
                name,
                tuple(sorted(values.items(), key=_attr_value_sort_key)),
                AttributeAuditStatus(
                    knowledge_rows[name]['status'] if name in knowledge_rows else 'unknown'
                ),
                knowledge_rows[name]['reason'] if name in knowledge_rows else '',
                knowledge_rows[name]['evidence'] if name in knowledge_rows else None,
                _value(knowledge_rows[name]['default_json'])
                if name in knowledge_rows and knowledge_rows[name]['default_json'] is not None
                else _UNKNOWN,
            )
            for name, values in sorted(counts.items(), key=lambda item: item[0].casefold())
        )

    @staticmethod
    def _variants(
        connection: sqlite3.Connection,
        entity_name: str,
        occurrences: Iterable[RawEntityOccurrence],
    ) -> tuple[EntityVariant, ...]:
        counts: Counter[str] = Counter()
        attrs_by_key: dict[str, tuple[dict[str, AttrValue], dict[str, AttrValue]]] = {}
        for occurrence in occurrences:
            attrs = _semantic_attrs(occurrence.attrs)
            meta = occurrence.source.meta
            key = _variant_key(attrs, meta)
            attrs_by_key[key] = attrs, meta
            counts[key] += 1
        observations_by_attrs: dict[str, list[VariantObservation]] = defaultdict(list)
        for row in connection.execute(
            """
            SELECT attrs_json, question, status, kind, reason, evidence
            FROM variant_observations WHERE entity_name = ?
            """,
            (entity_name,),
        ):
            observations_by_attrs[row['attrs_json']].append(
                VariantObservation(
                    ObservationQuestion(row['question']),
                    ObservationStatus(row['status']),
                    row['kind'],
                    row['reason'],
                    row['evidence'],
                )
            )
        return tuple(
            EntityVariant(
                attrs_by_key[attrs_json][0],
                attrs_by_key[attrs_json][1],
                count,
                tuple(
                    observations_by_attrs[attrs_json]
                    or observations_by_attrs[_variant_key(attrs_by_key[attrs_json][0])]
                ),
            )
            for attrs_json, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        )

    @staticmethod
    def _entity_kind_confirmation(
        connection: sqlite3.Connection, entity_name: str
    ) -> EntityKindConfirmation | None:
        row = connection.execute(
            """
            SELECT kind, reason, evidence FROM entity_kind_confirmations
            WHERE entity_name = ?
            """,
            (entity_name,),
        ).fetchone()
        return (
            None
            if row is None
            else EntityKindConfirmation(row['kind'], row['reason'], row['evidence'])
        )

    def _legacy_entity_kind_confirmation(
        self, connection: sqlite3.Connection, entity_name: str
    ) -> EntityKindConfirmation | None:
        """Find a pre-table whole-entity confirmation in an older imported report."""
        for row in connection.execute('SELECT id FROM audit_reports ORDER BY id DESC'):
            report_id = int(row['id'])
            variants = self._variants(
                connection,
                entity_name,
                self._occurrences(connection, report_id, entity_name),
            )
            confirmation = _legacy_entity_kind_confirmation(variants)
            if confirmation is not None:
                return confirmation
        return None


def _json(attrs: dict[str, AttrValue]) -> str:
    return json.dumps(attrs, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _value_json(value: AttrValue | None) -> str:
    """Serialize one confirmed JSON default, including an explicit ``null``."""
    return json.dumps(value, ensure_ascii=False)


def _value(value: str) -> AttrValue | None:
    """Read one confirmed JSON default without treating ``null`` as unknown."""
    parsed = json.loads(value)
    if parsed is not None and type(parsed) not in {bool, int, float, str}:
        raise TypeError('Expected a serialized collectible attribute value.')
    return parsed


def _legacy_entity_kind_confirmation(
    variants: tuple[EntityVariant, ...],
) -> EntityKindConfirmation | None:
    """Recognize the pre-confirmation-table bulk observations as one conclusion."""
    observations = []
    for variant in variants:
        classification = tuple(
            observation
            for observation in variant.observations
            if observation.question is ObservationQuestion.ENTITY_CLASSIFICATION
            and observation.status is ObservationStatus.CONFIRMED
        )
        if len(classification) != 1:
            return None
        observations.append(classification[0])
    if not observations:
        return None
    first = observations[0]
    if any(
        (observation.kind, observation.reason, observation.evidence)
        != (first.kind, first.reason, first.evidence)
        for observation in observations[1:]
    ):
        return None
    assert first.kind is not None
    return EntityKindConfirmation(first.kind, first.reason, first.evidence)


def _variant_key(attrs: dict[str, AttrValue], meta: dict[str, AttrValue] | None = None) -> str:
    """Return a stable observation key without rewriting legacy empty-meta keys."""
    if not meta:
        return _json(attrs)
    return json.dumps(
        {'attrs': attrs, 'meta': meta}, ensure_ascii=False, sort_keys=True, separators=(',', ':')
    )


def _attrs(value: str) -> dict[str, AttrValue]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError('Expected serialized entity attributes to be a JSON object.')
    return parsed


def _semantic_attrs(attrs: dict[str, AttrValue]) -> dict[str, AttrValue]:
    """Return raw explicit attributes except physical instance-location fields."""
    return {name: value for name, value in attrs.items() if name not in LOCATION_ATTR_NAMES}


def _attr_value_sort_key(item: tuple[AttrValue | None, int]) -> tuple[bool, str]:
    value, _ = item
    return value is not None, repr(value)


def _minimal_condition(
    attrs: dict[str, AttrValue],
    meta: dict[str, AttrValue],
    kind: str | None,
    classifications: Iterable[tuple[dict[str, AttrValue], dict[str, AttrValue], str | None]],
    affecting_names: set[str],
) -> tuple[dict[str, AttrValue], dict[str, AttrValue]] | None:
    """Find the smallest positive TOML condition that excludes known other kinds."""
    pairs = tuple(
        (name, value) for name, value in attrs.items() if name in affecting_names
    ) + tuple(
        (f'{META_ATTRIBUTE_PREFIX}{name}', value)
        for name, value in meta.items()
        if f'{META_ATTRIBUTE_PREFIX}{name}' in affecting_names
    )
    for size in range(len(pairs) + 1):
        for subset in combinations(pairs, size):
            when = {
                name: value for name, value in subset if not name.startswith(META_ATTRIBUTE_PREFIX)
            }
            meta_when = {
                name.removeprefix(META_ATTRIBUTE_PREFIX): value
                for name, value in subset
                if name.startswith(META_ATTRIBUTE_PREFIX)
            }
            if all(
                other_kind == kind
                or not (_attrs_match(other_attrs, when) and _attrs_match(other_meta, meta_when))
                for other_attrs, other_meta, other_kind in classifications
            ):
                return when, meta_when
    return None


def _default_fallback_candidates(
    detail: EntityAuditDetail,
    classifications: list[tuple[dict[str, AttrValue], dict[str, AttrValue], str | None]],
    candidates: tuple[RuleCandidate, ...],
    *,
    provisional: bool,
) -> tuple[RuleCandidate, ...]:
    """Offer safe fallbacks only for explicitly confirmed runtime defaults."""
    defaults = {
        attribute.name: default
        for attribute in detail.attributes
        if (default := attribute.default_value) is not _UNKNOWN and default is not None
    }
    fallbacks: dict[tuple[str | None, str, str], RuleCandidate] = {}
    for candidate in candidates:
        when, meta = _drop_default_conditions(candidate.when, candidate.meta, defaults)
        if (when, meta) == (candidate.when, candidate.meta):
            continue
        if not _fallback_is_safe(candidate, when, meta, classifications, candidates):
            continue
        key = candidate.kind, _json(when), _json(meta)
        fallbacks[key] = RuleCandidate(
            candidate.kind,
            when,
            meta,
            candidate.variant_count,
            fallback=True,
            provisional=provisional,
        )
    return tuple(
        fallbacks[key]
        for key in sorted(
            fallbacks,
            key=lambda item: (len(item[1]) + len(item[2]), item[0] is None, item),
        )
    )


def _drop_default_conditions(
    when: dict[str, AttrValue],
    meta: dict[str, AttrValue],
    defaults: Mapping[str, AttrValue | None],
) -> tuple[dict[str, AttrValue], dict[str, AttrValue]]:
    return (
        {name: value for name, value in when.items() if defaults.get(name) != value},
        {
            name: value
            for name, value in meta.items()
            if defaults.get(f'{META_ATTRIBUTE_PREFIX}{name}') != value
        },
    )


def _fallback_is_safe(
    candidate: RuleCandidate,
    when: dict[str, AttrValue],
    meta: dict[str, AttrValue],
    classifications: Iterable[tuple[dict[str, AttrValue], dict[str, AttrValue], str | None]],
    candidates: Iterable[RuleCandidate],
) -> bool:
    """Ensure every competing variant matching a fallback has a stricter rule."""
    specificity = len(when) + len(meta)
    for attrs, other_meta, kind in classifications:
        if kind == candidate.kind or not (
            _attrs_match(attrs, when) and _attrs_match(other_meta, meta)
        ):
            continue
        if not any(
            other.kind == kind
            and len(other.when) + len(other.meta) > specificity
            and _attrs_match(attrs, other.when)
            and _attrs_match(other_meta, other.meta)
            for other in candidates
        ):
            return False
    return True


def _attrs_match(actual: dict[str, AttrValue], expected: dict[str, AttrValue]) -> bool:
    return all(actual.get(name) == value for name, value in expected.items())


def _validate_observation_kind(status: ObservationStatus, kind: str | None) -> None:
    if status is ObservationStatus.CONFIRMED and kind is None:
        raise ValueError('A confirmed observation must identify a collectible kind.')
    if status is ObservationStatus.NOT_COLLECTIBLE and kind is not None:
        raise ValueError('A non-collectible observation must not identify a collectible kind.')
