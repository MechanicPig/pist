"""Read CollabUtils2 lobby identities and static journal references."""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass

from pist.game import binmap
from pist.game.content import BadContentEntry, ContentEntry
from pist.game.levels import Level, LoadedMap

JOURNAL_TRIGGER = 'CollabUtils2/JournalTrigger'


@dataclass(frozen=True, slots=True)
class JournalReferences:
    """Static Campaign references and diagnostics read from one lobby map."""

    campaign_refs: tuple[str, ...]
    invalid_count: int = 0

    @property
    def diagnostics(self) -> tuple[str, ...]:
        """Describe malformed references without persisting presentation text."""
        if not self.invalid_count:
            return ()
        return (f'发现 {self.invalid_count} 个没有有效 levelset 的日志入口。',)


def is_lobby(level: Level, collab_ids: Iterable[str]) -> bool:
    """Apply CollabUtils2's exact lobby SID predicate to one assembled Level."""
    return any(
        level.sid.startswith(f'{collab_id}/0-Lobbies/')
        and level.sid != f'{collab_id}/0-Lobbies/0-Prologue'
        for collab_id in collab_ids
    )


def journal_references(loaded_map: LoadedMap) -> JournalReferences:
    """Read exact JournalTrigger level-set identities in source order."""
    with _open_map_entry(loaded_map) as map_entry:
        data = map_entry.read_bytes()
    map_data = binmap.parse_map_bin(data, allow_trailing=True)
    result: list[str] = []
    seen: set[str] = set()
    invalid_count = 0
    for element in map_data.root.walk():
        if element.name != JOURNAL_TRIGGER:
            continue
        value = element.attrs.get('levelset')
        if not isinstance(value, str) or not value:
            invalid_count += 1
        elif value not in seen:
            seen.add(value)
            result.append(value)
    return JournalReferences(tuple(result), invalid_count)


def journal_fingerprint(loaded_map: LoadedMap) -> str:
    """Return a cheap source fingerprint for one concrete lobby map resource."""
    with _open_map_entry(loaded_map) as map_entry:
        return map_entry.fingerprint()


@contextmanager
def _open_map_entry(loaded_map: LoadedMap) -> Generator[ContentEntry]:
    try:
        with loaded_map.open_content() as root:
            map_entry = root.joinpath(loaded_map.info.file_path)
            if not map_entry.is_file():
                raise ValueError(f'Lobby map does not exist: {loaded_map.info.file_path!r}')
            yield map_entry
    except BadContentEntry as error:
        raise ValueError(
            f'Invalid content source for lobby map: {loaded_map.info.file_path!r}'
        ) from error
