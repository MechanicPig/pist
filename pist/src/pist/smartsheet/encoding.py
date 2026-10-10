"""Encode personal records into Tencent Smart Sheet cells."""

from datetime import date, datetime, time

from pydantic import JsonValue, TypeAdapter

from berries.models import FrozenModel
from pist.records.fields import RECORD_ATTRIBUTES
from pist.records.models import MapRecord
from pist.smartsheet.models import (
    MILLISECONDS_PER_SECOND,
    FieldsResult,
    FieldType,
    JsonObject,
    SheetTextFragment,
)
from pist.types import CellValue

type SmartSheetSourceValue = CellValue | tuple[str, ...] | date


class MapNameCell(FrozenModel):
    """A displayed map name and its optional recording link in one remote cell."""

    name: str
    video_url: str | None = None

    @classmethod
    def from_value(cls, value: JsonValue) -> MapNameCell:
        """Split a remote map-name cell without silently discarding distinct links."""
        fragments = _TEXT_FRAGMENTS.validate_python(value)
        links = {fragment.link for fragment in fragments if fragment.link}
        if len(links) > 1:
            raise ValueError('地图名单元格包含多个不同链接，不能映射为单个视频地址。')
        return cls(
            name=''.join(fragment.text for fragment in fragments), video_url=next(iter(links), None)
        )

    def to_value(self) -> JsonValue:
        """Combine the name and optional recording link into a Smart Sheet text cell."""
        if self.video_url is None:
            return [{'type': 'text', 'text': self.name}]
        return [{'type': 'url', 'text': self.name, 'link': self.video_url}]


_TEXT_FRAGMENTS = TypeAdapter(tuple[SheetTextFragment, ...])


def encode_record_values(record: MapRecord, fields: FieldsResult) -> JsonObject:
    """Encode every supported editable column, including explicit clearing."""
    values: JsonObject = {}
    for field in fields.fields:
        title = field.field_title
        if field.property_formula is not None or title not in RECORD_ATTRIBUTES:
            continue
        if title == '地图名':
            if field.field_type != FieldType.TEXT:
                raise ValueError('地图名必须是支持内嵌链接的文本字段。')
            values[title] = MapNameCell(name=record.map_name, video_url=record.video_url).to_value()
            continue
        if title == 'Mod名':
            if record.mod_name is None and record.mod_url is None:
                values[title] = []
            elif record.mod_name is None or record.mod_url is None:
                raise ValueError('Mod 名和链接需要同时填写或同时清空。')
            else:
                values[title] = _encode_field_value(
                    (record.mod_name, record.mod_url), field.field_type
                )
            continue
        value = getattr(record, RECORD_ATTRIBUTES[title])
        if value is None or value == '' or value == ():
            values[title] = (
                []
                if field.field_type
                in {
                    FieldType.TEXT,
                    FieldType.LINK,
                    FieldType.MULTI_SELECT,
                    FieldType.SINGLE_SELECT,
                }
                else None
            )
        else:
            values[title] = _encode_field_value(value, field.field_type)
    return values


def _encode_field_value(value: SmartSheetSourceValue, field_type: int) -> JsonValue:
    """Encode one native value in Tencent Smart Sheet's field-value shape."""
    if field_type == FieldType.TEXT and isinstance(value, str):
        return [{'type': 'text', 'text': value}]
    if field_type == FieldType.NUMBER and type(value) is int:
        return value
    if field_type == FieldType.CHECKBOX and isinstance(value, bool):
        return value
    if field_type == FieldType.DATE:
        if isinstance(value, str):
            value = date.fromisoformat(value)
        if isinstance(value, date):
            midnight = datetime.combine(value, time.min).astimezone()
            return str(int(midnight.timestamp() * MILLISECONDS_PER_SECOND))
    if (
        field_type == FieldType.LINK
        and isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(item, str) for item in value)
    ):
        return [{'type': 'url', 'text': value[0], 'link': value[1]}]
    if field_type in {FieldType.MULTI_SELECT, FieldType.SINGLE_SELECT}:
        items = (value,) if isinstance(value, str) else value
        if isinstance(items, tuple) and all(isinstance(item, str) for item in items):
            return [{'text': item} for item in items]
    raise ValueError(f'Unsupported value {value!r} for Smart Sheet field type {field_type}.')
