"""Persist user-maintained local data in one SQLite database."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from pist.game.routes import MapRoute
from pist.models import RecordDraft

LOCAL_DATA_PATH = Path('.pist/local-data.sqlite3')
LEGACY_DRAFTS_DIR = 'drafts'
LEGACY_ROUTES_DIR = 'routes'


class LocalDataStore:
    """Store record drafts and map routes together in local SQLite data."""

    def __init__(self, path: Path = LOCAL_DATA_PATH) -> None:
        self._path = path
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path)
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS drafts (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    mod_metadata_name TEXT,
                    map_file TEXT,
                    save_slot INTEGER,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS map_routes (
                    map_file TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS legacy_imports (
                    source_path TEXT PRIMARY KEY
                );
                """
            )
            self._upgrade_draft_identity_columns(connection)
            self._import_legacy_json(connection)

    @staticmethod
    def _upgrade_draft_identity_columns(connection: sqlite3.Connection) -> None:
        """Add and populate the identity columns introduced after early local drafts."""
        columns = {row[1] for row in connection.execute('PRAGMA table_info(drafts)')}
        for name, definition in (
            ('mod_metadata_name', 'TEXT'),
            ('map_file', 'TEXT'),
            ('save_slot', 'INTEGER'),
        ):
            if name not in columns:
                connection.execute(f'ALTER TABLE drafts ADD COLUMN {name} {definition}')
        rows = connection.execute(
            '''
            SELECT id, payload FROM drafts
            WHERE mod_metadata_name IS NULL OR map_file IS NULL
            '''
        ).fetchall()
        for draft_id, payload in rows:
            try:
                draft = RecordDraft.model_validate_json(payload)
            except (TypeError, ValidationError, ValueError):
                continue
            connection.execute(
                '''
                UPDATE drafts
                SET mod_metadata_name = ?, map_file = ?, save_slot = ?
                WHERE id = ?
                ''',
                (draft.mod_metadata_name, draft.map_file, draft.save_slot, draft_id),
            )
        connection.execute(
            '''
            CREATE INDEX IF NOT EXISTS drafts_by_map_save
            ON drafts (mod_metadata_name, map_file, save_slot, id DESC)
            '''
        )

    def _import_legacy_json(self, connection: sqlite3.Connection) -> None:
        """Import each prior JSON draft or route once without deleting its source file."""
        for path in self._legacy_paths(LEGACY_DRAFTS_DIR):
            if self._was_imported(connection, path):
                continue
            try:
                draft = RecordDraft.model_validate_json(path.read_text(encoding='utf-8'))
            except (OSError, ValidationError, ValueError):
                continue
            connection.execute(
                '''
                INSERT INTO drafts (created_at, mod_metadata_name, map_file, save_slot, payload)
                VALUES (?, ?, ?, ?, ?)
                ''',
                self._draft_row(draft),
            )
            self._mark_imported(connection, path)
        for path in self._legacy_paths(LEGACY_ROUTES_DIR):
            if self._was_imported(connection, path):
                continue
            try:
                route = MapRoute.model_validate_json(path.read_text(encoding='utf-8'))
            except (OSError, ValidationError, ValueError):
                continue
            connection.execute(
                """
                INSERT INTO map_routes (map_file, updated_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(map_file) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (route.map_file, datetime.now(tz=UTC).isoformat(), route.model_dump_json()),
            )
            self._mark_imported(connection, path)

    def _legacy_paths(self, directory: str) -> tuple[Path, ...]:
        return tuple(sorted((self._path.parent / directory).glob('*.json')))

    @staticmethod
    def _was_imported(connection: sqlite3.Connection, path: Path) -> bool:
        return connection.execute(
            'SELECT 1 FROM legacy_imports WHERE source_path = ?', (str(path.resolve()),)
        ).fetchone() is not None

    @staticmethod
    def _mark_imported(connection: sqlite3.Connection, path: Path) -> None:
        connection.execute(
            'INSERT INTO legacy_imports (source_path) VALUES (?)', (str(path.resolve()),)
        )

    def save_draft(self, draft: RecordDraft) -> int:
        """Create or update a draft and return its stable local database identifier."""
        if draft.time_played is None:
            raise ValueError('Cannot save a record draft without native play time.')
        with self._connection() as connection:
            existing_id = self._draft_id(connection, draft)
            if existing_id is not None:
                connection.execute(
                    '''
                    UPDATE drafts
                    SET created_at = ?, payload = ?
                    WHERE id = ?
                    ''',
                    (draft.created_at.astimezone(UTC).isoformat(), draft.model_dump_json(), existing_id),
                )
                return existing_id
            cursor = connection.execute(
                '''
                INSERT INTO drafts (created_at, mod_metadata_name, map_file, save_slot, payload)
                VALUES (?, ?, ?, ?, ?)
                ''',
                self._draft_row(draft),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def existing_draft_id(self, draft: RecordDraft) -> int | None:
        """Return the latest local draft for the same Mod, map, and save slot."""
        with self._connection() as connection:
            return self._draft_id(connection, draft)

    @staticmethod
    def _draft_id(connection: sqlite3.Connection, draft: RecordDraft) -> int | None:
        row = connection.execute(
            '''
            SELECT id FROM drafts
            WHERE mod_metadata_name = ? AND map_file = ? AND save_slot IS ?
            ORDER BY id DESC LIMIT 1
            ''',
            (draft.mod_metadata_name, draft.map_file, draft.save_slot),
        ).fetchone()
        return None if row is None else row[0]

    @staticmethod
    def _draft_row(draft: RecordDraft) -> tuple[str, str, str, int | None, str]:
        return (
            draft.created_at.astimezone(UTC).isoformat(),
            draft.mod_metadata_name,
            draft.map_file,
            draft.save_slot,
            draft.model_dump_json(),
        )

    def load_draft(self, draft_id: int) -> RecordDraft:
        """Load one saved draft by its stable local identifier."""
        with self._connection() as connection:
            row = connection.execute(
                'SELECT payload FROM drafts WHERE id = ?', (draft_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f'No saved local draft with id {draft_id}.')
        try:
            return RecordDraft.model_validate_json(row[0])
        except (TypeError, ValidationError, ValueError) as error:
            raise ValueError(f'Invalid local draft with id {draft_id}.') from error

    def save_route(self, route: MapRoute) -> None:
        """Replace the saved user-confirmed route for one concrete map file."""
        with self._connection() as connection:
            connection.execute(
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
        with self._connection() as connection:
            row = connection.execute(
                'SELECT payload FROM map_routes WHERE map_file = ?', (map_file,)
            ).fetchone()
        if row is None:
            return None
        try:
            route = MapRoute.model_validate_json(row[0])
        except (TypeError, ValidationError, ValueError) as error:
            raise ValueError(f'Invalid saved map route for {map_file!r}.') from error
        return route if route.map_file == map_file else None
