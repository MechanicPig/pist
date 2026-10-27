"""Explicit poem selection; resolving defaults never reads installed maps."""

from collections.abc import Mapping

from typing_extensions import Sentinel as sentinel

from berries.game.dialog import to_dialog_key
from berries.game.levels import LevelSide

DEFAULT_POEM_ID = sentinel('DEFAULT_POEM_ID')
type PoemID = str | DEFAULT_POEM_ID | None
type PoemIDs = Mapping[LevelSide, PoemID] | tuple[PoemID, PoemID, PoemID]

_VANILLA: dict[str, tuple[str | None, str | None]] = {
    'Celeste/0-Intro': (None, None),
    'Celeste/1-ForsakenCity': ('fc', 'fcr'),
    'Celeste/2-OldSite': ('os', 'osr'),
    'Celeste/3-CelestialResort': ('cr', 'crr'),
    'Celeste/4-GoldenRidge': ('cs', 'csr'),
    'Celeste/5-MirrorTemple': ('t', 'tr'),
    'Celeste/6-Reflection': ('tf', 'tfr'),
    'Celeste/7-Summit': ('ts', 'tsr'),
    'Celeste/8-Epilogue': (None, None),
    'Celeste/9-Core': ('mc', 'mcr'),
    'Celeste/LostLevels': ('fw', None),
}


def as_mapping(values: PoemIDs) -> Mapping[LevelSide, PoemID]:
    if isinstance(values, tuple):
        if len(values) != len(LevelSide):
            raise ValueError('Expected exactly three poem IDs in A/B/C order.')
        return dict(zip(LevelSide, values, strict=True))
    if not isinstance(values, Mapping):
        raise TypeError('Expected a poem mapping or an A/B/C tuple.')
    return values


def selected_ids(sid: str, values: Mapping[LevelSide, PoemID]) -> set[str]:
    if any(not isinstance(side, LevelSide) for side in values):
        raise TypeError('Poem mapping keys must be LevelSide values.')
    return {
        value for side, item in values.items() if (value := resolve(sid, side, item)) is not None
    }


def resolve(sid: str, side: LevelSide, value: PoemID) -> str | None:
    if value is DEFAULT_POEM_ID:
        if side is LevelSide.C:
            return None
        poems = _VANILLA.get(sid)
        if poems is not None:
            return poems[side]
        return f'{to_dialog_key(sid)}_{side.name}'.lower()
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError('Expected a poem ID, None, or DEFAULT_POEM_ID.')
    # RegisterPoemEntry lowercases new registrations; do not normalize previously
    # stored entries or assume all IDs can be derived from a SID.
    return value.lower() or None
