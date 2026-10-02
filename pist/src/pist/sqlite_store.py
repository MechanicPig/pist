"""Small SQLite connection boundary shared by independent stores."""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from pist.paths import PIST_DIR

LOCAL_DATA_PATH = PIST_DIR / 'local-data.sqlite3'


class SQLiteStore:
    """Own short-lived transactions for one SQLite-backed responsibility."""

    def __init__(self, path: Path = LOCAL_DATA_PATH) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
