"""Asynchronous GameBanana metadata lookup through the WEGFan mirror."""

from datetime import datetime

from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

WEGFAN_API_URL = 'https://celeste.weg.fan/api/v2/'
SEARCH_SIZE = 100
REQUEST_TIMEOUT = ClientTimeout(total=20)


class GameBananaAuthor(BaseModel):
    """One author listed in a GameBanana credit group."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    name: str
    role: str = ''
    url: str = ''


class GameBananaCredit(BaseModel):
    """One GameBanana credit group mirrored by WEGFan."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    group_name: str = Field(validation_alias=AliasChoices('groupName', 'group_name'))
    authors: tuple[GameBananaAuthor, ...] = ()


class GameBananaFileMod(BaseModel):
    """One Everest Mod packed in a GameBanana submission file."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    name: str


class GameBananaFile(BaseModel):
    """One downloadable file attached to a GameBanana submission."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    mods: tuple[GameBananaFileMod, ...] = ()


class GameBananaSubmission(BaseModel):
    """GameBanana submission metadata obtained through the WEGFan mirror."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    name: str
    submitter: str
    page_url: str | None = Field(default=None, validation_alias='pageUrl')
    latest_update_added_time: datetime = Field(validation_alias='latestUpdateAddedTime')
    description: str = ''
    credits: tuple[GameBananaCredit, ...] = ()
    files: tuple[GameBananaFile, ...] = ()

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
            (author.name, credit.group_name, author.role)
            for credit in self.credits
            for author in credit.authors
        )
        return choices or ((self.submitter, '提交者', ''),)

    def contains_mod(self, metadata_name: str) -> bool:
        """Return whether a submission file contains the exact Everest Mod name."""
        return any(mod.name == metadata_name for file in self.files for mod in file.mods)


class GameBananaSearchData(BaseModel):
    """The paginated data envelope returned by WEGFan submission search."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    content: tuple[GameBananaSubmission, ...] = ()


class GameBananaSearchResp(BaseModel):
    """Top-level WEGFan submission-search response."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    data: GameBananaSearchData


class GameBananaLookupError(RuntimeError):
    """A public GameBanana metadata lookup could not be completed."""


class GameBananaClient:
    """Look up submissions containing a named Everest Mod package."""

    async def lookup(self, metadata_name: str) -> GameBananaSubmission | None:
        """Return the unique exact submission match, or ``None`` when absent."""
        try:
            async with (
                ClientSession(base_url=WEGFAN_API_URL, timeout=REQUEST_TIMEOUT) as session,
                session.get(
                    'submission/search', params={'search': metadata_name, 'size': SEARCH_SIZE}
                ) as resp,
            ):
                resp.raise_for_status()
                content = await resp.read()
        except (TimeoutError, ClientError) as error:
            raise GameBananaLookupError('无法访问 WEGFan GameBanana 镜像。') from error
        try:
            results = GameBananaSearchResp.model_validate_json(content).data.content
        except ValueError as error:
            raise GameBananaLookupError('WEGFan 返回了无法识别的 GameBanana 数据。') from error
        matches = [result for result in results if result.contains_mod(metadata_name)]
        if len(matches) > 1:
            raise GameBananaLookupError(
                f'找到多个包含 {metadata_name!r} 的 GameBanana 提交，无法自动选择。'
            )
        return matches[0] if matches else None
