import asyncio
import json
from collections.abc import Callable
from typing import Self

import pytest
from aiohttp import ClientError

from berries import gamebanana as backend
from berries.gamebanana import GameBananaClient, GameBananaLookupError, GameBananaSubmission


class _Response:
    def __init__(self, payload: object) -> None:
        self._content = json.dumps(payload).encode()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def raise_for_status(self) -> None:
        pass

    async def read(self) -> bytes:
        return self._content


class _Session:
    def __init__(self, handler: Callable[[str, dict[str, object]], object]) -> None:
        self._handler = handler
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        self.closed = True

    def get(self, path: str, *, params: dict[str, object]) -> _Response:
        return _Response(self._handler(path, params))


def _mock_session(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[str, dict[str, object]], object]
) -> None:
    monkeypatch.setattr('berries.gamebanana.ClientSession', lambda **_: _Session(handler))


def test_lookup_selects_submission_containing_exact_metadata_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        'data': {
            'content': [
                {
                    'name': 'Similar Mod',
                    'submitter': 'Other',
                    'latestUpdateAddedTime': '2026-09-04T12:00:00Z',
                    'files': [{'mods': [{'name': 'SimilarMetadata'}]}],
                },
                {
                    'name': 'Exact Mod',
                    'submitter': 'Submitter',
                    'pageUrl': 'https://gamebanana.com/mods/123',
                    'latestUpdateAddedTime': '2026-09-04T12:00:00Z',
                    'description': '<p>适合初学者。<br>有进阶路线。</p>',
                    'credits': [
                        {
                            'groupName': 'Creator',
                            'authors': [{'name': 'Alice'}, {'name': 'Bob'}, {'name': 'Alice'}],
                        }
                    ],
                    'files': [{'mods': [{'name': 'ExactMetadata'}]}],
                },
            ]
        }
    }

    def handler(path: str, params: dict[str, object]) -> object:
        assert path == 'submission/search'
        assert params == {'search': 'ExactMetadata', 'size': 100}
        return payload

    _mock_session(monkeypatch, handler)

    result = asyncio.run(GameBananaClient().lookup('ExactMetadata'))

    assert result is not None
    assert result.name == 'Exact Mod'
    assert result.page_url == 'https://gamebanana.com/mods/123'
    assert result.description == '<p>适合初学者。<br>有进阶路线。</p>'
    assert result.authors == ('Alice', 'Bob')


def test_submission_preserves_credit_groups_roles_and_submitter_as_facts() -> None:
    submission = GameBananaSubmission.model_validate(
        {
            'name': 'Example',
            'submitter': 'Alice',
            'latestUpdateAddedTime': '2026-09-04T12:00:00Z',
            'credits': [
                {
                    'groupName': 'Creator',
                    'authors': [{'name': 'Alice', 'role': 'Mapper'}],
                },
                {
                    'groupName': 'Special Thanks',
                    'authors': [{'name': 'Bob', 'role': 'Translation'}],
                },
            ],
        }
    )
    assert submission.submitter == 'Alice'
    assert submission.authors == ('Alice', 'Bob')
    assert tuple(
        (author.name, credit.group_name, author.role)
        for credit in submission.credits
        for author in credit.authors
    ) == (
        ('Alice', 'Creator', 'Mapper'),
        ('Bob', 'Special Thanks', 'Translation'),
    )


def test_lookup_rejects_multiple_exact_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        'data': {
            'content': [
                {
                    'name': name,
                    'submitter': 'Submitter',
                    'latestUpdateAddedTime': '2026-09-04T12:00:00Z',
                    'files': [{'mods': [{'name': 'Metadata'}]}],
                }
                for name in ('First', 'Second')
            ]
        }
    }

    _mock_session(monkeypatch, lambda *_: payload)

    try:
        asyncio.run(GameBananaClient().lookup('Metadata'))
    except GameBananaLookupError as error:
        assert '多个' in str(error)
    else:
        raise AssertionError('Expected GameBananaLookupError.')


@pytest.mark.parametrize('found', (False, True))
def test_lookup_caches_success_and_absence_until_expiration_or_explicit_clear(
    monkeypatch: pytest.MonkeyPatch, found: bool
) -> None:
    now = [100.0]
    queries: list[str] = []

    def handler(path: str, params: dict[str, object]) -> object:
        queries.append(str(params['search']))
        return {
            'data': {
                'content': [
                    {
                        'name': 'Mod',
                        'submitter': 'Author',
                        'latestUpdateAddedTime': '2026-09-04T12:00:00Z',
                        'files': [{'mods': [{'name': params['search']}]}],
                    }
                ]
                if found
                else []
            }
        }

    _mock_session(monkeypatch, handler)
    monkeypatch.setattr(backend, 'monotonic', lambda: now[0])
    client = GameBananaClient(cache_ttl=10)

    async def run() -> None:
        first = await client.lookup('ExactMetadata')
        assert (first is not None) == found
        assert await client.lookup('ExactMetadata') is first
        assert len(queries) == 1
        await client.lookup('OtherMetadata')
        assert len(queries) == 2
        now[0] += 10
        await client.lookup('ExactMetadata')
        assert len(queries) == 3
        client.clear_cache('ExactMetadata')
        await client.lookup('ExactMetadata')
        assert len(queries) == 4
        client.clear_cache()
        await client.lookup('ExactMetadata')
        await client.lookup('OtherMetadata')
        assert len(queries) == 6

    asyncio.run(run())


def test_lookup_failure_is_not_cached_and_can_be_explicitly_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    def handler(path: str, params: dict[str, object]) -> object:
        queries.append(path)
        if len(queries) == 1:
            raise ClientError('Synthetic network failure')
        return {'data': {'content': []}}

    _mock_session(monkeypatch, handler)
    client = GameBananaClient()

    async def run() -> None:
        with pytest.raises(GameBananaLookupError):
            await client.lookup('Mod')
        assert await client.lookup('Mod') is None
        assert len(queries) == 2

    asyncio.run(run())


def test_lookup_shared_connections_close_on_exception_without_caching_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[_Session] = []
    queries: list[str] = []

    def handler(path: str, params: dict[str, object]) -> object:
        queries.append(path)
        return {'data': {'content': []}}

    def factory(**_: object) -> _Session:
        session = _Session(handler)
        sessions.append(session)
        return session

    monkeypatch.setattr(backend, 'ClientSession', factory)
    client = GameBananaClient(cache_ttl=0)

    async def run() -> None:
        async with client:
            assert sessions == []
            await asyncio.gather(client.lookup('A'), client.lookup('B'))
            await client.lookup('A')
            assert len(sessions) == 1
            assert len(queries) == 3
            assert not sessions[0].closed
            with pytest.raises(RuntimeError, match='already'):
                await client.__aenter__()
            raise ValueError('End workflow')

    with pytest.raises(ValueError, match='End workflow'):
        asyncio.run(run())
    assert sessions[0].closed
    asyncio.run(client.lookup('A'))
    assert len(queries) == 4
    assert len(sessions) == 2 and sessions[1].closed


@pytest.mark.parametrize('ttl', (-1, float('inf'), float('nan')))
def test_invalid_lookup_cache_duration_is_rejected(ttl: float) -> None:
    with pytest.raises(ValueError):
        GameBananaClient(cache_ttl=ttl)
