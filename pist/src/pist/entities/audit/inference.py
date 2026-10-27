"""Pure rule-candidate inference from reviewed entity variants."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from berries.entities.rules import EntityRule, EntityRuleConditions
from berries.game.binmap import AttrValue

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


def rule_candidates_for_detail(detail: EntityAuditDetail) -> tuple[RuleCandidate, ...]:
    """Suggest candidates from a detail payload already read for the audit UI."""
    if detail.kind_confirmation is not None:
        return (
            RuleCandidate(
                EntityRule(kind=detail.kind_confirmation.kind),
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
        evidence = (*variant.observations, *variant.equivalent_observations)
        if any(observation.status is ObservationStatus.CONFLICT for observation in evidence):
            continue
        outcomes = {
            observation.kind if observation.status is ObservationStatus.CONFIRMED else None
            for observation in evidence
            if observation.status
            in {ObservationStatus.CONFIRMED, ObservationStatus.NOT_COLLECTIBLE}
        }
        if len(outcomes) == 1:
            classifications.append(_Classification(variant.attrs, variant.meta, outcomes.pop()))
    if len(classifications) != len(detail.variants):
        return ()
    candidates: dict[tuple[str | None, EntityRuleConditions], int] = {}
    for classification in classifications:
        conditions = _kind_conditions(classification, classifications, affecting_names)
        if conditions is None:
            return ()
        key = classification.kind, conditions
        candidates[key] = candidates.get(key, 0) + 1
    rule_candidates = tuple(
        RuleCandidate(
            EntityRule(
                kind=kind,
                when=dict(conditions.when),
                meta=dict(conditions.meta),
                missing=conditions.missing,
                missing_meta=conditions.missing_meta,
            ),
            variant_count,
            provisional=provisional,
        )
        for (kind, conditions), variant_count in sorted(
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
    rule = EntityRule(
        kind=classification.kind,
        when=dict(conditions.when),
        meta=dict(conditions.meta),
        missing=conditions.missing,
        missing_meta=conditions.missing_meta,
    )
    if any(
        other.kind != classification.kind and rule.matches(other.attrs, other.meta)
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
        rule = candidate.rule
        when, meta = _drop_default_conditions(rule.when, rule.meta, defaults)
        if (when, meta) == (rule.when, rule.meta):
            continue
        if _fallback_hides_unclassified_values(candidate, when, meta, candidates):
            continue
        fallback = EntityRule(
            kind=rule.kind,
            when=when,
            meta=meta,
            missing=rule.missing,
            missing_meta=rule.missing_meta,
        )
        if not _fallback_is_safe(fallback, classifications, candidates):
            continue
        key = fallback.kind, fallback.conditions
        fallbacks[key] = RuleCandidate(
            fallback,
            candidate.variant_count,
            fallback=True,
            provisional=provisional,
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
    r"""Avoid broad fallbacks when an explicit missing-value rule already exists.

    Dropping a confirmed default such as ``moon = false`` produces an unconditional
    fallback. If the audit already has a ``missing = [\"moon\"]`` rule, absence is
    represented precisely; the broad rule would additionally classify every unseen
    value of ``moon`` without evidence, preventing it from surfacing for review.
    """
    dropped_attrs = candidate.rule.when.keys() - when.keys()
    dropped_meta = candidate.rule.meta.keys() - meta.keys()
    return any(
        bool(dropped_attrs & set(other.rule.missing))
        or bool(dropped_meta & set(other.rule.missing_meta))
        for other in candidates
    )


def _drop_default_conditions(
    when: dict[str, AttrValue],
    meta: dict[str, AttrValue],
    defaults: Mapping[str, AttrValue | None],
) -> tuple[dict[str, AttrValue], dict[str, AttrValue]]:
    return (
        {
            name: value
            for name, value in when.items()
            if type(defaults.get(name)) is not type(value) or defaults.get(name) != value
        },
        {
            name: value
            for name, value in meta.items()
            if type(defaults.get(f'{META_ATTR_PREFIX}{name}')) is not type(value)
            or defaults.get(f'{META_ATTR_PREFIX}{name}') != value
        },
    )


def _fallback_is_safe(
    fallback: EntityRule,
    classifications: Iterable[_Classification],
    candidates: Iterable[RuleCandidate],
) -> bool:
    """Require known competing outcomes to be covered by stricter rules."""
    specificity = fallback.conditions.specificity
    has_competing_kind = False
    for classification in classifications:
        if classification.kind == fallback.kind or not fallback.matches(
            classification.attrs, classification.meta
        ):
            continue
        has_competing_kind = True
        if not any(
            other.rule.kind == classification.kind
            and other.rule.conditions.specificity > specificity
            and other.rule.matches(classification.attrs, classification.meta)
            for other in candidates
        ):
            return False
    return has_competing_kind


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
