"""Normalize successful write cells without treating omitted fields as cleared."""

from collections.abc import Iterator

from pydantic import TypeAdapter

from .models import FieldsResult, FieldType, JsonObject, JsonValue

_OPTION_IDS = TypeAdapter(list[str])


class UnknownWriteOptionID(ValueError):
    """A response option needs one fresh schema lookup before it can be interpreted."""


def _select_cells(
    returned: JsonObject, submitted: JsonObject, fields: FieldsResult
) -> Iterator[tuple[str, list[str], dict[str, str]]]:
    for field in fields.fields:
        if field.field_title not in submitted or field.field_type not in (
            FieldType.SINGLE_SELECT,
            FieldType.MULTI_SELECT,
        ):
            continue
        raw = returned.get(field.field_title)
        if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
            continue
        ids = _OPTION_IDS.validate_python(raw, strict=True)
        options = {
            option.id: option.text for option in field.select_options if option.id is not None
        }
        yield field.field_title, ids, options


def needs_option_refresh(returned: JsonObject, submitted: JsonObject, fields: FieldsResult) -> bool:
    """Check for unknown select IDs without constructing a normalized response.

    Incomplete rows cannot be confirmed regardless of their option IDs; callers
    diagnose them independently rather than spend a schema request on them.
    """
    if submitted.keys() - returned.keys():
        return False
    return any(
        id_ not in options
        for _, ids, options in _select_cells(returned, submitted, fields)
        for id_ in ids
    )


def submitted_response_values(
    returned: JsonObject, submitted: JsonObject, fields: FieldsResult
) -> JsonObject:
    """Return explicit submitted cells, resolving select IDs through the checked schema.

    Unknown option IDs and missing cells cannot establish a confirmation baseline.
    Extra response cells are not evidence about fields that were not submitted.
    """
    missing = submitted.keys() - returned.keys()
    if missing:
        raise ValueError(f'写入响应缺少提交字段：{", ".join(sorted(missing))}。')
    values = {title: returned[title] for title in submitted}
    for title, ids, options in _select_cells(returned, submitted, fields):
        texts: list[JsonValue] = []
        for id_ in ids:
            if id_ not in options:
                raise UnknownWriteOptionID(f'写入响应包含未知选项 ID：{title} / {id_}。')
            texts.append({'text': options[id_]})
        values[title] = texts
    return values
