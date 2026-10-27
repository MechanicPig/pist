"""One-shot path and stream adapters for editable text documents."""

from abc import ABC, abstractmethod
from io import TextIOBase
from os import PathLike
from typing import IO, BinaryIO, Protocol, Self, TextIO, cast

type TextFile = str | PathLike[str] | BinaryIO | TextIO


class _TextParser[**P, T](Protocol):
    @classmethod
    def fromstring(cls, data: str | bytes, /, *args: P.args, **kwargs: P.kwargs) -> T: ...


class TextDocMixin(ABC):
    """Adapt format-specific loading and text output to paths and borrowed streams.

    Paths use UTF-8 on write; byte inputs retain their format's encoding rules.
    Borrowed streams remain open, at their resulting cursor, without automatic
    seeking, truncation or flushing. No file state is retained.
    """

    @classmethod
    def read[**P, T](
        cls: type[_TextParser[P, T]], file: TextFile, /, *args: P.args, **kwargs: P.kwargs
    ) -> T:
        """Read once, forwarding parser parameters to the concrete fromstring()."""
        if isinstance(file, (str, PathLike)):
            with open(file, 'rb') as stream:
                data = stream.read()
        else:
            data = file.read()
        try:
            return cls.fromstring(data, *args, **kwargs)
        except ValueError as error:
            error.add_note(f'While reading {file!s}.')
            raise

    @classmethod
    @abstractmethod
    def fromstring(cls, data: str | bytes, /) -> Self:
        """Decode contents into a new domain object using format-specific parameters."""
        ...

    def write(self, file: TextFile) -> None:
        """Serialize before opening the destination; write once without retrying."""
        text = self.tostring()
        if isinstance(file, (str, PathLike)):
            data = text.encode('utf-8')
            with open(file, 'wb') as stream:
                self._write(stream, data)
        elif isinstance(file, TextIOBase) or isinstance(getattr(file, 'encoding', None), str):
            # Standard text streams inherit TextIOBase; structural TextIO wrappers
            # expose an encoding. Never probe by writing and retry after TypeError.
            self._write(cast(TextIO, file), text)
        else:
            self._write(cast(BinaryIO, file), text.encode('utf-8'))

    @staticmethod
    def _write[T: (str, bytes)](stream: IO[T], data: T) -> None:
        written = stream.write(data)
        if written != len(data):
            raise OSError(f'Incomplete stream write: {written} of {len(data)} units.')

    @abstractmethod
    def tostring(self) -> str: ...
