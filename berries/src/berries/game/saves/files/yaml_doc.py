"""YAML adaptation for fully modeled Mod documents, without retained nodes."""

from abc import abstractmethod
from typing import ClassVar, Self

import yaml
from pydantic import TypeAdapter
from yaml.nodes import MappingNode

from berries.game.saves.codecs.yaml import nodes as yaml_nodes
from berries.game.saves.codecs.yaml import tags
from berries.game.saves.unknown import UnknownFieldPolicy, check_fields

from .save import ModSave

_UNKNOWN_POLICY = TypeAdapter(UnknownFieldPolicy)


class YamlDoc(ModSave):
    fields: ClassVar[frozenset[str]]

    def __init__(self) -> None:
        self._tags: tags.Tags = {}

    @classmethod
    def fromstring(cls, data: str | bytes, /, *, unknown: UnknownFieldPolicy = 'error') -> Self:
        result = cls()
        result._read_yaml(data, unknown=unknown)
        return result

    def _read_yaml(self, data: str | bytes, *, unknown: UnknownFieldPolicy = 'error') -> None:
        unknown = _UNKNOWN_POLICY.validate_python(unknown)
        root = yaml_nodes.compose(data)
        # compose never constructs objects, including for unknown/custom tags. Scalar
        # text and tags survive unchanged; numeric-looking SID/room keys stay verbatim.
        if not isinstance(root, MappingNode):
            raise ValueError('Expected a save YAML mapping.')  # noqa: TRY004 - invalid file shape
        entries = yaml_nodes.mapping(root)
        check_fields(entries, self.fields, path=type(self).__name__, unknown=unknown)
        root = MappingNode(
            root.tag,
            [(key, value) for key, value in root.value if yaml_nodes.scalar(key) in self.fields],
        )
        # Every explicit YAML tag uses '!'. Normal game output needs no second
        # tree walk; tagged inputs retain only sparse tag/type/path metadata.
        tagged = b'!' in data if isinstance(data, bytes) else '!' in data
        self._tags = tags.capture(root) if tagged else {}
        self._read_root(root, unknown=unknown)

    @abstractmethod
    def _read_root(self, root: MappingNode, *, unknown: UnknownFieldPolicy = 'error') -> None: ...

    def _dump(self, root: MappingNode) -> str:
        return yaml.serialize(tags.restore(root, self._tags))
