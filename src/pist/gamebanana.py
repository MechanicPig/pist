"""Asynchronous GameBanana metadata lookup through the WEGFan mirror."""

from aiohttp import ClientError, ClientSession, ClientTimeout

from pist.models import GameBananaSearchResp, GameBananaSubmission

WEGFAN_API_URL = 'https://celeste.weg.fan/api/v2/'
SEARCH_SIZE = 100
REQUEST_TIMEOUT = ClientTimeout(total=20)


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
