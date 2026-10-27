"""Coordinate category reference changes across rule files and audit knowledge."""

import sqlite3

from berries.entities.rules import EntityRules
from pist.entities.maintenance import EntityRuleLibrary

from .store import EntityAuditStore


def change_kind(
    config: EntityRuleLibrary,
    store: EntityAuditStore,
    old_name: str,
    new_name: str,
    rules: EntityRules,
) -> None:
    """Write rule files, then update audit references in the store's own transaction."""
    try:
        config.rename_kind(old_name, new_name, rules)
        store.rename_kind(old_name, new_name)
    except (sqlite3.Error, OSError) as error:
        raise RuntimeError(
            f'类别变更失败：{error}。规则文件可能已部分或全部写入，未自动恢复；'
            '请检查 Git diff 与审计数据库中的分类引用。'
        ) from error
