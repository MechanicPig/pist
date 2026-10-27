"""Application-owned presentation of campaign loading and reference issues."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

from berries.game import campaigns as backend
from berries.game import everest
from berries.game.collab import JournalRefs
from berries.game.map_hiders import MapHiderIssue

from . import map_names

_HIDER_FAILURE_LABELS = {'read': '读取失败', 'parse': '解析失败', 'validate': '结构无效'}


@dataclass(frozen=True, slots=True)
class CatalogLoadResult:
    """A public game catalog paired with Pist's loading diagnostics."""

    catalog: backend.CampaignCatalog
    diagnostics: tuple[str, ...]

    @classmethod
    def from_catalog(
        cls, catalog: backend.CampaignCatalog, *, diagnostics: Iterable[str] = ()
    ) -> Self:
        """Pair the unchanged catalog with formatted loading and scan/read messages."""
        return cls(
            catalog=catalog,
            diagnostics=(*diagnostics, *(issue_text(issue) for issue in catalog.issues)),
        )


def _dependency_list(issues: tuple[backend.DependencyIssue, ...]) -> str:
    """Describe missing or incompatible dependencies including loaded versions."""
    entries: list[str] = []
    for issue in issues:
        dependency = issue.dependency
        required = dependency.version or everest.DEFAULT_VERSION
        if issue.loaded is None:
            entries.append(f'{dependency.name}（未加载，需要 {required}）')
        else:
            installed = issue.loaded.version or everest.DEFAULT_VERSION
            entries.append(f'{dependency.name} {installed}（需要 {required}）')
    return '、'.join(entries)


def issue_text(issue: backend.CatalogIssue) -> str:
    """Render a game loading issue for Pist's Chinese warning display."""
    match issue:
        case backend.ModLoadingIssue():
            problems: list[str] = []
            if issue.required:
                problems.append(f'必需依赖：{_dependency_list(issue.required)}')
            if issue.optional:
                problems.append(f'可选依赖版本不兼容：{_dependency_list(issue.optional)}')
            reason = '；'.join(problems) or '依赖关系无法解析'
            return f'Mod 元数据 {issue.metadata.name}（{issue.mod.filename}）未加载：{reason}'
        case backend.ReservedCampaignIssue():
            sources = '、'.join(
                dict.fromkeys(
                    map_names.source_name(map_file) for map_file in issue.campaign.iter_maps()
                )
            )
            conflict = (
                f'；与官图 SID 冲突并会替换官图定义：{"、".join(issue.conflicting_sids)}'
                if issue.conflicting_sids
                else ''
            )
            return (
                'Mod 地图集 Maps/Celeste 使用 Everest 保留的 Celeste LevelSet'
                f'（来源：{sources}）；游戏会将其视为官图并使用官图存档{conflict}。'
            )
        case MapHiderIssue():
            failure = issue.failure
            stage = _HIDER_FAILURE_LABELS[failure.stage]
            detail = '' if failure.stage == 'validate' else f'（{failure.cause}）'
            return (
                f'[{issue.rule.id}] {issue.rule.hider} 地图隐藏设置{stage}：{failure.path}{detail}'
            )


def missing_ref_messages(refs: Iterable[str], *, source: str) -> tuple[str, ...]:
    """Describe references missing from the current campaign index."""
    return tuple(f'{source}的地图集不存在：{ref}' for ref in refs)


def journal_messages(refs: JournalRefs, missing: Iterable[str]) -> tuple[str, ...]:
    """Describe invalid JournalTriggers and references absent from the campaign index."""
    invalid = (
        (f'发现 {refs.invalid_count} 个没有有效 levelset 的日志入口。',)
        if refs.invalid_count
        else ()
    )
    return (*invalid, *missing_ref_messages(missing, source='日志引用'))
