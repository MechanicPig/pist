"""Save-domain identity, copying and live views without touching save files."""

from copy import copy, deepcopy
from operator import attrgetter

import pytest
from lxml import etree
from pydantic import BaseModel, ValidationError

from berries.containers import KeyedDict
from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.partition import ProgressPartition
from berries.game.saves.progress import (
    CampaignProgress,
    LevelProgress,
    MapProgress,
    PersonalBests,
    Session,
)
from berries.game.saves.progress.level import campaign_name
from berries.game.saves.progress.members import CampaignMembers
from berries.game.saves.rooms import RoomStats
from berries.map_entity_id import MapEntityID
from test_support.save_docs import parsed_session, require_map


@pytest.mark.parametrize(
    ('sid', 'expected'),
    [
        ('Pack/Nested/Map', 'Pack/Nested'),
        ('Standalone', ''),
        (r'Pack\Map', ''),
        ('Pack//Map', 'Pack/'),
        ('pack/Map', 'pack'),
    ],
)
def test_campaign_name_preserves_exact_sid_prefix(sid: str, expected: str) -> None:
    assert campaign_name(sid) == expected


def test_campaign_name_rejects_empty_sid() -> None:
    with pytest.raises(ValueError, match='SID must not be empty'):
        campaign_name('')


def _level(sid: str = 'Pack/Map') -> LevelProgress:
    return LevelProgress(
        sid,
        [
            MapProgress(deaths=3),
            MapProgress(deaths=5),
            MapProgress(),
        ],
        cassette=True,
    )


def test_collections_keep_references_and_replace_without_changing_order() -> None:
    first = _level('Pack/First')
    second = _level('Pack/Second')
    campaign = CampaignProgress('Pack', [first, second])
    assert campaign.levels[first.sid] is first
    assert campaign.levels.get(second.sid) is second
    assert first.sid in campaign.levels
    assert list(campaign.levels.keys()) == [first.sid, second.sid]
    assert list(campaign.levels.values()) == [first, second]
    replacement = _level(first.sid)
    campaign.levels.replace(replacement)
    assert list(campaign.levels.values()) == [replacement, second]
    del campaign.levels[first.sid]
    assert first.cassette  # Removing membership does not reset the borrowed object.
    assert list(campaign.levels.values()) == [second]


def test_non_collection_progress_uses_standard_copy_without_a_method() -> None:
    for value in (
        CampaignProgress('Pack'),
        LevelProgress('Pack/Map'),
        MapProgress(),
        PersonalBests(),
        ModSaveData(),
        parsed_session('Pack/Map', LevelSide.A),
        RoomStats(),
    ):
        if not isinstance(value, BaseModel):
            assert not hasattr(value, 'copy')
        copied = copy(value)
        assert type(copied) is type(value) and copied is not value


def test_level_exposes_a_plain_map_list_and_checks_shape_on_write() -> None:
    a = MapProgress(deaths=1)
    b = MapProgress(deaths=2)
    c = MapProgress(deaths=3)
    assert not hasattr(a, 'side')
    level = LevelProgress('Pack/Map', [a, b, c])
    assert type(level.maps) is list
    assert list(level.maps) == [a, b, c]
    assert len(level.maps) == 3 and c in level.maps
    assert MapProgress(deaths=1) in level.maps
    assert MapProgress() not in level.maps
    assert level.maps[0] is a and level.maps[-1] is c and level.maps[1:] == [b, c]
    assert level.maps[LevelSide.A] is level.maps[0]
    assert level.maps[LevelSide.B : LevelSide.C] == [b]
    assert level.maps[::-1] == [c, b, a]
    with pytest.raises(IndexError):
        level.maps[3]
    with pytest.raises(IndexError):
        level.maps[-4]
    assert level.maps[LevelSide.B] is b
    assert not hasattr(level, 'get')
    assert not hasattr(level, 'add') and not hasattr(level, 'remove')
    for maps in ([a], [a, b], [a, b, c, c]):
        malformed = LevelProgress('Pack/Map', maps)
        assert list(malformed.maps) == maps
        with pytest.raises(ValueError):
            malformed.tostring()
    for name in ('__getitem__', '__iter__', '__len__', '__setitem__', 'truncate', 'copy'):
        assert not hasattr(level, name)
    assert len(LevelProgress('Pack/New').maps) == 3
    assert list(LevelProgress('Pack/Reordered', [c, a, b]).maps) == [c, a, b]
    assert list(LevelProgress('Pack/Shared', [a, a, a]).maps) == [a, a, a]
    level.maps[LevelSide.B] = c
    assert level.maps[1] is c
    level.maps[-1] = None
    assert level.maps[2] is None
    with pytest.raises(IndexError):
        level.maps[3] = a
    level.maps.append(a)
    with pytest.raises(ValueError, match='three'):
        level.tostring()
    level.maps[:] = [a, b, c]
    assert level.tostring()


@pytest.mark.parametrize(
    ('name', 'sid'),
    [
        ('', 'Map'),
        ('Author/Pack', 'Author/Pack/Map'),
        ('Celeste', 'Celeste/1-ForsakenCity'),
        ('Pack/..', 'Pack/../Map'),
        ('Pack', 'Pack/Map.bin'),
    ],
)
def test_campaign_membership_uses_exact_sid_prefix(name: str, sid: str) -> None:
    level = _level(sid)
    campaign = CampaignProgress(name, [level])
    assert campaign.levels[sid] is level


@pytest.mark.parametrize('sid', ['pack/Map', 'Pack/Nested/Map', r'Pack\Map'])
@pytest.mark.parametrize('operation', ['construct', 'add', 'adddefault', 'replace', 'update'])
def test_campaign_membership_is_checked_on_write(sid: str, operation: str) -> None:
    wrong = _level(sid)
    campaign = CampaignProgress('Pack')

    def mutate() -> None:
        match operation:
            case 'construct':
                CampaignProgress('Pack', [wrong])
            case 'add':
                campaign.levels.add(wrong)
            case 'adddefault':
                campaign.levels.adddefault(wrong)
            case 'replace':
                campaign.levels.replace(wrong)
            case 'update':
                campaign.levels.update([wrong])

    with pytest.raises((BadProgressSave, KeyError)):
        mutate()
    assert not campaign.levels
    campaign.levels.add(_level())
    restored = CampaignProgress.from_xml(etree.fromstring(campaign.tostring()), strict=True)
    assert list(restored.levels) == ['Pack/Map']


@pytest.mark.parametrize('collection', ['campaigns', 'campaign'])
def test_duplicate_add_overwrites_and_missing_replace_delete_leave_membership(
    collection: str,
) -> None:
    if collection == 'campaigns':
        original = CampaignProgress('Pack')
        values = KeyedDict([original], key=attrgetter('name'))
        item = CampaignProgress('Pack')
        missing = CampaignProgress('Other')
        values.add(item)
        assert values['Pack'] is item
        with pytest.raises(KeyError):
            values.replace(missing)
        with pytest.raises(KeyError):
            del values[missing.name]
        assert len(values) == 1
    elif collection == 'campaign':
        campaign = CampaignProgress('Pack', [_level()])
        item = _level()
        campaign.levels.add(item)
        assert campaign.levels['Pack/Map'] is item
        with pytest.raises(KeyError):
            campaign.levels.replace(_level('Pack/Missing'))
        with pytest.raises(KeyError):
            del campaign.levels['Pack/Missing']
        assert len(campaign.levels) == 1


def test_level_map_adddefault_preserves_existing_and_adds_new_members() -> None:
    level = _level()
    campaign = CampaignProgress('Pack', [level])
    assert campaign.levels.adddefault(_level()) is level
    new = _level('Pack/Other')
    assert campaign.levels.adddefault(new) is new
    assert list(campaign.levels) == ['Pack/Map', 'Pack/Other']


def test_reset_keeps_structure_but_pure_collection_clear_removes_members() -> None:
    progress = MapProgress(
        time_played=Duration.from_seconds(17),
        deaths=10,
        completed=True,
        single_run_completed=True,
        heart=True,
        strawberries=[MapEntityID('room', 1)],
        checkpoints=['checkpoint'],
        bests=PersonalBests(Duration.from_seconds(12), Duration.from_seconds(15), 0, 4),
    )
    level = LevelProgress('Pack/Map', cassette=True)
    level.maps[LevelSide.A] = progress
    campaign = CampaignProgress(
        'Pack', [level], unlocked_levels=2, total_strawberries=1, poem=['heart']
    )
    campaign.reset()
    assert next(iter(campaign.levels.values())) is level and level.maps[LevelSide.A] is progress
    assert not level.cassette
    assert campaign.unlocked_levels == campaign.total_strawberries == 0 and not campaign.poem
    assert progress.time_played == Duration() and progress.deaths == 0
    assert not progress.completed and not progress.single_run_completed and not progress.heart
    assert not progress.strawberries and not progress.checkpoints
    assert progress.bests == PersonalBests()
    campaigns = KeyedDict([campaign], key=attrgetter('name'))
    campaigns.clear()
    assert not campaigns and next(iter(campaign.levels.values())) is level


def test_deepcopy_isolates_progress_and_preserves_internal_aliases() -> None:
    level = _level()
    campaign = CampaignProgress('Pack', [level])
    mods = ModSaveData(
        campaigns=CampaignMembers([campaign]),
        recycled_campaigns=CampaignMembers(
            [campaign],
            partition=ProgressPartition.RECYCLED,
        ),
    )
    mods.safe_session = parsed_session(level.sid, LevelSide.A, deaths=5)
    copied = deepcopy(mods)
    assert copied.campaigns['Pack'] is copied.recycled_campaigns['Pack']
    assert copied.campaigns['Pack'] is not campaign
    require_map(next(iter(copied.campaigns['Pack'].levels.values())).maps[LevelSide.A]).deaths = 100
    assert require_map(level.maps[LevelSide.A]).deaths == 3
    assert copied.safe_session is not None and copied.safe_session is not mods.safe_session
    copied.safe_session.deaths = 8
    assert mods.safe_session.deaths == 5
    standalone = deepcopy(level)
    require_map(standalone.maps[LevelSide.A]).checkpoints.add('new')
    assert not require_map(level.maps[LevelSide.A]).checkpoints


def test_campaign_shallow_copy_shares_level_collection_and_poem() -> None:
    level = _level()
    original = CampaignProgress(
        'Pack', [level], unlocked_levels=2, total_strawberries=1, poem=['heart']
    )
    branch = copy(original)
    assert branch is not original and next(iter(branch.levels.values())) is level
    assert branch.levels is original.levels
    assert branch.poem is original.poem
    branch.poem.append('other')
    branch.unlocked_levels = 3
    branch.total_strawberries = 2
    require_map(next(iter(branch.levels.values())).maps[LevelSide.A]).deaths = 9
    assert original.poem == ['heart', 'other']
    assert (original.unlocked_levels, original.total_strawberries) == (2, 1)
    assert require_map(next(iter(original.levels.values())).maps[LevelSide.A]).deaths == 9
    del branch.levels[level.sid]
    branch.levels.add(LevelProgress('Pack/Other'))
    assert [item.sid for item in original.levels.values()] == ['Pack/Other']
    assert [item.sid for item in branch.levels.values()] == ['Pack/Other']


@pytest.mark.parametrize('use_method', (False, True))
def test_level_collection_copy_owns_membership_but_shares_levels(use_method: bool) -> None:
    level = _level()
    campaign = CampaignProgress('Pack', [level], total_strawberries=1, poem=['heart'])
    original = campaign.levels
    copied = original.copy() if use_method else copy(original)
    assert copied is not original and copied[level.sid] is level
    del copied[level.sid]
    copied.add(LevelProgress('Pack/Other'))
    assert list(original.values()) == [level]
    assert list(copied) == ['Pack/Other']
    copied.add(LevelProgress('Pack/Another'))
    assert list(copied) == ['Pack/Other', 'Pack/Another']
    copied.clear()
    assert not copied and list(original.values()) == [level]
    assert campaign.total_strawberries == 1 and campaign.poem == ['heart']


def test_level_shallow_copy_shares_map_list_without_custom_copy_semantics() -> None:
    original = _level()
    branch = copy(original)
    assert branch is not original and branch.maps is original.maps
    branch.cassette = False
    del branch.maps[-1]
    assert original.cassette and len(original.maps) == 2


def test_explicit_map_list_copy_isolates_positions_but_shares_members() -> None:
    original = _level()
    branch = copy(original)
    branch.maps = original.maps.copy()
    assert branch is not original and branch.sid == original.sid
    assert all(branch.maps[side] is original.maps[side] for side in LevelSide)
    branch.cassette = False
    branch.maps[LevelSide.A] = MapProgress(deaths=10)
    require_map(branch.maps[LevelSide.B]).deaths = 20
    assert original.cassette and require_map(original.maps[LevelSide.A]).deaths == 3
    assert require_map(original.maps[LevelSide.B]).deaths == 20
    assert require_map(branch.maps[LevelSide.A]).deaths == 10


@pytest.mark.parametrize('use_method', (False, True))
def test_shallow_campaign_collection_copy_keeps_members_by_reference(use_method: bool) -> None:
    campaign = CampaignProgress('Pack', [_level()])
    original = KeyedDict([campaign], key=attrgetter('name'))
    copied = original.copy() if use_method else copy(original)
    assert copied is not original and copied['Pack'] is campaign
    del copied[campaign.name]
    copied.add(CampaignProgress('Other'))
    assert list(original.values()) == [campaign]
    assert list(copied) == ['Other']


def test_shallow_map_and_mods_copies_share_nested_objects() -> None:
    progress = require_map(_level().maps[LevelSide.A])
    copied = copy(progress)
    assert copied is not progress
    assert copied.strawberries is progress.strawberries
    assert copied.checkpoints is progress.checkpoints
    assert copied.bests is progress.bests
    copied.deaths = 99
    assert progress.deaths == 3
    copied.checkpoints.add('shared')
    assert progress.checkpoints == {'shared'}

    mods = ModSaveData(
        safe_session=parsed_session('Pack/Map', LevelSide.A),
        campaigns=CampaignMembers([CampaignProgress('Pack')]),
    )
    cloned = copy(mods)
    assert cloned is not mods
    assert cloned.campaigns is mods.campaigns
    assert cloned.recycled_campaigns is mods.recycled_campaigns
    assert cloned.safe_session is mods.safe_session
    cloned.safe_session = None
    assert not cloned.mods.has_session_data and cloned.mods.current_map is None
    assert mods.mods.has_session_data and mods.mods.current_map == ('Pack/Map', LevelSide.A)
    cloned.mods.last_map = ('Pack/Other', LevelSide.B)
    assert mods.mods.last_map is None


def test_domain_identity_is_read_only() -> None:
    campaign = CampaignProgress('Pack')
    level = _level()
    session = parsed_session('Pack/Map', LevelSide.A)
    assert campaign.key == campaign.name == 'Pack'
    assert level.key == level.sid == 'Pack/Map'
    for value, name in [
        (campaign, 'name'),
        (campaign, 'key'),
        (level, 'sid'),
        (level, 'key'),
        (session, 'sid'),
    ]:
        with pytest.raises((AttributeError, ValidationError)):
            setattr(value, name, 'changed')


def test_independent_defaults_and_copying_of_bests_and_sessions() -> None:
    first = MapProgress()
    second = MapProgress()
    first.bests.deaths = 3
    assert second.bests.deaths == 0
    session = parsed_session(
        'Pack/Map', LevelSide.B, room='room', time_played=Duration.from_seconds(-1)
    )
    copied = deepcopy(session)
    copied.room = 'other'
    assert session.room == 'room' and copied.sid == session.sid
    assert copied.time_played == Duration.from_seconds(-1)
    assert deepcopy(first.bests) == copy(first.bests)


@pytest.mark.parametrize('kind', ['level', 'session'])
def test_empty_sid_is_rejected(kind: str) -> None:
    if kind == 'level':
        with pytest.raises(ValueError, match='SID'):
            LevelProgress('')
    else:
        with pytest.raises(ValidationError, match='sid'):
            Session(
                sid='', side=LevelSide.A, room='', time_played=Duration(), deaths=0, room_deaths=0
            )
