"""Mod progress protocol names, encoding and Celeste entry constraints."""

from collections.abc import Mapping
from copy import copy
from operator import attrgetter
from typing import TYPE_CHECKING, TypedDict

from lxml import etree

from berries.containers import KeyedDict
from berries.game.saves.conflicts import (
    CampaignConflictResolver,
    LevelConflictContext,
    LevelConflictResolver,
    resolve_campaign,
    validate_level_result,
)
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.partition import ProgressPartition
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress, campaign_name
from berries.game.saves.progress.members import CampaignMembers
from berries.game.saves.unknown import UnknownFieldPolicy

from . import schema
from . import tree as xml_tree

if TYPE_CHECKING:
    from berries.game.saves.mod_save_data import ModSaveData
    from berries.game.saves.progress.views import VanillaProgressView

FIELDS = ('LevelSets', 'LevelSetRecycleBin', 'LastArea_Safe', 'CurrentSession_Safe')


def validate_vanilla_entry(entry: CampaignProgress, *, path: str = 'LevelSetStats') -> None:
    """Require the native Celeste entry identity and empty progress lists."""
    if entry.name != 'Celeste':
        raise BadProgressSave('Vanilla entry must be named Celeste.')
    if entry.levels or entry.unresolved_levels:
        raise BadProgressSave(f'Normal Celeste entry requires empty Areas at {path}.Areas.')
    if entry.poem:
        raise BadProgressSave(f'Normal Celeste entry requires empty Poem at {path}.Poem.')


def encode_campaigns(
    progress: ModSaveData,
    *,
    vanilla: CampaignProgress | VanillaProgressView | None = None,
) -> dict[str, etree.Element]:
    """Build fresh Campaign partition elements from the owning data model."""
    fields: dict[str, etree.Element] = {}
    for name, collection, entry in (
        ('LevelSets', progress.campaigns, progress.vanilla_entry),
        ('LevelSetRecycleBin', progress.recycled_campaigns, None),
    ):
        if name == 'LevelSets' and collection.get('Celeste') is not None:
            raise ValueError('Celeste progress belongs to MainSave.vanilla, not Mod campaigns.')
        collection.validate_ownership()
        group = etree.Element(name)
        if entry is not None:
            validate_vanilla_entry(entry, path=f'{name}.Celeste')
            if vanilla is not None:
                entry = copy(entry)
                entry.unlocked_levels = vanilla.unlocked_levels
                entry.total_strawberries = vanilla.total_strawberries
            node = entry.to_xml()
            group.append(node)
        for campaign in collection.values():
            group.append(campaign.to_xml())
        fields[name] = group
    return fields


class CampaignFields(TypedDict):
    """Decoded constructor inputs, without temporary model views or indexes."""

    campaigns: CampaignMembers
    recycled_campaigns: CampaignMembers
    vanilla_entry: CampaignProgress | None


def decode_campaigns(
    root: etree.Element,
    *,
    strict: bool = True,
    on_campaign_conflict: CampaignConflictResolver | None = None,
    on_level_conflict: LevelConflictResolver | None = None,
    vanilla: CampaignProgress | None = None,
    unknown: UnknownFieldPolicy = 'error',
    path: str | None = None,
) -> CampaignFields:
    """Decode partitions and resolve membership before root indexes are built."""
    path = str(root.tag) if path is None else path
    vanilla_entry = None
    active: KeyedDict[str, CampaignProgress] = KeyedDict(key=attrgetter('key'))
    recycled: KeyedDict[str, CampaignProgress] = KeyedDict(key=attrgetter('key'))
    for name, collection in (('LevelSets', active), ('LevelSetRecycleBin', recycled)):
        partition = ProgressPartition.MODS if name == 'LevelSets' else ProgressPartition.RECYCLED
        group = xml_tree.child(root, name)
        if group is not None:
            for i, node in enumerate(
                schema.members(group, 'LevelSetStats', unknown=unknown, path=f'{path}.{name}')
            ):
                campaign_name = node.get('Name', '')
                member_path = f'{path}.{name}[{i}]'
                if campaign_name == 'Celeste' and partition is ProgressPartition.MODS:
                    entry = CampaignProgress.from_xml(
                        node,
                        strict=strict,
                        on_conflict=on_level_conflict,
                        partition=partition,
                        unknown=unknown,
                        path=member_path,
                    )
                    if strict:
                        validate_vanilla_entry(entry, path=member_path)
                    old = vanilla_entry
                    if old is not None:
                        entry = resolve_campaign(
                            old,
                            entry,
                            strict=strict,
                            on_conflict=on_campaign_conflict,
                            partition=partition,
                        )
                    vanilla_entry = entry
                    continue
                campaign = CampaignProgress.from_xml(
                    node,
                    strict=strict,
                    on_conflict=on_level_conflict,
                    partition=partition,
                    unknown=unknown,
                    path=member_path,
                )
                previous = collection.get(campaign_name)
                if previous is not None:
                    campaign = resolve_campaign(
                        previous,
                        campaign,
                        on_conflict=on_campaign_conflict,
                        strict=strict,
                        partition=partition,
                    )
                    collection.replace(campaign)
                else:
                    collection.add(campaign)
    _resolve_level_members(
        active if vanilla is None else {'Celeste': vanilla, **active},
        strict=strict,
        on_level_conflict=on_level_conflict,
        partition=ProgressPartition.MODS,
        vanilla=vanilla,
    )
    _resolve_level_members(
        recycled,
        strict=strict,
        on_level_conflict=on_level_conflict,
        partition=ProgressPartition.RECYCLED,
    )
    return {
        'campaigns': CampaignMembers._from_decoded(active.values()),  # noqa: SLF001
        'recycled_campaigns': CampaignMembers._from_decoded(  # noqa: SLF001
            recycled.values(), partition=ProgressPartition.RECYCLED
        ),
        'vanilla_entry': vanilla_entry,
    }


def _resolve_level_members(
    campaigns: Mapping[str, CampaignProgress],
    *,
    strict: bool,
    on_level_conflict: LevelConflictResolver | None,
    partition: ProgressPartition,
    vanilla: CampaignProgress | None = None,
) -> None:
    """Resolve loaded SIDs in a name-indexed partition, then install conflicts.

    Campaign names have already been resolved by the loader. Callbacks may
    select or merge progress values, but must not change collection membership
    during this scan. Unique, misplaced values remain available for lenient
    inspection; only conflicted SIDs are installed into their correct parent.
    """
    seen: dict[str, tuple[CampaignProgress, LevelProgress]] = {}
    conflicted_sids: set[str] = set()
    for campaign in campaigns.values():
        for level in campaign.levels.values():
            old = seen.get(level.sid)
            if old is None:
                seen[level.sid] = campaign, level
                continue
            owner, previous = old
            old_partition = ProgressPartition.VANILLA if owner is vanilla else partition
            new_partition = ProgressPartition.VANILLA if campaign is vanilla else partition
            if strict or on_level_conflict is None:
                raise BadProgressSave(
                    f'Duplicate level progress for {level.sid!r}: '
                    f'{old_partition} campaign {owner.name!r} and '
                    f'{new_partition} campaign {campaign.name!r}.'
                )
            chosen = on_level_conflict(
                LevelConflictContext(previous, old_partition, owner),
                LevelConflictContext(level, new_partition, campaign),
            )
            validate_level_result(previous, chosen)
            dest = campaigns.get(campaign_name(level.sid))
            if dest is None:
                raise BadProgressSave('A resolved Level must be stored in its own campaign.')
            seen[level.sid] = dest, chosen
            conflicted_sids.add(level.sid)
    # Installing into a later campaign while still reading it can overwrite an
    # unread candidate or process a merged result twice. Install after the scan.
    for campaign in campaigns.values():
        for sid in tuple(campaign.levels):
            if sid in conflicted_sids and seen[sid][0] is not campaign:
                del campaign.levels[sid]
    for sid, (owner, chosen) in seen.items():
        if sid in conflicted_sids:
            owner.levels.add(chosen)
