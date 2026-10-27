"""Domain preparation with output preflights, independent of XML conversion."""

from collections.abc import Iterable, Mapping
from copy import deepcopy

from berries.game.levels import LevelSide
from berries.game.saves.codecs import values as values_codec
from berries.game.saves.codecs.xml import campaign as campaign_codec
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress
from berries.game.saves.progress.map import MapProgress


def prepared_level(
    level: LevelProgress,
    *,
    counter_origins: Mapping[LevelSide, MapProgress | None] | None = None,
    keep_sides: Iterable[LevelSide] = (),
) -> LevelProgress:
    """Prepare independent edited Maps, preserving the requested existing references.

    A newly created target Map starts with an empty collection counter, not a copy
    of the donor's historical count offset. The donor retains its own offset when
    its collected instances are removed. Validate output before copying; no XML
    is built and strict read-only timer rules do not apply to edited values.
    """
    level.validate_output()
    kept = set(keep_sides)
    maps: list[MapProgress | None] = []
    for side in LevelSide:
        progress = level.maps[side]
        assert progress is not None
        if side in kept:
            maps.append(progress)
            continue
        prepared = deepcopy(progress)
        if counter_origins is not None and side in counter_origins:
            origin = counter_origins[side]
            offset = 0 if origin is None else origin.berry_offset()
            prepared.total_strawberries = values_codec.parse_count(
                offset + len(prepared.strawberries)
            )
        maps.append(prepared)
    return LevelProgress(level.sid, maps, cassette=level.cassette, id=level.id)


def prepared_campaign(campaign: CampaignProgress) -> CampaignProgress:
    """Prepare metadata while keeping the already prepared Level objects by reference."""
    fields = campaign_codec.metadata_fields(campaign)
    for level in campaign.levels.values():
        campaign.levels.validate_member(level)
        level.validate_output()
    return CampaignProgress(
        fields.name,
        campaign.levels.values(),
        unlocked_levels=fields.unlocked_levels,
        total_strawberries=fields.total_strawberries,
        poem=fields.poem,
    )
