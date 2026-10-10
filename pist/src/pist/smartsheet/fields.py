"""The explicit public-field contract and Smart Sheet cell decoding boundary."""

import tomllib
from datetime import UTC, datetime
from importlib.resources import files

from pydantic import JsonValue, TypeAdapter, field_validator, model_validator

from berries.models import FrozenModel
from pist.records.fields import FORMULA_FIELD_TITLES, RECORD_ATTRIBUTES
from pist.records.models import MapRecord
from pist.smartsheet.encoding import MapNameCell
from pist.smartsheet.models import (
    MILLISECONDS_PER_SECOND,
    FieldsResult,
    FieldType,
    JsonObject,
    SheetTextFragment,
)
from pist.types import CellValue


class SheetFieldSpec(FrozenModel):
    """One supported public field and the remote schema required to use it."""

    title: str
    type: FieldType
    required: bool = False
    writable: bool = True

    @field_validator('type')
    @classmethod
    def supported_type(cls, value: FieldType) -> FieldType:
        """Reject formula columns, whose values are owned by the remote sheet."""
        if value is FieldType.FORMULA:
            raise ValueError('Configured record fields do not support formula columns.')
        return value


class SheetFields(FrozenModel):
    """Validate the supported schema and decode only explicitly supported cells."""

    fields: tuple[SheetFieldSpec, ...]

    @model_validator(mode='after')
    def unique_titles(self) -> SheetFields:
        if len({field.title for field in self.fields}) != len(self.fields):
            raise ValueError('Supported sheet field titles must be unique.')
        return self

    def issues(self, remote: FieldsResult) -> tuple[str, ...]:
        """Report missing required columns, duplicate titles and incompatible types."""
        issues: list[str] = []
        for spec in self.fields:
            matches = [field for field in remote.fields if field.field_title == spec.title]
            if not matches:
                if spec.required:
                    issues.append(f'缺少必需字段：{spec.title}')
            elif len(matches) != 1:
                issues.append(f'字段名称重复：{spec.title}')
            elif matches[0].field_type != spec.type or matches[0].property_formula is not None:
                issues.append(f'字段类型不符：{spec.title}（要求 {spec.type}）')
        supported = {spec.title for spec in self.fields}
        for field in remote.fields:
            if field.field_title in FORMULA_FIELD_TITLES:
                if field.field_type != FieldType.FORMULA or field.property_formula is None:
                    issues.append(f'公式字段类型不符：{field.field_title}')
            elif field.field_title not in supported:
                issues.append(f'尚未支持的表格字段：{field.field_title}')
        return tuple(issues)

    def writable_fields(self, remote: FieldsResult) -> FieldsResult:
        """Restrict encoding to supported writable columns actually present remotely."""
        titles = {spec.title for spec in self.fields if spec.writable}
        return FieldsResult(
            fields=[field for field in remote.fields if field.field_title in titles]
        )

    def comparable(self, values: JsonObject, remote: FieldsResult) -> JsonObject:
        """Normalize supported cells while ignoring rich-text decoration and extra columns."""
        present = {field.field_title for field in remote.fields}
        result: JsonObject = {}
        for spec in self.fields:
            if spec.title not in present:
                continue
            if spec.title == '地图名':
                raw = values.get(spec.title)
                cell = (
                    None if raw is None or raw == [] or raw == '' else MapNameCell.from_value(raw)
                )
                result[spec.title] = None if cell is None else [cell.name, cell.video_url]
                continue
            value = self.decode(spec, values.get(spec.title))
            if isinstance(value, tuple):
                items: list[JsonValue] = list(
                    sorted(value) if spec.type == FieldType.MULTI_SELECT else value
                )
                result[spec.title] = items
            else:
                result[spec.title] = value
        return result

    @staticmethod
    def decode(spec: SheetFieldSpec, raw: JsonValue) -> CellValue | None:
        """Convert a validated API cell into a local value without guessing its type."""
        if raw is None or raw == [] or raw == '':
            return False if spec.type == FieldType.CHECKBOX else None
        if spec.type in {
            FieldType.TEXT,
            FieldType.LINK,
            FieldType.MULTI_SELECT,
            FieldType.SINGLE_SELECT,
        }:
            fragments = _FRAGMENTS.validate_python(raw)
            texts = tuple(fragment.text for fragment in fragments)
            if spec.type == FieldType.LINK:
                if len(fragments) != 1 or fragments[0].link is None:
                    raise ValueError(f'Unsupported hyperlink cell: {spec.title}')
                return texts[0], fragments[0].link
            return texts if spec.type == FieldType.MULTI_SELECT else ''.join(texts)
        if spec.type == FieldType.NUMBER:
            return _INTEGER.validate_python(raw, strict=True)
        if spec.type == FieldType.CHECKBOX:
            return _BOOLEAN.validate_python(raw, strict=True)
        if spec.type == FieldType.DATE:
            unix_milliseconds = _INTEGER.validate_python(raw)
            return (
                datetime.fromtimestamp(unix_milliseconds / MILLISECONDS_PER_SECOND, UTC)
                .astimezone()
                .date()
                .isoformat()
            )
        raise ValueError(f'Unsupported Smart Sheet field type {spec.type} for {spec.title!r}.')

    def apply_remote(
        self, record: MapRecord, values: JsonObject, remote: FieldsResult
    ) -> MapRecord:
        """Replace public fields from a remote row, retaining identity and private game metadata."""
        data = record.model_dump()
        present = {field.field_title for field in remote.fields}
        for spec in self.fields:
            if spec.title not in present:
                continue
            if spec.title == '地图名':
                cell = MapNameCell.from_value(values.get(spec.title, []))
                data.update(map_name=cell.name, video_url=cell.video_url)
                continue
            value = self.decode(spec, values.get(spec.title))
            if spec.title == 'Mod名':
                if value is None:
                    data.update(mod_name=None, mod_url=None)
                else:
                    assert isinstance(value, tuple) and len(value) == 2
                    data.update(mod_name=value[0], mod_url=value[1])
            else:
                attribute = RECORD_ATTRIBUTES[spec.title]
                if attribute == 'n_main_rooms' and isinstance(value, int) and value <= 0:
                    value = None
                data[attribute] = value or () if spec.type == FieldType.MULTI_SELECT else value
        return MapRecord.model_validate(data)


_FRAGMENTS = TypeAdapter(tuple[SheetTextFragment, ...])
_INTEGER = TypeAdapter(int)
_BOOLEAN = TypeAdapter(bool)


def load_sheet_fields() -> SheetFields:
    """Load the packaged public-field contract; never infer it from a live sheet."""
    source = files('pist').joinpath('data/sheet_fields.toml').read_text(encoding='utf-8')
    return SheetFields.model_validate(tomllib.loads(source))
