"""The game's nullable, six-position SummitGems array."""

from typing import Annotated

from lxml import etree
from pydantic import Field, StrictBool, TypeAdapter

from berries.game.saves.codecs.values import SaveBool
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.unknown import UnknownFieldPolicy

from . import schema
from . import tree as xml_tree

N_GEMS = 6

_READ = TypeAdapter(list[SaveBool])
_WRITE = TypeAdapter(Annotated[list[StrictBool], Field(min_length=N_GEMS, max_length=N_GEMS)])


def validate(value: list[bool] | None, *, nullable: bool) -> None:
    if value is None:
        if not nullable:
            raise BadProgressSave('Session SummitGems must not be null.')
    else:
        _ = _WRITE.validate_python(value)


def from_xml(
    node: etree.Element,
    *,
    strict: bool = False,
    unknown: UnknownFieldPolicy = 'error',
    path: str = 'SummitGems',
) -> list[bool] | None:
    if xml_tree.is_nil(node):
        schema.check(node, children={'boolean'}, unknown=unknown, path=path)
        return None
    values = []
    for i, item in enumerate(schema.members(node, 'boolean', unknown=unknown, path=path)):
        schema.check(item, unknown=unknown, path=f'{path}[{i}]')
        values.append(xml_tree.text(item))
    result = _READ.validate_python(values)
    if strict and len(result) != N_GEMS:
        raise BadProgressSave(f'Normal SummitGems requires {N_GEMS} boolean entries.')
    return result


def to_xml(value: list[bool]) -> etree.Element:
    """Encode the game's non-null six-position array; null omission belongs to the field."""
    validate(value, nullable=False)
    node = etree.Element('SummitGems')
    for collected in value:
        etree.SubElement(node, 'boolean').text = str(collected).lower()
    return node
