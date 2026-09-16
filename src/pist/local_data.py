"""Persist user-maintained local data in one SQLite database."""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from pist.game.routes import MapRoute
from pist.records import MapRecord

LOCAL_DATA_PATH = Path('.pist/local-data.sqlite3')


class LocalDataStore:
    """Store map records and routes together in local SQLite data."""

    def __init__(self, path: Path = LOCAL_DATA_PATH) -> None:
        self._path = path
        self._initialize()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'drafts'"
                ).fetchone()
                is not None
                and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'records'"
                ).fetchone()
                is None
            ):
                conn.execute('ALTER TABLE drafts RENAME TO records')
                conn.execute('DROP INDEX IF EXISTS drafts_by_map_save')
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
                CREATE TABLE IF NOT EXISTS map_routes (
                    map_file TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS records_by_map_save
                ON records (mod_metadata_name, map_file, save_slot, id DESC);
                """
            )

    def save_record(self, record: MapRecord) -> int:
        """Create or update a record and return its stable local database identifier."""
        if record.time_played is None:
            raise ValueError('Cannot save a record without native play time.')
        with self._connect() as conn:
            existing_id = self._record_id(conn, record)
            if existing_id is not None:
                conn.execute(
                    """
                    UPDATE records
                    SET created_at = ?, payload = ?
                    WHERE id = ?
                    """,
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
                self._record_row(record),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def existing_record_id(self, record: MapRecord) -> int | None:
        """Return the latest local record for the same Mod, map, and save slot."""
        with self._connect() as conn:
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

    @staticmethod
    def _record_row(record: MapRecord) -> tuple[str, str, str, int, str]:
        return (
            record.created_at.astimezone(UTC).isoformat(),
            record.mod_metadata_name,
            record.map_file,
            record.save_slot,
            record.model_dump_json(),
        )

    def load_record(self, record_id: int) -> MapRecord:
        """Load one saved record by its stable local identifier."""
        with self._connect() as conn:
            row = conn.execute('SELECT payload FROM records WHERE id = ?', (record_id,)).fetchone()
        if row is None:
            raise ValueError(f'No saved local record with id {record_id}.')
        try:
            return MapRecord.model_validate_json(row[0])
        except (TypeError, ValidationError, ValueError) as error:
            raise ValueError(f'Invalid local record with id {record_id}.') from error

    def save_route(self, route: MapRoute) -> None:
        """Replace the saved user-confirmed route for one concrete map file."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO map_routes (map_file, updated_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(map_file) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (
                    route.map_file,
                    datetime.now(tz=UTC).isoformat(),
                    route.model_dump_json(),
                ),
            )

    def load_route(self, map_file: str) -> MapRoute | None:
        """Return the saved route for one map file, if there is one."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT payload FROM map_routes WHERE map_file = ?', (map_file,)
            ).fetchone()
        if row is None:
            return None
        try:
            route = MapRoute.model_validate_json(row[0])
        except (TypeError, ValidationError, ValueError) as error:
            raise ValueError(f'Invalid saved map route for {map_file!r}.') from error
        return route if route.map_file == map_file else None
