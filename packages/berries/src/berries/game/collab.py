"""Read CollabUtils2 lobby identities and static journal references."""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass

from berries.game import binmap
from berries.game.content import BadContentEntry, ContentEntry, entry_fingerprint
from berries.game.levels import Map

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


def is_lobby_sid(sid: str, collab_ids: Iterable[str]) -> bool:
    """Apply CollabUtils2's exact lobby predicate to one assembled map SID."""
    return any(
        sid.startswith(f'{collab_id}/0-Lobbies/') and sid != f'{collab_id}/0-Lobbies/0-Prologue'
        for collab_id in collab_ids
    )


def journal_references(map_file: Map) -> JournalReferences:
    """Read exact JournalTrigger level-set identities in source order."""
    with _open_map_entry(map_file) as map_entry:
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


def journal_fingerprint(map_file: Map) -> str:
    """Return a cheap source fingerprint for one concrete lobby map resource."""
    with _open_map_entry(map_file) as map_entry:
        return entry_fingerprint(map_entry)


@contextmanager
def _open_map_entry(map_file: Map) -> Generator[ContentEntry]:
    try:
        with map_file.open_content() as root:
            map_entry = root.joinpath(map_file.map_info.file_path)
            if not map_entry.is_file():
                raise ValueError(f'Lobby map does not exist: {map_file.map_info.file_path!r}')
            yield map_entry
    except BadContentEntry as error:
        raise ValueError(
            f'Invalid content source for lobby map: {map_file.map_info.file_path!r}'
        ) from error
