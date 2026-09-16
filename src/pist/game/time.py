"""In-game time values."""

from datetime import timedelta
from typing import Literal, Self

FILETIME_TICKS_PER_MILLISECOND = 10_000


class Time:
    """A non-negative duration stored as whole milliseconds.

    Game save files store durations as .NET ticks, despite calling the
    attributes ``TimePlayed`` and ``BestTime``.  A tick is 100 ns.
    """

    def __init__(self, total_milliseconds: int = 0) -> None:
        if not isinstance(total_milliseconds, int):
            raise TypeError(
                f'total_milliseconds must be an integer, not {type(total_milliseconds).__name__}'
            )
        if total_milliseconds < 0:
            raise ValueError('total_milliseconds must be non-negative.')
        self._total_milliseconds = total_milliseconds

    @property
    def total_milliseconds(self) -> int:
        """Return the total duration in milliseconds."""
        return self._total_milliseconds

    @property
    def filetime(self) -> int:
        """Return the duration in the ticks used by Game save files."""
        return self.total_milliseconds * FILETIME_TICKS_PER_MILLISECOND

    @classmethod
    def from_filetime(cls, filetime: int) -> Self:
        """Create a duration from Game save files' .NET tick count."""
        if not isinstance(filetime, int):
            raise TypeError(f'filetime must be an integer, not {type(filetime).__name__}')
        milliseconds, remainder = divmod(filetime, FILETIME_TICKS_PER_MILLISECOND)
        if remainder:
            raise ValueError(f'filetime must be a whole number of milliseconds: {filetime!r}')
        return cls(milliseconds)

    @classmethod
    def from_timedelta(cls, value: timedelta) -> Self:
        """Create a duration from a standard-library ``timedelta``."""
        return cls(int(value.total_seconds() * 1000))

    def to_timedelta(self) -> timedelta:
        """Convert this value to a standard-library ``timedelta``."""
        return timedelta(milliseconds=self.total_milliseconds)

    def ingame_format(self, timespec: Literal['milliseconds', 'seconds'] = 'milliseconds') -> str:
        """Format a duration in the usual timer notation."""
        hours, remainder = divmod(self.total_milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1_000)
        if timespec == 'milliseconds':
            suffix = f'.{milliseconds:03}'
        elif timespec == 'seconds':
            return f'{hours}:{minutes:02}:{seconds:02}'
        else:
            raise ValueError(f'Unknown timespec: {timespec!r}')
        return (
            f'{hours}:{minutes:02}:{seconds:02}{suffix}'
            if hours
            else f'{minutes}:{seconds:02}{suffix}'
        )

    def __str__(self) -> str:
        return self.ingame_format()

    def __repr__(self) -> str:
        if total_milliseconds := self.total_milliseconds:
            return f'{type(self).__name__}({total_milliseconds=})'
        return f'{type(self).__name__}()'

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Time) and self.total_milliseconds == other.total_milliseconds

    def __hash__(self) -> int:
        return hash(self.total_milliseconds)
