"""Read map files."""

from collections.abc import Iterator
from dataclasses import dataclass
from struct import unpack_from

BIN_HEADER = 'CELESTE MAP'
MAX_FILE_SIZE = 64 * 1024 * 1024
MAX_LOOKUP_SIZE = 65_535
MAX_ELEMENT_COUNT = 1_000_000
MAX_RECURSION_DEPTH = 1_000

type NumericAttrValue = int | float
type AttrValue = bool | NumericAttrValue | str


class BadMapBin(ValueError):
    """A map file is truncated, malformed, or exceeds parser safety limits."""


@dataclass(frozen=True, slots=True)
class BinElement:
    """One BinaryPacker element with typed attributes and child elements."""

    name: str
    attrs: dict[str, AttrValue]
    children: tuple[BinElement, ...]

    def walk(self) -> Iterator[BinElement]:
        """Yield this element and every descendant in depth-first order."""
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True, slots=True)
class BinMap:
    """The package name and root element decoded from one map file."""

    package: str
    root: BinElement


class _BinReader:
    """Bounds-checked little-endian reader for BinaryPacker primitives."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0
        self._element_count = 0

    def read_map(self, *, allow_trailing: bool) -> BinMap:
        """Decode a complete BinaryPacker map file."""
        package, lookup = self.read_header()
        root = self.read_element(lookup, depth=0)
        self.check_trailing(allow_trailing)
        return BinMap(package=package, root=root)

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
        """Decode one recursively nested BinaryPacker element."""
        name, attr_count = self.read_element_header(lookup, depth=depth)
        attrs = self.read_attrs(lookup, attr_count)
        children = tuple(self.read_element(lookup, depth=depth + 1) for _ in range(self.read_u16()))
        return BinElement(name=name, attrs=attrs, children=children)

    def read_element_header(self, lookup: tuple[str, ...], *, depth: int) -> tuple[str, int]:
        """Read one element's name and attribute count, enforcing parser limits."""
        if depth >= MAX_RECURSION_DEPTH:
            raise BadMapBin(f'Map element nesting exceeds {MAX_RECURSION_DEPTH}.')
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
        for _ in range(self.read_u16()):
            self.skip_element(lookup, depth=depth + 1)

    def skip_element(self, lookup: tuple[str, ...], *, depth: int) -> None:
        """Consume one element without allocating its attributes or children."""
        _, attr_count = self.read_element_header(lookup, depth=depth)
        self.skip_attrs(lookup, attr_count)
        self.skip_children(lookup, depth=depth)

    def read_value(self, lookup: tuple[str, ...]) -> AttrValue:
        """Decode a value after its one-byte BinaryPacker type tag."""
        match self.read_u8():
            case 0:
                return self.read_u8() != 0
            case 1:
                return self.read_u8()
            case 2:
                return self.read_i16()
            case 3:
                return self.read_i32()
            case 4:
                return self.read_f32()
            case 5:
                return self.read_lookup(lookup)
            case 6:
                return self.read_string()
            case 7:
                return self.read_rle_string()
            case type_id:
                raise BadMapBin(f'Unknown BinaryPacker value type: {type_id}.')

    def skip_value(self) -> None:
        """Consume one BinaryPacker attribute value without constructing it."""
        match self.read_u8():
            case 0 | 1:
                self.read_bytes(1)
            case 2:
                self.read_bytes(2)
            case 3 | 4:
                self.read_bytes(4)
            case 5:
                self.read_bytes(2)
            case 6:
                self.read_bytes(self.read_varlen())
            case 7:
                self.read_bytes(self.read_u16())
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
            decoded.extend([char] * times)
        try:
            return decoded.decode('utf-8')
        except UnicodeDecodeError as error:
            raise BadMapBin('RLE string is not valid UTF-8.') from error

    def read_string(self) -> str:
        """Read a UTF-8 string prefixed by BinaryPacker's variable-length integer."""
        length = self.read_varlen()
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
        return self.read_bytes(1)[0]

    def read_u16(self) -> int:
        """Read one little-endian unsigned short."""
        return unpack_from('<H', self.read_bytes(2))[0]

    def read_i16(self) -> int:
        """Read one little-endian signed short."""
        return unpack_from('<h', self.read_bytes(2))[0]

    def read_i32(self) -> int:
        """Read one little-endian signed long."""
        return unpack_from('<i', self.read_bytes(4))[0]

    def read_f32(self) -> float:
        """Read one little-endian IEEE-754 single-precision float."""
        return unpack_from('<f', self.read_bytes(4))[0]

    def read_bytes(self, length: int) -> bytes:
        """Read an exact byte count, rejecting truncated input."""
        end = self._offset + length
        if end > len(self._data):
            raise BadMapBin('Unexpected end of map file.')
        result = self._data[self._offset : end]
        self._offset = end
        return result


def parse_map_bin(data: bytes, *, allow_trailing: bool = False) -> BinMap:
    """Decode a `.bin` map file, optionally accepting extra payload data."""
    return _BinReader(data).read_map(allow_trailing=allow_trailing)


def parse_map_meta(data: bytes, *, allow_trailing: bool = False) -> dict[str, AttrValue]:
    """Read the top-level map metadata without constructing a full map tree."""
    return _BinReader(data).read_map_meta(allow_trailing=allow_trailing)
