"""SQLite cache for expensive, reproducible Collab catalog scans."""

import json
from pathlib import Path

from pydantic import ConfigDict, TypeAdapter, ValidationError

from berries.game.collab import JournalRefs
from berries.game.maps import Map, ModMap

from . import catalog_fingerprint
from .sqlite_store import LOCAL_DATA_PATH, SQLiteStore

type CollabJournalIconPaths = dict[str, str | None]
_ICON_PATHS = TypeAdapter(CollabJournalIconPaths, config=ConfigDict(strict=True))
_CAMPAIGN_REFS = TypeAdapter(tuple[str, ...], config=ConfigDict(strict=True))


class CatalogCache(SQLiteStore):
    """Cache scan facts without importing personal record or route models."""

    def __init__(self, path: Path = LOCAL_DATA_PATH) -> None:
        super().__init__(path)
        with self.connect() as conn:
            conn.executescript(  # pyright: ignore[reportUnusedCallResult]
                """
                CREATE TABLE IF NOT EXISTS collab_journal_icons (
                    mod_path TEXT NOT NULL,
                    map_files TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    icons TEXT NOT NULL,
                    PRIMARY KEY (mod_path, map_files)
                );
                CREATE TABLE IF NOT EXISTS collab_journal_refs (
                    mod_path TEXT NOT NULL,
                    map_file TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    campaign_refs TEXT NOT NULL,
                    invalid_count INTEGER NOT NULL CHECK (invalid_count >= 0),
                    PRIMARY KEY (mod_path, map_file)
                );
                """
            )

    def load_icons(
        self, mod_path: str, map_files: tuple[str, ...], fingerprint: str
    ) -> CollabJournalIconPaths | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT icons FROM collab_journal_icons
                WHERE mod_path = ? AND map_files = ? AND fingerprint = ?
                """,
                (mod_path, json.dumps(map_files), fingerprint),
            ).fetchone()
        if row is None:
            return None
        try:
            return _ICON_PATHS.validate_json(row[0])
        except TypeError, ValidationError:
            return None

    def save_icons(
        self,
        mod_path: str,
        map_files: tuple[str, ...],
        fingerprint: str,
        icons: CollabJournalIconPaths,
    ) -> None:
        with self.connect() as conn:
            conn.execute(  # pyright: ignore[reportUnusedCallResult]
                """
                INSERT INTO collab_journal_icons (mod_path, map_files, fingerprint, icons)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(mod_path, map_files) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    icons = excluded.icons
                """,
                (mod_path, json.dumps(map_files), fingerprint, json.dumps(icons, sort_keys=True)),
            )

    def load_refs(self, mod_path: str, map_file: str, fingerprint: str) -> JournalRefs | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT campaign_refs, invalid_count FROM collab_journal_refs
                WHERE mod_path = ? AND map_file = ? AND fingerprint = ?
                """,
                (mod_path, map_file, fingerprint),
            ).fetchone()
        if row is None:
            return None
        try:
            campaign_refs = _CAMPAIGN_REFS.validate_json(row[0])
        except TypeError, ValidationError:
            return None
        invalid_count = row[1]
        if type(invalid_count) is not int or invalid_count < 0:
            return None
        return JournalRefs(campaign_refs, invalid_count)

    def journal_refs(self, map_file: Map) -> JournalRefs:
        """Resolve journal references, reusing cached facts for unchanged Mod maps.

        Read errors and map decoding warnings propagate to the caller, which owns
        diagnostics and continuation policy. Failed reads are never cached.
        """
        if not isinstance(map_file, ModMap):
            return JournalRefs.from_map(map_file)
        mod_path = str(map_file.mod.path)
        path = map_file.file_path.as_posix()
        fingerprint = catalog_fingerprint.journal_fingerprint(map_file)
        cached = self.load_refs(mod_path, path, fingerprint)
        if cached is not None:
            return cached
        refs = JournalRefs.from_map(map_file)
        self.save_refs(mod_path, path, fingerprint, refs)
        return refs

    def save_refs(
        self,
        mod_path: str,
        map_file: str,
        fingerprint: str,
        refs: JournalRefs,
    ) -> None:
        with self.connect() as conn:
            conn.execute(  # pyright: ignore[reportUnusedCallResult]
                """
                INSERT INTO collab_journal_refs (
                    mod_path, map_file, fingerprint, campaign_refs, invalid_count
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(mod_path, map_file) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    campaign_refs = excluded.campaign_refs,
                    invalid_count = excluded.invalid_count
                """,
                (
                    mod_path,
                    map_file,
                    fingerprint,
                    json.dumps(refs.campaign_refs),
                    refs.invalid_count,
                ),
            )
