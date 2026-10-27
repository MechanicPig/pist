"""Asynchronous GameBanana metadata lookup through the WEGFan mirror."""

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from time import monotonic
from types import TracebackType
from typing import Self

from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import AliasChoices, Field

from .models import FrozenExternalModel

WEGFAN_API_URL = 'https://celeste.weg.fan/api/v2/'
SEARCH_SIZE = 100
REQUEST_TIMEOUT = ClientTimeout(total=20)
CACHE_TTL = 300.0
MAX_CACHE_ENTRIES = 256


class GameBananaAuthor(FrozenExternalModel):
    """One author listed in a GameBanana credit group."""

    name: str
    role: str = ''
    url: str = ''


class GameBananaCredit(FrozenExternalModel):
    """One GameBanana credit group mirrored by WEGFan."""

    group_name: str = Field(validation_alias=AliasChoices('groupName', 'group_name'))
    authors: tuple[GameBananaAuthor, ...] = ()


class GameBananaFileMod(FrozenExternalModel):
    """One Everest Mod packed in a GameBanana submission file."""

    name: str


class GameBananaFile(FrozenExternalModel):
    """One downloadable file attached to a GameBanana submission."""

    mods: tuple[GameBananaFileMod, ...] = ()


class GameBananaSubmission(FrozenExternalModel):
    """GameBanana submission metadata obtained through the WEGFan mirror."""

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

    def contains_mod(self, metadata_name: str) -> bool:
        """Return whether a submission file contains the exact Everest Mod name."""
        return any(mod.name == metadata_name for file in self.files for mod in file.mods)


class _SearchData(FrozenExternalModel):
    """The paginated data envelope returned by WEGFan submission search."""

    content: tuple[GameBananaSubmission, ...] = ()


class _SearchResp(FrozenExternalModel):
    """Top-level WEGFan submission-search response."""

    data: _SearchData


class GameBananaLookupError(RuntimeError):
    """A public GameBanana metadata lookup could not be completed."""


class GameBananaClient:
    """Look up submissions containing a named Everest Mod package."""

    def __init__(self, *, cache_ttl: float = CACHE_TTL) -> None:
        if not math.isfinite(cache_ttl) or cache_ttl < 0:
            raise ValueError('cache_ttl must be a finite non-negative duration.')
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, GameBananaSubmission | None]] = {}
        self._shared_session: ClientSession | None = None
        self._reuse_session = False
        self._session_lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        """Reuse HTTP connections during a group of metadata lookups."""
        if self._reuse_session:
            raise RuntimeError('GameBanana client is already in a session.')
        self._reuse_session = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close lookup connections and discard workflow-local cached metadata."""
        try:
            if self._shared_session is not None:
                await self._shared_session.close()
        finally:
            self._shared_session = None
            self._reuse_session = False
            self.clear_cache()

    def clear_cache(self, metadata_name: str | None = None) -> None:
        """Require a fresh lookup for one Mod, or for every previously queried Mod."""
        if metadata_name is None:
            self._cache.clear()
        else:
            self._cache.pop(metadata_name, None)  # pyright: ignore[reportUnusedCallResult]

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[ClientSession]:
        if not self._reuse_session:
            async with ClientSession(base_url=WEGFAN_API_URL, timeout=REQUEST_TIMEOUT) as session:
                yield session
            return
        async with self._session_lock:
            if self._shared_session is None:
                self._shared_session = ClientSession(
                    base_url=WEGFAN_API_URL, timeout=REQUEST_TIMEOUT
                )
        yield self._shared_session

    async def lookup(self, metadata_name: str) -> GameBananaSubmission | None:
        """Return an exact match, caching successful results and absence for five minutes.

        ``cache_ttl=0`` disables caching; failures are never cached. Use ``clear_cache``
        to explicitly fetch fresh metadata before the configured duration expires.
        """
        cached = self._cache.get(metadata_name)
        if cached is not None and cached[0] > monotonic():
            return cached[1]
        try:
            async with (
                self._session_scope() as session,
                session.get(
                    'submission/search', params={'search': metadata_name, 'size': SEARCH_SIZE}
                ) as resp,
            ):
                resp.raise_for_status()
                content = await resp.read()
        except (TimeoutError, ClientError) as error:
            raise GameBananaLookupError('无法访问 WEGFan GameBanana 镜像。') from error
        try:
            results = _SearchResp.model_validate_json(content).data.content
        except ValueError as error:
            raise GameBananaLookupError('WEGFan 返回了无法识别的 GameBanana 数据。') from error
        matches = [result for result in results if result.contains_mod(metadata_name)]
        if len(matches) > 1:
            raise GameBananaLookupError(
                f'找到多个包含 {metadata_name!r} 的 GameBanana 提交，无法自动选择。'
            )
        result = matches[0] if matches else None
        if self._cache_ttl:
            now = monotonic()
            self._cache = {key: value for key, value in self._cache.items() if value[0] > now}
            if len(self._cache) >= MAX_CACHE_ENTRIES:
                del self._cache[next(iter(self._cache))]
            self._cache[metadata_name] = (now + self._cache_ttl, result)
        return result
