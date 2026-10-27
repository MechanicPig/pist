"""Eager partition indexes coordinate every member write and retain live references."""

from copy import copy, deepcopy
from gc import collect
from weakref import ref

import pytest
from lxml import etree

from berries.game.levels import LevelSide
from berries.game.saves.conflicts import LevelConflictContext
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.progress import CampaignProgress, LevelProgress
from berries.game.saves.progress.members import CampaignMembers, LevelMembers
from test_support.save_docs import normal_save_xml, require_map


def _save() -> MainSave:
    save = MainSave()
    for group in (save.mods.campaigns, save.mods.recycled_campaigns):
        group.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    return save


def test_partition_indexes_and_mapping_views_reference_stored_values() -> None:
    save = _save()
    active, recycled = save.flat_levels, save.recycled_flat_levels
    assert active['Pack/Map'] is save.mods.campaigns['Pack'].levels['Pack/Map']
    assert recycled['Pack/Map'] is save.mods.recycled_campaigns['Pack'].levels['Pack/Map']
    assert active['Pack/Map'] is not recycled['Pack/Map']
    assert list(active)[-1] == 'Pack/Map'
    assert active.get('Missing', active['Pack/Map']) is active['Pack/Map']


@pytest.mark.parametrize('entry', ['campaign', 'flat'])
def test_both_level_write_paths_update_held_indexes(entry: str) -> None:
    save = _save()
    index = save.flat_levels
    keys, values, items = index.keys(), index.values(), index.items()
    members = save.mods.campaigns['Pack'].levels if entry == 'campaign' else index
    level = LevelProgress('Pack/New')
    members.add(level)
    assert level.sid in keys and level in values and (level.sid, level) in items
    assert save.mods.campaigns['Pack'].levels[level.sid] is index[level.sid] is level
    other = LevelProgress(level.sid)
    assert members.adddefault(other) is level
    members.replace(other)
    assert index[level.sid] is save.mods.campaigns['Pack'].levels[level.sid] is other
    assert members.pop(level.sid) is other
    assert level.sid not in keys and level.sid not in save.mods.campaigns['Pack'].levels
    assert members.pop('Missing', None) is None
    with pytest.raises(KeyError):
        members.replace(level)


def test_replacement_detachment_and_clear_keep_other_partition() -> None:
    save = _save()
    index, recycled = save.flat_levels, save.recycled_flat_levels
    old = save.mods.campaigns['Pack']
    replacement = CampaignProgress('Pack', [LevelProgress('Pack/New')])
    save.mods.campaigns.replace(replacement)
    old.levels.add(LevelProgress('Pack/Detached'))
    assert 'Pack/Detached' not in index and 'Pack/Map' not in index and 'Pack/New' in index
    assert save.mods.campaigns.popitem() == ('Pack', replacement)
    assert 'Pack/New' not in index and 'Pack/Map' in recycled
    replacement.levels.clear()
    assert list(recycled) == ['Pack/Map']


def test_whole_level_and_snapshot_replacements_keep_held_index_current() -> None:
    save = _save()
    index, recycled = save.flat_levels, save.recycled_flat_levels
    campaign = save.mods.campaigns['Pack']
    old = campaign.levels
    campaign.levels = [LevelProgress('Pack/New')]
    old.add(LevelProgress('Pack/Detached'))
    assert 'Pack/New' in index and 'Pack/Map' not in index and 'Pack/Detached' not in index
    assert 'Pack/Map' in old
    snapshot = ModSaveData()
    snapshot.campaigns.add(CampaignProgress('Other', [LevelProgress('Other/Map')]))
    save.install_mod_progress(snapshot)
    assert 'Other/Map' in index and 'Pack/New' not in index and not recycled
    snapshot.campaigns['Other'].levels.clear()
    assert 'Other/Map' not in index
    save.install_campaigns((save, CampaignProgress('Celeste', [LevelProgress('Celeste/New')])))
    assert 'Celeste/New' in index and 'Celeste/0-Intro' not in index


def test_shared_campaign_updates_all_roots_but_copy_does_not_copy_subscriptions() -> None:
    first, second = _save(), MainSave()
    campaign = first.mods.campaigns['Pack']
    second.mods.campaigns.add(campaign)
    campaign.levels.add(LevelProgress('Pack/Shared'))
    assert first.flat_levels['Pack/Shared'] is second.flat_levels['Pack/Shared']
    shallow = copy(campaign)
    shallow.levels = campaign.levels.copy()
    shallow.levels.clear()
    branch = deepcopy(campaign)
    branch.levels.clear()
    assert 'Pack/Shared' in first.flat_levels
    snapshot = deepcopy(first).mods
    snapshot.campaigns['Pack'].levels.clear()
    assert not snapshot.flat_levels and 'Pack/Shared' in first.flat_levels


@pytest.mark.parametrize('action', ['add', 'update', 'replace', 'assignment'])
def test_bad_new_members_fail_before_changing_any_index(action: str) -> None:
    save = _save()
    members = save.mods.campaigns['Pack'].levels
    before = tuple(members.items()), tuple(save.flat_levels.items())
    bad = LevelProgress('Wrong/Map')

    def mutate() -> None:
        if action == 'add':
            members.add(bad)
        elif action == 'update':
            members.update([LevelProgress('Pack/New'), bad])
        elif action == 'replace':
            members.replace(bad)
        else:
            save.mods.campaigns['Pack'].levels = [bad]

    with pytest.raises((BadProgressSave, KeyError)):
        mutate()
    assert before == (tuple(members.items()), tuple(save.flat_levels.items()))
    with pytest.raises(KeyError):
        save.flat_levels.update([LevelProgress('Pack/New'), bad])
    assert 'Pack/New' not in save.flat_levels


def test_load_detects_cross_campaign_conflict_without_querying() -> None:
    data = (
        '<SaveData><LevelSets>'
        '<LevelSetStats Name="Wrong"><Areas><AreaStats SID="Pack/Map"/></Areas></LevelSetStats>'
        '<LevelSetStats Name="Pack"><Areas><AreaStats SID="Pack/Map"/></Areas></LevelSetStats>'
        '</LevelSets></SaveData>'
    )
    with pytest.raises(BadProgressSave, match='Duplicate'):
        MainSave.fromstring(data, strict=False)


def test_load_callback_installs_result_and_queries_do_not_repeat_it() -> None:
    data = (
        '<SaveData><LevelSets><LevelSetStats Name="Pack"><Areas>'
        '<AreaStats SID="Pack/Map"/><AreaStats SID="Pack/Map"/>'
        '</Areas></LevelSetStats></LevelSets></SaveData>'
    )
    calls = []

    def choose(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        calls.append(old.progress.key)
        return new.progress

    save = MainSave.fromstring(data, strict=False, on_level_conflict=choose)
    assert calls == ['Pack/Map']
    level = save.flat_levels['Pack/Map']
    assert list(save.flat_levels.values()) == [level]
    assert save.mods.campaigns['Pack'].levels['Pack/Map'] is level
    assert calls == ['Pack/Map']


def test_missing_sides_remain_inspectable_without_flat_map_projection() -> None:
    save = MainSave.fromstring(
        '<SaveData><Areas><AreaStats SID="Celeste/Map"><Modes>'
        '<AreaModeStats Deaths="2"/></Modes></AreaStats></Areas></SaveData>',
        strict=False,
    )
    level = save.flat_levels['Celeste/Map']
    assert len(level.maps) == 1 and require_map(level.maps[0]).deaths == 2
    with pytest.raises(IndexError):
        level.maps[LevelSide.B]
    level.maps.extend([None, None, None])
    assert len(level.maps) == 4
    with pytest.raises(BadProgressSave):
        save.tostring()


def test_mod_campaign_container_cannot_be_replaced_or_include_celeste() -> None:
    save = _save()
    with pytest.raises(AttributeError):
        object.__setattr__(save.mods, 'campaigns', {})
    with pytest.raises(BadProgressSave):
        save.mods.campaigns.add(CampaignProgress('Celeste'))


def test_flat_batch_conflict_is_preflighted_before_installing_any_parent() -> None:
    save = MainSave.fromstring(
        '<SaveData><LevelSets><LevelSetStats Name="Wrong"><Areas>'
        '<AreaStats SID="Pack/Map"/></Areas></LevelSetStats>'
        '<LevelSetStats Name="Pack"/></LevelSets></SaveData>',
        strict=False,
    )
    original = tuple(save.flat_levels.items())
    other = MainSave()
    other.mods.campaigns.add(save.mods.campaigns['Pack'])
    with pytest.raises(BadProgressSave, match='Duplicate'):
        save.flat_levels.update([LevelProgress('Pack/New'), LevelProgress('Pack/Map')])
    assert tuple(save.flat_levels.items()) == original
    assert not save.mods.campaigns['Pack'].levels
    assert 'Pack/New' not in other.flat_levels and 'Pack/Map' not in other.flat_levels
    old = save.mods.campaigns['Wrong'].levels.pop('Pack/Map')
    save.flat_levels.add(old)
    assert save.mods.campaigns['Pack'].levels['Pack/Map'] is old
    assert other.flat_levels['Pack/Map'] is old


def test_cross_campaign_load_merge_is_installed_in_the_correct_parent() -> None:
    data = (
        '<SaveData><LevelSets><LevelSetStats Name="Wrong"><Areas>'
        '<AreaStats SID="Pack/Map"/></Areas></LevelSetStats>'
        '<LevelSetStats Name="Pack"><Areas><AreaStats SID="Pack/Map"/>'
        '</Areas></LevelSetStats></LevelSets></SaveData>'
    )

    def merge(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        result = deepcopy(new.progress)
        assert isinstance(old, LevelConflictContext) and old.campaign.name == 'Wrong'
        assert isinstance(new, LevelConflictContext) and new.campaign.name == 'Pack'
        assert old.partition == new.partition == 'mods'
        assert isinstance(result, LevelProgress)
        require_map(result.maps[0]).deaths = 9
        return result

    save = MainSave.fromstring(data, strict=False, on_level_conflict=merge)
    assert not save.mods.campaigns['Wrong'].levels
    result = save.mods.campaigns['Pack'].levels['Pack/Map']
    assert save.flat_levels['Pack/Map'] is result
    assert require_map(result.maps[0]).deaths == 9
    assert (
        MainSave.fromstring('<SaveData><Areas/></SaveData>', strict=False).flat_levels.get(
            'Pack/Map'
        )
        is None
    )


def test_load_merge_does_not_overwrite_a_later_unread_candidate() -> None:
    from berries.game.saves.files.mod_backup import ModBackupSave

    content = ''.join(
        f'<LevelSetStats Name="{name}"><Areas><AreaStats SID="Pack/Map"><Modes>'
        + f'<AreaModeStats Deaths="{deaths}"/>' * 3
        + '</Modes></AreaStats></Areas></LevelSetStats>'
        for name, deaths in [('Wrong1', 1), ('Wrong2', 2), ('Pack', 3)]
    )
    calls = []

    def merge(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        a, b = old.progress, new.progress
        assert isinstance(a, LevelProgress) and isinstance(b, LevelProgress)
        calls.append((require_map(a.maps[0]).deaths, require_map(b.maps[0]).deaths))
        result = deepcopy(new.progress)
        assert isinstance(result, LevelProgress)
        require_map(result.maps[0]).deaths += require_map(a.maps[0]).deaths
        return result

    save = ModBackupSave.fromstring(
        f'<ModSaveData><LevelSets>{content}</LevelSets></ModSaveData>',
        strict=False,
        on_level_conflict=merge,
    )
    assert calls == [(1, 2), (3, 3)]
    assert require_map(save.flat_levels['Pack/Map'].maps[0]).deaths == 6
    assert not save.mods.campaigns['Wrong1'].levels
    assert not save.mods.campaigns['Wrong2'].levels
    assert list(ModBackupSave.fromstring(save.tostring()).flat_levels) == ['Pack/Map']


def test_failed_campaign_or_root_replacement_leaves_existing_indexes_unchanged() -> None:
    save = MainSave.fromstring(
        '<SaveData><LevelSets><LevelSetStats Name="Wrong"><Areas>'
        '<AreaStats SID="Pack/Map"/></Areas></LevelSetStats>'
        '<LevelSetStats Name="Pack"/></LevelSets></SaveData>',
        strict=False,
    )
    before = tuple(save.flat_levels.items())
    previous = save.mods.campaigns['Pack']
    with pytest.raises(BadProgressSave, match='Duplicate'):
        save.mods.campaigns.replace(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    assert save.mods.campaigns['Pack'] is previous and tuple(save.flat_levels.items()) == before


def test_flat_clear_and_update_cover_multiple_campaigns_and_only_one_partition() -> None:
    save = _save()
    save.mods.campaigns.add(CampaignProgress('Other'))
    save.flat_levels.update([LevelProgress('Pack/New'), LevelProgress('Other/New')])
    assert save.mods.campaigns['Other'].levels['Other/New'] is save.flat_levels['Other/New']
    save.flat_levels.clear()
    assert not save.flat_levels and not save.vanilla.levels
    assert all(not campaign.levels for campaign in save.mods.campaigns.values())
    assert 'Pack/Map' in save.recycled_flat_levels


@pytest.mark.parametrize('root_type', [MainSave, ModBackupSave])
@pytest.mark.parametrize('group', ['LevelSets', 'LevelSetRecycleBin'])
def test_root_snapshot_replacement_rejects_misplacement_without_partial_install(
    root_type: type[MainSave | ModBackupSave], group: str
) -> None:
    save = root_type()
    old = save.mods
    old.campaigns.add(CampaignProgress('Old', [LevelProgress('Old/Map')]))
    old.recycled_campaigns.add(CampaignProgress('Old', [LevelProgress('Old/Map')]))
    active, recycled = save.flat_levels, save.recycled_flat_levels
    keys, recycled_keys = active.keys(), recycled.keys()
    before = tuple(active.items()), tuple(recycled.items()), save.tostring()
    # Also include a valid new active campaign: failure in the recycled partition
    # must not install even the otherwise valid active part of the replacement.
    other = 'LevelSetRecycleBin' if group == 'LevelSets' else 'LevelSets'
    incoming = ModSaveData.from_xml(
        etree.fromstring(
            f'<ModSaveData><{group}><LevelSetStats Name="Wrong"><Areas>'
            '<AreaStats SID="Pack/Map"/></Areas></LevelSetStats>'
            f'</{group}><{other}><LevelSetStats Name="New"><Areas>'
            '<AreaStats SID="New/Map"/></Areas></LevelSetStats>'
            f'</{other}></ModSaveData>'
        ),
        strict=False,
    )
    with pytest.raises(BadProgressSave, match='does not belong'):
        save.install_mod_progress(incoming)
    assert save.mods is old
    assert before == (tuple(active.items()), tuple(recycled.items()), save.tostring())
    assert 'New/Map' not in keys and 'New/Map' not in recycled_keys
    old.campaigns['Old'].levels.add(LevelProgress('Old/StillAttached'))
    assert 'Old/StillAttached' in keys and 'Old/StillAttached' not in recycled_keys
    incoming.campaigns.add(CampaignProgress('Detached', [LevelProgress('Detached/Map')]))
    assert 'Detached/Map' not in keys


def test_vanilla_replacement_rejects_misplacement_and_keeps_live_index() -> None:
    save = _save()
    old = save.vanilla
    index, keys = save.flat_levels, save.flat_levels.keys()
    before = tuple(index.items()), save.tostring()
    incoming = CampaignProgress.from_xml(
        etree.fromstring(
            '<LevelSetStats Name="Celeste"><Areas><AreaStats SID="Pack/Other"/></Areas></LevelSetStats>'
        ),
        strict=False,
    )
    with pytest.raises(BadProgressSave, match='does not belong'):
        save.install_campaigns((save, incoming))
    assert save.vanilla is old and before == (tuple(index.items()), save.tostring())
    old.levels.add(LevelProgress('Celeste/StillAttached'))
    assert 'Celeste/StillAttached' in keys and 'Pack/Other' not in keys


def test_main_load_resolves_complete_active_partition_including_vanilla_parent() -> None:
    sid = 'Celeste/1-ForsakenCity'
    wrong = ''.join(
        f'<LevelSetStats Name="Wrong{i}"><Areas><AreaStats SID="{sid}"><Modes>'
        + f'<AreaModeStats Deaths="{i + 1}"/>' * 3
        + '</Modes></AreaStats></Areas></LevelSetStats>'
        for i in range(2)
    )
    data = normal_save_xml(f'<SaveData><LevelSets>{wrong}</LevelSets></SaveData>')
    sources = []

    def choose(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        result = new.progress
        assert isinstance(new, LevelConflictContext)
        assert new.partition == 'mods'
        sources.append((old.partition, new.campaign.name))
        return result

    save = MainSave.fromstring(data, strict=False, on_level_conflict=choose)
    assert sources == [('vanilla', 'Wrong0'), ('vanilla', 'Wrong1')]
    result = save.vanilla.levels[sid]
    assert save.flat_levels[sid] is result
    assert require_map(result.maps[0]).deaths == 2
    assert all(not campaign.levels for campaign in save.mods.campaigns.values())
    assert not save.mods.flat_levels
    # The repair is installed into stored members and survives normal strict readback.
    reread = MainSave.fromstring(save.tostring())
    assert require_map(reread.flat_levels[sid].maps[0]).deaths == 2
    assert len(sources) == 2


def test_standalone_snapshot_cannot_invent_a_vanilla_parent_for_misplaced_duplicates() -> None:
    def choose(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        return new.progress

    content = ''.join(
        f'<LevelSetStats Name="Wrong{i}"><Areas>'
        '<AreaStats SID="Celeste/1-ForsakenCity"/></Areas></LevelSetStats>'
        for i in range(2)
    )
    with pytest.raises(BadProgressSave, match='own campaign'):
        ModBackupSave.fromstring(
            f'<ModSaveData><LevelSets>{content}</LevelSets></ModSaveData>',
            strict=False,
            on_level_conflict=choose,
        )


@pytest.mark.parametrize('first', ['vanilla', 'mods'])
def test_root_parts_can_be_repaired_in_either_order_with_retained_misplacements(first: str) -> None:
    save = MainSave.fromstring(
        '<SaveData><Areas><AreaStats SID="Alpha/Map"/></Areas>'
        '<LevelSets><LevelSetStats Name="Wrong"><Areas>'
        '<AreaStats SID="Beta/Map"/></Areas></LevelSetStats></LevelSets></SaveData>',
        strict=False,
    )
    index, keys = save.flat_levels, save.flat_levels.keys()
    vanilla, mods = save.vanilla, save.mods
    old_vanilla_levels = vanilla.levels
    old_mod_campaigns = mods.campaigns
    replacement = MainSave().vanilla
    snapshot = ModSaveData()
    if first == 'vanilla':
        save.install_campaigns((save, replacement))
        assert save.mods is mods and index['Beta/Map'] is mods.flat_levels['Beta/Map']
        assert 'Alpha/Map' not in keys
        save.install_mod_progress(snapshot)
    else:
        save.install_mod_progress(snapshot)
        assert save.vanilla is vanilla and index['Alpha/Map'] is vanilla.levels['Alpha/Map']
        assert 'Beta/Map' not in keys
        save.install_campaigns((save, replacement))
    assert save.vanilla is vanilla and save.mods is mods
    assert tuple(save.vanilla.levels.items()) == tuple(replacement.levels.items())
    assert save.mods.campaigns is snapshot.campaigns
    assert tuple(keys) == tuple(replacement.levels)
    assert all(index[sid] is level for sid, level in replacement.levels.items())
    # Old parts are detached, and a complete repair is safe to write/read strictly.
    old_vanilla_levels.add(LevelProgress('Celeste/Detached'))
    old_mod_campaigns['Wrong'].levels.add(LevelProgress('Wrong/Detached'))
    assert 'Celeste/Detached' not in keys and 'Wrong/Detached' not in keys
    assert tuple(MainSave.fromstring(save.tostring()).flat_levels) == tuple(keys)


@pytest.mark.parametrize('part', ['vanilla', 'mods'])
def test_root_part_replacement_still_checks_sid_conflicts_with_retained_input(part: str) -> None:
    save = MainSave.fromstring(
        '<SaveData><Areas><AreaStats SID="Alpha/Map"/></Areas>'
        '<LevelSets><LevelSetStats Name="Wrong"><Areas>'
        '<AreaStats SID="Celeste/1-ForsakenCity"/></Areas></LevelSetStats></LevelSets></SaveData>',
        strict=False,
    )
    index, recycled = save.flat_levels, save.recycled_flat_levels
    keys = index.keys()
    vanilla, mods = save.vanilla, save.mods
    before = tuple(index.items()), tuple(recycled.items())
    if part == 'vanilla':
        with pytest.raises(BadProgressSave, match='Duplicate'):
            save.install_campaigns((save, MainSave().vanilla))
    else:
        with pytest.raises(BadProgressSave, match='Duplicate'):
            save.install_mod_progress(
                ModSaveData(
                    campaigns=CampaignMembers(
                        [CampaignProgress('Alpha', [LevelProgress('Alpha/Map')])]
                    )
                )
            )
    assert save.vanilla is vanilla and save.mods is mods
    assert before == (tuple(index.items()), tuple(recycled.items()))
    vanilla.levels.add(LevelProgress('Celeste/StillAttached'))
    mods.campaigns['Wrong'].levels.add(LevelProgress('Wrong/StillAttached'))
    assert 'Celeste/StillAttached' in keys and 'Wrong/StillAttached' in keys


def test_detached_level_pop_and_popitem_do_not_change_original_membership() -> None:
    save = _save()
    campaign = save.mods.campaigns['Pack']
    branch = campaign.levels.copy()
    shared = branch['Pack/Map']
    added = LevelProgress('Pack/New')
    branch.add(added)
    assert branch.popitem() == ('Pack/New', added)
    assert branch.pop('Pack/Map') is shared
    assert branch.pop('Missing', None) is None
    assert not branch and save.flat_levels['Pack/Map'] is shared
    assert save.mods.campaigns['Pack'].levels['Pack/Map'] is shared
    with pytest.raises(KeyError):
        branch.popitem()
    with pytest.raises(KeyError):
        branch.pop('Pack/Map')


def test_detached_campaign_popitem_preserves_shared_campaign_references() -> None:
    save = _save()
    branch = save.mods.campaigns.copy()
    campaign = save.mods.campaigns['Pack']
    assert branch.popitem() == ('Pack', campaign)
    assert not branch and save.mods.campaigns['Pack'] is campaign
    # Removing the shared value from the copy does not detach the original root.
    campaign.levels.add(LevelProgress('Pack/StillAttached'))
    assert save.flat_levels['Pack/StillAttached'] is campaign.levels['Pack/StillAttached']
    assert branch.pop('Pack', None) is None
    with pytest.raises(KeyError):
        branch.popitem()


@pytest.mark.parametrize('entry', ['members', 'flat'])
def test_popitem_from_either_entry_updates_all_shared_indexes(entry: str) -> None:
    first, second = _save(), MainSave()
    campaign = first.mods.campaigns['Pack']
    second.mods.campaigns.add(campaign)
    first_keys, second_keys = first.flat_levels.keys(), second.flat_levels.keys()
    added = LevelProgress('Pack/New')
    campaign.levels.add(added)
    members = campaign.levels if entry == 'members' else second.mods.flat_levels
    assert members.popitem() == ('Pack/New', added)
    assert 'Pack/New' not in first_keys and 'Pack/New' not in second_keys
    assert 'Pack/New' not in campaign.levels
    assert first.mods.flat_levels['Pack/Map'] is second.mods.flat_levels['Pack/Map']
    assert 'Pack/Map' in first.recycled_flat_levels


def test_retained_members_do_not_keep_an_unused_index_alive() -> None:
    save = _save()
    members = save.mods.campaigns['Pack'].levels
    index_ref = ref(save.flat_levels)
    del save
    collect()
    assert index_ref() is None
    level = members['Pack/Map']
    assert members.popitem() == ('Pack/Map', level)
    members.add(level)
    assert members.pop('Pack/Map') is level


def test_rebinding_a_shared_member_container_only_changes_the_matching_campaign() -> None:
    first, second = _save(), MainSave()
    original = first.mods.campaigns['Pack']
    branch = copy(original)
    second.mods.campaigns.add(branch)
    old = original.levels
    branch.levels = [LevelProgress('Pack/Branch')]
    old.add(LevelProgress('Pack/Original'))
    assert 'Pack/Branch' not in first.flat_levels and 'Pack/Branch' in second.flat_levels
    assert 'Pack/Original' in first.flat_levels and 'Pack/Original' not in second.flat_levels
    assert 'Pack/Map' in first.flat_levels and 'Pack/Map' not in second.flat_levels
    branch.levels.add(LevelProgress('Pack/Branch2'))
    assert 'Pack/Branch2' not in first.flat_levels and 'Pack/Branch2' in second.flat_levels


@pytest.mark.parametrize('action', ['add', 'update'])
def test_campaign_write_preflights_all_shared_partitions_before_changing_members(
    action: str,
) -> None:
    first = _save()
    second = MainSave.fromstring(
        '<SaveData><Areas><AreaStats SID="Pack/Conflict"/></Areas></SaveData>',
        strict=False,
    )
    second.install_mod_progress(first.mods)
    original = first.mods.campaigns['Pack']
    before = tuple(first.flat_levels.items()), tuple(second.flat_levels.items())
    incoming = (
        CampaignProgress('Other', [LevelProgress('Other/New')]),
        CampaignProgress('Pack', [LevelProgress('Pack/Conflict')]),
    )
    if action == 'add':
        with pytest.raises(BadProgressSave, match='Duplicate'):
            first.mods.campaigns.add(incoming[1])
    else:
        with pytest.raises(BadProgressSave, match='Duplicate'):
            first.mods.campaigns.update(incoming)
    assert first.mods.campaigns['Pack'] is original
    assert 'Other' not in first.mods.campaigns
    assert before == (tuple(first.flat_levels.items()), tuple(second.flat_levels.items()))
    original.levels.add(LevelProgress('Pack/StillAttached'))
    assert 'Pack/StillAttached' in first.flat_levels and 'Pack/StillAttached' in second.flat_levels
    del second.vanilla.levels['Pack/Conflict']
    first.mods.campaigns.update(incoming)
    assert first.flat_levels['Other/New'] is second.flat_levels['Other/New']
    assert first.flat_levels['Pack/Conflict'] is second.flat_levels['Pack/Conflict']
    original.levels.add(LevelProgress('Pack/Detached'))
    assert 'Pack/Detached' not in first.flat_levels and 'Pack/Detached' not in second.flat_levels


def test_flat_index_insertion_order_is_independent_of_campaign_grouping() -> None:
    save = _save()
    save.mods.campaigns.add(CampaignProgress('Other', [LevelProgress('Other/Map')]))
    keys = save.mods.flat_levels.keys()
    save.flat_levels.add(LevelProgress('Pack/New'))
    assert list(keys) == ['Pack/Map', 'Other/Map', 'Pack/New']
    replacement = LevelProgress('Pack/Map')
    save.flat_levels.replace(replacement)
    assert list(keys) == ['Pack/Map', 'Other/Map', 'Pack/New']
    save.mods.campaigns.replace(CampaignProgress('Pack', [replacement, LevelProgress('Pack/Last')]))
    assert list(keys) == ['Pack/Map', 'Other/Map', 'Pack/Last']
    assert save.mods.flat_levels.popitem()[0] == 'Pack/Last'
    assert 'Pack/Last' not in save.mods.campaigns['Pack'].levels


def test_local_membership_edits_do_not_revisit_unrelated_level_identities() -> None:
    reads = 0

    class TrackedLevel(LevelProgress):
        @property
        def sid(self) -> str:
            nonlocal reads
            reads += 1
            return super().sid

    save = MainSave()
    campaign = CampaignProgress('Pack', (TrackedLevel(f'Pack/{i}') for i in range(100)))
    save.mods.campaigns.add(campaign)
    save.mods.campaigns.add(CampaignProgress('Other'))
    reads = 0
    campaign.levels.add(LevelProgress('Pack/New'))
    campaign.levels.replace(LevelProgress('Pack/New'))
    campaign.levels.pop('Pack/New')
    save.flat_levels.update([LevelProgress('Pack/Batch'), LevelProgress('Other/Batch')])
    save.mods.campaigns.replace(CampaignProgress('Other', [LevelProgress('Other/New')]))
    save.mods.campaigns['Other'].levels = [LevelProgress('Other/Rebound')]
    save.mods.campaigns.pop('Other')
    assert reads == 0
    assert len(campaign.levels) == 101
    assert save.flat_levels['Pack/Batch'] is campaign.levels['Pack/Batch']


@pytest.mark.parametrize('deep', [False, True])
def test_copy_of_lenient_level_members_preserves_input_without_attaching_indexes(
    deep: bool,
) -> None:
    save = MainSave.fromstring(
        '<SaveData><LevelSets><LevelSetStats Name="Wrong"><Areas>'
        '<AreaStats SID="Pack/Map"/></Areas></LevelSetStats></LevelSets></SaveData>',
        strict=False,
    )
    original = save.mods.campaigns['Wrong'].levels
    branch = deepcopy(original) if deep else copy(original)
    assert branch.name == original.name
    assert list(branch) == ['Pack/Map']
    assert (branch['Pack/Map'] is original['Pack/Map']) is not deep
    with pytest.raises(BadProgressSave, match='does not belong'):
        branch.validate_ownership()
    branch.clear()
    branch.add(LevelProgress('Wrong/New'))
    assert 'Pack/Map' in save.flat_levels and 'Wrong/New' not in save.flat_levels


@pytest.mark.parametrize('deep', [False, True])
def test_copy_of_lenient_campaign_members_retains_independent_membership(deep: bool) -> None:
    save = MainSave.fromstring(
        '<SaveData><LevelSets><LevelSetStats Name="Wrong"><Areas>'
        '<AreaStats SID="Pack/Map"/></Areas></LevelSetStats></LevelSets></SaveData>',
        strict=False,
    )
    original = save.mods.campaigns
    branch = deepcopy(original) if deep else copy(original)
    assert list(branch) == ['Wrong']
    assert (branch['Wrong'] is original['Wrong']) is not deep
    with pytest.raises(BadProgressSave, match='does not belong'):
        branch.validate_ownership()
    branch.clear()
    branch.add(CampaignProgress('Other', [LevelProgress('Other/New')]))
    assert list(original) == ['Wrong']
    assert 'Pack/Map' in save.flat_levels and 'Other/New' not in save.flat_levels


def test_members_constructors_copy_existing_members_but_validate_new_input() -> None:
    save = MainSave.fromstring(
        '<SaveData><LevelSets><LevelSetStats Name="Wrong"><Areas>'
        '<AreaStats SID="Pack/Map"/></Areas></LevelSetStats></LevelSets></SaveData>',
        strict=False,
    )
    levels = save.mods.campaigns['Wrong'].levels
    branch = LevelMembers('Wrong', levels)
    assert branch['Pack/Map'] is levels['Pack/Map']
    with pytest.raises(BadProgressSave, match='does not belong'):
        LevelMembers('Wrong', levels.values())
    with pytest.raises(BadProgressSave, match='does not belong'):
        LevelMembers('Other', levels)
    repaired = LevelMembers('Pack', levels)
    assert repaired['Pack/Map'] is levels['Pack/Map']
    branch.clear()
    assert 'Pack/Map' in save.flat_levels

    campaigns = CampaignMembers(save.mods.campaigns)
    assert campaigns['Wrong'] is save.mods.campaigns['Wrong']
    with pytest.raises(BadProgressSave, match='does not belong'):
        CampaignMembers(save.mods.campaigns.values())
    campaigns.clear()
    assert 'Wrong' in save.mods.campaigns and 'Pack/Map' in save.flat_levels


@pytest.mark.parametrize(
    ('entry', 'detached'),
    [
        ('levels', False),
        ('levels', True),
        ('campaigns', False),
        ('campaigns', True),
        ('flat', False),
    ],
)
@pytest.mark.parametrize('default', [None, False, 0, '', object()])
def test_pop_default_matches_mapping_conventions_and_preserves_index_coordination(
    entry: str, detached: bool, default: object
) -> None:
    save = _save()
    if entry == 'campaigns':
        campaigns = save.mods.campaigns
        members = campaigns.copy() if detached else campaigns
        key = 'Pack'
    elif entry == 'levels':
        levels = save.mods.campaigns['Pack'].levels
        members = levels.copy() if detached else levels
        key = 'Pack/Map'
    else:
        members = save.flat_levels
        key = 'Pack/Map'
    expected = members[key]
    assert members.pop('Missing', default) is default
    assert members.pop('Missing', default=default) is default
    with pytest.raises(KeyError):
        members.pop('Missing')
    assert members[key] is expected
    assert members.pop(key, default=default) is expected
    assert key not in members
    assert ('Pack/Map' in save.flat_levels) is detached
    assert 'Pack/Map' in save.recycled_flat_levels
