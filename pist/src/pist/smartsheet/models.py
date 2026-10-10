"""Tencent Smart Sheet protocol models and field identifiers."""

from enum import IntEnum, StrEnum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue

from berries.models import ExternalModel, FrozenModel

MAX_PAGE_SIZE = 100
MILLISECONDS_PER_SECOND = 1000
MAIN_TABLE_TITLE = '主表'

type JsonObject = dict[str, JsonValue]


class FieldType(IntEnum):
    """Known Smart Sheet field types; remote metadata may contain other IDs."""

    TEXT = 1
    NUMBER = 2
    CHECKBOX = 3
    DATE = 4
    LINK = 8
    MULTI_SELECT = 9
    SINGLE_SELECT = 17
    FORMULA = 19


class TencentApiResp(ExternalModel):
    """Common OpenAPI response envelope."""

    ret: int
    msg: str | None = None
    data: JsonObject = Field(default_factory=dict)


class SmartSheetOperation(StrEnum):
    """One Smart Sheet API operation supported by this client."""

    GET_FIELDS = 'getFields'
    GET_RECORDS = 'getRecords'
    GET_VIEWS = 'getViews'
    ADD_RECORDS = 'addRecords'
    UPDATE_RECORDS = 'updateRecords'


class SmartSheetOperationOptions(FrozenModel):
    """Validated parameters serializable beneath one dynamic API operation key."""


class PageOptions(SmartSheetOperationOptions):
    """Pagination parameters for a list operation."""

    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)


class RecordReadOptions(PageOptions):
    """Read all records or only the explicitly requested remote identities."""

    record_ids: tuple[str, ...] | None = Field(
        default=None,
        validation_alias=AliasChoices('record_ids', 'recordIDs'),
        serialization_alias='recordIDs',
    )


class RecordWrite(FrozenModel):
    """One record create or update payload."""

    model_config = ConfigDict(populate_by_name=True)

    values: JsonObject
    record_id: str | None = Field(default=None, serialization_alias='recordID')


class RecordWriteOptions(SmartSheetOperationOptions):
    """Parameters for a record create or update operation."""

    records: tuple[RecordWrite, ...]


class FileIdConversion(ExternalModel):
    """Possible fields returned by the file-ID conversion endpoint."""

    file_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices('fileID', 'fileId', 'ID', 'id'),
    )


class SmartSheet(ExternalModel):
    """A Smart Sheet sub-sheet."""

    model_config = ConfigDict(extra='allow', populate_by_name=True)

    sheet_id: str = Field(validation_alias='sheetID')
    title: str = ''
    is_visible: bool | None = Field(default=None, validation_alias='isVisible')
    type: str | None = None


class SmartSheetSelectOption(ExternalModel):
    """One visible option declared by a Smart Sheet select field."""

    text: str


class SmartSheetSingleSelect(ExternalModel):
    """Options declared for one Smart Sheet single-select field."""

    options: list[SmartSheetSelectOption] = Field(default_factory=list)


class SmartSheetField(ExternalModel):
    """One Smart Sheet field, as returned by the metadata operation."""

    field_id: str = Field(validation_alias='fieldID')
    field_title: str = Field(validation_alias='fieldTitle')
    field_type: int = Field(validation_alias='fieldType')
    property_formula: object | None = Field(default=None, validation_alias='propertyFormula')
    property_single_select: SmartSheetSingleSelect | None = Field(
        default=None,
        validation_alias='propertySingleSelect',
    )


class FieldsResult(ExternalModel):
    """Validated result of the ``getFields`` operation."""

    fields: list[SmartSheetField]
    total: int | None = None


class ViewsResult(ExternalModel):
    """Validated result of the ``getViews`` operation."""

    total: int | None = None


class SmartSheetRecord(ExternalModel):
    """One Smart Sheet record returned by a read or write operation."""

    record_id: str | None = Field(default=None, validation_alias='recordID')
    values: JsonObject = Field(default_factory=dict)


class SheetTextFragment(ExternalModel):
    """A text or hyperlink fragment returned in a Smart Sheet cell."""

    text: str
    link: str | None = None


class RecordsResult(ExternalModel):
    """Validated result of a record read or write operation."""

    records: list[SmartSheetRecord]
    total: int | None = None


class GetSheetsData(ExternalModel):
    """Data payload for the Smart Sheet metadata operation."""

    sheets: list[SmartSheet] = Field(default_factory=list, validation_alias='getSheet')


class SubSheetInspection(BaseModel):
    """Validated metadata returned for one sub-sheet."""

    sheet: SmartSheet
    views: ViewsResult
    fields: FieldsResult
    records: RecordsResult


class InspectionReport(BaseModel):
    """Persisted raw output of a read-only Smart Sheet inspection."""

    file_id: str
    sheets: list[SubSheetInspection]


class MainSheetSnapshot(FrozenModel):
    """Live main-table schema and complete rows, optionally restricted to requested IDs."""

    file_id: str
    sheet_id: str
    fields: FieldsResult
    records: tuple[SmartSheetRecord, ...]
