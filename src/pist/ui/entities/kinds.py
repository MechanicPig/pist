"""Reusable Textual controls for the configurable entity-kind hierarchy."""

from collections import defaultdict

from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import Tree
from textual.widgets._tree import NodeID, TreeNode

from pist.entities.rules import EntityRules


class KindTree(Tree[str]):
    """A kind tree with explicit expand, confirm, and context-menu gestures."""

    ICON_NODE = '▸ '
    ICON_NODE_EXPANDED = '▾ '
    ICON_LEAF = '∗ '

    class ContextRequested(Message):
        """Request the context menu for a kind node, if any."""

        def __init__(self, parent: str | None) -> None:
            self.kind = parent
            super().__init__()

    class KindConfirmed(Message):
        """Request choosing a kind after a double click."""

        def __init__(self, kind: str) -> None:
            self.kind = kind
            super().__init__()

    def render_label(self, node: TreeNode[str], base_style: Style, style: Style) -> Text:
        """Give leaf kinds the same compact marker as the Mods dependency tree."""
        text = super().render_label(node, base_style, style)
        return Text.assemble((self.ICON_LEAF, base_style), text) if not node.allow_expand else text

    def _context_node_for(self, event: events.MouseEvent) -> TreeNode[str] | None:
        style = event.style
        node_id = None if style is None else style.meta.get('node')
        if not isinstance(node_id, int):
            return None
        return self.get_node_by_id(NodeID(node_id))

    def _click_node_for(self, event: events.Click) -> TreeNode[str] | None:
        style = event.style
        line = None if style is None else style.meta.get('line')
        return self.get_node_at_line(line) if isinstance(line, int) else None

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 3:
            node = self._context_node_for(event)
            parent = None if node is None else node.data
            self.post_message(self.ContextRequested(parent))
            event.prevent_default()
            event.stop()
            return
        await super()._on_mouse_down(event)

    async def _on_click(self, event: events.Click) -> None:
        if event.button == 3:
            event.prevent_default()
            event.stop()
            return
        if event.button == 1:
            node = self._click_node_for(event)
            if node is not None:
                if event.chain >= 2 and node.data is not None:
                    self.post_message(self.KindConfirmed(node.data))
                else:
                    self._toggle_node(node)
                event.prevent_default()
                event.stop()
                return
        await super()._on_click(event)


def kind_button_label(rules: EntityRules, kind: str | None, *, prompt: str) -> str:
    """Format a selected kind for its button, or retain the descriptive prompt."""
    if kind is None:
        return prompt
    definition = rules.kinds[kind]
    return f'{definition.label} ({kind})'


def add_kind_nodes(parent: TreeNode[str], rules: EntityRules) -> None:
    """Add the configured kind hierarchy beneath one Tree node."""
    children: dict[str | None, list[str]] = defaultdict(list)
    for name, kind in rules.kinds.items():
        children[kind.parent].append(name)

    def visit(node: TreeNode[str], parent_name: str | None) -> None:
        for name in sorted(
            children[parent_name], key=lambda child: rules.kinds[child].label.casefold()
        ):
            kind = rules.kinds[name]
            child = node.add(
                f'{kind.label} ({name})',
                data=name,
                allow_expand=bool(children[name]),
            )
            visit(child, name)

    visit(parent, None)
