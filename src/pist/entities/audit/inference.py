"""Pure rule-candidate inference from reviewed entity variants."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pist.game.binmap import AttrValue

from ..rules import EntityRuleConditions
from .models import (
    META_ATTR_PREFIX,
    UNKNOWN,
    AttrAuditStatus,
    EntityAuditDetail,
    ObservationStatus,
    RuleCandidate,
)


@dataclass(frozen=True, slots=True)
class _Classification:
    """One fully reviewed raw variant and its resulting collectible kind."""

    attrs: dict[str, AttrValue]
    meta: dict[str, AttrValue]
    kind: str | None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One deduplicated rule candidate with its represented variant count."""

    when: dict[str, AttrValue]
    meta: dict[str, AttrValue]
    variant_count: int


def rule_candidates_for_detail(detail: EntityAuditDetail) -> tuple[RuleCandidate, ...]:
    """Suggest candidates from a detail payload already read for the audit UI."""
    if detail.kind_confirmation is not None:
        return (
            RuleCandidate(
                detail.kind_confirmation.kind,
                {},
                {},
                len(detail.variants),
            ),
        )
    provisional = any(
        attr.status is AttrAuditStatus.LIKELY_NOT_AFFECT_KIND for attr in detail.attr_summaries
    )
    affecting_names = {
        attr.name for attr in detail.attr_summaries if attr.status is AttrAuditStatus.AFFECTS_KIND
    }
    classifications: list[_Classification] = []
    for variant in detail.variants:
        if any(
            observation.status is ObservationStatus.CONFLICT for observation in variant.observations
        ):
            continue
        outcomes = {
            observation.kind if observation.status is ObservationStatus.CONFIRMED else None
            for observation in variant.observations
            if observation.status
            in {ObservationStatus.CONFIRMED, ObservationStatus.NOT_COLLECTIBLE}
        }
        if len(outcomes) == 1:
            classifications.append(_Classification(variant.attrs, variant.meta, outcomes.pop()))
    if len(classifications) != len(detail.variants):
        return ()
    candidates: dict[tuple[str | None, EntityRuleConditions], _Candidate] = {}
    for classification in classifications:
        conditions = _kind_conditions(classification, classifications, affecting_names)
        if conditions is None:
            continue
        key = classification.kind, conditions
        previous = candidates.get(key)
        candidates[key] = _Candidate(
            dict(conditions.when),
            dict(conditions.meta),
            1 if previous is None else previous.variant_count + 1,
        )
    rule_candidates = tuple(
        RuleCandidate(
            kind,
            candidate.when,
            candidate.meta,
            candidate.variant_count,
            provisional=provisional,
            missing=conditions.missing,
            missing_meta=conditions.missing_meta,
        )
        for (kind, conditions), candidate in sorted(
            candidates.items(),
            key=lambda item: _rule_candidate_sort_key(*item[0]),
        )
    )
    return (
        *rule_candidates,
        *_default_fallback_candidates(
            detail, classifications, rule_candidates, provisional=provisional
        ),
    )


def _kind_conditions(
    classification: _Classification,
    classifications: Iterable[_Classification],
    affecting_names: set[str],
) -> EntityRuleConditions | None:
    """Keep every kind-affecting condition while excluding known other kinds."""
    attr_names = {name for name in affecting_names if not name.startswith(META_ATTR_PREFIX)}
    meta_names = {
        name.removeprefix(META_ATTR_PREFIX)
        for name in affecting_names
        if name.startswith(META_ATTR_PREFIX)
    }
    conditions = EntityRuleConditions(
        tuple(
            sorted(
                (name, classification.attrs[name])
                for name in attr_names
                if name in classification.attrs
            )
        ),
        tuple(
            sorted(
                (name, classification.meta[name])
                for name in meta_names
                if name in classification.meta
            )
        ),
        tuple(sorted(attr_names - classification.attrs.keys())),
        tuple(sorted(meta_names - classification.meta.keys())),
    )
    if any(
        other.kind != classification.kind and _conditions_match(other, conditions)
        for other in classifications
    ):
        return None
    return conditions


def _default_fallback_candidates(
    detail: EntityAuditDetail,
    classifications: list[_Classification],
    candidates: tuple[RuleCandidate, ...],
    *,
    provisional: bool,
) -> tuple[RuleCandidate, ...]:
    """Offer fallbacks only for confirmed defaults with covered competing outcomes."""
    defaults = {
        attr.name: default
        for attr in detail.attr_summaries
        if (default := attr.default_value) is not UNKNOWN and default is not None
    }
    fallbacks: dict[tuple[str | None, EntityRuleConditions], RuleCandidate] = {}
    for candidate in candidates:
        when, meta = _drop_default_conditions(candidate.when, candidate.meta, defaults)
        if (when, meta) == (candidate.when, candidate.meta):
            continue
        if _fallback_hides_unclassified_values(candidate, when, meta, candidates):
            continue
        if not _fallback_is_safe(candidate, when, meta, classifications, candidates):
            continue
        key = (
            candidate.kind,
            EntityRuleConditions(
                tuple(sorted(when.items())),
                tuple(sorted(meta.items())),
                candidate.missing,
                candidate.missing_meta,
            ),
        )
        fallbacks[key] = RuleCandidate(
            candidate.kind,
            when,
            meta,
            candidate.variant_count,
            fallback=True,
            provisional=provisional,
            missing=candidate.missing,
            missing_meta=candidate.missing_meta,
        )
    return tuple(
        fallbacks[key]
        for key in sorted(
            fallbacks,
            key=lambda item: _rule_candidate_sort_key(*item),
        )
    )


def _fallback_hides_unclassified_values(
    candidate: RuleCandidate,
    when: Mapping[str, AttrValue],
    meta: Mapping[str, AttrValue],
    candidates: Iterable[RuleCandidate],
) -> bool:
    """Avoid broad fallbacks when an explicit missing-value rule already exists.

    Dropping a confirmed default such as ``moon = false`` produces an unconditional
    fallback. If the audit already has a ``missing = [\"moon\"]`` rule, absence is
    represented precisely; the broad rule would additionally classify every unseen
    value of ``moon`` without evidence, preventing it from surfacing for review.
    """
    dropped_attrs = candidate.when.keys() - when.keys()
    dropped_meta = candidate.meta.keys() - meta.keys()
    return any(
        bool(dropped_attrs & set(other.missing)) or bool(dropped_meta & set(other.missing_meta))
        for other in candidates
    )


def _drop_default_conditions(
    when: dict[str, AttrValue],
    meta: dict[str, AttrValue],
    defaults: Mapping[str, AttrValue | None],
) -> tuple[dict[str, AttrValue], dict[str, AttrValue]]:
    return (
        {name: value for name, value in when.items() if defaults.get(name) != value},
        {
            name: value
            for name, value in meta.items()
            if defaults.get(f'{META_ATTR_PREFIX}{name}') != value
        },
    )


def _fallback_is_safe(
    candidate: RuleCandidate,
    when: dict[str, AttrValue],
    meta: dict[str, AttrValue],
    classifications: Iterable[_Classification],
    candidates: Iterable[RuleCandidate],
) -> bool:
    """Require known competing outcomes to be covered by stricter rules."""
    fallback = RuleCandidate(
        candidate.kind,
        when,
        meta,
        candidate.variant_count,
        missing=candidate.missing,
        missing_meta=candidate.missing_meta,
    )
    specificity = _candidate_specificity(fallback)
    has_competing_kind = False
    for classification in classifications:
        if classification.kind == candidate.kind or not _candidate_matches(
            fallback, classification
        ):
            continue
        has_competing_kind = True
        if not any(
            other.kind == classification.kind
            and _candidate_specificity(other) > specificity
            and _candidate_matches(other, classification)
            for other in candidates
        ):
            return False
    return has_competing_kind


def _attrs_match(
    actual: Mapping[str, AttrValue], expected: Iterable[tuple[str, AttrValue]]
) -> bool:
    return all(
        name in actual and type(actual[name]) is type(value) and actual[name] == value
        for name, value in expected
    )


def _conditions_match(classification: _Classification, conditions: EntityRuleConditions) -> bool:
    return (
        _attrs_match(classification.attrs, conditions.when)
        and _attrs_match(classification.meta, conditions.meta)
        and all(name not in classification.attrs for name in conditions.missing)
        and all(name not in classification.meta for name in conditions.missing_meta)
    )


def _candidate_specificity(candidate: RuleCandidate) -> int:
    return candidate.conditions.specificity


def _rule_candidate_sort_key(
    kind: str | None, conditions: EntityRuleConditions
) -> tuple[int, bool, str, str, str, tuple[str, ...], tuple[str, ...]]:
    return (
        conditions.specificity,
        kind is None,
        '' if kind is None else kind,
        json.dumps(
            dict(conditions.when), ensure_ascii=False, sort_keys=True, separators=(',', ':')
        ),
        json.dumps(
            dict(conditions.meta), ensure_ascii=False, sort_keys=True, separators=(',', ':')
        ),
        conditions.missing,
        conditions.missing_meta,
    )


def _candidate_matches(candidate: RuleCandidate, classification: _Classification) -> bool:
    return _conditions_match(classification, candidate.conditions)
