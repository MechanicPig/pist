"""SQLite-backed knowledge collected while auditing unknown map entities."""

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Collection, Generator, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from pydantic import TypeAdapter, ValidationError

from pist.game.binmap import AttrValue
from pist.game.map_source import MapSource

from ..rules import EntityRule, EntityRuleLayer, EntityRulesForId
from .inference import rule_candidates_for_detail
from .models import (
    CLASSIFICATION_IRRELEVANT_STATUS_PLACEHOLDERS,
    CLASSIFICATION_IRRELEVANT_STATUSES,
    LOCATION_ATTR_NAMES,
    META_ATTR_PREFIX,
    UNKNOWN,
    AttrAuditStatus,
    AttrAuditSummary,
    AuditMapOccurrences,
    AuditReport,
    AuditSource,
    DefaultValue,
    EntityAuditDetail,
    EntityAuditStatus,
    EntityAuditSummary,
    EntityKindConfirmation,
    EntityVariant,
    EntityVariantSignature,
    ObservationQuestion,
    ObservationStatus,
    RawEntityOccurrence,
    RuleCandidate,
    VariantObservation,
)
from .models import VariantKey as _VariantKey

LOCAL_AUDIT_DB_PATH = Path('.pist/entity-audit.sqlite3')
ATTRS_ADAPTER = TypeAdapter(dict[str, AttrValue])
ATTR_VALUE_ADAPTER = TypeAdapter(AttrValue | None)
SEMANTIC_ATTRS_SQL = (
    "json_remove(attrs_json, '$.id', '$.x', '$.y', '$.width', '$.height', '$.originX', '$.originY')"
)


@dataclass(frozen=True, slots=True)
class _VariantReviewChecker:
    """An immutable audit snapshot used while reviewing one decoded map."""

    ignored_names_by_entity: Mapping[str, frozenset[str]]
    reviewed_signatures_by_entity: Mapping[str, frozenset[EntityVariantSignature]]

    def __call__(
        self,
        entity_name: str,
        attrs: Mapping[str, AttrValue],
        meta: Mapping[str, AttrValue],
    ) -> bool:
        ignored_names = self.ignored_names_by_entity.get(entity_name, frozenset())
        signatures = self.reviewed_signatures_by_entity.get(entity_name, frozenset())
        return bool(signatures) and (
            _variant_signature(_semantic_attrs(dict(attrs)), meta, ignored_names) not in signatures
        )


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
        with self._connect() as conn:
            row = conn.execute(
                'SELECT id FROM audit_reports WHERE digest = ?', (digest,)
            ).fetchone()
            if row is not None:
                return int(row['id'])
            cursor = conn.execute(
                'INSERT INTO audit_reports(path, digest) VALUES (?, ?)', (str(path), digest)
            )
            if cursor.lastrowid is None:
                raise RuntimeError('SQLite did not return an audit report ID.')
            report_id = cursor.lastrowid
            for group in report.entities:
                conn.execute(
                    'INSERT OR IGNORE INTO entity_knowledge(entity_name) VALUES (?)',
                    (group.entity_name,),
                )
                for occurrence in group.occurrences:
                    source = occurrence.source
                    attrs_json = _json(occurrence.attrs)
                    conn.execute(
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
        with self._connect() as conn:
            report_id = self._report_id(conn, report_id)
            if report_id is None:
                return ()
            rows = conn.execute(
                """
                SELECT raw.entity_name, COUNT(*) AS occurrence_count, knowledge.status
                FROM raw_entity_occurrences AS raw
                JOIN entity_knowledge AS knowledge ON knowledge.entity_name = raw.entity_name
                WHERE raw.report_id = ?
                GROUP BY raw.entity_name
                ORDER BY occurrence_count DESC, raw.entity_name COLLATE NOCASE
                """,
                (report_id,),
            )
            map_files: dict[str, set[str]] = defaultdict(set)
            for row in conn.execute(
                """
                SELECT DISTINCT entity_name, map_file FROM raw_entity_occurrences
                WHERE report_id = ?
                """,
                (report_id,),
            ):
                map_files[row['entity_name']].add(row['map_file'])
            return tuple(
                EntityAuditSummary(
                    row['entity_name'],
                    int(row['occurrence_count']),
                    None,
                    EntityAuditStatus(row['status']),
                    tuple(sorted(map_files[row['entity_name']], key=str.casefold)),
                )
                for row in rows
            )

    def latest_report_id(self) -> int | None:
        """Return the most recently imported raw report, if one exists."""
        with self._connect() as conn:
            return self._report_id(conn, None)

    def entity_detail(self, entity_name: str, report_id: int | None = None) -> EntityAuditDetail:
        """Return raw occurrences and attribute knowledge for one entity ID."""
        with self._connect() as conn:
            report_id = self._report_id(conn, report_id)
            row = conn.execute(
                'SELECT status, reason, evidence FROM entity_knowledge WHERE entity_name = ?',
                (entity_name,),
            ).fetchone()
            if row is None:
                raise ValueError(f'Unknown audited entity: {entity_name!r}')
            variants = () if report_id is None else self._variants(conn, report_id, entity_name)
            attr_summaries = self._attr_summaries(conn, entity_name, variants)
            confirmation = self._entity_kind_confirmation(conn, entity_name)
            return EntityAuditDetail(
                entity_name,
                EntityAuditStatus(row['status']),
                row['reason'],
                row['evidence'],
                confirmation,
                attr_summaries,
                variants,
            )

    def occurrences(
        self, entity_name: str, report_id: int | None = None
    ) -> tuple[RawEntityOccurrence, ...]:
        """Load raw positions only for an explicit occurrence-inspection request."""
        with self._connect() as conn:
            report_id = self._report_id(conn, report_id)
            return () if report_id is None else self._occurrences(conn, report_id, entity_name)

    def map_occurrences(
        self, entity_name: str, source: AuditSource, report_id: int | None = None
    ) -> tuple[RawEntityOccurrence, ...]:
        """Load raw coordinates for one map preview only."""
        with self._connect() as conn:
            report_id = self._report_id(conn, report_id)
            if report_id is None:
                return ()
            return self._occurrences(conn, report_id, entity_name, source)

    def occurrence_maps(
        self,
        entity_name: str,
        report_id: int | None = None,
        variants: Collection[EntityVariant] | None = None,
    ) -> tuple[AuditMapOccurrences, ...]:
        """Return map and room counts without materializing raw entity instances."""
        with self._connect() as conn:
            report_id = self._report_id(conn, report_id)
            if report_id is None:
                return ()
            rows = conn.execute(
                f"""
                SELECT {SEMANTIC_ATTRS_SQL} AS semantic_attrs_json, meta_json,
                    scope, map_file, map_name, mod_name, mod_file, package, room, COUNT(*) AS count
                FROM raw_entity_occurrences
                WHERE report_id = ? AND entity_name = ?
                GROUP BY {SEMANTIC_ATTRS_SQL}, meta_json, scope, map_file, map_name,
                    mod_name, mod_file, package, room
                """,
                (report_id, entity_name),
            )
            requested = (
                None
                if variants is None
                else {_variant_key(variant.attrs, variant.meta) for variant in variants}
            )
            maps: dict[
                tuple[str, str, str | None, str | None], tuple[AuditSource, Counter[str]]
            ] = {}
            for row in rows:
                if (
                    requested is not None
                    and _variant_key(_attrs(row['semantic_attrs_json']), _attrs(row['meta_json']))
                    not in requested
                ):
                    continue
                map_key = row['scope'], row['map_file'], row['mod_file'], row['package']
                entry = maps.get(map_key)
                if entry is None:
                    source = AuditSource(
                        scope=row['scope'],
                        map_file=row['map_file'],
                        map_name=row['map_name'],
                        mod_name=row['mod_name'],
                        mod_file=row['mod_file'],
                        package=row['package'],
                        meta={},
                    )
                    room_counts: Counter[str] = Counter()
                    maps[map_key] = source, room_counts
                else:
                    _, room_counts = entry
                room_counts[row['room']] += int(row['count'])
        return tuple(
            AuditMapOccurrences(
                source,
                tuple(
                    sorted(
                        room_counts.items(),
                        key=lambda item: (-item[1], item[0].casefold()),
                    )
                ),
            )
            for source, room_counts in maps.values()
        )

    def needs_variant_review(
        self,
        entity_name: str,
        attrs: Mapping[str, AttrValue],
        meta: Mapping[str, AttrValue],
    ) -> bool:
        """Return whether one map entity differs from every reviewed classification variant.

        Attributes explicitly or provisionally marked irrelevant are omitted from the
        comparison.  A wholly unreviewed entity remains the ordinary audit workflow;
        this method only identifies a new variant after at least one terminal
        classification has been recorded for the entity.
        """
        return self.variant_review_checker({entity_name})(entity_name, attrs, meta)

    def unreviewed_variant_count(self, entity_name: str, report_id: int | None = None) -> int:
        """Count one report entity's variants not covered by terminal review.

        Unlike navigation summaries, this only decodes rows for the selected
        entity. Historical observations remain part of the comparison so a newly
        imported report can recognize a variant confirmed in an older report.
        """
        with self._connect() as conn:
            report_id = self._report_id(conn, report_id)
            if report_id is None:
                return 0
            ignored_names = {
                row['name']
                for row in conn.execute(
                    f"""
                    SELECT name FROM attr_knowledge
                    WHERE entity_name = ?
                        AND status IN ({CLASSIFICATION_IRRELEVANT_STATUS_PLACEHOLDERS})
                    """,
                    (entity_name, *CLASSIFICATION_IRRELEVANT_STATUSES),
                )
            }
            reviewed = {
                _variant_signature(*_variant_key_parts(row['attrs_json']), ignored_names)
                for row in conn.execute(
                    """
                    SELECT attrs_json FROM variant_observations
                    WHERE entity_name = ? AND question = ? AND status IN (?, ?)
                    """,
                    (
                        entity_name,
                        ObservationQuestion.ENTITY_CLASSIFICATION,
                        ObservationStatus.CONFIRMED,
                        ObservationStatus.NOT_COLLECTIBLE,
                    ),
                )
            }
            if not reviewed:
                return 0
            current = {
                _variant_signature(
                    _attrs(row['semantic_attrs_json']),
                    _attrs(row['meta_json']),
                    ignored_names,
                )
                for row in conn.execute(
                    f"""
                    SELECT DISTINCT {SEMANTIC_ATTRS_SQL} AS semantic_attrs_json, meta_json
                    FROM raw_entity_occurrences
                    WHERE report_id = ? AND entity_name = ?
                    """,
                    (report_id, entity_name),
                )
            }
        return len(current - reviewed)

    def raw_variant_count(self, entity_name: str, report_id: int | None = None) -> int:
        """Return one entity's count of distinct raw attribute records in a report."""
        with self._connect() as conn:
            report_id = self._report_id(conn, report_id)
            if report_id is None:
                return 0
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT attrs_json) AS variant_count
                FROM raw_entity_occurrences
                WHERE report_id = ? AND entity_name = ?
                """,
                (report_id, entity_name),
            ).fetchone()
        assert row is not None
        return int(row['variant_count'])

    def variant_review_checker(
        self, entity_names: Collection[str] | None = None
    ) -> _VariantReviewChecker:
        """Return an immutable snapshot for checking the given map entity IDs.

        With no IDs, retain the complete snapshot behavior used by the single-entity
        ``needs_variant_review`` query.
        """
        names = None if entity_names is None else tuple(sorted(set(entity_names)))
        if names == ():
            return _VariantReviewChecker(MappingProxyType({}), MappingProxyType({}))
        entity_clause = (
            '' if names is None else f' AND entity_name IN ({", ".join("?" for _ in names)})'
        )
        with self._connect() as conn:
            ignored_by_entity: dict[str, set[str]] = defaultdict(set)
            for row in conn.execute(
                f"""
                SELECT entity_name, name FROM attr_knowledge
                WHERE status IN ({CLASSIFICATION_IRRELEVANT_STATUS_PLACEHOLDERS}){entity_clause}
                """,
                (*CLASSIFICATION_IRRELEVANT_STATUSES, *(names or ())),
            ):
                ignored_by_entity[row['entity_name']].add(row['name'])
            known_by_entity: dict[str, set[EntityVariantSignature]] = defaultdict(set)
            for row in conn.execute(
                f"""
                SELECT entity_name, attrs_json FROM variant_observations
                WHERE question = ? AND status IN (?, ?){entity_clause}
                """,
                (
                    ObservationQuestion.ENTITY_CLASSIFICATION,
                    ObservationStatus.CONFIRMED,
                    ObservationStatus.NOT_COLLECTIBLE,
                    *(names or ()),
                ),
            ):
                entity_name = row['entity_name']
                attrs, meta = _variant_key_parts(row['attrs_json'])
                known_by_entity[entity_name].add(
                    _variant_signature(attrs, meta, ignored_by_entity[entity_name])
                )
        return _VariantReviewChecker(
            MappingProxyType(
                {name: frozenset(values) for name, values in ignored_by_entity.items()}
            ),
            MappingProxyType(
                {name: frozenset(signatures) for name, signatures in known_by_entity.items()}
            ),
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
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO entity_knowledge(entity_name, status, reason, evidence)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_name) DO UPDATE SET
                    status = excluded.status, reason = excluded.reason, evidence = excluded.evidence
                """,
                (entity_name, status, reason, evidence),
            )

    def save_attr_knowledge(
        self,
        entity_name: str,
        name: str,
        status: AttrAuditStatus,
        *,
        reason: str = '',
        evidence: str | None = None,
        default_value: DefaultValue = UNKNOWN,
    ) -> None:
        """Update knowledge scoped to exactly one entity ID and attribute name."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO attr_knowledge(
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
                    None if default_value is UNKNOWN else _value_json(default_value),
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
        with self._connect() as conn:
            conn.execute(
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
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO entity_kind_confirmations(entity_name, kind, reason, evidence)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_name) DO UPDATE SET
                    kind = excluded.kind, reason = excluded.reason, evidence = excluded.evidence
                """,
                (entity_name, kind, reason, evidence),
            )
            conn.execute(
                'DELETE FROM entity_kind_confirmation_variants WHERE entity_name = ?',
                (entity_name,),
            )
            conn.executemany(
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
        with self._connect() as conn:
            confirmation = self._entity_kind_confirmation(conn, entity_name)
            if confirmation is None:
                return 0
            keys = tuple(
                (row['attrs_json'],)
                for row in conn.execute(
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
                    cursor = conn.execute(
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
            conn.execute(
                'DELETE FROM entity_kind_confirmation_variants WHERE entity_name = ?',
                (entity_name,),
            )
            conn.execute(
                'DELETE FROM entity_kind_confirmations WHERE entity_name = ?',
                (entity_name,),
            )
        return removed

    def rename_kind(self, old_name: str, new_name: str) -> None:
        """Move persisted audit conclusions to a renamed shared kind ID."""
        if old_name == new_name:
            return
        with self._connect() as conn:
            conn.execute(
                'UPDATE variant_observations SET kind = ? WHERE kind = ?',
                (new_name, old_name),
            )
            conn.execute(
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
        with self._connect() as conn:
            for variant in variants:
                conn.execute(
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
        return rule_candidates_for_detail(self.entity_detail(entity_name, report_id))

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
                EntityRule(
                    kind=candidate.kind,
                    when=candidate.when,
                    meta=candidate.meta,
                    missing=candidate.missing,
                    missing_meta=candidate.missing_meta,
                )
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
        with self._connect() as conn:
            report_id = self._report_id(conn, report_id)
            if report_id is None:
                return ()
            rows = conn.execute(
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
        with self._connect() as conn:
            conn.executescript(
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
                CREATE TABLE IF NOT EXISTS attr_knowledge (
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

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys = ON')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _report_id(conn: sqlite3.Connection, report_id: int | None) -> int | None:
        if report_id is not None:
            return report_id
        row = conn.execute('SELECT id FROM audit_reports ORDER BY id DESC LIMIT 1').fetchone()
        return None if row is None else int(row['id'])

    @staticmethod
    def _occurrences(
        conn: sqlite3.Connection,
        report_id: int,
        entity_name: str,
        source: AuditSource | None = None,
    ) -> tuple[RawEntityOccurrence, ...]:
        source_conditions = ''
        source_values: tuple[MapSource | str | None, ...] = ()
        if source is not None:
            source_conditions = ' AND scope = ? AND map_file = ? AND mod_file IS ? AND package IS ?'
            source_values = (source.scope, source.map_file, source.mod_file, source.package)
        rows = conn.execute(
            f"""
            SELECT attrs_json, scope, map_file, map_name, mod_name, mod_file, package, meta_json,
                room, entity_id
            FROM raw_entity_occurrences
            WHERE report_id = ? AND entity_name = ?{source_conditions}
            ORDER BY map_name COLLATE NOCASE, map_file COLLATE NOCASE, room COLLATE NOCASE
            """,
            (report_id, entity_name, *source_values),
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
    def _attr_summaries(
        conn: sqlite3.Connection,
        entity_name: str,
        variants: Iterable[EntityVariant],
    ) -> tuple[AttrAuditSummary, ...]:
        counts: dict[str, Counter[AttrValue | None]] = defaultdict(Counter)
        total_occurrence_count = 0
        for variant in variants:
            attrs: dict[str, AttrValue] = {
                name: value
                for name, value in variant.attrs.items()
                if name not in LOCATION_ATTR_NAMES
            }
            attrs.update(
                (f'{META_ATTR_PREFIX}{name}', value) for name, value in variant.meta.items()
            )
            for name, values in counts.items():
                if name not in attrs:
                    values[None] += variant.occurrence_count
            for name, value in attrs.items():
                if name not in counts and total_occurrence_count:
                    counts[name][None] = total_occurrence_count
                counts[name][value] += variant.occurrence_count
            total_occurrence_count += variant.occurrence_count
        knowledge_rows = {
            row['name']: row
            for row in conn.execute(
                """
                SELECT name, status, reason, evidence, default_json
                FROM attr_knowledge WHERE entity_name = ?
                """,
                (entity_name,),
            )
        }
        return tuple(
            AttrAuditSummary(
                name,
                tuple(sorted(values.items(), key=_attr_value_sort_key)),
                AttrAuditStatus(
                    knowledge_rows[name]['status'] if name in knowledge_rows else 'unknown'
                ),
                knowledge_rows[name]['reason'] if name in knowledge_rows else '',
                knowledge_rows[name]['evidence'] if name in knowledge_rows else None,
                _value(knowledge_rows[name]['default_json'])
                if name in knowledge_rows and knowledge_rows[name]['default_json'] is not None
                else UNKNOWN,
            )
            for name, values in sorted(counts.items(), key=lambda item: item[0].casefold())
        )

    @staticmethod
    def _variants(
        conn: sqlite3.Connection,
        report_id: int,
        entity_name: str,
    ) -> tuple[EntityVariant, ...]:
        observations_by_attrs: dict[str, list[VariantObservation]] = defaultdict(list)
        for row in conn.execute(
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
        rows = conn.execute(
            f"""
            SELECT {SEMANTIC_ATTRS_SQL} AS semantic_attrs_json, meta_json, COUNT(*) AS occurrence_count
            FROM raw_entity_occurrences
            WHERE report_id = ? AND entity_name = ?
            GROUP BY {SEMANTIC_ATTRS_SQL}, meta_json
            ORDER BY occurrence_count DESC, semantic_attrs_json
            """,
            (report_id, entity_name),
        )
        return tuple(
            EntityVariant(
                attrs := _attrs(row['semantic_attrs_json']),
                meta := _attrs(row['meta_json']),
                int(row['occurrence_count']),
                tuple(
                    observations_by_attrs[_variant_key(attrs, meta)]
                    or observations_by_attrs[_variant_key(attrs)]
                ),
            )
            for row in rows
        )

    @staticmethod
    def _entity_kind_confirmation(
        conn: sqlite3.Connection, entity_name: str
    ) -> EntityKindConfirmation | None:
        row = conn.execute(
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


def _json(attrs: dict[str, AttrValue]) -> str:
    return json.dumps(attrs, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _value_json(value: AttrValue | None) -> str:
    """Serialize one confirmed JSON default, including an explicit ``null``."""
    return json.dumps(value, ensure_ascii=False)


def _value(value: str) -> AttrValue | None:
    """Read one confirmed JSON default without treating ``null`` as unknown."""
    try:
        return ATTR_VALUE_ADAPTER.validate_json(value)
    except ValidationError as error:
        raise TypeError('Expected a serialized collectible attribute value.') from error


def _variant_key(attrs: dict[str, AttrValue], meta: dict[str, AttrValue] | None = None) -> str:
    """Return a stable observation key for explicit entity and map attributes."""
    return json.dumps(
        {'attrs': attrs, 'meta': meta or {}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )


def _attrs(value: str) -> dict[str, AttrValue]:
    try:
        return ATTRS_ADAPTER.validate_json(value)
    except ValidationError as error:
        raise TypeError('Expected serialized entity attributes to be a JSON object.') from error


def _semantic_attrs(attrs: dict[str, AttrValue]) -> dict[str, AttrValue]:
    """Return raw explicit attributes except physical instance-location fields."""
    return {name: value for name, value in attrs.items() if name not in LOCATION_ATTR_NAMES}


def _variant_key_parts(key: str) -> tuple[dict[str, AttrValue], dict[str, AttrValue]]:
    """Decode one stored variant key."""
    try:
        parsed = _VariantKey.model_validate_json(key)
    except ValidationError as error:
        raise TypeError('Expected serialized entity variant attributes and metadata.') from error
    return parsed.attrs, parsed.meta


def _variant_signature(
    attrs: Mapping[str, AttrValue],
    meta: Mapping[str, AttrValue],
    ignored_names: Collection[str],
) -> EntityVariantSignature:
    """Return the raw presence/value signature still relevant to classification review."""
    return EntityVariantSignature(
        tuple(sorted((name, value) for name, value in attrs.items() if name not in ignored_names)),
        tuple(
            sorted(
                (name, value)
                for name, value in meta.items()
                if f'{META_ATTR_PREFIX}{name}' not in ignored_names
            )
        ),
    )


def _attr_value_sort_key(item: tuple[AttrValue | None, int]) -> tuple[bool, str]:
    value, _ = item
    return value is not None, repr(value)


def _validate_observation_kind(status: ObservationStatus, kind: str | None) -> None:
    if status is ObservationStatus.CONFIRMED and kind is None:
        raise ValueError('A confirmed observation must identify a collectible kind.')
    if status is ObservationStatus.NOT_COLLECTIBLE and kind is not None:
        raise ValueError('A non-collectible observation must not identify a collectible kind.')
