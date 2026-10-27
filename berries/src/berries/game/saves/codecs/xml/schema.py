"""Schema checks and temporary XML collection adapters for save value models."""

from collections.abc import Iterator
from collections.abc import Set as AbstractSet

from lxml import etree

from berries.game.saves.unknown import UnknownFieldPolicy, check_fields
from berries.map_entity_id import MapEntityID

from . import fields as xml_fields
from . import tree

_EMPTY: frozenset[str] = frozenset()


def check(
    node: etree.Element,
    *,
    attributes: AbstractSet[str] = _EMPTY,
    children: AbstractSet[str] = _EMPTY,
    unknown: UnknownFieldPolicy = 'error',
    path: str,
) -> None:
    check_fields(
        (
            f'@{name}'
            for name in node.attrib
            if name not in attributes and name != f'{{{tree.XSI}}}nil'
        ),
        _EMPTY,
        path=path,
        unknown=unknown,
    )
    check_fields(
        (child.tag for child in node if isinstance(child.tag, str)),
        children,
        path=path,
        unknown=unknown,
    )


def members(
    group: etree.Element | None,
    name: str,
    *,
    unknown: UnknownFieldPolicy = 'error',
    path: str,
) -> Iterator[etree.Element]:
    if group is None:
        return
    check(group, children={name}, unknown=unknown, path=path)
    if not tree.is_nil(group):
        yield from group.iterchildren(name)


def strings(
    group: etree.Element | None, *, unknown: UnknownFieldPolicy = 'error', path: str
) -> list[str]:
    if group is None:
        return []
    return xml_fields.StringItems.from_xml(group, unknown=unknown, path=path).values


def entities(
    group: etree.Element | None, *, unknown: UnknownFieldPolicy = 'error', path: str
) -> set[MapEntityID]:
    if group is None:
        return set()
    return set(xml_fields.EntityItems.from_xml(group, unknown=unknown, path=path).values)


def append_strings(root: etree.Element, name: str, values: list[str]) -> None:
    root.append(xml_fields.StringItems(values=values).to_xml(name))


def append_entities(root: etree.Element, name: str, values: set[MapEntityID]) -> None:
    root.append(xml_fields.EntityItems(values=sorted(values, key=str)).to_xml(name))
