"""Keep YAML nodes intact instead of coercing unknown scalars through Python values."""

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode

try:
    from yaml import CSafeLoader as _SafeLoader
except ImportError:
    from yaml import SafeLoader as _SafeLoader

STR_TAG = 'tag:yaml.org,2002:str'
MAP_TAG = 'tag:yaml.org,2002:map'


def compose(data: bytes | str) -> Node | None:
    """Compose scalars without constructing objects, using libyaml when available."""
    try:
        return yaml.compose(data, Loader=_SafeLoader)
    except (yaml.YAMLError, UnicodeError) as error:
        raise ValueError(f'Invalid save YAML: {error}') from error


def mapping(node: Node) -> dict[str, Node]:
    if not isinstance(node, MappingNode):
        raise ValueError('Expected a YAML mapping.')  # noqa: TRY004 - invalid file shape
    result: dict[str, Node] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value in result:
            raise ValueError('Expected unique scalar YAML mapping keys.')
        if key.tag == 'tag:yaml.org,2002:null':
            raise ValueError('Expected a non-null scalar YAML mapping key.')
        result[key.value] = value
    return result


def scalar(node: Node) -> str:
    if not isinstance(node, ScalarNode):
        raise ValueError('Expected a YAML scalar.')  # noqa: TRY004 - invalid file shape
    if node.tag == 'tag:yaml.org,2002:null':
        raise ValueError('Expected a non-null YAML scalar.')
    return node.value


def put(node: MappingNode, key: str, value: Node) -> None:
    for i, (existing, _) in enumerate(node.value):
        if isinstance(existing, ScalarNode) and existing.value == key:
            node.value[i] = existing, value
            return
    node.value.append((ScalarNode(STR_TAG, key), value))
