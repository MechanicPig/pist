"""Conflict callback contexts and incremental result validation."""

from collections.abc import Callable
from dataclasses import dataclass

from .errors import BadProgressSave
from .partition import ProgressPartition
from .progress.campaign import CampaignProgress
from .progress.level import LevelProgress


@dataclass(frozen=True, slots=True)
class CampaignConflictContext:
    """One campaign candidate and its partition; the progress remains editable."""

    progress: CampaignProgress
    partition: ProgressPartition


@dataclass(frozen=True, slots=True)
class LevelConflictContext:
    """One editable Level candidate with its actual or resolved enclosing campaign."""

    progress: LevelProgress
    partition: ProgressPartition
    campaign: CampaignProgress


type _ConflictResolver[Context, Result] = Callable[[Context, Context], Result]
type CampaignConflictResolver = _ConflictResolver[CampaignConflictContext, CampaignProgress]
type LevelConflictResolver = _ConflictResolver[LevelConflictContext, LevelProgress]


def validate_campaign_result(
    old: CampaignProgress,
    result: CampaignProgress,
) -> None:
    if not isinstance(result, CampaignProgress) or result.key != old.key:
        raise BadProgressSave(f'Invalid duplicate result: expected CampaignProgress {old.key!r}.')


def validate_level_result(old: LevelProgress, result: LevelProgress) -> None:
    if not isinstance(result, LevelProgress) or result.key != old.key:
        raise BadProgressSave(f'Invalid duplicate result: expected LevelProgress {old.key!r}.')


def resolve_campaign(
    old: CampaignProgress,
    new: CampaignProgress,
    *,
    strict: bool = True,
    on_conflict: CampaignConflictResolver | None = None,
    partition: ProgressPartition = ProgressPartition.MODS,
) -> CampaignProgress:
    if strict or on_conflict is None:
        raise BadProgressSave(f'Duplicate campaign progress in {partition}: {old.key!r}.')
    result = on_conflict(
        CampaignConflictContext(old, partition), CampaignConflictContext(new, partition)
    )
    validate_campaign_result(old, result)
    return result


def resolve_level(
    old: LevelProgress,
    new: LevelProgress,
    *,
    strict: bool = True,
    on_conflict: LevelConflictResolver | None = None,
    partition: ProgressPartition = ProgressPartition.MODS,
    campaign: CampaignProgress,
) -> LevelProgress:
    """Resolve candidates from one Campaign in the explicitly supplied partition."""
    if strict or on_conflict is None:
        raise BadProgressSave(
            f'Duplicate level progress in {partition}, campaign {campaign.name!r}: {old.key!r}.'
        )
    result = on_conflict(
        LevelConflictContext(old, partition, campaign),
        LevelConflictContext(new, partition, campaign),
    )
    validate_level_result(old, result)
    return result
