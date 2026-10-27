"""Reject accidental access to the application's persistent development database."""

from contextlib import AbstractContextManager
from sqlite3 import Connection

import pytest

from pist.sqlite_store import LOCAL_DATA_PATH, SQLiteStore


@pytest.fixture(autouse=True)
def protect_application_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require tests to supply an isolated store instead of using the default path."""
    connect = SQLiteStore.connect
    protected_path = LOCAL_DATA_PATH.resolve()

    def isolated_connect(store: SQLiteStore) -> AbstractContextManager[Connection]:
        if store.path.resolve() == protected_path:
            raise AssertionError('Tests must inject a temporary database path.')
        return connect(store)

    monkeypatch.setattr(SQLiteStore, 'connect', isolated_connect)
