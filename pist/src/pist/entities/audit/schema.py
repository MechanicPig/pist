"""Storage contract for deduplicated raw audit occurrences."""

import hashlib
import json

type OccurrenceValues = tuple[
    str, str, str, str, str, str | None, str | None, str | None, str, str, int | None
]

RAW_OCCURRENCES_SQL = """
CREATE TABLE IF NOT EXISTS raw_entity_occurrences (
    id INTEGER PRIMARY KEY,
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
    entity_id INTEGER,
    fingerprint BLOB NOT NULL UNIQUE CHECK (length(fingerprint) = 32)
);
CREATE INDEX IF NOT EXISTS raw_entity_occurrences_entity ON raw_entity_occurrences(entity_name);
"""


def occurrence_fingerprint(values: OccurrenceValues) -> bytes:
    """Fingerprint the complete stored occurrence, preserving nulls and exact identifiers."""
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    ).digest()
