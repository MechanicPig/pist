"""Textual UI for turning static Loenn placements into entity rules."""

from collections.abc import Iterable
from contextlib import ExitStack
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Header,
    Input,
    ListItem,
    ListView,
    Select,
    Static,
)

from pist.game.entities import EntityConfigStore, EntityRuleConflict
from pist.game.modpath import ModPath
from pist.loenn import LoennMod, LoennPlacement, LoennRegistry, LoennWarning, load_loenn_registry

from .tui import RefreshableCssApp

TEMPLATE_FILTER_ID = 'template-filter'
TEMPLATE_LIST_ID = 'template-list'
TEMPLATE_DETAIL_ID = 'template-detail'
TEMPLATE_DETAIL_CONTENT_ID = 'template-detail-content'
RULE_CONFLICTS_ID = 'rule-conflicts'
KIND_INPUT_ID = 'kind-input'
SAVE_RULE_ID = 'save-rule'


def search_placements(
    placements: Iterable[LoennPlacement], query: str
) -> tuple[LoennPlacement, ...]:
    """Return placements whose visible editor identity contains every search word."""
    words = tuple(word.casefold() for word in query.split() if word)
    return tuple(
        placement
        for placement in placements
        if all(
            word
            in (f'{placement.entity_name} {placement.name} {placement.display_name}').casefold()
            for word in words
        )
    )


class LoennPlacementItem(ListItem):
    """One selectable static Loenn placement template."""

    def __init__(self, placement: LoennPlacement) -> None:
        label = Text()
        label.append(placement.display_name, style='bold')
        if placement.mod_name is not None:
            label.append(f' [{placement.mod_name}]', style='dark_orange')
        label.append('\n')
        label.append(placement.entity_name, style='dim')
        super().__init__(Static(label))
        self.placement = placement

    def filter(self, query: str) -> None:
        """Show this template only when it matches the current query."""
        self.display = self.placement in search_placements((self.placement,), query)


class EntityRuleApp(RefreshableCssApp[None]):
    """Search static Loenn placements and save their entity rules to TOML."""

    CSS_PATH = 'styles/entity_rules.tcss'
    TITLE = 'Pist · 实体规则'
    BINDINGS: ClassVar = [
        ('escape', 'quit', '退出'),
        ('ctrl+s', 'save_rule', '保存'),
    ]

    def __init__(self, registry: LoennRegistry, store: EntityConfigStore) -> None:
        super().__init__()
        self._store = store
        self._rules = store.load()
        self._conflicts = store.conflicts
        self._placements = tuple(
            sorted(
                (
                    placement
                    for entity_name in registry.entity_names()
                    for placement in registry.placements_for(entity_name)
                ),
                key=lambda placement: (
                    placement.display_name.casefold(),
                    placement.entity_name.casefold(),
                    placement.name.casefold(),
                ),
            )
        )
        self._warnings = registry.warnings
        self._selected: LoennPlacement | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder='搜索名称、实体 ID 或 placement', id=TEMPLATE_FILTER_ID)
        if self._warnings:
            yield Collapsible(
                Static(self._warning_text(self._warnings)),
                title=f'警告：已跳过 {len(self._warnings)} 个 Loenn 文件',
                collapsed=True,
                collapsed_symbol='▸',
                expanded_symbol='▾',
                id='template-warnings',
            )
        if self._conflicts:
            yield Collapsible(
                Static(self._conflict_text(self._conflicts)),
                title=f'本地规则覆盖了 {len(self._conflicts)} 条共享规则',
                collapsed=True,
                collapsed_symbol='▸',
                expanded_symbol='▾',
                id=RULE_CONFLICTS_ID,
            )
        with Horizontal():
            yield ListView(*self._items(self._placements), id=TEMPLATE_LIST_ID)
            with Vertical(id='template-right'):
                with VerticalScroll(id=TEMPLATE_DETAIL_ID):
                    yield Static(
                        '从左侧选择一个静态 Loenn template。',
                        id=TEMPLATE_DETAIL_CONTENT_ID,
                    )
                yield Select(
                    ((f'{kind.label} ({name})', name) for name, kind in self._rules.kinds.items()),
                    prompt='选择要归类到的 kind',
                    id=KIND_INPUT_ID,
                )
                yield Button('保存', id=SAVE_RULE_ID, variant='primary', disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(f'#{TEMPLATE_FILTER_ID}', Input).focus()

    @on(Input.Changed, f'#{TEMPLATE_FILTER_ID}')
    def filter_placements(self, event: Input.Changed) -> None:
        """Filter templates without changing the current rule configuration."""
        templates = self.query_one(f'#{TEMPLATE_LIST_ID}', ListView)
        for item in templates.query(LoennPlacementItem):
            item.filter(event.value)

    @on(ListView.Selected, f'#{TEMPLATE_LIST_ID}')
    def select_placement(self, event: ListView.Selected) -> None:
        """Show one placement's exact static rule condition."""
        if not isinstance(event.item, LoennPlacementItem):
            return
        self._selected = event.item.placement
        self.query_one(f'#{TEMPLATE_DETAIL_CONTENT_ID}', Static).update(
            self._detail(self._selected)
        )
        self.query_one(f'#{SAVE_RULE_ID}', Button).disabled = False

    @on(Button.Pressed, f'#{SAVE_RULE_ID}')
    def save_rule(self) -> None:
        """Save the selected template as an entity-and-attribute rule."""
        if self._selected is None:
            return
        kind = self.query_one(f'#{KIND_INPUT_ID}', Select).value
        if not isinstance(kind, str):
            self.notify('请先选择 kind。', severity='warning')
            return
        try:
            self._rules = self._rules.with_rule(
                self._selected.entity_name,
                kind,
                self._selected.attrs,
            )
            self._store.save(self._rules)
        except ValueError as error:
            self.notify(str(error), severity='error')
            return
        self.notify(f'已写入 {self._store.path}: {self._selected.entity_name} → {kind}。')

    def action_save_rule(self) -> None:
        self.save_rule()

    @staticmethod
    def _items(placements: Iterable[LoennPlacement]) -> tuple[LoennPlacementItem, ...]:
        return tuple(LoennPlacementItem(placement) for placement in placements)

    def _detail(self, placement: LoennPlacement) -> Text:
        text = Text()
        text.append(f'{placement.display_name}\n', style='bold cyan')
        text.append(f'实体: {placement.entity_name}\n')
        text.append(f'放置预设: {placement.name}\n')
        if placement.mod_name is not None:
            text.append(f'Mod: {placement.mod_name}\n', style='dark_orange')
        text.append(f'来源: {placement.source}\n', style='dim')
        text.append('静态属性:\n', style='bold')
        if placement.attrs:
            for name, value in placement.attrs.items():
                text.append(f'  {name} = {value!r}\n')
        else:
            text.append('  （无）\n', style='dim')
        return text

    @staticmethod
    def _warning_text(warnings: Iterable[LoennWarning]) -> Text:
        text = Text()
        for warning in warnings:
            text.append(f'• {warning.source}: {warning.message}\n', style='yellow')
        text.rstrip()
        return text

    @staticmethod
    def _conflict_text(conflicts: Iterable[EntityRuleConflict]) -> Text:
        text = Text()
        for conflict in conflicts:
            when = ', '.join(f'{name} = {value!r}' for name, value in conflict.when) or '默认条件'
            text.append(
                f'• {conflict.entity_name} ({when}): '
                f'{conflict.shared_kind} → {conflict.local_kind}\n',
                style='yellow',
            )
        text.rstrip()
        return text


async def manage_entity_rules(mods: Iterable[tuple[Path, str]], *, shared: bool = False) -> None:
    """Open the static-Loenn-template rule manager for enabled Mod paths."""
    with ExitStack() as stack:
        loenn_mods = tuple(
            LoennMod(stack.enter_context(ModPath(path)), metadata_name)
            for path, metadata_name in mods
        )
        registry = load_loenn_registry(loenn_mods)
    await EntityRuleApp(registry, EntityConfigStore(shared=shared)).run_async()
