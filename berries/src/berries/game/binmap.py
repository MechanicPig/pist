"""Read map files."""

from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum
from os import environ
from struct import unpack_from

BIN_HEADER = 'CELESTE MAP'
MAX_FILE_SIZE = 64 * 1024 * 1024
MAX_DECODED_TEXT_SIZE = 64 * 1024 * 1024
MAX_LOOKUP_SIZE = 65_535
MAX_ELEMENT_COUNT = 1_000_000
DEFAULT_MAX_DEPTH = 32

type NumericAttrValue = int | float
type AttrValue = bool | NumericAttrValue | str


class ValueTag(IntEnum):
    """BinaryPacker's one-byte attribute value discriminators."""

    BOOLEAN = 0
    BYTE = 1
    SHORT = 2
    INT = 3
    FLOAT = 4
    LOOKUP_STRING = 5
    STRING = 6
    RLE_STRING = 7


class BadMapBin(ValueError):
    """A map file is truncated, malformed, or exceeds parser safety limits."""


@dataclass(frozen=True, slots=True)
class BinElement:
    """One BinaryPacker element with typed attributes and child elements."""

    name: str
    attrs: dict[str, AttrValue]
    children: tuple[BinElement, ...]

    def child(self, name: str) -> BinElement | None:
        """Return the first direct child with this exact name, if present."""
        return next((child for child in self.children if child.name == name), None)

    def walk(self) -> Iterator[BinElement]:
        """Yield this element and every descendant in depth-first order."""
        pending: list[Iterator[BinElement]] = [iter((self,))]
        while pending:
            element = next(pending[-1], None)
            if element is None:
                pending.pop()
            else:
                yield element
                pending.append(iter(element.children))


@dataclass(frozen=True, slots=True)
class BinMap:
    """The package name and root element decoded from one map file."""

    package: str
    root: BinElement

    def iter_rooms(self) -> Iterator[BinElement]:
        """Yield direct level elements from the first levels node in file order."""
        if (rooms := self.root.child('levels')) is not None:
            for room in rooms.children:
                if room.name == 'level':
                    yield room


@dataclass(slots=True)
class _ElementFrame:
    """An element whose remaining child payload has not yet been decoded."""

    name: str
    attrs: dict[str, AttrValue]
    children: list[BinElement]
    remaining_children: int


class _BinReader:
    """Bounds-checked little-endian reader for BinaryPacker primitives."""

    def __init__(self, data: bytes, *, max_depth: int | None = None) -> None:
        if max_depth is None:
            try:
                max_depth = int(environ.get('BERRIES_MAX_MAP_DEPTH', str(DEFAULT_MAX_DEPTH)))
            except ValueError as error:
                raise ValueError('BERRIES_MAX_MAP_DEPTH must be a positive integer.') from error
        if type(max_depth) is not int or max_depth < 1:
            raise ValueError('Map depth limit must be a positive integer.')
        self._data = data
        self._max_depth = max_depth
        self._offset = 0
        self._element_count = 0
        self._decoded_text_size = 0

    def read_map(self, *, allow_trailing: bool) -> BinMap:
        """Decode a complete BinaryPacker map file."""
        pkg, lookup = self.read_header()
        root = self.read_element(lookup, depth=0)
        self.check_trailing(allow_trailing)
        return BinMap(package=pkg, root=root)

    def read_map_meta(self, *, allow_trailing: bool) -> dict[str, AttrValue]:
        """Read only the root ``meta`` element while skipping all other map data."""
        _, lookup = self.read_header()
        root_name, attr_count = self.read_element_header(lookup, depth=0)
        if root_name != 'Map':
            raise BadMapBin(f'Unexpected map root element: {root_name!r}.')
        self.skip_attrs(lookup, attr_count)
        meta: dict[str, AttrValue] = {}
        for _ in range(self.read_u16()):
            name, attr_count = self.read_element_header(lookup, depth=1)
            if name == 'meta':
                meta = self.read_attrs(lookup, attr_count)
                if allow_trailing:
                    return meta
                self.skip_children(lookup, depth=1)
            else:
                self.skip_attrs(lookup, attr_count)
                self.skip_children(lookup, depth=1)
        self.check_trailing(allow_trailing)
        return meta

    def read_header(self) -> tuple[str, tuple[str, ...]]:
        """Read the common BinaryPacker header and string lookup table."""
        if len(self._data) > MAX_FILE_SIZE:
            raise BadMapBin(f'Map file exceeds {MAX_FILE_SIZE} byte limit.')
        if self.read_string() != BIN_HEADER:
            raise BadMapBin('Invalid map header.')
        return self.read_string(), tuple(self.read_string() for _ in range(self.read_u16()))

    def check_trailing(self, allow_trailing: bool) -> None:
        """Reject unconsumed bytes unless the caller explicitly allows them."""
        if self._offset != len(self._data) and not allow_trailing:
            raise BadMapBin('Unexpected trailing data after map root element.')

    def read_element(self, lookup: tuple[str, ...], *, depth: int) -> BinElement:
        """Decode one element and its descendants within the nesting safety limit."""
        pending = [self.read_element_frame(lookup, depth=depth)]
        while True:
            frame = pending[-1]
            if frame.remaining_children:
                frame.remaining_children -= 1
                pending.append(self.read_element_frame(lookup, depth=depth + len(pending)))
                continue
            element = BinElement(frame.name, frame.attrs, tuple(frame.children))
            pending.pop()
            if not pending:
                return element
            pending[-1].children.append(element)

    def read_element_frame(self, lookup: tuple[str, ...], *, depth: int) -> _ElementFrame:
        """Read an element's attributes and the number of child payloads it owns."""
        name, attr_count = self.read_element_header(lookup, depth=depth)
        attrs = self.read_attrs(lookup, attr_count)
        return _ElementFrame(name, attrs, [], self.read_u16())

    def read_element_header(self, lookup: tuple[str, ...], *, depth: int) -> tuple[str, int]:
        """Read one element's name and attribute count, enforcing parser limits."""
        if depth >= self._max_depth:
            raise BadMapBin(f'Map element nesting exceeds {self._max_depth} levels.')
        self._element_count += 1
        if self._element_count > MAX_ELEMENT_COUNT:
            raise BadMapBin(f'Map element count exceeds {MAX_ELEMENT_COUNT}.')
        return self.read_lookup(lookup), self.read_u8()

    def read_attrs(self, lookup: tuple[str, ...], count: int) -> dict[str, AttrValue]:
        """Decode ``count`` BinaryPacker attributes."""
        return {self.read_lookup(lookup): self.read_value(lookup) for _ in range(count)}

    def skip_attrs(self, lookup: tuple[str, ...], count: int) -> None:
        """Skip ``count`` attributes without decoding their values."""
        for _ in range(count):
            self.read_lookup(lookup)
            self.skip_value()

    def skip_children(self, lookup: tuple[str, ...], *, depth: int) -> None:
        """Skip all children belonging to the current element."""
        pending = [self.read_u16()]
        while pending:
            if not pending[-1]:
                pending.pop()
                continue
            pending[-1] -= 1
            _, attr_count = self.read_element_header(lookup, depth=depth + len(pending))
            self.skip_attrs(lookup, attr_count)
            pending.append(self.read_u16())

    def read_value(self, lookup: tuple[str, ...]) -> AttrValue:
        """Decode a value after its one-byte BinaryPacker type tag."""
        match self.read_u8():
            case ValueTag.BOOLEAN:
                return self.read_u8() != 0
            case ValueTag.BYTE:
                return self.read_u8()
            case ValueTag.SHORT:
                return self.read_i16()
            case ValueTag.INT:
                return self.read_i32()
            case ValueTag.FLOAT:
                return self.read_f32()
            case ValueTag.LOOKUP_STRING:
                return self.read_lookup(lookup)
            case ValueTag.STRING:
                return self.read_string()
            case ValueTag.RLE_STRING:
                return self.read_rle_string()
            case type_id:
                raise BadMapBin(f'Unknown BinaryPacker value type: {type_id}.')

    def skip_value(self) -> None:
        """Consume one BinaryPacker attribute value without constructing it."""
        match self.read_u8():
            case ValueTag.BOOLEAN | ValueTag.BYTE:
                self.skip_bytes(1)
            case ValueTag.SHORT:
                self.skip_bytes(2)
            case ValueTag.INT | ValueTag.FLOAT:
                self.skip_bytes(4)
            case ValueTag.LOOKUP_STRING:
                self.skip_bytes(2)
            case ValueTag.STRING:
                self.skip_bytes(self.read_varlen())
            case ValueTag.RLE_STRING:
                self.skip_bytes(self.read_u16())
            case type_id:
                raise BadMapBin(f'Unknown BinaryPacker value type: {type_id}.')

    def read_lookup(self, lookup: tuple[str, ...]) -> str:
        """Return a string-table entry referenced by an unsigned short index."""
        index = self.read_u16()
        try:
            return lookup[index]
        except IndexError:
            raise BadMapBin(f'String-table index out of range: {index}.') from None

    def read_rle_string(self) -> str:
        """Decode BinaryPacker's count-byte RLE string representation."""
        length = self.read_u16()
        if length % 2:
            raise BadMapBin('RLE payload length must be even.')
        decoded = bytearray()
        for _ in range(length // 2):
            times = self.read_u8()
            char = self.read_u8()
            self._reserve_decoded_text(times)
            decoded.extend(bytes((char,)) * times)
        try:
            return decoded.decode('utf-8')
        except UnicodeDecodeError as error:
            raise BadMapBin('RLE string is not valid UTF-8.') from error

    def read_string(self) -> str:
        """Read a UTF-8 string prefixed by BinaryPacker's variable-length integer."""
        length = self.read_varlen()
        self._reserve_decoded_text(length)
        try:
            return self.read_bytes(length).decode('utf-8')
        except UnicodeDecodeError as error:
            raise BadMapBin('String is not valid UTF-8.') from error

    def read_varlen(self) -> int:
        """Read the little-endian base-128 length encoding used by BinaryPacker."""
        value = 0
        multiplier = 1
        for _ in range(5):
            byte = self.read_u8()
            value += (byte & 0x7F) * multiplier
            if byte < 0x80:
                return value
            multiplier *= 0x80
        raise BadMapBin('Variable-length integer exceeds five bytes.')

    def read_u8(self) -> int:
        """Read one unsigned byte."""
        return self._data[self._consume(1)]

    def read_u16(self) -> int:
        """Read one little-endian unsigned short."""
        return unpack_from('<H', self._data, self._consume(2))[0]

    def read_i16(self) -> int:
        """Read one little-endian signed short."""
        return unpack_from('<h', self._data, self._consume(2))[0]

    def read_i32(self) -> int:
        """Read one little-endian signed long."""
        return unpack_from('<i', self._data, self._consume(4))[0]

    def read_f32(self) -> float:
        """Read one little-endian IEEE-754 single-precision float."""
        return unpack_from('<f', self._data, self._consume(4))[0]

    def read_bytes(self, length: int) -> bytes:
        """Read an exact byte count, rejecting truncated input."""
        offset = self._consume(length)
        return self._data[offset : offset + length]

    def skip_bytes(self, length: int) -> None:
        """Consume an exact byte count without creating a temporary slice."""
        self._consume(length)

    def _consume(self, length: int) -> int:
        """Advance by ``length`` bytes and return the former offset."""
        end = self._offset + length
        if end > len(self._data):
            raise BadMapBin('Unexpected end of map file.')
        offset = self._offset
        self._offset = end
        return offset

    def _reserve_decoded_text(self, size: int) -> None:
        """Reject strings whose total decoded payload exceeds the map safety limit."""
        self._decoded_text_size += size
        if self._decoded_text_size > MAX_DECODED_TEXT_SIZE:
            raise BadMapBin(f'Decoded text exceeds {MAX_DECODED_TEXT_SIZE} byte limit.')


def parse_map_bin(
    data: bytes, *, allow_trailing: bool = False, max_depth: int | None = None
) -> BinMap:
    """Decode a map with a configurable level limit including the root element.

    An explicit max_depth overrides BERRIES_MAX_MAP_DEPTH; otherwise the environment
    setting or DEFAULT_MAX_DEPTH is used. Exceeding the limit raises BadMapBin.
    """
    return _BinReader(data, max_depth=max_depth).read_map(allow_trailing=allow_trailing)


def parse_map_meta(
    data: bytes, *, allow_trailing: bool = False, max_depth: int | None = None
) -> dict[str, AttrValue]:
    """Read metadata within a configurable level limit, including the root element.

    With allow_trailing, return as soon as root metadata is found; unread payload
    is not validated. Otherwise, validate the complete map structure.
    The depth limit follows the same argument/environment/default priority as
    parse_map_bin.
    """
    return _BinReader(data, max_depth=max_depth).read_map_meta(allow_trailing=allow_trailing)
