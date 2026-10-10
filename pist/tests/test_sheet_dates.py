"""Test date boundaries in positive and negative system-local time zones."""

from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from typing import Self

import pytest
from sheet_factory import make_sheet_fields

from pist.records.models import MapRecord
from pist.smartsheet import encoding, fields
from pist.smartsheet.models import JsonObject


def use_timezone(monkeypatch: pytest.MonkeyPatch, offset: int) -> tzinfo:
    """Simulate the standard library's local-time defaults without changing the host zone."""
    zone = timezone(timedelta(hours=offset))

    class LocalDatetime(datetime):
        @classmethod
        def fromtimestamp(cls, timestamp: float, tz: tzinfo | None = None) -> Self:
            value = super().fromtimestamp(timestamp, zone if tz is None else tz)
            return value.replace(tzinfo=None) if tz is None else value

        @classmethod
        def combine(cls, date: date, time: time, tzinfo: tzinfo | None = None) -> Self:
            return super().combine(date, time, zone if tzinfo is None else tzinfo)

        def astimezone(self, tz: tzinfo | None = None) -> Self:
            return super().astimezone(zone if tz is None else tz)

    monkeypatch.setattr(fields, 'datetime', LocalDatetime)
    monkeypatch.setattr(encoding, 'datetime', LocalDatetime)
    return zone


@pytest.mark.parametrize('offset', (-5, 8))
@pytest.mark.parametrize('raw', (1779897600000, '1779897600000'))
def test_reported_timestamp_uses_local_calendar_date(
    monkeypatch: pytest.MonkeyPatch, offset: int, raw: int | str
) -> None:
    zone = use_timezone(monkeypatch, offset)
    expected = datetime.fromtimestamp(int(raw) / 1000, zone).date()
    schema = make_sheet_fields(('起始日期', 4), ('结束日期', 4), ('更新时间', 4))
    contract = fields.load_sheet_fields()
    values: JsonObject = {field.field_title: raw for field in schema.fields}
    record = MapRecord(created_at=datetime.now(UTC), map_name='Map')
    adopted = contract.apply_remote(record, values, schema)
    assert adopted.started_at == adopted.finished_at == adopted.mod_updated_at == expected
    if offset == 8:
        assert expected == date(2026, 5, 28)  # The user's displayed sheet date.
    encoded = encoding.encode_record_values(adopted, schema)
    expected_midnight = str(int(datetime.combine(expected, time.min, zone).timestamp() * 1000))
    assert all(encoded[field.field_title] == expected_midnight for field in schema.fields)
    assert contract.comparable(values, schema) == contract.comparable(encoded, schema)
