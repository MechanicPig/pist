"""Temporary XML parsing and field access at the save boundary."""

from lxml import etree

XSI = 'http://www.w3.org/2001/XMLSchema-instance'


def parser(*, encoding: str | None = None) -> etree.XMLParser:
    return etree.XMLParser(
        encoding=encoding,
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        remove_blank_text=False,
        remove_comments=False,
        remove_pis=False,
        strip_cdata=False,
        ns_clean=False,
        collect_ids=False,
    )


def child(node: etree.Element, name: str) -> etree.Element | None:
    found = None
    for item in node.iterchildren(tag=name):
        if found is not None:
            raise ValueError(f'Duplicate XML field {name!r}.')
        found = item
    return found


def text(node: etree.Element) -> str:
    return (node.text or '') + ''.join(item.tail or '' for item in node)


def is_nil(node: etree.Element) -> bool:
    return node.get(f'{{{XSI}}}nil') in ('true', '1')


def parse(data: bytes | str) -> etree.ElementTree:
    """Parse a complete save XML document without assuming a particular root."""
    try:
        # Unicode input is already decoded (notably Collab's embedded XML).
        # Its declaration may still say UTF-16; override it only for str input.
        encoding = 'utf-8' if isinstance(data, str) else None
        raw = data.encode('utf-8') if isinstance(data, str) else data
        doc = etree.fromstring(raw, parser(encoding=encoding)).getroottree()
    except (etree.XMLSyntaxError, UnicodeError) as error:
        raise ValueError(f'Invalid XML: {error}') from error
    if doc.docinfo.doctype:
        raise ValueError('Save XML must not contain a document type declaration.')
    return doc
