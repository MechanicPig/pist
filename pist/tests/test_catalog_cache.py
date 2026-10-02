from pathlib import Path

from berries.game.collab import JournalReferences
from pist.catalog_cache import CatalogCache


def test_catalog_cache_caches_collab_journal_icons(tmp_path: Path) -> None:
    store = CatalogCache(tmp_path / '.pist/local-data.sqlite3')
    map_files = ('Maps/Collab/1-Easy.bin', 'Maps/Collab/2-Medium.bin')
    icons = {
        map_files[0]: 'areas/Collab/meters/1-easy',
        map_files[1]: None,
    }

    store.save_icons('C:/Celeste/Mods/Collab.zip', map_files, 'first', icons)

    assert store.load_icons('C:/Celeste/Mods/Collab.zip', map_files, 'first') == icons
    assert store.load_icons('C:/Celeste/Mods/Collab.zip', map_files, 'changed') is None


def test_catalog_cache_discards_invalid_cached_collab_journal_icons(tmp_path: Path) -> None:
    store = CatalogCache(tmp_path / '.pist/local-data.sqlite3')
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO collab_journal_icons (mod_path, map_files, fingerprint, icons)
            VALUES (?, ?, ?, ?)
            """,
            ('C:/Celeste/Mods/Collab.zip', '[]', 'first', '{"Maps/Collab.bin": 1}'),
        )

    assert store.load_icons('C:/Celeste/Mods/Collab.zip', (), 'first') is None


def test_catalog_cache_caches_collab_journal_refs(tmp_path: Path) -> None:
    store = CatalogCache(tmp_path / '.pist/local-data.sqlite3')
    references = JournalReferences(('Collab/1-Easy', 'Addon/Maps'), 2)

    store.save_references(
        'C:/Celeste/Mods/Collab.zip',
        'Maps/Collab/0-Lobbies/1-Easy.bin',
        'first',
        references,
    )

    assert (
        store.load_references(
            'C:/Celeste/Mods/Collab.zip',
            'Maps/Collab/0-Lobbies/1-Easy.bin',
            'first',
        )
        == references
    )
    assert (
        store.load_references(
            'C:/Celeste/Mods/Collab.zip',
            'Maps/Collab/0-Lobbies/1-Easy.bin',
            'changed',
        )
        is None
    )
