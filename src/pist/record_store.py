"""Persist personal map records."""

import sqlite3
from datetime import UTC
from pathlib import Path

from pydantic import ValidationError

from pist.records import MapRecord
from pist.sqlite_store import LOCAL_DATA_PATH, SQLiteStore


class RecordStore(SQLiteStore):
    """Store personal records without depending on routes or scan caches."""

    def __init__(self, path: Path = LOCAL_DATA_PATH) -> None:
        super().__init__(path)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    mod_metadata_name TEXT,
                    map_file TEXT,
                    save_slot INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS records_by_map_save
                ON records (mod_metadata_name, map_file, save_slot, id DESC);
                """
            )

    def save(self, record: MapRecord) -> int:
        if record.time_played is None:
            raise ValueError('Cannot save a record without native play time.')
        with self.connect() as conn:
            existing_id = self._record_id(conn, record)
            if existing_id is not None:
                conn.execute(
                    'UPDATE records SET created_at = ?, payload = ? WHERE id = ?',
                    (
                        record.created_at.astimezone(UTC).isoformat(),
                        record.model_dump_json(),
                        existing_id,
                    ),
                )
                return existing_id
            cursor = conn.execute(
                """
                INSERT INTO records (created_at, mod_metadata_name, map_file, save_slot, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.created_at.astimezone(UTC).isoformat(),
                    record.mod_metadata_name,
                    record.map_file,
                    record.save_slot,
                    record.model_dump_json(),
                ),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def existing_id(self, record: MapRecord) -> int | None:
        with self.connect() as conn:
            return self._record_id(conn, record)

    @staticmethod
    def _record_id(conn: sqlite3.Connection, record: MapRecord) -> int | None:
        row = conn.execute(
            """
            SELECT id FROM records
            WHERE mod_metadata_name = ? AND map_file = ? AND save_slot = ?
            ORDER BY id DESC LIMIT 1
            """,
            (record.mod_metadata_name, record.map_file, record.save_slot),
        ).fetchone()
        return None if row is None else row[0]

    def load(self, record_id: int) -> MapRecord:
        with self.connect() as conn:
            row = conn.execute('SELECT payload FROM records WHERE id = ?', (record_id,)).fetchone()
        if row is None:
            raise ValueError(f'No saved local record with id {record_id}.')
        try:
            return MapRecord.model_validate_json(row[0])
        except (TypeError, ValidationError, ValueError) as error:
            raise ValueError(f'Invalid local record with id {record_id}.') from error
