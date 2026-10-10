"""Small synthetic Smart Sheet schema factory for Pist's offline tests."""

from copy import deepcopy

from pist.smartsheet.models import (
    FieldsResult,
    JsonObject,
    MainSheetSnapshot,
    RecordWrite,
    SmartSheetField,
    SmartSheetRecord,
)


def make_sheet_fields(*items: tuple[str, int]) -> FieldsResult:
    return FieldsResult(
        fields=[
            SmartSheetField.model_validate(
                {'fieldID': f'f{i}', 'fieldTitle': title, 'fieldType': type_}
            )
            for i, (title, type_) in enumerate(items)
        ]
    )


class FakeSheet:
    def __init__(self) -> None:
        self.fields = make_sheet_fields(
            ('地图名', 1), ('Mod元数据名', 1), ('状态', 17), ('备注', 1), ('死亡数', 2)
        )
        self.rows: dict[str, JsonObject] = {}
        self.writes: list[tuple[str | None, JsonObject]] = []
        self.reads = 0
        self.read_selections: list[tuple[str, ...] | None] = []
        self.update_batches: list[tuple[RecordWrite, ...]] = []
        self.read_error = False
        self.write_error = False
        self.verify_error = False

    async def read_main_sheet(
        self, source: str, *, record_ids: tuple[str, ...] | None = None
    ) -> MainSheetSnapshot:
        self.reads += 1
        self.read_selections.append(record_ids)
        assert source == 'source'
        if self.read_error or (self.verify_error and self.writes):
            raise OSError('offline')
        return MainSheetSnapshot(
            file_id='file',
            sheet_id='main',
            fields=self.fields,
            records=tuple(
                SmartSheetRecord.model_validate({'recordID': id_, 'values': deepcopy(values)})
                for id_, values in self.rows.items()
                if record_ids is None or id_ in record_ids
            ),
        )

    async def write_record(
        self, snapshot: MainSheetSnapshot, values: JsonObject, *, record_id: str | None
    ) -> str:
        assert (snapshot.file_id, snapshot.sheet_id) == ('file', 'main')
        self.writes.append((record_id, deepcopy(values)))
        if self.write_error:
            raise OSError('write failed')
        target = record_id or 'created'
        self.rows.setdefault(target, {}).update(deepcopy(values))
        return target

    async def update_records(
        self, snapshot: MainSheetSnapshot, records: tuple[RecordWrite, ...]
    ) -> None:
        self.update_batches.append(records)
        for record in records:
            assert record.record_id is not None
            await self.write_record(snapshot, record.values, record_id=record.record_id)
