"""CommunalHelper's persistent Summit Gem keys, not its current Mod session."""

from pydantic import StrictStr, TypeAdapter
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from berries.game.saves.codecs.yaml import nodes as yaml_nodes
from berries.game.saves.unknown import UnknownFieldPolicy

from .yaml_doc import YamlDoc

_STRINGS = TypeAdapter(set[StrictStr])


class CommunalHelperSave(YamlDoc):
    mod_name = 'CommunalHelper'
    fields = frozenset({'SummitGems'})

    def __init__(self) -> None:
        super().__init__()
        self.summit_gems: set[str] | None = None
        self._present = False

    def _read_root(self, root: MappingNode, *, unknown: UnknownFieldPolicy = 'error') -> None:
        entries = yaml_nodes.mapping(root)
        gems = entries.get('SummitGems')
        if gems is None or gems.tag == 'tag:yaml.org,2002:null':
            self.summit_gems: set[str] | None = None
        else:
            if not isinstance(gems, SequenceNode):
                raise ValueError('Expected a SummitGems YAML sequence.')
            self.summit_gems = _STRINGS.validate_python(
                [yaml_nodes.scalar(node) for node in gems.value]
            )
        self._present = gems is not None

    def tostring(self) -> str:
        root = MappingNode(yaml_nodes.MAP_TAG, [])
        if self.summit_gems is not None:
            values = _STRINGS.validate_python(self.summit_gems)
            gems = SequenceNode(
                'tag:yaml.org,2002:seq',
                [ScalarNode(yaml_nodes.STR_TAG, value) for value in sorted(values)],
            )
            yaml_nodes.put(root, 'SummitGems', gems)
        elif self._present:
            yaml_nodes.put(root, 'SummitGems', ScalarNode('tag:yaml.org,2002:null', 'null'))
        return self._dump(root)
