"""Textual UI for entity-level audit knowledge."""

import asyncio
import difflib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Literal

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, ListItem, ListView, Select, Static
from textual.widgets._select import SelectCurrent, SelectOverlay

from pist.entities.audit import (
    META_ATTRIBUTE_PREFIX,
    UNKNOWN,
    AttributeAuditStatus,
    AttributeAuditSummary,
    DefaultValue,
    EntityAuditDetail,
    EntityAuditStatus,
    EntityAuditStore,
    EntityAuditSummary,
    EntityVariant,
    ObservationQuestion,
    ObservationStatus,
    RuleCandidate,
    occurrences_for_variants,
)
from pist.entities.rules import (
    EntityConfigStore,
    EntityRuleLayer,
    EntityRules,
    EntityStat,
    EntityTableField,
    entity_rules_toml,
)
from pist.game.binmap import AttrValue
from pist.game.saves import MapProgress, SaveReader
from pist.map_preview import MapPreview

from ..tui import RefreshableCssApp
from .kinds import KindTree, add_kind_nodes, kind_button_label
from .occurrences import MapOccurrences, OccurrenceScreen
from .occurrences import load_occurrence_map as _load_occurrence_map
from .occurrences import occurrence_markers as _occurrence_markers

ENTITY_FILTER_ID = 'audit-entity-filter'
ENTITY_LIST_ID = 'audit-entity-list'
DETAIL_ID = 'audit-detail'
ENTITY_STATUS_ID = 'audit-entity-status'
ENTITY_EVIDENCE_ID = 'audit-entity-evidence'
ENTITY_NOTE_ID = 'audit-entity-note'
SAVE_ENTITY_ID = 'audit-save-entity'
ENTITY_KIND_ID = 'audit-entity-kind'
CONFIRM_ENTITY_KIND_ID = 'audit-confirm-entity-kind'
REVOKE_ENTITY_KIND_ID = 'audit-revoke-entity-kind'
ATTRIBUTE_SELECT_ID = 'audit-attribute-select'
ATTRIBUTE_STATUS_ID = 'audit-attribute-status'
ATTRIBUTE_DEFAULT_ID = 'audit-attribute-default'
ATTRIBUTE_EVIDENCE_ID = 'audit-attribute-evidence'
ATTRIBUTE_NOTE_ID = 'audit-attribute-note'
SAVE_ATTRIBUTE_ID = 'audit-save-attribute'
VARIANT_SELECT_ID = 'audit-variant-select'
OBSERVATION_QUESTION_ID = 'audit-observation-question'
OBSERVATION_STATUS_ID = 'audit-observation-status'
OBSERVATION_KIND_ID = 'audit-observation-kind'
OBSERVATION_EVIDENCE_ID = 'audit-observation-evidence'
OBSERVATION_NOTE_ID = 'audit-observation-note'
SAVE_OBSERVATION_ID = 'audit-save-observation'
VIEW_OCCURRENCES_ID = 'audit-view-occurrences'
VIEW_GROUP_OCCURRENCES_ID = 'audit-view-group-occurrences'
REFRESH_AUDIT_RULES_ID = 'audit-refresh-audit-rules'
KIND_TREE_ID = 'audit-kind-tree'
NEW_KIND_NAME_ID = 'audit-new-kind-name'
NEW_KIND_LABEL_ID = 'audit-new-kind-label'
NEW_KIND_SPRITE_ID = 'audit-new-kind-sprite'
NEW_KIND_PARENT_ID = 'audit-new-kind-parent'
NEW_KIND_STAT_ID = 'audit-new-kind-stat'
NEW_KIND_TABLE_ID = 'audit-new-kind-table'
NEW_KIND_FIELD_ID = 'audit-new-kind-field'
NEW_KIND_SELECT_VALUE_ID = 'audit-new-kind-select-value'
SAVE_NEW_KIND_ID = 'audit-save-new-kind'

ENTITY_STATUS_LABELS: Mapping[EntityAuditStatus, str] = MappingProxyType(
    {
        EntityAuditStatus.UNKNOWN: '未审查',
        EntityAuditStatus.ENTITY_CANDIDATE: '实体候选',
        EntityAuditStatus.IGNORED: '已排除',
    }
)
ATTRIBUTE_STATUS_LABELS: Mapping[AttributeAuditStatus, str] = MappingProxyType(
    {
        AttributeAuditStatus.UNKNOWN: '未审查',
        AttributeAuditStatus.AFFECTS_KIND: '影响分类',
        AttributeAuditStatus.DOES_NOT_AFFECT_KIND: '不影响分类',
        AttributeAuditStatus.LIKELY_NOT_AFFECT_KIND: '暂定不影响分类',
        AttributeAuditStatus.UNSURE_DEFAULT: '默认值待确认',
        AttributeAuditStatus.AFFECTS_BEHAVIOR: '影响行为',
    }
)
OBSERVATION_QUESTION_LABELS: Mapping[ObservationQuestion, str] = MappingProxyType(
    {
        ObservationQuestion.ENTITY_CLASSIFICATION: '实体分类',
        ObservationQuestion.PAUSE_MENU_COUNT: '暂停菜单计数',
        ObservationQuestion.DEBUG_MAP_COLOR: 'Debug Map 颜色',
        ObservationQuestion.TOTAL_STRAWBERRY_COUNT: '草莓总数',
    }
)
OBSERVATION_STATUS_LABELS: Mapping[ObservationStatus, str] = MappingProxyType(
    {
        ObservationStatus.UNKNOWN: '未确认',
        ObservationStatus.CONFIRMED: '已确认',
        ObservationStatus.NOT_COLLECTIBLE: '排除',
        ObservationStatus.CONFLICT: '有冲突',
    }
)
ENTITY_STATUS_STYLES: Mapping[EntityAuditStatus, str] = MappingProxyType(
    {
        EntityAuditStatus.UNKNOWN: 'yellow',
        EntityAuditStatus.IGNORED: 'dim',
        EntityAuditStatus.ENTITY_CANDIDATE: 'cyan',
    }
)
ATTRIBUTE_STATUS_STYLES: Mapping[AttributeAuditStatus, str] = MappingProxyType(
    {
        AttributeAuditStatus.UNKNOWN: 'yellow',
        AttributeAuditStatus.AFFECTS_KIND: 'cyan',
        AttributeAuditStatus.DOES_NOT_AFFECT_KIND: 'green',
        AttributeAuditStatus.LIKELY_NOT_AFFECT_KIND: 'dark_orange',
        AttributeAuditStatus.UNSURE_DEFAULT: 'magenta',
        AttributeAuditStatus.AFFECTS_BEHAVIOR: 'dark_orange',
    }
)


@dataclass(frozen=True, slots=True)
class NewKind:
    """One validated category edit returned by the category-editor screen."""

    name: str
    label: str
    parent: str | None
    sprite: str | None
    stat: EntityStat
    table_field: EntityTableField | None
    select_value: str | None


type SaveKind = Callable[[NewKind, str | None], EntityRules | None]
type DeleteKind = Callable[[str], EntityRules | None]


class AuditSelectOverlay(SelectOverlay):
    """Keep wheel events inside an expanded audit dropdown at either scroll edge."""

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        super()._on_mouse_scroll_down(event)
        event.stop()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        super()._on_mouse_scroll_up(event)
        event.stop()


class AuditSelect(Select[str]):
    """A string Select whose option overlay never scrolls its parent container."""

    def compose(self) -> ComposeResult:
        yield SelectCurrent(self.prompt)
        yield AuditSelectOverlay(type_to_search=self._type_to_search).data_bind(
            compact=Select.compact
        )


class EntityAuditItem(ListItem):
    """One first-level entity summary."""

    def __init__(self, summary: EntityAuditSummary) -> None:
        self.summary = summary
        super().__init__(Static(self._label()))

    def update_summary(self, summary: EntityAuditSummary) -> None:
        """Refresh the visible state after saving entity knowledge."""
        self.summary = summary
        self.query_one(Static).update(self._label())

    def filter(self, query: str) -> None:
        """Filter by entity ID and current audit status."""
        words = tuple(word.casefold() for word in query.split() if word)
        text = f'{self.summary.entity_name} {self.summary.status.value}'
        self.display = all(word in text.casefold() for word in words)

    def _label(self) -> Text:
        text = Text(self.summary.entity_name, style='bold')
        text.append(
            f' [{ENTITY_STATUS_LABELS[self.summary.status]}]',
            style=ENTITY_STATUS_STYLES[self.summary.status],
        )
        text.append(
            f'\n{self.summary.occurrence_count} instances · {self.summary.variant_count} variants',
            style='dim',
        )
        return text


class EntityAuditGroupItem(ListItem):
    """A non-selectable header separating entity review states."""

    def __init__(self, status: EntityAuditStatus, count: int) -> None:
        self.status = status
        self.count = count
        super().__init__(Static(self._label()), disabled=True)

    def update_count(self, count: int) -> None:
        """Update the group count without replacing the navigation list."""
        self.count = count
        self.query_one(Static).update(self._label())

    def _label(self) -> Text:
        return Text(f'{ENTITY_STATUS_LABELS[self.status]}（{self.count}）', style='bold')


class AttributeAuditItem(ListItem):
    """One second-level attribute summary, retaining missing-value counts."""

    def __init__(self, summary: AttributeAuditSummary) -> None:
        self.summary = summary
        super().__init__(Static(self._label()))

    def _label(self) -> Text:
        text = Text(_audit_attribute_name(self.summary.name), style='bold')
        text.append(
            f' [{ATTRIBUTE_STATUS_LABELS[self.summary.status]}]',
            style=ATTRIBUTE_STATUS_STYLES[self.summary.status],
        )
        text.append('\n')
        text.append(_value_counts_text(self.summary), style='dim')
        if self.summary.default_value is not UNKNOWN:
            text.append(
                f'\n默认值: {json.dumps(self.summary.default_value, ensure_ascii=False)}',
                style='cyan',
            )
        return text


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
                yield Static(_audit_layer_diff_text(self._diff), id='audit-rules-refresh-diff')
            with Horizontal():
                yield Button('取消', id='audit-rules-refresh-cancel')
                yield Button('保存', id='audit-rules-refresh-save', variant='primary')

    @on(Button.Pressed, '#audit-rules-refresh-cancel')
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, '#audit-rules-refresh-save')
    def save(self) -> None:
        self.dismiss(True)


class KindDeleteScreen(ModalScreen[bool]):
    """Require an explicit acknowledgement before a kind is replaced by its parent."""

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(self, rules: EntityRules, kind: str) -> None:
        super().__init__()
        definition = rules.kinds[kind]
        assert definition.parent is not None
        self._kind = kind
        self._label = definition.label
        self._fallback = definition.parent
        self._rule_count = rules.direct_rule_count(kind)
        self._child_count = sum(item.parent == kind for item in rules.kinds.values())

    def compose(self) -> ComposeResult:
        with Vertical(id='kind-delete-dialog'):
            yield Static('删除类别', classes='audit-section-title')
            text = Text()
            text.append(f'删除 {self._label} ({self._kind})？\n\n')
            text.append(f'直接命中的 {self._rule_count} 条规则将改为父类别 {self._fallback}。\n')
            text.append(f'{self._child_count} 个子类别也会提升到该父类别。\n')
            text.append('审计中的已确认类别会同步降级。', style='yellow')
            yield Static(text)
            with Horizontal():
                yield Button('取消', id='kind-delete-cancel')
                yield Button('删除', id='kind-delete-confirm', variant='error')

    @on(Button.Pressed, '#kind-delete-cancel')
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, '#kind-delete-confirm')
    def confirm(self) -> None:
        self.dismiss(True)


class KindEditorScreen(ModalScreen[NewKind | None]):
    """Create or edit one local collectible kind without editing TOML by hand."""

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(
        self,
        rules: EntityRules,
        *,
        kind: str | None = None,
        parent: str | None = None,
        save_kind: SaveKind | None = None,
    ) -> None:
        super().__init__()
        self._rules = rules
        self._kind = kind
        self._definition = None if kind is None else rules.kinds[kind]
        self._parent_kind = parent if self._definition is None else self._definition.parent
        self._save_kind = save_kind

    def compose(self) -> ComposeResult:
        with VerticalScroll(id='new-kind-dialog'):
            yield Static(
                '新增类别' if self._kind is None else '编辑类别', classes='audit-section-title'
            )
            yield Input(
                self._kind or '',
                placeholder='类别 ID，例如 customberry',
                id=NEW_KIND_NAME_ID,
            )
            yield Input(
                '' if self._definition is None else self._definition.label,
                placeholder='显示名称（留空则使用类别 ID）',
                id=NEW_KIND_LABEL_ID,
            )
            yield Input(
                (
                    ''
                    if self._definition is None or self._definition.sprite is None
                    else self._definition.sprite
                ),
                placeholder='预览贴图路径，例如 customberry.png（可留空）',
                id=NEW_KIND_SPRITE_ID,
            )
            yield Button(
                kind_button_label(self._rules, self._parent_kind, prompt='父类别：无'),
                id=NEW_KIND_PARENT_ID,
            )
            stat = EntityStat.NONE if self._definition is None else self._definition.stat
            table_field = None if self._definition is None else self._definition.table_field
            yield AuditSelect(
                _entity_stat_options(),
                value=stat.value,
                id=NEW_KIND_STAT_ID,
            )
            yield Input(
                '' if table_field is None else table_field.table,
                placeholder='目标表名，例如主表',
                id=NEW_KIND_TABLE_ID,
                disabled=stat is EntityStat.NONE,
            )
            yield Input(
                '' if table_field is None else table_field.field,
                placeholder='目标字段名，例如红草莓数',
                id=NEW_KIND_FIELD_ID,
                disabled=stat is EntityStat.NONE,
            )
            yield Input(
                '' if self._definition is None else self._definition.select_value or '',
                placeholder='单选值，例如通关收集（仅 select 的子类别可填）',
                id=NEW_KIND_SELECT_VALUE_ID,
                disabled=not self._can_set_select_value(stat),
            )
            with Horizontal():
                yield Button('取消', id='new-kind-cancel')
                yield Button('保存', id=SAVE_NEW_KIND_ID, variant='primary')

    @on(Button.Pressed, '#new-kind-cancel')
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, f'#{NEW_KIND_PARENT_ID}')
    def select_parent(self) -> None:
        self.app.push_screen(
            KindPickerScreen(
                self._rules,
                title='选择父类别',
                allow_blank=True,
                save_kind=self._save_kind,
            ),
            self._set_parent,
        )

    def _set_parent(self, parent: str | None) -> None:
        self._parent_kind = parent
        self.query_one(f'#{NEW_KIND_PARENT_ID}', Button).label = kind_button_label(
            self._rules, parent, prompt='父类别：无'
        )
        self._update_select_value_disabled()

    @on(Select.Changed, f'#{NEW_KIND_STAT_ID}')
    def change_stat(self, event: Select.Changed) -> None:
        """Enable table-field inputs only for output-producing statistics."""
        enabled = event.value != EntityStat.NONE.value
        self.query_one(f'#{NEW_KIND_TABLE_ID}', Input).disabled = not enabled
        self.query_one(f'#{NEW_KIND_FIELD_ID}', Input).disabled = not enabled
        self._update_select_value_disabled()

    def _can_set_select_value(self, stat: EntityStat) -> bool:
        """Return whether this kind inherits a select-stat owner from its parent."""
        if stat is not EntityStat.NONE:
            return False
        parent = self._parent_kind
        while parent is not None:
            definition = self._rules.kinds[parent]
            if definition.stat is EntityStat.SELECT:
                return True
            parent = definition.parent
        return False

    def _update_select_value_disabled(self) -> None:
        stat = EntityStat(self.query_one(f'#{NEW_KIND_STAT_ID}', Select).value)
        self.query_one(
            f'#{NEW_KIND_SELECT_VALUE_ID}', Input
        ).disabled = not self._can_set_select_value(stat)

    @on(Button.Pressed, f'#{SAVE_NEW_KIND_ID}')
    def save(self) -> None:
        name = self.query_one(f'#{NEW_KIND_NAME_ID}', Input).value.strip()
        if not name:
            self.notify('请填写类别 ID。', severity='warning')
            return
        label = self.query_one(f'#{NEW_KIND_LABEL_ID}', Input).value.strip() or name
        sprite = self.query_one(f'#{NEW_KIND_SPRITE_ID}', Input).value.strip() or None
        stat = EntityStat(self.query_one(f'#{NEW_KIND_STAT_ID}', Select).value)
        table_field = None
        if stat is not EntityStat.NONE:
            table = self.query_one(f'#{NEW_KIND_TABLE_ID}', Input).value.strip()
            field = self.query_one(f'#{NEW_KIND_FIELD_ID}', Input).value.strip()
            if not table or not field:
                self.notify('统计类别需要填写目标表名和字段名。', severity='warning')
                return
            table_field = EntityTableField(table=table, field=field)
        select_value = self.query_one(f'#{NEW_KIND_SELECT_VALUE_ID}', Input).value.strip() or None
        self.dismiss(
            NewKind(
                name=name,
                label=label,
                parent=self._parent_kind,
                sprite=sprite,
                stat=stat,
                table_field=table_field,
                select_value=select_value,
            )
        )


class KindContextScreen(ModalScreen[Literal['add', 'edit', 'delete'] | None]):
    """Small context menu for creating or editing categories in the kind tree."""

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(self, rules: EntityRules, kind: str | None, *, allow_delete: bool = False) -> None:
        super().__init__()
        self._rules = rules
        self._kind = kind
        self._allow_delete = allow_delete

    def compose(self) -> ComposeResult:
        with Vertical(id='kind-context-dialog'):
            if self._kind is None:
                yield Static('类别', classes='audit-section-title')
                yield Button('新增根类别', id='kind-context-add', variant='primary')
            else:
                yield Static(
                    kind_button_label(self._rules, self._kind, prompt='类别'),
                    classes='audit-section-title',
                )
                yield Button('编辑该类别', id='kind-context-edit')
                yield Button('新增子类别', id='kind-context-add', variant='primary')
                if self._allow_delete:
                    yield Button(
                        '删除该类别',
                        id='kind-context-delete',
                        variant='error',
                        disabled=self._rules.kinds[self._kind].parent is None,
                    )
            yield Button('取消', id='kind-context-cancel')

    @on(Button.Pressed)
    def choose_action(self, event: Button.Pressed) -> None:
        action: Literal['add', 'edit', 'delete'] | None
        match event.button.id:
            case 'kind-context-add':
                action = 'add'
            case 'kind-context-edit':
                action = 'edit'
            case 'kind-context-delete':
                action = 'delete'
            case _:
                action = None
        self.dismiss(action)


class KindPickerScreen(ModalScreen[str | None]):
    """Select a kind without relying on SelectOverlay's CJK rendering path."""

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(
        self,
        rules: EntityRules,
        *,
        title: str = '选择类别',
        allow_blank: bool = False,
        save_kind: SaveKind | None = None,
        delete_kind: DeleteKind | None = None,
    ) -> None:
        super().__init__()
        self._rules = rules
        self._title = title
        self._allow_blank = allow_blank
        self._save_kind = save_kind
        self._delete_kind = delete_kind

    def compose(self) -> ComposeResult:
        with VerticalScroll(id='kind-picker-dialog'):
            yield Static(self._title, classes='audit-section-title')
            tree = KindTree('类别', id=KIND_TREE_ID)
            tree.show_root = False
            tree.auto_expand = False
            add_kind_nodes(tree.root, self._rules)
            tree.root.expand_all()
            yield tree
            if self._allow_blank:
                yield Button('不设父类别', id='kind-picker-clear')

    @on(KindTree.KindConfirmed)
    def select_kind(self, event: KindTree.KindConfirmed) -> None:
        if self.app.screen is not self:
            return
        event.stop()
        self.dismiss(event.kind)

    @on(KindTree.ContextRequested)
    def open_context_menu(self, event: KindTree.ContextRequested) -> None:
        if self._save_kind is None:
            self.notify('当前类别选择器不能新增类别。', severity='warning')
            return
        self.app.push_screen(
            KindContextScreen(self._rules, event.kind, allow_delete=self._delete_kind is not None),
            lambda action, kind=event.kind: self._run_context_action(kind, action),
        )

    def _run_context_action(
        self, kind: str | None, action: Literal['add', 'edit', 'delete'] | None
    ) -> None:
        if action is None:
            return
        if action == 'edit' and kind is None:
            return
        if action == 'delete':
            if kind is None or self._delete_kind is None:
                return
            if self._rules.kinds[kind].parent is None:
                self.notify('根类别没有可降级的父类别，不能删除。', severity='warning')
                return
            self.app.push_screen(
                KindDeleteScreen(self._rules, kind),
                lambda confirmed: self._delete_kind_result(kind, confirmed),
            )
            return
        self.app.push_screen(
            KindEditorScreen(
                self._rules,
                kind=kind if action == 'edit' else None,
                parent=kind if action == 'add' else None,
                save_kind=self._save_kind,
            ),
            lambda result: self._save_kind_result(result, kind if action == 'edit' else None),
        )

    def _delete_kind_result(self, kind: str, confirmed: bool | None) -> None:
        if not confirmed:
            return
        delete_kind = self._delete_kind
        if delete_kind is None:
            return
        rules = delete_kind(kind)
        if rules is None:
            return
        parent = self._rules.kinds[kind].parent
        assert parent is not None
        self._rules = rules
        self.dismiss(parent)

    def _save_kind_result(self, result: NewKind | None, previous_name: str | None) -> None:
        if result is None:
            return
        save_kind = self._save_kind
        if save_kind is None:
            return
        rules = save_kind(result, previous_name)
        if rules is None:
            return
        self._rules = rules
        self.dismiss(result.name)

    @on(Button.Pressed, '#kind-picker-clear')
    def clear_kind(self) -> None:
        if self.app.screen is not self:
            return
        self.dismiss(None)


class VariantAuditItem(ListItem):
    """One raw attribute combination for recording game-observation evidence."""

    def __init__(self, variant: EntityVariant) -> None:
        self.variant = variant
        super().__init__(Static(self._label()))

    def _label(self) -> Text:
        text = Text(_attrs_text(self.variant.attrs), style='bold')
        if self.variant.meta:
            text.append(f'\n地图元数据: {_attrs_text(self.variant.meta)}', style='cyan')
        text.append(f'\n{self.variant.occurrence_count} instances', style='dim')
        if self.variant.observations:
            states = ', '.join(
                OBSERVATION_STATUS_LABELS[observation.status]
                for observation in self.variant.observations
            )
            text.append(f' · {states}', style='cyan')
        return text


@dataclass(frozen=True, slots=True)
class ClassificationGroup:
    """Raw variants sharing every attribute not confirmed irrelevant to kind."""

    attrs: dict[str, AttrValue | None]
    meta: dict[str, AttrValue | None]
    variants: tuple[EntityVariant, ...]

    @property
    def occurrence_count(self) -> int:
        return sum(variant.occurrence_count for variant in self.variants)

    @property
    def classification(self) -> tuple[ObservationStatus, str | None] | None:
        """Return a shared terminal conclusion, only when every variant has one."""
        conclusions: set[tuple[ObservationStatus, str | None]] = set()
        for variant in self.variants:
            variant_conclusions = {
                (observation.status, observation.kind)
                for observation in variant.observations
                if observation.question is ObservationQuestion.ENTITY_CLASSIFICATION
                and observation.status
                in {ObservationStatus.CONFIRMED, ObservationStatus.NOT_COLLECTIBLE}
            }
            if len(variant_conclusions) != 1:
                return None
            conclusions.update(variant_conclusions)
        return conclusions.pop() if len(conclusions) == 1 else None


class EntityAuditApp(RefreshableCssApp[None]):
    """Review unknown entity IDs before proposing TOML collectible rules."""

    CSS_PATH = '../styles/entities_audit.tcss'
    TITLE = 'Pist · 实体规则审计'
    BINDINGS: ClassVar = [
        ('escape', 'quit', '退出'),
        ('ctrl+s', 'save_entity', '保存'),
    ]

    def __init__(
        self,
        store: EntityAuditStore,
        report_id: int,
        save_reader: SaveReader | None = None,
        kind_store: EntityConfigStore | None = None,
        game_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._kind_store = kind_store or EntityConfigStore()
        self._rules = self._kind_store.load()
        self._kind_values: dict[str, str | None] = {
            ENTITY_KIND_ID: None,
            OBSERVATION_KIND_ID: None,
        }
        self._report_id = report_id
        self._save_reader = save_reader
        self._game_dir = game_dir
        self._summaries = store.entity_summaries(report_id)
        self._summary_sort_keys: dict[str, tuple[int, int, str]] = {}
        self._status_counts = Counter(summary.status for summary in self._summaries)
        self._selected_entity: EntityAuditDetail | None = None
        self._selected_attribute: AttributeAuditSummary | None = None
        self._groups: tuple[ClassificationGroup, ...] = ()
        self._selected_group: ClassificationGroup | None = None
        self._candidates: tuple[RuleCandidate, ...] = ()

    def compose(self) -> ComposeResult:
        yield Header()
        yield self._toolbar()
        yield Horizontal(
            ListView(*self._entity_items(), id=ENTITY_LIST_ID),
            self._detail_pane(),
            id='audit-body',
        )
        yield Footer()

    @staticmethod
    def _toolbar() -> Horizontal:
        return Horizontal(
            Input(placeholder='筛选实体 ID 或审查状态', id=ENTITY_FILTER_ID),
            Button('从审计刷新规则', id=REFRESH_AUDIT_RULES_ID, variant='primary'),
            id='audit-toolbar',
        )

    def _detail_pane(self) -> VerticalScroll:
        return VerticalScroll(
            Static('请从左侧选择一个实体。', id='audit-detail-content'),
            Button('查看出现位置', id=VIEW_OCCURRENCES_ID, disabled=True),
            self._entity_review(),
            self._attribute_review(),
            id='audit-right',
        )

    def _entity_review(self) -> Vertical:
        return Vertical(
            Static('实体审查', classes='audit-section-title'),
            AuditSelect(_entity_status_options(), id=ENTITY_STATUS_ID, disabled=True),
            Input(placeholder='证据来源（可选）', id=ENTITY_EVIDENCE_ID, disabled=True),
            Input(placeholder='原因或备注', id=ENTITY_NOTE_ID, disabled=True),
            Button('保存', id=SAVE_ENTITY_ID, disabled=True, variant='primary'),
            Button('确认整实体类别（不受属性影响时使用）', id=ENTITY_KIND_ID, disabled=True),
            Horizontal(
                Button('确认', id=CONFIRM_ENTITY_KIND_ID, disabled=True, variant='primary'),
                Button('撤回', id=REVOKE_ENTITY_KIND_ID, disabled=True),
                id='audit-entity-kind-actions',
            ),
            id='audit-entity-review',
        )

    def _attribute_review(self) -> Vertical:
        return Vertical(
            Static('属性审查', classes='audit-section-title'),
            AuditSelect((), prompt='选择属性', id=ATTRIBUTE_SELECT_ID, disabled=True),
            AuditSelect(_attribute_status_options(), id=ATTRIBUTE_STATUS_ID, disabled=True),
            Input(
                placeholder='确认的默认值（JSON，例如 null 或 false；留空表示未确认）',
                id=ATTRIBUTE_DEFAULT_ID,
                disabled=True,
            ),
            Input(placeholder='证据来源（可选）', id=ATTRIBUTE_EVIDENCE_ID, disabled=True),
            Input(placeholder='原因或备注', id=ATTRIBUTE_NOTE_ID, disabled=True),
            Button('保存', id=SAVE_ATTRIBUTE_ID, disabled=True, variant='primary'),
            self._classification_review(),
            id='audit-attribute-review',
        )

    def _classification_review(self) -> Vertical:
        return Vertical(
            Static('分类确认', classes='audit-section-title'),
            AuditSelect((), prompt='选择分类属性组合', id=VARIANT_SELECT_ID, disabled=True),
            Button('查看该组合的地图', id=VIEW_GROUP_OCCURRENCES_ID, disabled=True),
            AuditSelect(_observation_question_options(), id=OBSERVATION_QUESTION_ID, disabled=True),
            AuditSelect(_observation_status_options(), id=OBSERVATION_STATUS_ID, disabled=True),
            Button('确认的类别（已确认时必填）', id=OBSERVATION_KIND_ID, disabled=True),
            Input(placeholder='证据来源（可选）', id=OBSERVATION_EVIDENCE_ID, disabled=True),
            Input(placeholder='观察备注', id=OBSERVATION_NOTE_ID, disabled=True),
            Button('保存', id=SAVE_OBSERVATION_ID, disabled=True, variant='primary'),
            id='audit-classification-review',
        )

    def on_mount(self) -> None:
        self.query_one(f'#{ENTITY_FILTER_ID}', Input).focus()

    @on(Input.Changed, f'#{ENTITY_FILTER_ID}')
    def filter_entities(self, event: Input.Changed) -> None:
        """Filter entity navigation without changing raw data or knowledge."""
        self._apply_entity_filter(event.value)

    def _apply_entity_filter(self, query: str) -> None:
        """Apply the current navigation query after an in-place list refresh."""
        entities = self.query_one(f'#{ENTITY_LIST_ID}', ListView)
        for item in entities.query(EntityAuditItem):
            item.filter(query)
        for group in entities.query(EntityAuditGroupItem):
            group.display = any(
                item.display
                for item in entities.query(EntityAuditItem)
                if item.summary.status is group.status
            )

    @on(ListView.Selected, f'#{ENTITY_LIST_ID}')
    def select_entity(self, event: ListView.Selected) -> None:
        """Open the second-level entity and attribute review view."""
        if not isinstance(event.item, EntityAuditItem):
            return
        self._selected_entity = self._store.entity_detail(
            event.item.summary.entity_name, self._report_id
        )
        self._selected_attribute = None
        self._selected_group = None
        self._refresh_detail()
        self._update_entity_controls(self._selected_entity)
        attribute_select = self.query_one(f'#{ATTRIBUTE_SELECT_ID}', Select)
        attribute_select.disabled = False
        attribute_select.set_options(_attribute_options(self._selected_entity.attr_summaries))
        attribute_select.clear()
        self._disable_attribute_controls()
        self._refresh_variants()
        self._disable_observation_controls()

    @on(Select.Changed, f'#{ATTRIBUTE_SELECT_ID}')
    def select_attribute(self, event: Select.Changed) -> None:
        """Enable scoped knowledge editing for a single entity attribute."""
        if self._selected_entity is None or not isinstance(event.value, str):
            return
        self._selected_attribute = next(
            attribute
            for attribute in self._selected_entity.attr_summaries
            if attribute.name == event.value
        )
        self._update_attribute_controls(self._selected_attribute)

    @on(Select.Changed, f'#{VARIANT_SELECT_ID}')
    def select_variant(self, event: Select.Changed) -> None:
        """Enable one classification conclusion for every raw variant in a group."""
        if self._selected_entity is None or not isinstance(event.value, str):
            return
        self._selected_group = self._groups[int(event.value)]
        self._update_observation_controls(self._selected_group)

    @on(Select.Changed, f'#{OBSERVATION_STATUS_ID}')
    def update_observation_kind(self, event: Select.Changed) -> None:
        """An excluded conclusion deliberately has no configured kind."""
        if self._selected_group is None or not isinstance(event.value, str):
            return
        self._update_observation_kind_control(ObservationStatus(event.value))

    @on(Button.Pressed, f'#{SAVE_ENTITY_ID}')
    def save_entity(self) -> None:
        """Persist entity-level audit status and evidence."""
        if self._selected_entity is None:
            return
        status = self.query_one(f'#{ENTITY_STATUS_ID}', Select).value
        if not isinstance(status, str):
            self.notify('请选择实体审查状态。', severity='warning')
            return
        previous = self._selected_entity
        reason = self.query_one(f'#{ENTITY_NOTE_ID}', Input).value.strip()
        evidence = _evidence_value(self.query_one(f'#{ENTITY_EVIDENCE_ID}', Input))
        self._store.save_entity_knowledge(
            previous.entity_name,
            EntityAuditStatus(status),
            reason=reason,
            evidence=evidence,
        )
        self._selected_entity = replace(
            previous, status=EntityAuditStatus(status), reason=reason, evidence=evidence
        )
        self._render_detail()
        self._update_entity_controls(self._selected_entity)
        self._refresh_entity_item(previous.status)
        self.notify('已保存实体审查。')

    @on(Button.Pressed, f'#{VIEW_OCCURRENCES_ID}')
    def view_occurrences(self) -> None:
        """Open map and room counts without adding another main-page scroll area."""
        if self._selected_entity is not None:
            self.app.push_screen(
                OccurrenceScreen(
                    self._selected_entity.entity_name,
                    self._selected_entity.occurrences,
                    self._map_progress,
                    self._start_occurrence_preview if self._game_dir is not None else None,
                )
            )

    @on(Button.Pressed, f'#{VIEW_GROUP_OCCURRENCES_ID}')
    def view_group_occurrences(self) -> None:
        """Show maps for the selected aggregation, not every raw entity occurrence."""
        if self._selected_entity is None or self._selected_group is None:
            return
        occurrences = occurrences_for_variants(
            self._selected_entity.occurrences, self._selected_group.variants
        )
        label = _attrs_text(self._selected_group.attrs)
        if self._selected_group.meta:
            label = f'{label} · {_meta_text(self._selected_group.meta)}'
        self.app.push_screen(
            OccurrenceScreen(
                f'{self._selected_entity.entity_name}（{label}）',
                occurrences,
                self._map_progress,
                self._start_occurrence_preview if self._game_dir is not None else None,
            )
        )

    def _start_occurrence_preview(self, occurrences: MapOccurrences) -> None:
        """Keep the audit TUI responsive while a read-only browser preview is open."""
        self.run_worker(
            self._preview_occurrences(occurrences),
            name='entity-audit-map-preview',
            group='entity-audit-map-preview',
            exclusive=False,
        )

    async def _preview_occurrences(self, occurrences: MapOccurrences) -> None:
        assert self._game_dir is not None
        try:
            map_info, layout = await asyncio.to_thread(
                _load_occurrence_map, self._game_dir, occurrences.source
            )
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        markers = _occurrence_markers(occurrences.occurrences)
        await MapPreview(map_info, layout, audit_markers=markers, read_only=True).preview()

    @on(Button.Pressed, f'#{CONFIRM_ENTITY_KIND_ID}')
    def confirm_entity_kind(self) -> None:
        """Confirm that every current raw variant belongs to one collectible kind."""
        if self._selected_entity is None:
            return
        kind = self._kind_values[ENTITY_KIND_ID]
        if kind is None:
            self.notify('请选择确认的类别。', severity='warning')
            return
        try:
            count = self._store.confirm_entity_kind(
                self._selected_entity.entity_name,
                self._report_id,
                kind,
                reason=self.query_one(f'#{ENTITY_NOTE_ID}', Input).value.strip(),
                evidence=_evidence_value(self.query_one(f'#{ENTITY_EVIDENCE_ID}', Input)),
            )
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        self._reload_selected_entity()
        self.notify(f'已将 {count} 个原始属性变体确认为 {kind}。')

    @on(Button.Pressed, f'#{REVOKE_ENTITY_KIND_ID}')
    def revoke_entity_kind(self) -> None:
        """Restore attribute review after withdrawing the whole-entity conclusion."""
        if self._selected_entity is None:
            return
        count = self._store.revoke_entity_kind(self._selected_entity.entity_name)
        self._reload_selected_entity()
        self.notify(f'已撤回整实体类别确认，并恢复属性审查（移除 {count} 项观察）。')

    @on(Button.Pressed)
    def choose_kind(self, event: Button.Pressed) -> None:
        """Open the stable kind picker for either audit conclusion control."""
        control_id = event.button.id
        if not isinstance(control_id, str) or control_id not in self._kind_values:
            return
        self.push_screen(
            KindPickerScreen(
                self._rules,
                save_kind=self._save_kind_from_picker,
                delete_kind=self._delete_kind_from_picker,
            ),
            lambda kind: self._set_kind(control_id, kind),
        )

    def _set_kind(self, control_id: str, kind: str | None) -> None:
        if kind is None:
            return
        self._kind_values[control_id] = kind
        self.query_one(f'#{control_id}', Button).label = kind_button_label(
            self._rules, kind, prompt='选择类别'
        )

    @on(Button.Pressed, f'#{SAVE_ATTRIBUTE_ID}')
    def save_attribute(self) -> None:
        """Persist knowledge scoped to the selected entity and attribute only."""
        if self._selected_entity is None or self._selected_attribute is None:
            return
        status = self.query_one(f'#{ATTRIBUTE_STATUS_ID}', Select).value
        if not isinstance(status, str):
            self.notify('请选择属性审查状态。', severity='warning')
            return
        try:
            default_value = _default_value(self.query_one(f'#{ATTRIBUTE_DEFAULT_ID}', Input).value)
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        self._store.save_attribute_knowledge(
            self._selected_entity.entity_name,
            self._selected_attribute.name,
            AttributeAuditStatus(status),
            reason=self.query_one(f'#{ATTRIBUTE_NOTE_ID}', Input).value.strip(),
            evidence=_evidence_value(self.query_one(f'#{ATTRIBUTE_EVIDENCE_ID}', Input)),
            default_value=default_value,
        )
        self._reload_selected_entity()
        self.notify('已保存属性审查。')

    def _save_kind_from_picker(
        self, result: NewKind, previous_name: str | None
    ) -> EntityRules | None:
        """Persist a kind created or edited from a classification picker."""
        try:
            rules = (
                self._rules.with_kind(
                    result.name,
                    result.label,
                    parent=result.parent,
                    sprite=result.sprite,
                    stat=result.stat,
                    table_field=result.table_field,
                    select_value=result.select_value,
                )
                if previous_name is None
                else self._rules.with_renamed_kind(
                    previous_name,
                    result.name,
                    result.label,
                    parent=result.parent,
                    sprite=result.sprite,
                    stat=result.stat,
                    table_field=result.table_field,
                    select_value=result.select_value,
                )
            )
            if previous_name is not None and previous_name != result.name:
                self._kind_store.rename_kind(previous_name, result.name, rules)
                self._store.rename_kind(previous_name, result.name)
                self._kind_values = {
                    control_id: result.name if value == previous_name else value
                    for control_id, value in self._kind_values.items()
                }
            else:
                self._kind_store.save(rules)
            self._rules = rules
        except ValueError as error:
            self.notify(str(error), severity='warning', markup=False)
            return None
        if self._selected_entity is not None:
            self._reload_selected_entity()
        self._refresh_kind_options()
        action = (
            '新增'
            if previous_name is None
            else '重命名'
            if previous_name != result.name
            else '更新'
        )
        self.notify(f'已{action}类别：{result.label} ({result.name})。')
        return self._rules

    def _delete_kind_from_picker(self, kind: str) -> EntityRules | None:
        """Delete one kind after its picker confirms fallback to its parent."""
        try:
            fallback = self._rules.kinds[kind].parent
            if fallback is None:
                raise ValueError(f'Cannot delete root entity kind: {kind!r}')
            rules = self._rules.with_deleted_kind(kind)
            self._kind_store.delete_kind(kind, fallback, rules)
            self._store.rename_kind(kind, fallback)
            self._kind_values = {
                control_id: fallback if value == kind else value
                for control_id, value in self._kind_values.items()
            }
            self._rules = rules
        except ValueError as error:
            self.notify(str(error), severity='warning', markup=False)
            return None
        if self._selected_entity is not None:
            self._reload_selected_entity()
        self._refresh_kind_options()
        self.notify(f'已删除类别：{kind}，规则已降级到 {fallback}。')
        return self._rules

    @on(Button.Pressed, f'#{SAVE_OBSERVATION_ID}')
    def save_observation(self) -> None:
        """Persist a manually confirmed or conflicted game-behavior observation."""
        if self._selected_entity is None or self._selected_group is None:
            return
        question = self.query_one(f'#{OBSERVATION_QUESTION_ID}', Select).value
        status = self.query_one(f'#{OBSERVATION_STATUS_ID}', Select).value
        if not isinstance(question, str) or not isinstance(status, str):
            self.notify('请选择观察问题和状态。', severity='warning')
            return
        try:
            variant_count = len(self._selected_group.variants)
            kind = (
                None
                if ObservationStatus(status) is ObservationStatus.NOT_COLLECTIBLE
                else self._kind_values[OBSERVATION_KIND_ID]
            )
            self._store.save_group_observation(
                self._selected_entity.entity_name,
                self._selected_group.variants,
                ObservationQuestion(question),
                ObservationStatus(status),
                kind=kind,
                reason=self.query_one(f'#{OBSERVATION_NOTE_ID}', Input).value.strip(),
                evidence=_evidence_value(self.query_one(f'#{OBSERVATION_EVIDENCE_ID}', Input)),
            )
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        self._reload_selected_entity()
        self.notify(f'已保存 {variant_count} 个原始变体的分类确认。')

    def action_save_entity(self) -> None:
        self.save_entity()

    def _map_progress(self, map_file: str) -> MapProgress:
        if self._save_reader is None:
            return MapProgress.UNRECORDED
        try:
            return self._save_reader.map_progress(map_file)
        except ValueError:
            return MapProgress.ENTERED

    def _entity_items(self) -> tuple[EntityAuditItem | EntityAuditGroupItem, ...]:
        summaries_by_status: dict[EntityAuditStatus, list[EntityAuditSummary]] = defaultdict(list)
        for summary in self._summaries:
            summaries_by_status[summary.status].append(summary)
        items: list[EntityAuditItem | EntityAuditGroupItem] = []
        for status in _entity_status_order():
            summaries = summaries_by_status[status]
            if not summaries:
                continue
            summaries.sort(key=self._entity_sort_key)
            items.append(EntityAuditGroupItem(status, len(summaries)))
            items.extend(EntityAuditItem(summary) for summary in summaries)
        return tuple(items)

    def _entity_sort_key(self, summary: EntityAuditSummary) -> tuple[int, int, str]:
        """Return the immutable session-local sort key for one entity summary."""
        return self._summary_sort_keys.setdefault(
            summary.entity_name,
            (
                min(
                    (self._map_progress(map_file) for map_file in summary.map_files),
                    default=MapProgress.UNRECORDED,
                ),
                -summary.occurrence_count,
                summary.entity_name.casefold(),
            ),
        )

    def _reload_selected_entity(self) -> None:
        assert self._selected_entity is not None
        self._selected_entity = self._store.entity_detail(
            self._selected_entity.entity_name, self._report_id
        )
        self._refresh_detail()
        self._update_entity_controls(self._selected_entity)
        attribute_select = self.query_one(f'#{ATTRIBUTE_SELECT_ID}', Select)
        attribute_select.set_options(_attribute_options(self._selected_entity.attr_summaries))
        if self._selected_attribute is not None:
            self._selected_attribute = next(
                attribute
                for attribute in self._selected_entity.attr_summaries
                if attribute.name == self._selected_attribute.name
            )
            attribute_select.value = self._selected_attribute.name
            self._update_attribute_controls(self._selected_attribute)
        self._refresh_variants()
        self._disable_observation_controls()

    def _refresh_entity_item(self, previous_status: EntityAuditStatus) -> None:
        """Refresh just the saved item, moving it between status groups when needed."""
        assert self._selected_entity is not None
        name = self._selected_entity.entity_name
        summary = next(summary for summary in self._summaries if summary.entity_name == name)
        updated = replace(summary, status=self._selected_entity.status)
        self._summaries = tuple(
            updated if item.entity_name == name else item for item in self._summaries
        )
        entities = self.query_one(f'#{ENTITY_LIST_ID}', ListView)
        item = next(
            item for item in entities.query(EntityAuditItem) if item.summary.entity_name == name
        )

        if previous_status is self._selected_entity.status:
            item.update_summary(updated)
            return

        query = self.query_one(f'#{ENTITY_FILTER_ID}', Input).value
        self._status_counts[previous_status] -= 1
        self._status_counts[self._selected_entity.status] += 1
        self.run_worker(
            self._move_entity_item(
                entities,
                item,
                updated,
                previous_status,
                self._selected_entity.status,
                query,
            ),
            group='entity-audit-navigation',
            exclusive=True,
        )

    async def _move_entity_item(
        self,
        entities: ListView,
        item: EntityAuditItem,
        summary: EntityAuditSummary,
        previous_status: EntityAuditStatus,
        status: EntityAuditStatus,
        query: str,
    ) -> None:
        """Move one mounted item, preserving all unaffected navigation widgets."""
        items = list(entities.children)
        await entities.remove_items([items.index(item)])
        # A removed Textual widget loses its composed child tree, so create only this
        # one replacement rather than remounting the entire navigation list.
        item = EntityAuditItem(summary)

        previous_group = self._group_item(entities, previous_status)
        if self._status_counts[previous_status]:
            assert previous_group is not None
            previous_group.update_count(self._status_counts[previous_status])
        elif previous_group is not None:
            items = list(entities.children)
            await entities.remove_items([items.index(previous_group)])

        group = self._group_item(entities, status)
        if group is None:
            group = EntityAuditGroupItem(status, self._status_counts[status])
            index = self._new_group_index(entities, status)
            await entities.insert(index, [group, item])
        else:
            group.update_count(self._status_counts[status])
            await entities.insert(self._entity_insert_index(entities, group, summary), [item])

        item.filter(query)
        self._update_group_visibility(entities, previous_status)
        self._update_group_visibility(entities, status)

    @staticmethod
    def _group_item(entities: ListView, status: EntityAuditStatus) -> EntityAuditGroupItem | None:
        return next(
            (
                item
                for item in entities.children
                if isinstance(item, EntityAuditGroupItem) and item.status is status
            ),
            None,
        )

    def _new_group_index(self, entities: ListView, status: EntityAuditStatus) -> int:
        """Place a newly non-empty group before its next configured sibling."""
        order = _entity_status_order()
        status_index = order.index(status)
        return next(
            (
                index
                for index, item in enumerate(entities.children)
                if isinstance(item, EntityAuditGroupItem)
                and order.index(item.status) > status_index
            ),
            len(entities.children),
        )

    def _entity_insert_index(
        self, entities: ListView, group: EntityAuditGroupItem, summary: EntityAuditSummary
    ) -> int:
        """Find the sorted position for one item inside an already-mounted group."""
        items = list(entities.children)
        start = items.index(group) + 1
        end = next(
            (
                index
                for index in range(start, len(items))
                if isinstance(items[index], EntityAuditGroupItem)
            ),
            len(items),
        )
        key = self._entity_sort_key(summary)
        for index in range(start, end):
            item = items[index]
            if isinstance(item, EntityAuditItem) and self._entity_sort_key(item.summary) > key:
                return index
        return end

    @staticmethod
    def _update_group_visibility(entities: ListView, status: EntityAuditStatus) -> None:
        """Refresh only the changed group's filter visibility."""
        group = EntityAuditApp._group_item(entities, status)
        if group is not None:
            group.display = any(
                item.display
                for item in entities.query(EntityAuditItem)
                if item.summary.status is status
            )

    def _refresh_detail(self) -> None:
        assert self._selected_entity is not None
        self._candidates = self._store.rule_candidates_for_detail(self._selected_entity)
        self._render_detail()

    def _render_detail(self) -> None:
        """Render a detail already loaded into memory without recomputing candidates."""
        assert self._selected_entity is not None
        self.query_one('#audit-detail-content', Static).update(
            self._detail(self._selected_entity, self._candidates)
        )

    @on(Button.Pressed, f'#{REFRESH_AUDIT_RULES_ID}')
    def refresh_audit_rules(self) -> None:
        """Preview then replace published rules from completed audit decisions."""
        layer = self._store.generated_rule_layer(self._report_id)
        try:
            EntityRules.model_validate({'kinds': self._rules.kinds, 'entities': layer.entities})
            current = self._kind_store.load_shared_layer()
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        diff = _audit_layer_diff(current, layer)
        if not diff:
            self.notify('实体规则没有变化。')
            return
        self.push_screen(
            AuditRulesRefreshScreen(diff),
            lambda confirmed: self._save_audit_rules(layer) if confirmed else None,
        )

    def _save_audit_rules(self, layer: EntityRuleLayer) -> None:
        try:
            self._kind_store.save_generated_layer(layer)
            self._rules = self._kind_store.load()
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        if self._selected_entity is not None:
            self._refresh_detail()
        self.notify(f'已刷新实体规则：{len(layer.entities)} 个实体。')

    def _update_entity_controls(self, detail: EntityAuditDetail) -> None:
        status = self.query_one(f'#{ENTITY_STATUS_ID}', Select)
        status.disabled = False
        status.value = detail.status.value
        evidence = self.query_one(f'#{ENTITY_EVIDENCE_ID}', Input)
        evidence.disabled = False
        _set_optional_evidence(evidence, detail.evidence)
        note = self.query_one(f'#{ENTITY_NOTE_ID}', Input)
        note.disabled = False
        note.value = detail.reason
        self.query_one(f'#{SAVE_ENTITY_ID}', Button).disabled = False
        confirmed_kind = None if detail.kind_confirmation is None else detail.kind_confirmation.kind
        self._kind_values[ENTITY_KIND_ID] = confirmed_kind
        kind_button = self.query_one(f'#{ENTITY_KIND_ID}', Button)
        kind_button.disabled = False
        kind_button.label = kind_button_label(
            self._rules, confirmed_kind, prompt='确认整实体类别（不受属性影响时使用）'
        )
        self.query_one(f'#{CONFIRM_ENTITY_KIND_ID}', Button).disabled = False
        revoke = self.query_one(f'#{REVOKE_ENTITY_KIND_ID}', Button)
        revoke.disabled = detail.kind_confirmation is None
        self.query_one('#audit-attribute-review', Vertical).display = (
            detail.kind_confirmation is None
        )
        self.query_one(f'#{VIEW_OCCURRENCES_ID}', Button).disabled = False

    def _update_attribute_controls(self, summary: AttributeAuditSummary) -> None:
        status = self.query_one(f'#{ATTRIBUTE_STATUS_ID}', Select)
        status.disabled = False
        status.value = summary.status.value
        default = self.query_one(f'#{ATTRIBUTE_DEFAULT_ID}', Input)
        default.disabled = False
        default.value = (
            json.dumps(summary.default_value) if summary.default_value is not UNKNOWN else ''
        )
        evidence = self.query_one(f'#{ATTRIBUTE_EVIDENCE_ID}', Input)
        evidence.disabled = False
        _set_optional_evidence(evidence, summary.evidence)
        note = self.query_one(f'#{ATTRIBUTE_NOTE_ID}', Input)
        note.disabled = False
        note.value = summary.reason
        self.query_one(f'#{SAVE_ATTRIBUTE_ID}', Button).disabled = False

    def _disable_attribute_controls(self) -> None:
        for widget_id, widget_type in (
            (ATTRIBUTE_STATUS_ID, Select),
            (ATTRIBUTE_DEFAULT_ID, Input),
            (ATTRIBUTE_EVIDENCE_ID, Input),
            (ATTRIBUTE_NOTE_ID, Input),
            (SAVE_ATTRIBUTE_ID, Button),
        ):
            widget = self.query_one(f'#{widget_id}', widget_type)
            widget.disabled = True

    def _update_observation_controls(self, group: ClassificationGroup) -> None:
        self.query_one(f'#{VIEW_GROUP_OCCURRENCES_ID}', Button).disabled = False
        for widget_id, widget_type in (
            (OBSERVATION_QUESTION_ID, Select),
            (OBSERVATION_STATUS_ID, Select),
            (OBSERVATION_KIND_ID, Button),
            (OBSERVATION_EVIDENCE_ID, Input),
            (OBSERVATION_NOTE_ID, Input),
            (SAVE_OBSERVATION_ID, Button),
        ):
            self.query_one(f'#{widget_id}', widget_type).disabled = False
        observations = {
            observation for variant in group.variants for observation in variant.observations
        }
        if len(observations) == 1:
            observation = observations.pop()
            self.query_one(f'#{OBSERVATION_QUESTION_ID}', Select).value = observation.question.value
            self.query_one(f'#{OBSERVATION_STATUS_ID}', Select).value = observation.status.value
            kind = self.query_one(f'#{OBSERVATION_KIND_ID}', Button)
            if observation.kind in self._rules.kinds:
                self._kind_values[OBSERVATION_KIND_ID] = observation.kind
                kind.label = kind_button_label(self._rules, observation.kind, prompt='选择 kind')
            else:
                self._kind_values[OBSERVATION_KIND_ID] = None
                kind.label = '确认的类别（已确认时必填）'
            _set_optional_evidence(
                self.query_one(f'#{OBSERVATION_EVIDENCE_ID}', Input), observation.evidence
            )
            self.query_one(f'#{OBSERVATION_NOTE_ID}', Input).value = observation.reason
        else:
            self.query_one(f'#{OBSERVATION_QUESTION_ID}', Select).clear()
            self.query_one(f'#{OBSERVATION_STATUS_ID}', Select).clear()
            self._kind_values[OBSERVATION_KIND_ID] = None
            self.query_one(f'#{OBSERVATION_KIND_ID}', Button).label = '确认的类别（已确认时必填）'
            self.query_one(f'#{OBSERVATION_EVIDENCE_ID}', Input).value = ''
            self.query_one(f'#{OBSERVATION_NOTE_ID}', Input).value = ''
        status = self.query_one(f'#{OBSERVATION_STATUS_ID}', Select).value
        if isinstance(status, str):
            self._update_observation_kind_control(ObservationStatus(status))

    def _update_observation_kind_control(self, status: ObservationStatus) -> None:
        kind = self.query_one(f'#{OBSERVATION_KIND_ID}', Button)
        if status is ObservationStatus.NOT_COLLECTIBLE:
            self._kind_values[OBSERVATION_KIND_ID] = None
            kind.label = '排除（不分配类别）'
            kind.disabled = True
        else:
            kind.disabled = False
            if self._kind_values[OBSERVATION_KIND_ID] is None:
                kind.label = '确认的类别（已确认时必填）'

    def _disable_observation_controls(self) -> None:
        for widget_id, widget_type in (
            (OBSERVATION_QUESTION_ID, Select),
            (OBSERVATION_STATUS_ID, Select),
            (OBSERVATION_KIND_ID, Button),
            (OBSERVATION_EVIDENCE_ID, Input),
            (OBSERVATION_NOTE_ID, Input),
            (SAVE_OBSERVATION_ID, Button),
        ):
            self.query_one(f'#{widget_id}', widget_type).disabled = True

    def _refresh_variants(self) -> None:
        assert self._selected_entity is not None
        self._groups = _classification_groups(self._selected_entity)
        self._selected_group = None
        variants = self.query_one(f'#{VARIANT_SELECT_ID}', Select)
        variants.disabled = False
        variants.set_options(_classification_group_options(self._groups, self._rules))
        variants.clear()
        self.query_one(f'#{VIEW_GROUP_OCCURRENCES_ID}', Button).disabled = True

    def _refresh_kind_options(self) -> None:
        for widget_id, kind in self._kind_values.items():
            self.query_one(f'#{widget_id}', Button).label = kind_button_label(
                self._rules, kind, prompt='选择类别'
            )

    @staticmethod
    def _detail(detail: EntityAuditDetail, candidates: tuple[RuleCandidate, ...]) -> Text:
        text = Text()
        text.append(f'{detail.entity_name}\n', style='bold cyan')
        text.append(
            f'实体状态: {ENTITY_STATUS_LABELS[detail.status]}\n',
            style=ENTITY_STATUS_STYLES[detail.status],
        )
        if detail.reason:
            text.append(f'备注: {detail.reason}\n')
        if detail.kind_confirmation is not None:
            text.append(f'整实体类别: {detail.kind_confirmation.kind}\n', style='green')
        likely_irrelevant = [
            attribute.name
            for attribute in detail.attr_summaries
            if attribute.status is AttributeAuditStatus.LIKELY_NOT_AFFECT_KIND
        ]
        if likely_irrelevant:
            text.append(
                f'暂定无关属性: {", ".join(map(_audit_attribute_name, likely_irrelevant))}\n',
                style='dark_orange',
            )
            text.append('它们已用于粗审聚合；生成的规则候选会标为暂定。\n', style='dim')
        if candidates:
            text.append('规则候选（仍需人工确认）:\n', style='bold')
            for candidate in candidates:
                text.append(
                    f'  {_rule_candidate_text(candidate)}: {_attrs_text(candidate.when)} '
                    f'{_meta_text(candidate.meta)} '
                    f'({candidate.variant_count} 个已确认变体)\n',
                    style='green',
                )
        return text


async def review_entity_audit(
    report_path: Path | None = None,
    *,
    save_reader: SaveReader | None = None,
    game_dir: Path | None = None,
) -> None:
    """Optionally import a report, then open its persisted entity-level review UI."""
    store = EntityAuditStore()
    report_id = (
        store.import_report(report_path) if report_path is not None else store.latest_report_id()
    )
    if report_id is None:
        raise ValueError(
            'No collectible audit report is imported yet. Specify a JSON report path first.'
        )
    await EntityAuditApp(store, report_id, save_reader, game_dir=game_dir).run_async()


def _entity_status_options() -> tuple[tuple[str, str], ...]:
    return tuple((ENTITY_STATUS_LABELS[status], status.value) for status in EntityAuditStatus)


def _entity_stat_options() -> tuple[tuple[str, str], ...]:
    return (
        ('不统计', EntityStat.NONE.value),
        ('统计数量（整数）', EntityStat.COUNT.value),
        ('是否存在（布尔值）', EntityStat.EXIST.value),
        ('单选值（字符串）', EntityStat.SELECT.value),
    )


def _entity_status_order() -> tuple[EntityAuditStatus, ...]:
    """Keep work queues first and clearly excluded entities last."""
    return (
        EntityAuditStatus.UNKNOWN,
        EntityAuditStatus.ENTITY_CANDIDATE,
        EntityAuditStatus.IGNORED,
    )


def _audit_layer_diff(current: EntityRuleLayer, refreshed: EntityRuleLayer) -> str:
    """Return a compact unified diff for the generated audit-rule layer."""
    before = entity_rules_toml(current).splitlines()
    after = entity_rules_toml(refreshed).splitlines()
    return '\n'.join(
        difflib.unified_diff(
            before,
            after,
            fromfile='src/pist/data/entities.toml（当前）',
            tofile='src/pist/data/entities.toml（刷新后）',
            lineterm='',
        )
    )


def _audit_layer_diff_text(diff: str) -> Text:
    """Render a unified diff with immediately scannable addition/removal colors."""
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


def _attribute_status_options() -> tuple[tuple[str, str], ...]:
    return tuple((ATTRIBUTE_STATUS_LABELS[status], status.value) for status in AttributeAuditStatus)


def _attribute_options(
    attr_summaries: tuple[AttributeAuditSummary, ...],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            (
                f'{_audit_attribute_name(attribute.name)} · {_value_counts_text(attribute)} '
                f'[{ATTRIBUTE_STATUS_LABELS[attribute.status]}]'
            ),
            attribute.name,
        )
        for attribute in attr_summaries
    )


def _observation_question_options() -> tuple[tuple[str, str], ...]:
    return tuple(
        (OBSERVATION_QUESTION_LABELS[question], question.value) for question in ObservationQuestion
    )


def _observation_status_options() -> tuple[tuple[str, str], ...]:
    return tuple((OBSERVATION_STATUS_LABELS[status], status.value) for status in ObservationStatus)


def _classification_groups(detail: EntityAuditDetail) -> tuple[ClassificationGroup, ...]:
    """Merge only attributes already confirmed irrelevant to kind."""
    excluded = {
        AttributeAuditStatus.DOES_NOT_AFFECT_KIND,
        AttributeAuditStatus.LIKELY_NOT_AFFECT_KIND,
        AttributeAuditStatus.AFFECTS_BEHAVIOR,
    }
    names = tuple(
        attribute.name for attribute in detail.attr_summaries if attribute.status not in excluded
    )
    groups: dict[tuple[AttrValue | None, ...], list[EntityVariant]] = defaultdict(list)
    for variant in detail.variants:
        groups[
            tuple(
                variant.meta.get(name.removeprefix(META_ATTRIBUTE_PREFIX))
                if name.startswith(META_ATTRIBUTE_PREFIX)
                else variant.attrs.get(name)
                for name in names
            )
        ].append(variant)
    return tuple(
        ClassificationGroup(
            {
                name: value
                for name, value in zip(names, values, strict=True)
                if not name.startswith(META_ATTRIBUTE_PREFIX)
            },
            {
                name.removeprefix(META_ATTRIBUTE_PREFIX): value
                for name, value in zip(names, values, strict=True)
                if name.startswith(META_ATTRIBUTE_PREFIX)
            },
            tuple(variants),
        )
        for values, variants in sorted(
            groups.items(),
            key=lambda item: (
                -sum(variant.occurrence_count for variant in item[1]),
                repr(item[0]),
            ),
        )
    )


def _classification_group_options(
    groups: tuple[ClassificationGroup, ...], rules: EntityRules
) -> tuple[tuple[Text, str], ...]:
    return tuple(
        (
            _classification_group_label(
                group,
                rules,
            ),
            str(index),
        )
        for index, group in enumerate(groups)
    )


def _classification_group_label(group: ClassificationGroup, rules: EntityRules) -> Text:
    """Highlight terminal conclusions so incomplete groups remain obvious."""
    classification = group.classification
    if classification is None:
        label = Text(_classification_conditions_text(group.attrs, group.meta))
        label.append(f'（{group.occurrence_count}实体，{len(group.variants)}变体）', style='dim')
        label.append(' [未确认]', style='dark_orange')
        return label
    label = Text(_classification_conditions_text(group.attrs, group.meta))
    label.append(f'（{group.occurrence_count}实体，{len(group.variants)}变体）', style='dim')
    status, kind = classification
    if status is ObservationStatus.NOT_COLLECTIBLE:
        label.append(' [排除]', style='dim')
    elif kind is not None:
        definition = rules.kinds.get(kind)
        kind_label = kind if definition is None else definition.label
        label.append(f' [{kind_label}]', style='green')
    return label


def _classification_conditions_text(attrs: Mapping[str, object], meta: Mapping[str, object]) -> str:
    """Render entity and map conditions without implying separate per-field counts."""
    fields = [
        *(f'{name} = {_display_attr_value(value)}' for name, value in attrs.items()),
        *(f'meta.{name} = {_display_attr_value(value)}' for name, value in meta.items()),
    ]
    return ', '.join(fields) or '无分类属性'


def _evidence_value(input: Input) -> str | None:
    return input.value.strip() or None


def _default_value(value: str) -> DefaultValue:
    """Parse a JSON default, where blank is unknown and ``null`` is a confirmed default."""
    value = value.strip()
    if not value:
        return UNKNOWN
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError('默认值必须是 JSON 标量，例如 null、false、1 或 "text"。') from error
    if parsed is not None and type(parsed) not in {bool, int, float, str}:
        raise ValueError('默认值只能是 JSON null、布尔、数字或字符串。')
    return parsed


def _set_optional_evidence(input: Input, evidence: str | None) -> None:
    input.value = '' if evidence is None else evidence


def _value_counts_text(summary: AttributeAuditSummary) -> str:
    return ', '.join(
        [f'{_display_attr_value(value)}: {count}' for value, count in summary.value_counts]
    )


def _audit_attribute_name(name: str) -> str:
    """Hide the storage prefix while making map-level fields explicit in the UI."""
    if name.startswith(META_ATTRIBUTE_PREFIX):
        return f'meta.{name.removeprefix(META_ATTRIBUTE_PREFIX)}'
    return name


def _attrs_text(attrs: Mapping[str, object]) -> str:
    if not attrs:
        return '无分类属性'
    return ', '.join([f'{name} = {_display_attr_value(value)}' for name, value in attrs.items()])


def _display_attr_value(value: object) -> str:
    """Use one spelling for an attribute that was not written in a map."""
    return 'null' if value is None else repr(value)


def _meta_text(meta: Mapping[str, object]) -> str:
    """Format optional map-level conditions without calling an empty mapping an attribute."""
    return '' if not meta else f'地图元数据: {_attrs_text(meta)}'


def _rule_candidate_text(candidate: RuleCandidate) -> str:
    """Return the user-facing terminal outcome of one generated rule candidate."""
    result = '排除' if candidate.exclude else candidate.kind or ''
    notes = []
    if candidate.fallback:
        notes.append('默认值兜底')
    if candidate.provisional:
        notes.append('含暂定无关属性')
    return result if not notes else f'{result}（{"；".join(notes)}）'
