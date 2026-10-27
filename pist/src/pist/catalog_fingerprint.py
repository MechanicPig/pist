"""Cheap physical signatures for Pist's rebuildable map catalog caches."""

from collections.abc import Iterable
from hashlib import sha256

from berries.game import content
from berries.game.content import ContentPath
from berries.game.map_data import open_map_entry
from berries.game.maps import Map
from berries.game.mods import InstalledMod


def _entry_fingerprint(entry: content.ContentEntry) -> str:
    """Return a cheap signature that changes when one content entry changes."""
    if isinstance(entry, content.DirContentEntry):
        stat = entry.path.stat()
        return f'{stat.st_size}:{stat.st_mtime_ns}'
    if isinstance(entry, content.ZipContentEntry):
        info = entry.zip_file.member_index.entries[entry.at.as_posix()]
        return f'{info.CRC}:{info.compress_size}:{info.file_size}:{info.date_time}'
    raise TypeError(f'Unsupported content entry type: {type(entry).__name__}')


def journal_fingerprint(map_file: Map) -> str:
    """Return a cheap source fingerprint for one concrete lobby map resource."""
    with open_map_entry(map_file) as map_entry:
        return _entry_fingerprint(map_entry)


def collab_journal_icon_fingerprint(mod: InstalledMod, maps: Iterable[ContentPath]) -> str:
    """Return a fingerprint that changes when a source map package changes."""
    map_files = sorted(map_info for map_info in maps)
    digest = sha256()
    with mod.open() as root:
        for map_file in map_files:
            entry = root.joinpath(map_file)
            digest.update(f'{map_file}\0{_entry_fingerprint(entry)}\0'.encode())
    return digest.hexdigest()
