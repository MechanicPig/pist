"""Pydantic models for Tencent Docs API payloads."""

from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from pist.game.dialog import default_campaign_name, default_map_name, map_base_file_and_side

type JsonObject = dict[str, JsonValue]
type LocalizedNames = dict[str, str]

DIALOG_LANGUAGES = ('zh-cn', 'en')


def localized_name(names: Mapping[str, str], languages: Iterable[str]) -> str | None:
    """Return the first configured language available in a name mapping."""
    for lang in languages:
        name = names.get(lang)
        if name is not None:
            return name

    for name in names.values():
        return name

    return None


def _base_file_from_data(data: dict[str, object]) -> str:
    """Build a map's base file when callers do not already provide one."""
    file_path = data['file_path']
    assert isinstance(file_path, str)
    return map_base_file_and_side(file_path)[0]


_json_object_adapter = TypeAdapter(JsonObject)


def parse_json_object(value: object, *, context: str) -> JsonObject:
    """Validate a dynamically named API operation result as a JSON object."""
    try:
        return _json_object_adapter.validate_python(value)
    except ValidationError as error:
        raise TypeError(f'Tencent Docs returned an invalid {context} object.') from error


class TencentApiResp(BaseModel):
    """Common OpenAPI response envelope."""

    model_config = ConfigDict(extra='ignore')

    ret: int
    msg: str | None = None
    data: JsonObject = Field(default_factory=dict)


class FileIdConversion(BaseModel):
    """Possible fields returned by the file-ID conversion endpoint."""

    model_config = ConfigDict(extra='ignore')

    file_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices('fileID', 'fileId', 'ID', 'id'),
    )


class SmartSheet(BaseModel):
    """A Smart Sheet sub-sheet."""

    model_config = ConfigDict(extra='allow', populate_by_name=True)

    sheet_id: str = Field(validation_alias='sheetID')
    title: str = ''
    is_visible: bool | None = Field(default=None, validation_alias='isVisible')
    type: str | None = None


class SmartSheetField(BaseModel):
    """One Smart Sheet field, as returned by the metadata operation."""

    model_config = ConfigDict(extra='ignore')

    field_id: str = Field(validation_alias='fieldID')
    field_title: str = Field(validation_alias='fieldTitle')
    field_type: int = Field(validation_alias='fieldType')
    property_formula: object | None = Field(default=None, validation_alias='propertyFormula')


class GetSheetsData(BaseModel):
    model_config = ConfigDict(extra='ignore')

    sheets: list[SmartSheet] = Field(default_factory=list, validation_alias='getSheet')


class SubSheetInspection(BaseModel):
    """Raw metadata returned for one sub-sheet."""

    sheet: SmartSheet
    views: JsonObject
    fields: JsonObject
    records: JsonObject


class InspectionReport(BaseModel):
    """Persisted raw output of a read-only Smart Sheet inspection."""

    file_id: str
    sheets: list[SubSheetInspection]


class EverestManifest(BaseModel):
    """The first package entry in an Everest ``everest.yaml`` manifest."""

    model_config = ConfigDict(extra='ignore', populate_by_name=True)

    name: str = Field(validation_alias='Name')
    version: str | None = Field(default=None, validation_alias='Version')
    dependencies: list[Dependency] = Field(default_factory=list, validation_alias='Dependencies')
    optional_dependencies: list[Dependency] = Field(
        default_factory=list, validation_alias='OptionalDependencies'
    )


class Dependency(BaseModel):
    """One required or optional Everest Mod dependency."""

    model_config = ConfigDict(extra='ignore', populate_by_name=True)

    name: str = Field(validation_alias='Name')
    version: str | None = Field(default=None, validation_alias='Version')


class InstalledMod(BaseModel):
    """An enabled local Mod package discovered under the Game Mods directory."""

    source: Literal['zip', 'directory']
    filename: str
    path: str
    metadata_name: str
    metadata_version: str | None
    dependencies: list[Dependency] = Field(default_factory=list)
    optional_dependencies: list[Dependency] = Field(default_factory=list)
    collab_id: str | None = None
    map_files: list[str] = Field(default_factory=list)
    maps: list[LocalMap] = Field(default_factory=list)
    campaigns: list[LocalCampaign] = Field(default_factory=list)


class LocalMap(BaseModel):
    """One map file and its localized display-name candidates."""

    file_path: str
    dialog_key: str
    base_file: str = Field(default_factory=_base_file_from_data)
    side: Literal['B', 'C'] | None = None
    names: LocalizedNames = Field(default_factory=dict)
    author_texts: LocalizedNames = Field(default_factory=dict)
    collab_credit_tags: LocalizedNames = Field(default_factory=dict)

    @property
    def fallback_name(self) -> str:
        """Return the Game-generated map name when Dialog has no entry."""
        name = default_map_name(self.base_file)
        return f'{name} {self.side}' if self.side is not None else name


class LocalCampaign(BaseModel):
    """One playable campaign and its contained map files."""

    directory: str
    dialog_key: str
    kind: Literal['campaign', 'collab_lobby', 'collab_prologue'] = 'campaign'
    names: LocalizedNames = Field(default_factory=dict)
    maps: list[LocalMap] = Field(default_factory=list)

    @property
    def fallback_name(self) -> str:
        """Return the Game-generated campaign name when Dialog has no entry."""
        return default_campaign_name(self.directory)


class ModScanReport(BaseModel):
    """Persisted result of an offline scan of locally enabled Mods."""

    mods_directory: str
    blacklist_entries: list[str]
    skipped_blacklisted: list[str]
    disabled_mod_names: list[str] = Field(default_factory=list)
    mods: list[InstalledMod]


class RecordDraft(BaseModel):
    """One local map record awaiting enrichment and Smart Sheet insertion."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    created_at: datetime
    mod_metadata_name: str
    mod_name: str | None = None
    mod_url: str | None = None
    mod_updated_at: datetime | None = None
    credits: list[JsonObject] = Field(default_factory=list)
    map_name: str
    map_english_name: str | None = None
    map_file: str
    sid: str
    side: Literal['A', 'B', 'C']
    authors: tuple[str, ...] = ()
    difficulty: str | None = None
    save_slot: int | None = None
    time_played: str | None = None
    deaths: int | None = None
    completed: bool | None = None
    table_values: dict[str, dict[str, int | bool | str]] = Field(default_factory=dict)


class GameBananaAuthor(BaseModel):
    """One author listed in a GameBanana credit group."""

    model_config = ConfigDict(extra='ignore')

    name: str
    role: str = ''
    url: str = ''


class GameBananaCredit(BaseModel):
    """One GameBanana credit group mirrored by WEGFan."""

    model_config = ConfigDict(extra='ignore')

    groupName: str
    authors: list[GameBananaAuthor] = Field(default_factory=list)


class GameBananaFileMod(BaseModel):
    """One Everest Mod packed in a GameBanana submission file."""

    model_config = ConfigDict(extra='ignore')

    name: str


class GameBananaFile(BaseModel):
    """One downloadable file attached to a GameBanana submission."""

    model_config = ConfigDict(extra='ignore')

    mods: list[GameBananaFileMod] = Field(default_factory=list)


class GameBananaSubmission(BaseModel):
    """GameBanana submission metadata obtained through the WEGFan mirror."""

    model_config = ConfigDict(extra='ignore')

    name: str
    submitter: str
    page_url: str | None = Field(default=None, validation_alias='pageUrl')
    latest_update_added_time: datetime = Field(validation_alias='latestUpdateAddedTime')
    description: str = ''
    credits: list[GameBananaCredit] = Field(default_factory=list)
    files: list[GameBananaFile] = Field(default_factory=list)

    @property
    def authors(self) -> tuple[str, ...]:
        """Return all distinct Credit author names in their listed order."""
        return tuple(
            dict.fromkeys(author.name for credit in self.credits for author in credit.authors)
        )

    @property
    def author_choices(self) -> tuple[tuple[str, str, str], ...]:
        """Return raw Credit entries for explicit author selection in the UI."""
        choices = tuple(
            (author.name, credit.groupName, author.role)
            for credit in self.credits
            for author in credit.authors
        )
        return choices or ((self.submitter, '提交者', ''),)

    def contains_mod(self, metadata_name: str) -> bool:
        """Return whether a submission file contains the exact Everest Mod name."""
        return any(mod.name == metadata_name for file in self.files for mod in file.mods)


class GameBananaSearchData(BaseModel):
    """The paginated data envelope returned by WEGFan submission search."""

    model_config = ConfigDict(extra='ignore')

    content: list[GameBananaSubmission] = Field(default_factory=list)


class GameBananaSearchResp(BaseModel):
    """Top-level WEGFan submission-search response."""

    model_config = ConfigDict(extra='ignore')

    data: GameBananaSearchData


class TencentDocsCredentials(BaseModel):
    """Direct Tencent Docs credentials stored in Windows Credential Manager."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    client_id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    open_id: str = Field(min_length=1)


class PistSettings(BaseModel):
    """Persisted local presentation settings for pist."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    theme: Literal['textual-dark', 'textual-light'] = 'textual-dark'
    dialog_languages: tuple[str, ...] = DIALOG_LANGUAGES
    game_dir: Path | None = None
    smartsheet_url: str | None = None
