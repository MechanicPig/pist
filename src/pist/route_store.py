"""Persist personal route-editor state."""

from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from pist.routes import MapRoute
from pist.sqlite_store import LOCAL_DATA_PATH, SQLiteStore


class RouteStore(SQLiteStore):
    """Store user-confirmed routes independently of records and scan caches."""

    def __init__(self, path: Path = LOCAL_DATA_PATH) -> None:
        super().__init__(path)
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS map_routes (
                    map_file TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def save(self, route: MapRoute) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO map_routes (map_file, updated_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(map_file) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (route.map_file, datetime.now(tz=UTC).isoformat(), route.model_dump_json()),
            )

    def load(self, map_file: str) -> MapRoute | None:
        with self.connect() as conn:
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
