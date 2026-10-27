"""Textual UI for entity-level audit knowledge."""

import asyncio
import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, OptionList, Select, Static
from textual.widgets.option_list import Option, OptionDoesNotExist

from berries.entities.rules import (
    EntityRule,
    EntityRuleLayer,
    EntityRules,
)
from berries.game.content import ContentPath
from berries.map_preview import MapPreview
from pist.entities import audit as backend
from pist.entities.audit import kind_changes
from pist.entities.audit.inference import rule_candidates_for_detail
from pist.entities.audit.models import RuleGenerationResult
from pist.entities.config import EntityConfigStore
from pist.entities.maintenance import EntityRuleLibrary
from pist.save_progress import MapProgress, SaveProgressIndex
from pist.saves import SaveProgressReader
from pist.ui.entities.kinds import kind_button_label
from pist.ui.entities.occurrences import (
    OccurrenceScreen,
    load_occurrence_map,
    occurrence_preview_entities,
)
from pist.ui.tui import RefreshableCssApp

from .classification import ClassificationGroup, classification_groups
from .kind_dialogs import (
    AuditSelect,
    KindEdit,
    KindPickerScreen,
)
from .rules_refresh import AuditRulesRefreshScreen, IncompleteAuditReviewScreen, audit_layer_diff

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
ATTR_SELECT_ID = 'audit-attribute-select'
ATTR_STATUS_ID = 'audit-attribute-status'
ATTR_DEFAULT_ID = 'audit-attribute-default'
ATTR_EVIDENCE_ID = 'audit-attribute-evidence'
ATTR_NOTE_ID = 'audit-attribute-note'
SAVE_ATTR_ID = 'audit-save-attribute'
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

ENTITY_STATUS_LABELS: Mapping[backend.EntityAuditStatus, str] = MappingProxyType(
    {
        backend.EntityAuditStatus.UNKNOWN: '未审查',
        backend.EntityAuditStatus.ENTITY_CANDIDATE: '实体候选',
        backend.EntityAuditStatus.IGNORED: '已排除',
    }
)
ATTR_STATUS_LABELS: Mapping[backend.AttrAuditStatus, str] = MappingProxyType(
    {
        backend.AttrAuditStatus.UNKNOWN: '未审查',
        backend.AttrAuditStatus.AFFECTS_KIND: '影响分类',
        backend.AttrAuditStatus.DOES_NOT_AFFECT_KIND: '不影响分类',
        backend.AttrAuditStatus.LIKELY_NOT_AFFECT_KIND: '暂定不影响分类',
        backend.AttrAuditStatus.UNSURE_DEFAULT: '默认值待确认',
        backend.AttrAuditStatus.AFFECTS_BEHAVIOR: '影响行为',
    }
)
OBSERVATION_QUESTION_LABELS: Mapping[backend.ObservationQuestion, str] = MappingProxyType(
    {
        backend.ObservationQuestion.ENTITY_CLASSIFICATION: '实体分类',
        backend.ObservationQuestion.PAUSE_MENU_COUNT: '暂停菜单计数',
        backend.ObservationQuestion.DEBUG_MAP_COLOR: 'Debug Map 颜色',
        backend.ObservationQuestion.TOTAL_STRAWBERRY_COUNT: '草莓总数',
    }
)
OBSERVATION_STATUS_LABELS: Mapping[backend.ObservationStatus, str] = MappingProxyType(
    {
        backend.ObservationStatus.UNKNOWN: '未确认',
        backend.ObservationStatus.CONFIRMED: '已确认',
        backend.ObservationStatus.NOT_COLLECTIBLE: '排除',
        backend.ObservationStatus.CONFLICT: '有冲突',
    }
)
ENTITY_STATUS_STYLES: Mapping[backend.EntityAuditStatus, str] = MappingProxyType(
    {
        backend.EntityAuditStatus.UNKNOWN: 'yellow',
        backend.EntityAuditStatus.IGNORED: 'dim',
        backend.EntityAuditStatus.ENTITY_CANDIDATE: 'cyan',
    }
)
ATTR_STATUS_STYLES: Mapping[backend.AttrAuditStatus, str] = MappingProxyType(
    {
        backend.AttrAuditStatus.UNKNOWN: 'yellow',
        backend.AttrAuditStatus.AFFECTS_KIND: 'cyan',
        backend.AttrAuditStatus.DOES_NOT_AFFECT_KIND: 'green',
        backend.AttrAuditStatus.LIKELY_NOT_AFFECT_KIND: 'dark_orange',
        backend.AttrAuditStatus.UNSURE_DEFAULT: 'magenta',
        backend.AttrAuditStatus.AFFECTS_BEHAVIOR: 'dark_orange',
    }
)


def _entity_option_label(summary: backend.EntityAuditSummary) -> Text:
    """Render one virtualized entity-navigation row."""
    text = Text(summary.entity_name, style='bold')
    text.append(  # pyright: ignore[reportUnusedCallResult]
        f' [{ENTITY_STATUS_LABELS[summary.status]}]',
        style=ENTITY_STATUS_STYLES[summary.status],
    )
    text.append(f'\n{summary.occurrence_count} instances', style='dim')  # pyright: ignore[reportUnusedCallResult]
    if summary.variant_count is not None:
        text.append(f' · {summary.variant_count} variants', style='dim')  # pyright: ignore[reportUnusedCallResult]
    if summary.unreviewed_variant_count:
        text.append(  # pyright: ignore[reportUnusedCallResult]
            f' · {summary.unreviewed_variant_count} 个新变体待复核', style='dark_orange'
        )
    if summary.status is backend.EntityAuditStatus.ENTITY_CANDIDATE:
        if summary.rules_complete is False:
            text.append(' · 未完全覆盖', style='dark_orange')  # pyright: ignore[reportUnusedCallResult]
        elif summary.rules_complete is None:
            text.append(' · 覆盖检查中', style='dim')  # pyright: ignore[reportUnusedCallResult]
    return text


class EntityAuditApp(RefreshableCssApp[None]):
    """Review unknown entity IDs before proposing TOML collectible rules."""

    CSS_PATH = '../../styles/entities_audit.tcss'
    TITLE = 'Pist · 实体规则审计'
    BINDINGS: ClassVar = [
        ('escape', 'quit', '退出'),
        ('ctrl+s', 'save_entity', '保存'),
    ]

    def __init__(
        self,
        store: backend.EntityAuditStore,
        save_reader: SaveProgressReader | None = None,
        kind_store: EntityConfigStore | EntityRuleLibrary | None = None,
        game_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._kind_store = kind_store or EntityConfigStore()
        self._rule_library = (
            self._kind_store
            if isinstance(self._kind_store, EntityRuleLibrary)
            else EntityRuleLibrary(
                self._kind_store.shared_path, kinds_path=self._kind_store.kinds_path
            )
        )
        self._rules = self._kind_store.load()
        self._kind_values: dict[str, str | None] = {
            ENTITY_KIND_ID: None,
            OBSERVATION_KIND_ID: None,
        }
        self._save_progress = None if save_reader is None else SaveProgressIndex(save_reader)
        self._game_dir = game_dir
        self._summaries = store.entity_summaries()
        self._summary_sort_keys: dict[str, tuple[int, int, str]] = {}
        self._selected_entity: backend.EntityAuditDetail | None = None
        self._selected_attr: backend.AttrAuditSummary | None = None
        self._groups: tuple[ClassificationGroup, ...] = ()
        self._selected_group: ClassificationGroup | None = None
        self._candidates: tuple[backend.RuleCandidate, ...] = ()

    def compose(self) -> ComposeResult:
        yield Header()
        yield self._toolbar()
        yield Horizontal(
            OptionList(*self._entity_options(), id=ENTITY_LIST_ID),
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
            AuditSelect((), prompt='选择属性', id=ATTR_SELECT_ID, disabled=True),
            AuditSelect(_attr_status_options(), id=ATTR_STATUS_ID, disabled=True),
            Input(
                placeholder='确认的默认值（JSON，例如 null 或 false；留空表示未确认）',
                id=ATTR_DEFAULT_ID,
                disabled=True,
            ),
            Input(placeholder='证据来源（可选）', id=ATTR_EVIDENCE_ID, disabled=True),
            Input(placeholder='原因或备注', id=ATTR_NOTE_ID, disabled=True),
            Button('保存', id=SAVE_ATTR_ID, disabled=True, variant='primary'),
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
        self.query_one(f'#{ENTITY_FILTER_ID}', Input).focus()  # pyright: ignore[reportUnusedCallResult]
        self.run_worker(self._load_rule_coverage(), group='audit-rule-coverage', exclusive=True)  # pyright: ignore[reportUnusedCallResult]

    async def _load_rule_coverage(self) -> None:
        """Populate candidate coverage without blocking navigation or replacing newer edits."""
        try:
            result = await asyncio.to_thread(self._store.rule_generation_result)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self.notify(f'候选覆盖检查失败：{error}', severity='error')
            return
        self._apply_rule_coverage(result, initial=True)

    def _apply_rule_coverage(self, result: RuleGenerationResult, *, initial: bool = False) -> None:
        incomplete = frozenset(result.incomplete_entities)
        self._summaries = tuple(
            replace(summary, rules_complete=summary.entity_name not in incomplete)
            if summary.status is backend.EntityAuditStatus.ENTITY_CANDIDATE
            and (not initial or summary.rules_complete is None)
            else summary
            for summary in self._summaries
        )
        for summary in self._summaries:
            self._update_entity_option(summary)

    @on(Input.Changed, f'#{ENTITY_FILTER_ID}')
    def filter_entities(self, event: Input.Changed) -> None:
        """Filter entity navigation without changing raw data or knowledge."""
        self._apply_entity_filter(event.value)

    def _apply_entity_filter(self, query: str) -> None:
        """Rebuild the virtual navigation options for the current filter."""
        self._refresh_entity_options(query)

    @on(OptionList.OptionSelected, f'#{ENTITY_LIST_ID}')
    def select_entity(self, event: OptionList.OptionSelected) -> None:
        """Open the second-level entity and attribute review view."""
        if event.option_id is None:
            return
        self._begin_entity_load(event.option_id)

    def _begin_entity_load(self, entity_name: str) -> None:
        """Load one potentially large entity detail without blocking the TUI loop."""
        summary = next(summary for summary in self._summaries if summary.entity_name == entity_name)
        self._selected_entity = None
        self.query_one('#audit-detail-content', Static).update(self._loading_detail(summary))
        self._disable_entity_controls()
        self.query_one(f'#{ENTITY_STATUS_ID}', Select).value = summary.status.value
        self._disable_attribute_controls()
        self._disable_observation_controls()
        self.run_worker(  # pyright: ignore[reportUnusedCallResult]
            self._load_entity(entity_name),
            group='entity-audit-detail',
            exclusive=True,
        )

    async def _load_entity(self, entity_name: str) -> None:
        detail, variant_count, unreviewed_variant_count = await asyncio.to_thread(
            self._load_entity_data, entity_name
        )
        self._select_entity(entity_name, detail, variant_count, unreviewed_variant_count)

    def _load_entity_data(self, entity_name: str) -> tuple[backend.EntityAuditDetail, int, int]:
        """Read all deferred summary values in the worker thread, not the UI loop."""
        return (
            self._store.entity_detail(entity_name),
            self._store.raw_variant_count(entity_name),
            self._store.unreviewed_variant_count(entity_name),
        )

    def _select_entity(
        self,
        entity_name: str,
        detail: backend.EntityAuditDetail | None = None,
        variant_count: int | None = None,
        unreviewed_variant_count: int | None = None,
    ) -> None:
        """Load one selected entity into the detail controls."""
        self._selected_entity = detail or self._store.entity_detail(entity_name)
        self._refresh_selected_summary(
            self._selected_entity,
            variant_count=variant_count,
            unreviewed_variant_count=unreviewed_variant_count,
        )
        self._selected_attr = None
        self._selected_group = None
        self._refresh_detail()
        self._update_entity_controls(self._selected_entity)
        attr_select = self.query_one(f'#{ATTR_SELECT_ID}', Select)
        attr_select.disabled = False
        attr_select.set_options(_attr_options(self._selected_entity.attr_summaries))
        attr_select.clear()
        self._disable_attribute_controls()
        self._refresh_variants()
        self._disable_observation_controls()

    @on(Select.Changed, f'#{ATTR_SELECT_ID}')
    def select_attr(self, event: Select.Changed) -> None:
        """Enable scoped knowledge editing for a single entity attribute."""
        if self._selected_entity is None or not isinstance(event.value, str):
            return
        self._selected_attr = next(
            attribute
            for attribute in self._selected_entity.attr_summaries
            if attribute.name == event.value
        )
        self._update_attr_controls(self._selected_attr)

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
        self._update_observation_kind_control(backend.ObservationStatus(event.value))

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
            backend.EntityAuditStatus(status),
            reason=reason,
            evidence=evidence,
        )
        self._selected_entity = replace(
            previous, status=backend.EntityAuditStatus(status), reason=reason, evidence=evidence
        )
        self._render_detail()
        self._update_entity_controls(self._selected_entity)
        self._refresh_entity_item()
        self.notify('已保存实体审查。')

    @on(Button.Pressed, f'#{VIEW_OCCURRENCES_ID}')
    def view_occurrences(self) -> None:
        """Open map and room counts without adding another main-page scroll area."""
        if self._selected_entity is not None:
            entity_name = self._selected_entity.entity_name
            self.run_worker(  # pyright: ignore[reportUnusedCallResult]
                self._load_occurrences(entity_name, label=entity_name),
                group='entity-audit-occurrences',
                exclusive=True,
            )

    @on(Button.Pressed, f'#{VIEW_GROUP_OCCURRENCES_ID}')
    def view_group_occurrences(self) -> None:
        """Show maps for the selected aggregation, not every raw entity occurrence."""
        if self._selected_entity is None or self._selected_group is None:
            return
        entity_name = self._selected_entity.entity_name
        group = self._selected_group
        label = _attrs_text(group.attrs)
        if group.meta:
            label = f'{label} · {_meta_text(group.meta)}'
        self.run_worker(  # pyright: ignore[reportUnusedCallResult]
            self._load_occurrences(
                entity_name,
                group.variants,
                label=f'{entity_name}（{label}）',
            ),
            group='entity-audit-occurrences',
            exclusive=True,
        )

    async def _load_occurrences(
        self,
        entity_name: str,
        variants: tuple[backend.EntityVariant, ...] | None = None,
        *,
        label: str,
    ) -> None:
        maps = await asyncio.to_thread(self._store.occurrence_maps, entity_name, variants)
        preview = None
        if self._game_dir is not None:
            preview = lambda data: self._start_occurrence_preview(entity_name, data, variants)
        self.app.push_screen(  # pyright: ignore[reportUnusedCallResult]
            OccurrenceScreen(
                label,
                maps,
                self._map_progress,
                preview,
            )
        )

    def _start_occurrence_preview(
        self,
        entity_name: str,
        occurrences: backend.AuditMapOccurrences,
        variants: tuple[backend.EntityVariant, ...] | None,
    ) -> None:
        """Keep the audit TUI responsive while a read-only browser preview is open."""
        self.run_worker(  # pyright: ignore[reportUnusedCallResult]
            self._preview_occurrences(entity_name, occurrences, variants),
            name='entity-audit-map-preview',
            group='entity-audit-map-preview',
            exclusive=False,
        )

    async def _preview_occurrences(
        self,
        entity_name: str,
        occurrences: backend.AuditMapOccurrences,
        variants: tuple[backend.EntityVariant, ...] | None,
    ) -> None:
        assert self._game_dir is not None
        try:
            map_info, layout = await asyncio.to_thread(
                load_occurrence_map, self._game_dir, occurrences.source
            )
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        raw_occurrences = await asyncio.to_thread(
            self._store.map_occurrences,
            entity_name,
            occurrences.source,
        )
        if variants is not None:
            raw_occurrences = backend.occurrences_for_variants(raw_occurrences, variants)
        entities = occurrence_preview_entities(raw_occurrences)
        await MapPreview(
            map_info,
            layout,
            extra_entities=entities,
            title=occurrences.source.map_name,
        ).preview()

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
        self.push_screen(  # pyright: ignore[reportUnusedCallResult]
            KindPickerScreen(
                self._rules,
                save_kind=(
                    self._save_kind_from_picker
                    if isinstance(self._kind_store, EntityRuleLibrary)
                    else None
                ),
                delete_kind=(
                    self._delete_kind_from_picker
                    if isinstance(self._kind_store, EntityRuleLibrary)
                    else None
                ),
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

    @on(Button.Pressed, f'#{SAVE_ATTR_ID}')
    def save_attr(self) -> None:
        """Persist knowledge scoped to the selected entity and attribute only."""
        if self._selected_entity is None or self._selected_attr is None:
            return
        status = self.query_one(f'#{ATTR_STATUS_ID}', Select).value
        if not isinstance(status, str):
            self.notify('请选择属性审查状态。', severity='warning')
            return
        try:
            default_value = _default_value(self.query_one(f'#{ATTR_DEFAULT_ID}', Input).value)
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        self._store.save_attr_knowledge(
            self._selected_entity.entity_name,
            self._selected_attr.name,
            backend.AttrAuditStatus(status),
            reason=self.query_one(f'#{ATTR_NOTE_ID}', Input).value.strip(),
            evidence=_evidence_value(self.query_one(f'#{ATTR_EVIDENCE_ID}', Input)),
            default_value=default_value,
        )
        self._reload_selected_entity()
        self.notify('已保存属性审查。')

    def _save_kind_from_picker(
        self, result: KindEdit, previous_name: str | None
    ) -> EntityRules | None:
        """Persist a kind created or edited from a classification picker."""
        if not isinstance(self._kind_store, EntityRuleLibrary):
            self.notify('分类变更仅在共享维护模式开放。', severity='warning')
            return None
        try:
            rules = (
                self._rules.with_kind(result.name, result.definition)
                if previous_name is None
                else self._rules.with_renamed_kind(
                    previous_name,
                    result.name,
                    result.definition,
                )
            )
            if previous_name is not None and previous_name != result.name:
                kind_changes.change_kind(
                    self._rule_library, self._store, previous_name, result.name, rules
                )
                self._kind_values = {
                    control_id: result.name if value == previous_name else value
                    for control_id, value in self._kind_values.items()
                }
            else:
                self._kind_store.save(rules)
            self._rules = rules
        except (OSError, RuntimeError, ValueError) as error:
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
        self.notify(f'已{action}类别：{result.definition.label} ({result.name})。')
        return self._rules

    def _delete_kind_from_picker(self, kind: str) -> EntityRules | None:
        """Delete one kind after its picker confirms fallback to its parent."""
        if not isinstance(self._kind_store, EntityRuleLibrary):
            self.notify('分类变更仅在共享维护模式开放。', severity='warning')
            return None
        try:
            fallback = self._rules.kinds[kind].parent
            if fallback is None:
                raise ValueError(f'Cannot delete root entity kind: {kind!r}')
            rules = self._rules.with_deleted_kind(kind)
            kind_changes.change_kind(self._rule_library, self._store, kind, fallback, rules)
            self._kind_values = {
                control_id: fallback if value == kind else value
                for control_id, value in self._kind_values.items()
            }
            self._rules = rules
        except (OSError, RuntimeError, ValueError) as error:
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
                if backend.ObservationStatus(status) is backend.ObservationStatus.NOT_COLLECTIBLE
                else self._kind_values[OBSERVATION_KIND_ID]
            )
            self._store.save_group_observation(
                self._selected_entity.entity_name,
                self._selected_group.variants,
                backend.ObservationQuestion(question),
                backend.ObservationStatus(status),
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
        if self._save_progress is None:
            return MapProgress.UNRECORDED
        try:
            return self._save_progress.local_map_progress(ContentPath(map_file))
        except ValueError:
            return MapProgress.ENTERED

    def _entity_options(self, query: str = '') -> tuple[Option, ...]:
        """Build grouped virtual navigation rows in their stable sort order."""
        words = tuple(word.casefold() for word in query.split() if word)
        summaries_by_status: dict[backend.EntityAuditStatus, list[backend.EntityAuditSummary]] = (
            defaultdict(list)
        )
        for summary in self._summaries:
            text = f'{summary.entity_name} {summary.status.value}'.casefold()
            if all(word in text for word in words):
                summaries_by_status[summary.status].append(summary)
        options: list[Option] = []
        for status in _entity_status_order():
            summaries = summaries_by_status[status]
            if not summaries:
                continue
            summaries.sort(key=self._entity_sort_key)
            options.append(
                Option(
                    Text(f'{ENTITY_STATUS_LABELS[status]}（{len(summaries)}）', style='bold'),
                    disabled=True,
                )
            )
            options.extend(
                Option(_entity_option_label(summary), id=summary.entity_name)
                for summary in summaries
            )
        return tuple(options)

    def _refresh_entity_options(self, query: str, highlight: str | None = None) -> None:
        """Rebuild filtered or regrouped rows without resetting the viewport."""
        entities = self.query_one(f'#{ENTITY_LIST_ID}', OptionList)
        scroll = entities.scroll_offset
        if highlight is None and entities.highlighted is not None:
            highlight = entities.get_option_at_index(entities.highlighted).id
        entities.clear_options()  # pyright: ignore[reportUnusedCallResult]
        entities.add_options(self._entity_options(query))  # pyright: ignore[reportUnusedCallResult]
        if highlight is not None and any(option.id == highlight for option in entities.options):
            entities.highlighted = entities.get_option_index(highlight)
        self.call_after_refresh(  # pyright: ignore[reportUnusedCallResult]
            entities.scroll_to, x=scroll.x, y=scroll.y, animate=False, immediate=True, force=True
        )

    def _update_entity_option(self, summary: backend.EntityAuditSummary) -> None:
        """Update a visible row's details without changing selection or scroll."""
        entities = self.query_one(f'#{ENTITY_LIST_ID}', OptionList)
        try:
            option = entities.get_option(summary.entity_name)
        except OptionDoesNotExist:
            return
        prompt = _entity_option_label(summary)
        if option.prompt != prompt:
            entities.replace_option_prompt(summary.entity_name, prompt)  # pyright: ignore[reportUnusedCallResult]

    def _entity_sort_key(self, summary: backend.EntityAuditSummary) -> tuple[int, int, str]:
        """Return the immutable session-local sort key for one entity summary."""
        cached = self._summary_sort_keys.get(summary.entity_name)
        if cached is not None:
            return cached
        sort_key = (
            min(
                (self._map_progress(map_file) for map_file in summary.map_files),
                default=MapProgress.UNRECORDED,
            ),
            -summary.occurrence_count,
            summary.entity_name.casefold(),
        )
        self._summary_sort_keys[summary.entity_name] = sort_key
        return sort_key

    def _reload_selected_entity(self) -> None:
        assert self._selected_entity is not None
        self._selected_entity = self._store.entity_detail(self._selected_entity.entity_name)
        self._refresh_selected_summary(self._selected_entity)
        self._refresh_detail()
        self._update_entity_controls(self._selected_entity)
        attr_select = self.query_one(f'#{ATTR_SELECT_ID}', Select)
        attr_select.set_options(_attr_options(self._selected_entity.attr_summaries))
        if self._selected_attr is not None:
            self._selected_attr = next(
                attribute
                for attribute in self._selected_entity.attr_summaries
                if attribute.name == self._selected_attr.name
            )
            attr_select.value = self._selected_attr.name
            self._update_attr_controls(self._selected_attr)
        self._refresh_variants()
        self._disable_observation_controls()

    def _refresh_selected_summary(
        self,
        detail: backend.EntityAuditDetail,
        *,
        variant_count: int | None = None,
        unreviewed_variant_count: int | None = None,
    ) -> None:
        """Fill deferred navigation data after loading one selected entity."""
        name = detail.entity_name
        summary = next(summary for summary in self._summaries if summary.entity_name == name)
        updated = replace(
            summary,
            rules_complete=(
                bool(rule_candidates_for_detail(detail))
                if detail.status is backend.EntityAuditStatus.ENTITY_CANDIDATE
                else None
            ),
            variant_count=(
                self._store.raw_variant_count(name) if variant_count is None else variant_count
            ),
            unreviewed_variant_count=(
                self._store.unreviewed_variant_count(name)
                if unreviewed_variant_count is None
                else unreviewed_variant_count
            ),
        )
        if summary == updated:
            return
        self._summaries = tuple(
            updated if item.entity_name == name else item for item in self._summaries
        )
        self._update_entity_option(updated)

    def _refresh_entity_item(self) -> None:
        """Refresh the saved entity's virtual navigation option."""
        assert self._selected_entity is not None
        name = self._selected_entity.entity_name
        summary = next(summary for summary in self._summaries if summary.entity_name == name)
        updated = replace(
            summary,
            status=self._selected_entity.status,
            rules_complete=(
                bool(rule_candidates_for_detail(self._selected_entity))
                if self._selected_entity.status is backend.EntityAuditStatus.ENTITY_CANDIDATE
                else None
            ),
        )
        self._summaries = tuple(
            updated if item.entity_name == name else item for item in self._summaries
        )
        if summary.status is updated.status:
            self._update_entity_option(updated)
        else:
            query = self.query_one(f'#{ENTITY_FILTER_ID}', Input).value
            self._refresh_entity_options(query, highlight=name)

    def _refresh_detail(self) -> None:
        assert self._selected_entity is not None
        self._candidates = rule_candidates_for_detail(self._selected_entity)
        self._render_detail()

    def _render_detail(self) -> None:
        """Render a detail already loaded into memory without recomputing candidates."""
        assert self._selected_entity is not None
        self.query_one('#audit-detail-content', Static).update(
            self._detail(self._selected_entity, self._candidates)
        )

    @staticmethod
    def _loading_detail(summary: backend.EntityAuditSummary) -> Text:
        """Render summary data already available before loading the entity detail."""
        text = Text()
        text.append(f'{summary.entity_name}\n', style='bold cyan')  # pyright: ignore[reportUnusedCallResult]
        text.append(  # pyright: ignore[reportUnusedCallResult]
            f'实体状态: {ENTITY_STATUS_LABELS[summary.status]}\n',
            style=ENTITY_STATUS_STYLES[summary.status],
        )
        text.append(f'{summary.occurrence_count} 个实体\n', style='dim')  # pyright: ignore[reportUnusedCallResult]
        text.append('正在加载属性与变体…', style='dim')  # pyright: ignore[reportUnusedCallResult]
        return text

    @on(Button.Pressed, f'#{REFRESH_AUDIT_RULES_ID}')
    @work(group='audit-rule-generation', exclusive=True)
    async def refresh_audit_rules(self) -> None:
        """Preview then replace published rules from completed audit decisions."""
        try:
            result = await asyncio.to_thread(self._store.rule_generation_result)
            self._apply_rule_coverage(result)
            if result.incomplete_entities and not await self.push_screen_wait(
                IncompleteAuditReviewScreen(result.incomplete_entities)
            ):
                return
            layer = result.layer
            # Check cross-layer kind references before offering this layer for publication.
            _ = EntityRules(kinds=self._rules.kinds, entities=layer.entities)
            current = self._rule_library.load_layer()
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self.notify(str(error), severity='warning')
            return
        diff = audit_layer_diff(current, layer)
        if not diff:
            self.notify('实体规则没有变化。')
            return

        def save_if_confirmed(confirmed: bool | None) -> None:
            if confirmed:
                self._save_audit_rules(layer)  # pyright: ignore[reportUnusedCallResult]

        self.push_screen(AuditRulesRefreshScreen(diff), save_if_confirmed)  # pyright: ignore[reportUnusedCallResult]

    @work(group='audit-rule-save', exclusive=True)
    async def _save_audit_rules(self, layer: EntityRuleLayer) -> None:
        try:
            latest = await asyncio.to_thread(self._store.rule_generation_result)
            self._apply_rule_coverage(latest)
            if latest.layer != layer:
                self.notify('审计结论已变化，请重新刷新并确认规则差异。', severity='warning')
                return
            self._rule_library.save_layer(layer)
            self._rules = self._kind_store.load()
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self.notify(str(error), severity='warning')
            return
        if self._selected_entity is not None:
            self._refresh_detail()
        self.notify(f'已刷新实体规则：{len(layer.entities)} 个实体。')

    def _update_entity_controls(self, detail: backend.EntityAuditDetail) -> None:
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

    def _disable_entity_controls(self) -> None:
        for widget_id, widget_type in (
            (ENTITY_STATUS_ID, Select),
            (ENTITY_EVIDENCE_ID, Input),
            (ENTITY_NOTE_ID, Input),
            (SAVE_ENTITY_ID, Button),
            (ENTITY_KIND_ID, Button),
            (CONFIRM_ENTITY_KIND_ID, Button),
            (REVOKE_ENTITY_KIND_ID, Button),
            (VIEW_OCCURRENCES_ID, Button),
            (ATTR_SELECT_ID, Select),
        ):
            self.query_one(f'#{widget_id}', widget_type).disabled = True

    def _update_attr_controls(self, summary: backend.AttrAuditSummary) -> None:
        status = self.query_one(f'#{ATTR_STATUS_ID}', Select)
        status.disabled = False
        status.value = summary.status.value
        default = self.query_one(f'#{ATTR_DEFAULT_ID}', Input)
        default.disabled = False
        default.value = (
            json.dumps(summary.default_value)
            if summary.default_value is not backend.UNKNOWN
            else ''
        )
        evidence = self.query_one(f'#{ATTR_EVIDENCE_ID}', Input)
        evidence.disabled = False
        _set_optional_evidence(evidence, summary.evidence)
        note = self.query_one(f'#{ATTR_NOTE_ID}', Input)
        note.disabled = False
        note.value = summary.reason
        self.query_one(f'#{SAVE_ATTR_ID}', Button).disabled = False

    def _disable_attribute_controls(self) -> None:
        for widget_id, widget_type in (
            (ATTR_STATUS_ID, Select),
            (ATTR_DEFAULT_ID, Input),
            (ATTR_EVIDENCE_ID, Input),
            (ATTR_NOTE_ID, Input),
            (SAVE_ATTR_ID, Button),
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
        if group.classification is not None:
            status, kind_name = group.classification
            self.query_one(
                f'#{OBSERVATION_QUESTION_ID}', Select
            ).value = backend.ObservationQuestion.ENTITY_CLASSIFICATION.value
            self.query_one(f'#{OBSERVATION_STATUS_ID}', Select).value = status.value
            if kind_name in self._rules.kinds:
                self._kind_values[OBSERVATION_KIND_ID] = kind_name
                self.query_one(f'#{OBSERVATION_KIND_ID}', Button).label = kind_button_label(
                    self._rules, kind_name, prompt='选择 kind'
                )
        status = self.query_one(f'#{OBSERVATION_STATUS_ID}', Select).value
        if isinstance(status, str):
            self._update_observation_kind_control(backend.ObservationStatus(status))

    def _update_observation_kind_control(self, status: backend.ObservationStatus) -> None:
        kind = self.query_one(f'#{OBSERVATION_KIND_ID}', Button)
        if status is backend.ObservationStatus.NOT_COLLECTIBLE:
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
        self._groups = classification_groups(self._selected_entity)
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
    def _detail(
        detail: backend.EntityAuditDetail, candidates: tuple[backend.RuleCandidate, ...]
    ) -> Text:
        text = Text()
        text.append(f'{detail.entity_name}\n', style='bold cyan')  # pyright: ignore[reportUnusedCallResult]
        text.append(  # pyright: ignore[reportUnusedCallResult]
            f'实体状态: {ENTITY_STATUS_LABELS[detail.status]}\n',
            style=ENTITY_STATUS_STYLES[detail.status],
        )
        if detail.reason:
            text.append(f'备注: {detail.reason}\n')  # pyright: ignore[reportUnusedCallResult]
        if detail.kind_confirmation is not None:
            text.append(f'整实体类别: {detail.kind_confirmation.kind}\n', style='green')  # pyright: ignore[reportUnusedCallResult]
        likely_irrelevant = [
            attribute.name
            for attribute in detail.attr_summaries
            if attribute.status is backend.AttrAuditStatus.LIKELY_NOT_AFFECT_KIND
        ]
        if likely_irrelevant:
            text.append(  # pyright: ignore[reportUnusedCallResult]
                f'暂定无关属性: {", ".join(map(_audit_attribute_name, likely_irrelevant))}\n',
                style='dark_orange',
            )
            text.append('它们已用于粗审聚合；生成的规则候选会标为暂定。\n', style='dim')  # pyright: ignore[reportUnusedCallResult]
        if candidates:
            text.append('规则候选（仍需人工确认）:\n', style='bold')  # pyright: ignore[reportUnusedCallResult]
            for candidate in candidates:
                text.append(  # pyright: ignore[reportUnusedCallResult]
                    f'  {_rule_candidate_text(candidate)}: {_attrs_text(candidate.rule.when)} '
                    f'{_meta_text(candidate.rule.meta)}{_missing_text(candidate.rule)} '
                    f'({candidate.variant_count} 个已确认变体)\n',
                    style='green',
                )
        return text


async def review_entity_audit(
    report_path: Path | None = None,
    *,
    save_reader: SaveProgressReader | None = None,
    game_dir: Path | None = None,
) -> None:
    """Optionally import raw data, then review the complete persisted knowledge base."""
    store = backend.EntityAuditStore()
    if report_path is not None:
        await asyncio.to_thread(store.import_report, report_path)  # pyright: ignore[reportUnusedCallResult]
    await EntityAuditApp(store, save_reader, game_dir=game_dir).run_async()


def _entity_status_options() -> tuple[tuple[str, str], ...]:
    return tuple(
        (ENTITY_STATUS_LABELS[status], status.value) for status in backend.EntityAuditStatus
    )


def _entity_status_order() -> tuple[backend.EntityAuditStatus, ...]:
    """Keep work queues first and clearly excluded entities last."""
    return (
        backend.EntityAuditStatus.UNKNOWN,
        backend.EntityAuditStatus.ENTITY_CANDIDATE,
        backend.EntityAuditStatus.IGNORED,
    )


def _attr_status_options() -> tuple[tuple[str, str], ...]:
    return tuple((ATTR_STATUS_LABELS[status], status.value) for status in backend.AttrAuditStatus)


def _attr_options(
    attr_summaries: tuple[backend.AttrAuditSummary, ...],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            (
                f'{_audit_attribute_name(attribute.name)} · {_value_counts_text(attribute)} '
                f'[{ATTR_STATUS_LABELS[attribute.status]}]'
            ),
            attribute.name,
        )
        for attribute in attr_summaries
    )


def _observation_question_options() -> tuple[tuple[str, str], ...]:
    return tuple(
        (OBSERVATION_QUESTION_LABELS[question], question.value)
        for question in backend.ObservationQuestion
    )


def _observation_status_options() -> tuple[tuple[str, str], ...]:
    return tuple(
        (OBSERVATION_STATUS_LABELS[status], status.value) for status in backend.ObservationStatus
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
            str(i),
        )
        for i, group in enumerate(groups)
    )


def _classification_group_label(group: ClassificationGroup, rules: EntityRules) -> Text:
    """Highlight terminal conclusions so incomplete groups remain obvious."""
    classification = group.classification
    if classification is None:
        label = Text(_classification_conditions_text(group.attrs, group.meta))
        label.append(  # pyright: ignore[reportUnusedCallResult]
            f'（{group.occurrence_count}实体，{len(group.variants)}变体）', style='dim'
        )
        label.append(' [未确认]', style='dark_orange')  # pyright: ignore[reportUnusedCallResult]
        return label
    label = Text(_classification_conditions_text(group.attrs, group.meta))
    label.append(f'（{group.occurrence_count}实体，{len(group.variants)}变体）', style='dim')  # pyright: ignore[reportUnusedCallResult]
    status, kind = classification
    if status is backend.ObservationStatus.NOT_COLLECTIBLE:
        label.append(' [排除]', style='dim')  # pyright: ignore[reportUnusedCallResult]
    elif kind is not None:
        definition = rules.kinds.get(kind)
        kind_label = kind if definition is None else definition.label
        label.append(f' [{kind_label}]', style='green')  # pyright: ignore[reportUnusedCallResult]
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


def _default_value(value: str) -> backend.DefaultValue:
    """Parse a JSON default, where blank is unknown and ``null`` is a confirmed default."""
    value = value.strip()
    if not value:
        return backend.UNKNOWN
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError('默认值必须是 JSON 标量，例如 null、false、1 或 "text"。') from error
    if parsed is not None and type(parsed) not in {bool, int, float, str}:
        raise ValueError('默认值只能是 JSON null、布尔、数字或字符串。')
    return parsed


def _set_optional_evidence(input: Input, evidence: str | None) -> None:
    input.value = '' if evidence is None else evidence


def _value_counts_text(summary: backend.AttrAuditSummary) -> str:
    return ', '.join(
        [f'{_display_attr_value(value)}: {count}' for value, count in summary.value_counts]
    )


def _audit_attribute_name(name: str) -> str:
    """Hide the storage prefix while making map-level fields explicit in the UI."""
    if name.startswith(backend.META_ATTR_PREFIX):
        return f'meta.{name.removeprefix(backend.META_ATTR_PREFIX)}'
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


def _missing_text(rule: EntityRule) -> str:
    """Format presence conditions inferred from attributes absent in a raw variant."""
    conditions: list[str] = []
    if rule.missing:
        conditions.append(f'未写入属性: {", ".join(rule.missing)}')
    if rule.missing_meta:
        conditions.append(f'未写入地图元数据: {", ".join(rule.missing_meta)}')
    return '' if not conditions else f' {"；".join(conditions)}'


def _rule_candidate_text(candidate: backend.RuleCandidate) -> str:
    """Return the user-facing terminal outcome of one generated rule candidate."""
    result = '排除' if candidate.rule.kind is None else candidate.rule.kind
    notes = []
    if candidate.fallback:
        notes.append('默认值兜底')
    if candidate.provisional:
        notes.append('含暂定无关属性')
    return result if not notes else f'{result}（{"；".join(notes)}）'
