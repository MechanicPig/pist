"""Current-session structure checks and protocol-specific XML encoding."""

from typing import TYPE_CHECKING

from lxml import etree
from pydantic import TypeAdapter

from berries.game.saves.codecs import single
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.unknown import UnknownFieldPolicy
from berries.game.vanilla import VANILLA_SIDS

from . import fields, schema
from . import tree as xml_tree

_TEXT = TypeAdapter(fields.XmlText)

if TYPE_CHECKING:
    from berries.game.saves.progress.session import Session


def read_respawn(
    node: etree.Element,
    *,
    strict: bool = False,
    unknown: UnknownFieldPolicy = 'error',
    path: str = 'RespawnPoint',
) -> tuple[float, float] | None:
    if xml_tree.is_nil(node):
        schema.check(node, children={'X', 'Y'}, unknown=unknown, path=path)
        return None
    value = fields.RespawnFields.from_xml(node, strict=strict, unknown=unknown, path=path)
    return value.x, value.y


def write_respawn(value: tuple[float, float]) -> etree.Element:
    return fields.RespawnFields(X=value[0], Y=value[1]).to_xml('RespawnPoint')


def read_sliders(
    node: etree.Element,
    *,
    strict: bool = False,
    unknown: UnknownFieldPolicy = 'error',
    path: str = 'Sliders',
) -> dict[str, float]:
    result = {}
    for i, slider in enumerate(schema.members(node, 'Slider', unknown=unknown, path=path)):
        slider_path = f'{path}[{i}]'
        schema.check(slider, attributes={'Name', 'Value'}, unknown=unknown, path=slider_path)
        name = _TEXT.validate_python(slider.get('Name'))
        if strict and name in result:
            raise BadProgressSave(f'Duplicate Slider name at {slider_path!r}.')
        result[name] = single.parse(slider.get('Value', '0'))
    return result


def write_sliders(values: dict[str, float]) -> etree.Element:
    root = etree.Element('Sliders')
    for name, value in values.items():
        etree.SubElement(
            root, 'Slider', Name=_TEXT.validate_python(name), Value=single.format_value(value)
        )  # pyright: ignore[reportUnusedCallResult]
    return root


def current_element(session: Session, *, vanilla: bool) -> etree.Element:
    """Build a temporary current-session element without an XML round trip."""
    root = session.to_xml()
    root.tag = 'CurrentSession' if vanilla else 'CurrentSession_Safe'
    area = xml_tree.child(root, 'Area')
    if area is None:
        raise ValueError('Missing session Area.')
    if vanilla:
        try:
            level_id = VANILLA_SIDS.index(session.sid)
        except ValueError as error:
            raise ValueError(f'Not an original-game session SID: {session.sid!r}.') from error
        area.set('ID', str(level_id))
        area.attrib.pop('SID', None)  # pyright: ignore[reportUnusedCallResult]
        old = xml_tree.child(root, 'OldStats')
        if old is not None:
            old.attrib.pop('SID', None)  # pyright: ignore[reportUnusedCallResult]
    else:
        # Everest's SID setter resolves the runtime ID. Keep SID last so a
        # retained numeric ID cannot subsequently override that resolution.
        area.attrib.pop('SID', None)  # pyright: ignore[reportUnusedCallResult]
        area.set('SID', session.sid)
    return root
