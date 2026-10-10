"""Coordinate category reference changes across rule files and audit knowledge."""

import sqlite3

from berries.entities.rules import EntityConfigStore, EntityRules

from .store import EntityAuditStore


def change_kind(
    config: EntityConfigStore,
    store: EntityAuditStore,
    old_name: str,
    new_name: str,
    rules: EntityRules,
) -> None:
    """Commit audit references after rule files, restoring rules if the database fails."""
    try:
        with config.kind_change(old_name, new_name, rules):
            store.rename_kind(old_name, new_name)
    except (sqlite3.Error, OSError) as error:
        raise RuntimeError(f'类别变更失败，规则文件已恢复：{error}') from error
