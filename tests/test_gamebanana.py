import asyncio
import json
from collections.abc import Callable
from typing import Self

from pist.gamebanana import GameBananaClient, GameBananaLookupError
from pist.models import GameBananaSearchResp


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

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def get(self, path: str, *, params: dict[str, object]) -> _Response:
        return _Response(self._handler(path, params))


def _mock_session(monkeypatch, handler: Callable[[str, dict[str, object]], object]) -> None:
    monkeypatch.setattr('pist.gamebanana.ClientSession', lambda **_: _Session(handler))


def test_lookup_selects_submission_containing_exact_metadata_name(monkeypatch) -> None:
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


def test_submission_preserves_raw_credit_groups_for_author_selection() -> None:
    submission = GameBananaSearchResp.model_validate(
        {
            'data': {
                'content': [
                    {
                        'name': 'Example',
                        'submitter': 'Submitter',
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
                ]
            }
        }
    ).data.content[0]

    assert submission.author_choices == (
        ('Alice', 'Creator', 'Mapper'),
        ('Bob', 'Special Thanks', 'Translation'),
    )


def test_lookup_rejects_multiple_exact_matches(monkeypatch) -> None:
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
