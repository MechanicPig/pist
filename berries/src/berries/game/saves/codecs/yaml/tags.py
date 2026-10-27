"""Sparse, read-only tag metadata for rebuilt domain fields, without retaining values."""

from collections.abc import Iterator
from copy import copy
from typing import Literal

from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

type _Step = tuple[Literal['key', 'value', 'item'], str, int]
type _Path = tuple[_Step, ...]
type Tags = dict[_Path, tuple[str, type[Node]]]

_STANDARD_TAGS = {
    f'tag:yaml.org,2002:{name}'
    for name in ('str', 'int', 'float', 'bool', 'null', 'timestamp', 'binary', 'map', 'seq')
}


def _children(node: Node) -> Iterator[tuple[_Step, int, Node]]:
    occurrences: dict[str, int] = {}
    if isinstance(node, MappingNode):
        for i, (key, value) in enumerate(node.value):
            label = key.value if isinstance(key, ScalarNode) else ''
            occurrence = occurrences.get(label, 0)
            occurrences[label] = occurrence + 1
            yield ('key', label, occurrence), i, key
            yield ('value', label, occurrence), i, value
    elif isinstance(node, SequenceNode):
        for i, value in enumerate(node.value):
            # Scalar tags follow their value across set sorting or room reordering,
            # rather than being reassigned to whichever item now occupies index i.
            label = value.value if isinstance(value, ScalarNode) else ''
            occurrence = occurrences.get(label, 0)
            occurrences[label] = occurrence + 1
            yield ('item', label, occurrence), i, value


def capture(root: Node) -> Tags:
    result: Tags = {}
    active: set[int] = set()
    stack: list[tuple[Node, _Path, bool]] = [(root, (), False)]
    while stack:
        node, path, leaving = stack.pop()
        if leaving:
            active.remove(id(node))
            continue
        if id(node) in active:
            continue  # Opaque recursive aliases remain the original codec's concern.
        if node.tag not in _STANDARD_TAGS:
            result[path] = node.tag, type(node)
        active.add(id(node))
        stack.append((node, path, True))
        stack.extend((child, (*path, step), False) for step, _, child in _children(node))
    return result


def _restore(node: Node, path: _Path, tag: str, kind: type[Node]) -> Node:
    if not path:
        if not isinstance(node, kind):
            raise ValueError(f'Cannot preserve YAML tag {tag!r} after changing its node type.')
        if node.tag == tag:
            return node
        result = copy(node)
        result.tag = tag
        return result
    for step, i, child in _children(node):
        if step != path[0]:
            continue
        replacement = _restore(child, path[1:], tag, kind)
        if replacement is child:
            return node
        # Only changed paths are copied. In particular, never retag a pending
        # record's borrowed input nodes or an opaque alias that shares them.
        result = copy(node)
        if isinstance(node, MappingNode) and isinstance(result, MappingNode):
            result.value = list(node.value)
            key, value = node.value[i]
            result.value[i] = (replacement, value) if step[0] == 'key' else (key, replacement)
        elif isinstance(node, SequenceNode) and isinstance(result, SequenceNode):
            result.value = list(node.value)
            result.value[i] = replacement
        return result
    return node  # An explicitly removed member does not need its old tag restored.


def restore(root: Node, tags: Tags) -> Node:
    for path, (tag, kind) in tags.items():
        root = _restore(root, path, tag, kind)
    return root
