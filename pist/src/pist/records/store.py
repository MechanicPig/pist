"""Persist personal records and their explicit Smart Sheet associations."""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from sqlite3 import Row

from pydantic import JsonValue, PositiveInt, TypeAdapter, ValidationError

from berries.models import FrozenModel
from pist.records.models import MapRecord
from pist.records.schema import RECORD_TABLE_SQL, record_columns, record_from_row
from pist.smartsheet.models import FieldsResult, MainSheetSnapshot
from pist.sqlite_store import LOCAL_DATA_PATH, SQLiteStore

_VALUES = TypeAdapter(dict[str, JsonValue])


class RecordSyncState(StrEnum):
    """Whether the latest local record has been confirmed against the remote row."""

    UNCHECKED = 'unchecked'
    PENDING = 'pending'
    SYNCED = 'synced'


class StoredRecord(FrozenModel):
    """One persisted record together with its synchronization state."""

    record: MapRecord
    sync_state: RecordSyncState


class RecordStorageError(RuntimeError):
    """A local record operation failed at the filesystem or SQLite boundary."""


class RecordSheetLink(FrozenModel):
    """Identify the remote row associated with one internal local record."""

    local_id: PositiveInt
    file_id: str
    sheet_id: str
    record_id: str
    confirmed_values: dict[str, JsonValue] | None = None
    confirmed_at: datetime | None = None


class RecordSheetSnapshot(FrozenModel):
    """Last observed remote contents, independent of acknowledgement."""

    link: RecordSheetLink
    values: dict[str, JsonValue]
    fields: FieldsResult
    read_at: datetime


class RecordSheetAddAttempt(FrozenModel):
    """An add request whose remote row identity has not yet been durably recorded."""

    local_id: PositiveInt
    file_id: str
    sheet_id: str
    started_at: datetime
    mod_metadata_name: str
    map_name: str


class RecordStore(SQLiteStore):
    """Save by explicit identity; map matching only supplies editing candidates."""

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        """Run one transaction, reporting storage failures as record-domain errors."""
        try:
            with super().connect() as conn:
                yield conn
        except (sqlite3.Error, OSError) as error:
            raise RecordStorageError(f'本地记录存储操作失败：{self.path}：{error}') from error

    def __init__(self, path: Path = LOCAL_DATA_PATH) -> None:
        super().__init__(path)
        with self.connect() as conn:
            conn.executescript(RECORD_TABLE_SQL)
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS records_by_map
                ON records (mod_metadata_name, map_file);
                CREATE TABLE IF NOT EXISTS record_sheet_links (
                    local_id INTEGER NOT NULL REFERENCES records (id),
                    file_id TEXT NOT NULL,
                    sheet_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    confirmed_values TEXT,
                    confirmed_at TEXT,
                    CHECK ((confirmed_values IS NULL) = (confirmed_at IS NULL)),
                    PRIMARY KEY (local_id, file_id, sheet_id),
                    UNIQUE (file_id, sheet_id, record_id)
                );
                CREATE TABLE IF NOT EXISTS record_sheet_snapshots (
                    local_id INTEGER NOT NULL REFERENCES records (id),
                    file_id TEXT NOT NULL,
                    sheet_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    values_json TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    read_at TEXT NOT NULL,
                    PRIMARY KEY (local_id, file_id, sheet_id)
                );
                CREATE TABLE IF NOT EXISTS record_number_counter (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_number INTEGER NOT NULL CHECK (last_number >= 0)
                );
                INSERT OR IGNORE INTO record_number_counter VALUES (1, 0);
                CREATE TABLE IF NOT EXISTS record_sheet_add_attempts (
                    local_id INTEGER NOT NULL REFERENCES records (id),
                    file_id TEXT NOT NULL,
                    sheet_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    mod_metadata_name TEXT NOT NULL,
                    map_name TEXT NOT NULL,
                    PRIMARY KEY (local_id, file_id, sheet_id)
                );
                """
            )

    def save(self, record: MapRecord) -> int:
        """Insert a new record or update exactly its explicit internal ID."""
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if record.local_id is not None:
                row = conn.execute(
                    'SELECT record_number FROM records WHERE id = ?', (record.local_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f'No saved local record with id {record.local_id}.')
                if record.record_number != row[0]:
                    raise ValueError('Cannot change an existing formal record number.')
                local_id = record.local_id
            else:
                columns = record_columns(record)
                cursor = conn.execute(
                    f'INSERT INTO records ({", ".join(columns)}, sync_state) '
                    f"VALUES ({', '.join('?' for _ in columns)}, 'pending')",
                    tuple(columns.values()),
                )
                assert cursor.lastrowid is not None
                local_id = cursor.lastrowid
            number = record.record_number
            status = record.status
            if number is not None and status == '未开始':
                raise ValueError('A numbered record cannot return to 未开始.')
            if (
                number is None
                and record.time_played is not None
                and status not in (None, '', '未开始')
            ):
                number = conn.execute(
                    'SELECT last_number + 1 FROM record_number_counter WHERE id = 1'
                ).fetchone()[0]
            if number is not None:
                conn.execute(
                    'UPDATE record_number_counter SET last_number = MAX(last_number, ?) WHERE id = 1',
                    (number,),
                )
            saved = record.model_copy(update={'local_id': local_id, 'record_number': number})
            columns = record_columns(saved)
            conn.execute(
                f'UPDATE records SET {", ".join(f"{name} = ?" for name in columns)}, '
                "sync_state = 'pending' WHERE id = ?",
                (*columns.values(), local_id),
            )
        return local_id

    def list_records(self) -> tuple[StoredRecord, ...]:
        """Read all local records and sync states in stable formal-number order."""
        with self.connect() as conn:
            conn.row_factory = Row
            rows = conn.execute(
                'SELECT * FROM records ORDER BY record_number IS NULL, record_number, id'
            ).fetchall()
        try:
            return tuple(
                StoredRecord(record=record_from_row(row), sync_state=row['sync_state'])
                for row in rows
            )
        except (TypeError, ValueError) as error:
            raise RecordStorageError('本地记录列表包含无效数据。') from error

    def matching_ids(self, record: MapRecord) -> tuple[int, ...]:
        """Find map candidates independently of the originating save slot."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM records WHERE mod_metadata_name = ? AND (
                    (map_file IS NOT NULL AND map_file = ?) OR
                    (map_file IS NULL AND map_name = ?)
                ) ORDER BY id
                """,
                (record.mod_metadata_name, record.map_file, record.map_name),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def load(self, record_id: int) -> MapRecord:
        """Load a record by its internal ID, including its formal number."""
        with self.connect() as conn:
            conn.row_factory = Row
            row = conn.execute('SELECT * FROM records WHERE id = ?', (record_id,)).fetchone()
        if row is None:
            raise ValueError(f'No saved local record with id {record_id}.')
        try:
            return record_from_row(row)
        except (TypeError, ValidationError, ValueError) as error:
            raise ValueError(f'Invalid local record with id {record_id}.') from error

    def link_sheet_record(self, link: RecordSheetLink) -> None:
        """Associate an exact remote row and resolve its add attempt atomically."""
        with self.connect() as conn:
            if link.confirmed_values is not None or link.confirmed_at is not None:
                raise ValueError('Use confirm_sheet_record to acknowledge remote data.')
            conn.execute('PRAGMA foreign_keys = ON')
            conn.execute(
                'INSERT INTO record_sheet_links (local_id, file_id, sheet_id, record_id) '
                'VALUES (?, ?, ?, ?)',
                (link.local_id, link.file_id, link.sheet_id, link.record_id),
            )
            conn.execute(
                'DELETE FROM record_sheet_add_attempts '
                'WHERE local_id = ? AND file_id = ? AND sheet_id = ?',
                (link.local_id, link.file_id, link.sheet_id),
            )

    def begin_sheet_add(self, attempt: RecordSheetAddAttempt) -> None:
        """Persist an add intent before sending it, rejecting unresolved prior attempts."""
        with self.connect() as conn:
            conn.execute('PRAGMA foreign_keys = ON')
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute(
                'SELECT 1 FROM record_sheet_add_attempts WHERE local_id = ?', (attempt.local_id,)
            ).fetchone():
                raise ValueError('之前的新增结果尚未确认，不能再次新增。')
            conn.execute(
                'INSERT INTO record_sheet_add_attempts '
                '(local_id, file_id, sheet_id, started_at, mod_metadata_name, map_name) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (
                    attempt.local_id,
                    attempt.file_id,
                    attempt.sheet_id,
                    attempt.started_at.astimezone(UTC).isoformat(),
                    attempt.mod_metadata_name,
                    attempt.map_name,
                ),
            )

    def sheet_add_attempts(self, record_id: int) -> tuple[RecordSheetAddAttempt, ...]:
        """Return unresolved add attempts independently of editable record names."""
        with self.connect() as conn:
            rows = conn.execute(
                'SELECT file_id, sheet_id, started_at, mod_metadata_name, map_name '
                'FROM record_sheet_add_attempts '
                'WHERE local_id = ? ORDER BY file_id, sheet_id',
                (record_id,),
            ).fetchall()
        return tuple(
            RecordSheetAddAttempt(
                local_id=record_id,
                file_id=file,
                sheet_id=sheet,
                started_at=datetime.fromisoformat(start),
                mod_metadata_name=mod_name,
                map_name=map_name,
            )
            for file, sheet, start, mod_name, map_name in rows
        )

    def bound_sheet_record_ids(self, file_id: str, sheet_id: str) -> frozenset[str]:
        """Return remote row identities already associated with any local record."""
        with self.connect() as conn:
            rows = conn.execute(
                'SELECT record_id FROM record_sheet_links WHERE file_id = ? AND sheet_id = ?',
                (file_id, sheet_id),
            ).fetchall()
        return frozenset(row[0] for row in rows)

    def allow_sheet_add_retry(self, attempt: RecordSheetAddAttempt) -> None:
        """Resolve the exact attempt after explicit confirmation that no row was added."""
        with self.connect() as conn:
            cursor = conn.execute(
                'DELETE FROM record_sheet_add_attempts WHERE local_id = ? AND file_id = ? '
                'AND sheet_id = ? AND started_at = ? AND mod_metadata_name = ? AND map_name = ?',
                (
                    attempt.local_id,
                    attempt.file_id,
                    attempt.sheet_id,
                    attempt.started_at.astimezone(UTC).isoformat(),
                    attempt.mod_metadata_name,
                    attempt.map_name,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError('新增尝试已变化，请重新核对。')

    def sheet_links(self, record_id: int) -> tuple[RecordSheetLink, ...]:
        """Return the remote associations of one internal local record."""
        with self.connect() as conn:
            rows = conn.execute(
                'SELECT file_id, sheet_id, record_id, confirmed_values, confirmed_at '
                'FROM record_sheet_links WHERE local_id = ? ORDER BY file_id, sheet_id',
                (record_id,),
            ).fetchall()
        return tuple(
            RecordSheetLink(
                local_id=record_id,
                file_id=file,
                sheet_id=sheet,
                record_id=remote,
                confirmed_values=None if values is None else _VALUES.validate_json(values),
                confirmed_at=None if checked is None else datetime.fromisoformat(checked),
            )
            for file, sheet, remote, values, checked in rows
        )

    def sync_state(self, record_id: int) -> RecordSyncState:
        """Return the acknowledgement state of the latest local contents."""
        with self.connect() as conn:
            row = conn.execute(
                'SELECT sync_state FROM records WHERE id = ?', (record_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f'No saved local record with id {record_id}.')
        return RecordSyncState(row[0])

    def observe_sheet(self, snapshot: MainSheetSnapshot, *, read_at: datetime) -> None:
        """Save every associated remote row and its schema without advancing the baseline."""
        rows = {row.record_id: row for row in snapshot.records}
        fields_json = snapshot.fields.model_dump_json()
        read_at_text = read_at.astimezone(UTC).isoformat()
        with self.connect() as conn:
            links = conn.execute(
                'SELECT local_id, record_id FROM record_sheet_links WHERE file_id = ? AND sheet_id = ?',
                (snapshot.file_id, snapshot.sheet_id),
            ).fetchall()
            for local_id, remote_id in links:
                if remote_id not in rows:
                    continue
                conn.execute(
                    'INSERT INTO record_sheet_snapshots '
                    '(local_id, file_id, sheet_id, record_id, values_json, fields_json, read_at) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (local_id, file_id, sheet_id) '
                    'DO UPDATE SET record_id = excluded.record_id, values_json = excluded.values_json, '
                    'fields_json = excluded.fields_json, read_at = excluded.read_at',
                    (
                        local_id,
                        snapshot.file_id,
                        snapshot.sheet_id,
                        remote_id,
                        _VALUES.dump_json(rows[remote_id].values).decode(),
                        fields_json,
                        read_at_text,
                    ),
                )

    def sheet_snapshots(self, record_id: int) -> tuple[RecordSheetSnapshot, ...]:
        """Return the complete last-observed remote rows, including formula results."""
        with self.connect() as conn:
            rows = conn.execute(
                'SELECT file_id, sheet_id, record_id, values_json, fields_json, read_at '
                'FROM record_sheet_snapshots WHERE local_id = ? ORDER BY file_id, sheet_id',
                (record_id,),
            ).fetchall()
        return tuple(
            RecordSheetSnapshot(
                link=RecordSheetLink(
                    local_id=record_id, file_id=file, sheet_id=sheet, record_id=remote
                ),
                values=_VALUES.validate_json(values),
                fields=FieldsResult.model_validate_json(fields, by_name=True),
                read_at=datetime.fromisoformat(read),
            )
            for file, sheet, remote, values, fields, read in rows
        )

    def confirm_sheet_record(
        self,
        link: RecordSheetLink,
        values: dict[str, JsonValue],
        *,
        submitted: MapRecord,
        confirmed_at: datetime,
    ) -> None:
        """Acknowledge verified remote contents without losing newer local edits.

        Call only after the remote row has been confirmed to contain the submitted
        public values, either by equality checking or verified writing. A read alone
        before editing or a failed write is not acknowledgement.
        If local contents changed during submission, they remain pending.
        """
        if submitted.local_id != link.local_id:
            raise ValueError('The submitted record does not match the remote association.')
        payload = _VALUES.dump_json(_VALUES.validate_python(values)).decode()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.row_factory = Row
            row = conn.execute('SELECT * FROM records WHERE id = ?', (link.local_id,)).fetchone()
            if row is None:
                raise ValueError(f'No saved local record with id {link.local_id}.')
            current = record_from_row(row)
            conn.execute(
                'INSERT INTO record_sheet_links '
                '(local_id, file_id, sheet_id, record_id, confirmed_values, confirmed_at) '
                'VALUES (?, ?, ?, ?, ?, ?) '
                'ON CONFLICT (local_id, file_id, sheet_id) DO UPDATE SET '
                'confirmed_values = excluded.confirmed_values, '
                'confirmed_at = excluded.confirmed_at WHERE record_id = excluded.record_id',
                (
                    link.local_id,
                    link.file_id,
                    link.sheet_id,
                    link.record_id,
                    payload,
                    confirmed_at.astimezone(UTC).isoformat(),
                ),
            )
            if conn.execute('SELECT changes()').fetchone()[0] != 1:
                raise ValueError('Cannot replace an existing remote row association.')
            conn.execute(
                'DELETE FROM record_sheet_add_attempts '
                'WHERE local_id = ? AND file_id = ? AND sheet_id = ?',
                (link.local_id, link.file_id, link.sheet_id),
            )
            state = RecordSyncState.SYNCED if current == submitted else RecordSyncState.PENDING
            conn.execute('UPDATE records SET sync_state = ? WHERE id = ?', (state, link.local_id))
