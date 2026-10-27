"""One-shot file I/O, independent document lifetimes and borrowed streams."""

from collections.abc import Buffer
from copy import copy, deepcopy
from io import BytesIO
from pathlib import Path
from typing import Self

import pytest

from berries.game.saves.files import Save


class TextDoc(Save):
    def __init__(self) -> None:
        self.text = ''

    @classmethod
    def fromstring(cls, data: str | bytes, /) -> Self:
        result = cls()
        result.text = data.decode('utf-8') if isinstance(data, bytes) else data
        return result

    def tostring(self) -> str:
        if self.text == 'invalid':
            raise ValueError('Cannot encode invalid data.')
        return self.text


def test_read_releases_source_and_edits_require_explicit_write(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.write_bytes(b'original')
    doc = TextDoc.read(source)
    source.unlink()
    assert doc.text == 'original'
    doc.text = 'edited'
    assert not source.exists() and not target.exists()
    doc.write(target)
    assert target.read_bytes() == b'edited'
    doc.text = 'again'
    doc.write(target)
    doc.write(source)
    assert target.read_bytes() == source.read_bytes() == b'again'


def test_empty_document_can_write_without_reading(tmp_path: Path) -> None:
    doc = TextDoc()
    assert doc.text == ''
    doc.text = 'new'
    path = tmp_path / 'new'
    doc.write(str(path))
    assert TextDoc.read(str(path)).text == 'new'


def test_encoding_failure_does_not_open_or_truncate_target(tmp_path: Path) -> None:
    path = tmp_path / 'source'
    path.write_bytes(b'original')
    doc = TextDoc.read(path)
    doc.text = 'invalid'
    with pytest.raises(ValueError, match='encode'):
        doc.write(path)
    assert path.read_bytes() == b'original'
    doc.text = 'fixed'
    doc.write(path)
    assert path.read_bytes() == b'fixed'


def test_borrowed_streams_follow_current_cursor_without_truncating_or_closing() -> None:
    source = BytesIO(b'prefix:original')
    source.seek(7)
    doc = TextDoc.read(source)
    source.close()
    assert doc.text == 'original'
    doc.text = 'new'
    target = BytesIO(b'prefix:old-long-tail')
    target.seek(7)
    doc.write(target)
    assert target.getvalue() == b'prefix:new-long-tail'
    assert not target.closed


def test_borrowed_stream_is_not_closed_on_decode_or_encode_failure() -> None:
    source = BytesIO(b'\xff')
    with pytest.raises(UnicodeDecodeError):
        TextDoc.read(source)
    assert not source.closed
    doc = TextDoc()
    doc.text = 'invalid'
    target = BytesIO(b'original')
    with pytest.raises(ValueError):
        doc.write(target)
    assert target.getvalue() == b'original' and target.tell() == 0 and not target.closed


def test_nonseekable_streams_are_supported() -> None:
    class ForwardOnly(BytesIO):
        def seek(self, offset: int, whence: int = 0) -> int:
            raise OSError('No seeking')

        def seekable(self) -> bool:
            return False

    source = ForwardOnly(b'original')
    doc = TextDoc.read(source)
    target = ForwardOnly()
    doc.write(target)
    assert target.getvalue() == b'original'
    assert not source.closed and not target.closed


def test_short_write_reports_partial_output_without_retrying() -> None:
    class ShortStream(BytesIO):
        def write(self, data: Buffer, /) -> int:
            return super().write(memoryview(data)[:1])

    doc = TextDoc()
    doc.text = 'new'
    target = ShortStream()
    with pytest.raises(OSError, match='Incomplete stream write'):
        doc.write(target)
    assert target.getvalue() == b'n' and not target.closed
    complete = BytesIO()
    doc.write(complete)
    assert complete.getvalue() == b'new'


def test_write_errors_leave_memory_usable(tmp_path: Path) -> None:
    doc = TextDoc()
    doc.text = 'new'
    with pytest.raises(FileNotFoundError):
        doc.write(tmp_path / 'missing' / 'save')
    assert doc.text == 'new'
    doc.write(tmp_path / 'save')
    assert (tmp_path / 'save').read_bytes() == b'new'


def test_saves_support_standard_copy_without_file_sessions() -> None:
    doc = TextDoc.read(BytesIO(b'original'))
    for copier in (copy, deepcopy):
        cloned = copier(doc)
        assert cloned is not doc
        assert cloned.text == doc.text
    for name in ('close', 'closed', '__enter__', '__exit__', 'copy'):
        assert not hasattr(doc, name)
