"""Format-independent one-shot text document adapters."""

from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path
from typing import Self

import pytest

from berries.text_doc import TextDocMixin


class Note(TextDocMixin):
    def __init__(self) -> None:
        self.text = ''

    @classmethod
    def fromstring(cls, data: str | bytes, /) -> Self:
        result = cls()
        result.text = data.decode('utf-8') if isinstance(data, bytes) else data
        return result

    def tostring(self) -> str:
        return self.text


class EncodedNote(Note):
    @classmethod
    def fromstring(cls, data: str | bytes, /, *, encoding: str = 'utf-8') -> Self:
        result = cls()
        result.text = data.decode(encoding) if isinstance(data, bytes) else data
        return result


def test_read_forwards_parser_arguments_without_retaining_them(tmp_path: Path) -> None:
    data = '你好'.encode('utf-16')
    path = tmp_path / 'note'
    path.write_bytes(data)
    for note in (
        EncodedNote.read(path, encoding='utf-16'),
        EncodedNote.read(BytesIO(data), encoding='utf-16'),
        EncodedNote.fromstring(data, encoding='utf-16'),
        EncodedNote.fromstring('你好', encoding='utf-16'),
    ):
        assert isinstance(note, EncodedNote)
        assert note.tostring() == '你好'
        assert not hasattr(note, 'encoding')


def test_invalid_parser_arguments_fail_without_closing_input() -> None:
    stream = BytesIO(b'unread')
    with pytest.raises(TypeError):
        Note.read(stream, unknown=True)  # pyright: ignore[reportCallIssue]
    assert not stream.closed
    with pytest.raises(TypeError):
        Note.fromstring('contents', unknown=True)  # pyright: ignore[reportCallIssue]


def test_fromstring_treats_strings_as_contents_not_paths(tmp_path: Path) -> None:
    path = tmp_path / 'note'
    path.write_text('你好', encoding='utf-8')
    assert Note.read(path).tostring() == '你好'
    assert Note.read(str(path)).tostring() == '你好'
    assert Note.fromstring(str(path)).tostring() == str(path)
    assert Note.fromstring('你好'.encode()).tostring() == '你好'


def test_text_and_binary_streams_use_current_cursor_without_taking_ownership() -> None:
    text = StringIO('prefix:你好')
    text.seek(7)
    note = Note.read(text)
    assert note.tostring() == '你好' and not text.closed
    binary = BytesIO(b'prefix:' + '你好'.encode())
    binary.seek(7)
    assert Note.read(binary).tostring() == '你好' and not binary.closed

    text_output = StringIO('prefix:long-tail')
    text_output.seek(7)
    note.write(text_output)
    assert text_output.getvalue() == 'prefix:你好ng-tail' and not text_output.closed
    binary_output = BytesIO()
    note.write(binary_output)
    assert binary_output.getvalue() == '你好'.encode() and not binary_output.closed


def test_text_stream_owns_its_encoding_while_paths_use_utf8(tmp_path: Path) -> None:
    note = Note.fromstring('你好')
    binary = BytesIO()
    text = TextIOWrapper(binary, encoding='utf-16', newline='')
    try:
        note.write(text)
        text.flush()
        assert binary.getvalue().decode('utf-16') == '你好'
    finally:
        text.detach()
    path = tmp_path / 'note'
    note.write(path)
    assert path.read_bytes() == '你好'.encode()


def test_short_text_write_is_reported_without_retrying() -> None:
    class ShortText(StringIO):
        def write(self, text: str, /) -> int:
            return super().write(text[:1])

    output = ShortText()
    with pytest.raises(OSError, match='Incomplete stream write'):
        Note.fromstring('你好').write(output)
    assert output.getvalue() == '你' and not output.closed


def test_read_adds_file_context_without_wrapping_the_decoder_error(tmp_path: Path) -> None:
    error = ValueError('invalid contents')

    class InvalidNote(Note):
        @classmethod
        def fromstring(cls, data: str | bytes, /) -> Self:
            raise error

    path = tmp_path / 'note'
    path.write_text('invalid', encoding='utf-8')
    with pytest.raises(ValueError) as caught:
        InvalidNote.read(path)
    assert caught.value is error
    assert error.__notes__ == [f'While reading {path}.']


def test_fromstring_does_not_invent_a_file_source() -> None:
    error = ValueError('invalid contents')

    class InvalidNote(Note):
        @classmethod
        def fromstring(cls, data: str | bytes, /) -> Self:
            raise error

    with pytest.raises(ValueError) as caught:
        InvalidNote.fromstring('invalid')
    assert caught.value is error
    assert not getattr(error, '__notes__', ())
