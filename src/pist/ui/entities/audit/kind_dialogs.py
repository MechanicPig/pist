"""Category editing dialogs and their specialized selector control."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Literal

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static
from textual.widgets._select import SelectCurrent, SelectOverlay

from pist.entities.rules import EntityRules, EntityStat, EntityTableField

from ..kinds import KindTree, add_kind_nodes, kind_button_label

NEW_KIND_NAME_ID = 'audit-new-kind-name'
NEW_KIND_LABEL_ID = 'audit-new-kind-label'
NEW_KIND_SPRITE_ID = 'audit-new-kind-sprite'
NEW_KIND_PARENT_ID = 'audit-new-kind-parent'
NEW_KIND_STAT_ID = 'audit-new-kind-stat'
NEW_KIND_TABLE_ID = 'audit-new-kind-table'
NEW_KIND_FIELD_ID = 'audit-new-kind-field'
NEW_KIND_SELECT_VALUE_ID = 'audit-new-kind-select-value'
SAVE_NEW_KIND_ID = 'audit-save-new-kind'
KIND_TREE_ID = 'audit-kind-tree'


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


def _entity_stat_options() -> tuple[tuple[str, str], ...]:
    return (
        ('不统计', EntityStat.NONE.value),
        ('统计数量（整数）', EntityStat.COUNT.value),
        ('是否存在（布尔值）', EntityStat.EXIST.value),
        ('单选值（字符串）', EntityStat.SELECT.value),
    )


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
