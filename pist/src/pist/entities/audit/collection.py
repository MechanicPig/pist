"""Turn encountered collected-entity rule issues into raw audit input."""

from collections import defaultdict
from collections.abc import Iterable

from pist.entities.collected import CollectedEntityReviewIssue

from .models import AuditGroup, AuditOccurrence, AuditReport, AuditSource


def collected_issues_report(
    issues: Iterable[CollectedEntityReviewIssue], source: AuditSource
) -> AuditReport:
    """Preserve actual entity attributes and map metadata, without inferring a kind."""
    groups: dict[str, list[AuditOccurrence]] = defaultdict(list)
    for issue in issues:
        if issue.entity_name is None or issue.attrs is None:
            continue
        groups[issue.entity_name].append(
            AuditOccurrence(
                source=source.model_copy(
                    update={'meta': source.meta if issue.meta is None else issue.meta}
                ),
                room=issue.collected_id.room,
                entity_id=issue.collected_id.entity_id,
                attrs=issue.attrs,
            )
        )
    return AuditReport(
        entities=tuple(
            AuditGroup(entity_name=name, occurrences=tuple(occurrences))
            for name, occurrences in groups.items()
        )
    )
