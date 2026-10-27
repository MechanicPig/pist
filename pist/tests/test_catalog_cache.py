from pathlib import Path

import pytest

from berries.game.collab import JournalRefs
from berries.game.content import ContentPath
from berries.game.maps import Map, ModMap
from pist.catalog_cache import CatalogCache
from test_support.mod_factory import make_installed_mod


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
    refs = JournalRefs(('Collab/1-Easy', 'Addon/Maps'), 2)

    store.save_refs(
        'C:/Celeste/Mods/Collab.zip',
        'Maps/Collab/0-Lobbies/1-Easy.bin',
        'first',
        refs,
    )

    assert (
        store.load_refs(
            'C:/Celeste/Mods/Collab.zip',
            'Maps/Collab/0-Lobbies/1-Easy.bin',
            'first',
        )
        == refs
    )
    assert (
        store.load_refs(
            'C:/Celeste/Mods/Collab.zip',
            'Maps/Collab/0-Lobbies/1-Easy.bin',
            'changed',
        )
        is None
    )


@pytest.mark.parametrize('fail_first', (False, True))
def test_journal_refs_reuse_persistent_cache_but_do_not_cache_failed_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_first: bool
) -> None:
    path = ContentPath('Maps/Example/0-Lobbies/1-Easy.bin')
    mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path=tmp_path / 'Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        maps=[path],
    )
    map_file = ModMap(path, mod)
    calls = 0

    def read_refs(_: Map) -> JournalRefs:
        nonlocal calls
        calls += 1
        if fail_first and calls == 1:
            raise ValueError('invalid map')
        return JournalRefs(('Example/1-Easy',))

    monkeypatch.setattr('pist.catalog_cache.JournalRefs.from_map', read_refs)
    monkeypatch.setattr(
        'pist.catalog_cache.catalog_fingerprint.journal_fingerprint', lambda _: 'first'
    )
    database = tmp_path / 'catalog.sqlite3'
    first = CatalogCache(database)
    if fail_first:
        with pytest.raises(ValueError, match='invalid map'):
            first.journal_refs(map_file)
    assert first.journal_refs(map_file).campaign_refs == ('Example/1-Easy',)
    assert CatalogCache(database).journal_refs(map_file).campaign_refs == ('Example/1-Easy',)
    assert calls == (2 if fail_first else 1)
