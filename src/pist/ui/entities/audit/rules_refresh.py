"""Generated-rule diff presentation for the entity-audit workflow."""

import difflib
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from pist.entities.rules import EntityRuleLayer, EntityRulesForId, entity_rules_toml


class AuditRulesRefreshScreen(ModalScreen[bool]):
    """Review the generated-layer diff before replacing it."""

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(self, diff: str) -> None:
        super().__init__()
        self._diff = diff

    def compose(self) -> ComposeResult:
        with Vertical(id='audit-rules-refresh-dialog'):
            yield Static('审计规则变更预览', classes='audit-section-title')
            with VerticalScroll(id='audit-rules-refresh-body'):
                yield Static(_diff_text(self._diff), id='audit-rules-refresh-diff')
            with Horizontal():
                yield Button('取消', id='audit-rules-refresh-cancel')
                yield Button('保存', id='audit-rules-refresh-save', variant='primary')

    @on(Button.Pressed, '#audit-rules-refresh-cancel')
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, '#audit-rules-refresh-save')
    def save(self) -> None:
        self.dismiss(True)


def audit_layer_diff(current: EntityRuleLayer, refreshed: EntityRuleLayer) -> str:
    """Return compact unified diffs grouped by changed entity ID."""
    return '\n\n'.join(
        diff
        for entity_name in sorted(current.entities.keys() | refreshed.entities.keys())
        if (
            diff := _entity_rule_diff(
                entity_name,
                current.entities.get(entity_name),
                refreshed.entities.get(entity_name),
            )
        )
    )


def _entity_rule_diff(
    entity_name: str,
    current: EntityRulesForId | None,
    refreshed: EntityRulesForId | None,
) -> str:
    """Return one entity's diff, including its TOML table name in both headers."""
    before = _entity_rule_toml(entity_name, current)
    after = _entity_rule_toml(entity_name, refreshed)
    if before == after:
        return ''
    table_name = _entity_table_name(entity_name)
    return '\n'.join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f'{table_name}（当前）',
            tofile=f'{table_name}（刷新后）',
            lineterm='',
        )
    )


def _entity_rule_toml(entity_name: str, rules: EntityRulesForId | None) -> str:
    """Serialize one entity table, or an empty block when it does not yet exist."""
    if rules is None:
        return ''
    return entity_rules_toml(EntityRuleLayer(entities={entity_name: rules}))


def _entity_table_name(entity_name: str) -> str:
    """Return the canonical TOML table header for one entity ID."""
    return _entity_rule_toml(entity_name, EntityRulesForId(rules=())).splitlines()[0]


def _diff_text(diff: str) -> Text:
    text = Text()
    for line in diff.splitlines():
        style = (
            'cyan'
            if line.startswith(('---', '+++'))
            else 'yellow'
            if line.startswith('@@')
            else 'green'
            if line.startswith('+')
            else 'red'
            if line.startswith('-')
            else 'dim'
        )
        text.append(f'{line}\n', style=style)
    text.rstrip()
    return text
