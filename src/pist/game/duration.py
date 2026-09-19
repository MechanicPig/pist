"""Game duration values encoded as .NET filetime ticks at storage boundaries."""

from datetime import timedelta
from typing import Literal, Self

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from pist.types import parse_decimal_int

FILETIME_TICKS_PER_MILLISECOND = 10_000


class Duration:
    """A non-negative Game duration internally stored as whole milliseconds.

    The constructor and Pydantic boundary handling use the .NET filetime ticks
    stored by Game saves. Use :meth:`from_milliseconds` for internal values.
    """

    def __init__(self, filetime: int = 0) -> None:
        if not isinstance(filetime, int) or isinstance(filetime, bool):
            raise TypeError(f'filetime must be an integer, not {type(filetime).__name__}')
        if filetime < 0:
            raise ValueError('filetime must be non-negative.')
        milliseconds, remainder = divmod(filetime, FILETIME_TICKS_PER_MILLISECOND)
        if remainder:
            raise ValueError('filetime ticks must represent whole milliseconds.')
        self._total_milliseconds = milliseconds

    @property
    def total_milliseconds(self) -> int:
        """Return the total duration in milliseconds."""
        return self._total_milliseconds

    @property
    def filetime(self) -> int:
        """Return the duration in the ticks used by Game save files."""
        return self.total_milliseconds * FILETIME_TICKS_PER_MILLISECOND

    @classmethod
    def from_milliseconds(cls, total_milliseconds: int = 0) -> Self:
        """Create a duration from an internal whole-millisecond value."""
        if not isinstance(total_milliseconds, int) or isinstance(total_milliseconds, bool):
            raise TypeError(
                f'total_milliseconds must be an integer, not {type(total_milliseconds).__name__}'
            )
        if total_milliseconds < 0:
            raise ValueError('total_milliseconds must be non-negative.')
        return cls(total_milliseconds * FILETIME_TICKS_PER_MILLISECOND)

    @classmethod
    def from_timedelta(cls, value: timedelta) -> Self:
        """Create a duration from a non-negative whole-millisecond ``timedelta``."""
        if not isinstance(value, timedelta):
            raise TypeError(f'value must be a timedelta, not {type(value).__name__}')
        total_microseconds = (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
        if total_microseconds < 0:
            raise ValueError('timedelta must be non-negative.')
        milliseconds, remainder = divmod(total_microseconds, 1_000)
        if remainder:
            raise ValueError('timedelta must represent whole milliseconds.')
        return cls.from_milliseconds(milliseconds)

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
        if filetime := self.filetime:
            return f'{type(self).__name__}({filetime=})'
        return f'{type(self).__name__}()'

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Duration) and self.total_milliseconds == other.total_milliseconds

    def __hash__(self) -> int:
        return hash(self.total_milliseconds)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: object, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Validate external filetime values while preserving internal instances."""
        return core_schema.no_info_plain_validator_function(
            cls._from_pydantic_filetime,
            json_schema_input_schema=core_schema.union_schema(
                [core_schema.int_schema(), core_schema.str_schema()]
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls._serialize_filetime,
                return_schema=core_schema.int_schema(),
            ),
        )

    @classmethod
    def _from_pydantic_filetime(cls, value: object) -> Self:
        if isinstance(value, cls):
            return value
        return cls(parse_decimal_int(value))

    @staticmethod
    def _serialize_filetime(value: Duration) -> int:
        return value.filetime
