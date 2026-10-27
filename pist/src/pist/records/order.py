"""Plan explicit corrections to play order without changing record identities."""

from pydantic import PositiveInt

from berries.models import FrozenModel

from .models import MapRecord


class RecordNumberChange(FrozenModel):
    """One record's old and proposed place in the existing number sequence."""

    local_id: PositiveInt
    map_name: str
    old_number: PositiveInt
    new_number: PositiveInt


class RecordOrderPlan(FrozenModel):
    """A preview and its expected complete numbered order for stale-plan detection."""

    expected: tuple[tuple[PositiveInt, PositiveInt], ...]
    changes: tuple[RecordNumberChange, ...]


def plan_record_move(
    records: tuple[MapRecord, ...], local_id: int, target_id: int, *, before: bool
) -> RecordOrderPlan:
    """Move one numbered record around another, preserving existing numbers and gaps."""
    numbered = sorted(
        ((record.record_number, record) for record in records if record.record_number is not None),
        key=lambda item: item[0],
    )
    expected: list[tuple[int, int]] = []
    for number, record in numbered:
        if record.local_id is None:
            raise ValueError('顺序调整只支持已保存且已编号的记录。')
        expected.append((record.local_id, number))
    if len({id_ for id_, _ in expected}) != len(expected) or len(
        {number for _, number in expected}
    ) != len(expected):
        raise ValueError('记录身份或编号重复，无法调整顺序。')
    ordered = [record for _, record in numbered]
    by_id = {record.local_id: record for record in ordered}
    if local_id not in by_id or target_id not in by_id:
        raise ValueError('只能将已编号记录移到另一条已编号记录之前或之后。')
    if local_id == target_id:
        return RecordOrderPlan(expected=tuple(expected), changes=())
    moved = by_id[local_id]
    ordered.remove(moved)
    index = ordered.index(by_id[target_id]) + (not before)
    ordered.insert(index, moved)
    changes: list[RecordNumberChange] = []
    for record, (_, number) in zip(ordered, expected, strict=True):
        if record.record_number != number:
            assert record.local_id is not None and record.record_number is not None
            changes.append(
                RecordNumberChange(
                    local_id=record.local_id,
                    map_name=record.map_name,
                    old_number=record.record_number,
                    new_number=number,
                )
            )
    return RecordOrderPlan(expected=tuple(expected), changes=tuple(changes))
