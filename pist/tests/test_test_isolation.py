"""Exercise the test-only guard before any persistent database can be opened."""

from pathlib import Path

import pytest

from pist.sqlite_store import LOCAL_DATA_PATH, SQLiteStore


@pytest.mark.parametrize(
    'path',
    (LOCAL_DATA_PATH, LOCAL_DATA_PATH.parent / 'unused' / '..' / LOCAL_DATA_PATH.name),
)
def test_personal_database_is_rejected(path: Path) -> None:
    with (
        pytest.raises(AssertionError, match='temporary database path'),
        SQLiteStore(path).connect(),
    ):
        pytest.fail('The personal database must not be opened.')


def test_temporary_database_remains_usable(tmp_path: Path) -> None:
    with SQLiteStore(tmp_path / 'isolated.sqlite3').connect() as connection:
        assert connection.execute('SELECT 1').fetchone() == (1,)
