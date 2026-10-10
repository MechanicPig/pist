"""Validate credential storage without accessing any platform's system vault."""

import asyncio

import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordSetError
from pydantic import ValidationError

from pist.credentials import CredentialStore, TencentDocsCredentials
from pist.credentials.store import DIRECT_CREDENTIALS_KEY, SERVICE_NAME


class MemoryKeyring(KeyringBackend):
    """An injected backend; inherited priority keeps it out of automatic selection."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}
        self.fail_write = False

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail_write:
            raise PasswordSetError('Synthetic write failure')
        self.entries[service, username] = password


def test_credential_store_uses_injected_backend_for_save_and_load() -> None:
    backend = MemoryKeyring()
    store = CredentialStore(backend)

    async def exercise() -> None:
        await store.save_credentials('test-client', 'test-token', 'test-open-id')
        await store.save_credentials('test-client', 'new-token', 'test-open-id')
        loaded = await store.load_credentials()
        assert loaded == TencentDocsCredentials(
            client_id='test-client', access_token='new-token', open_id='test-open-id'
        )

    asyncio.run(exercise())
    assert set(backend.entries) == {('pist.tencent_docs', 'direct_credentials')}


def test_missing_credentials_report_configuration_error() -> None:
    with pytest.raises(RuntimeError, match='No Tencent Docs credentials'):
        asyncio.run(CredentialStore(MemoryKeyring()).load_credentials())


@pytest.mark.parametrize('payload', ('not json', '{}', '{"client_id": "test-client"}'))
def test_invalid_stored_credentials_report_configuration_error(payload: str) -> None:
    backend = MemoryKeyring()
    backend.set_password(SERVICE_NAME, DIRECT_CREDENTIALS_KEY, payload)

    with pytest.raises(RuntimeError, match='Stored Tencent Docs credentials are invalid'):
        asyncio.run(CredentialStore(backend).load_credentials())


def test_invalid_credentials_do_not_replace_saved_credentials() -> None:
    backend = MemoryKeyring()
    store = CredentialStore(backend)

    async def exercise() -> None:
        await store.save_credentials('client', 'token', 'open-id')
        with pytest.raises(ValidationError):
            await store.save_credentials('client', '', 'open-id')
        assert (await store.load_credentials()).access_token == 'token'

    asyncio.run(exercise())


def test_backend_write_failure_is_reported_without_losing_existing_credentials() -> None:
    backend = MemoryKeyring()
    store = CredentialStore(backend)

    async def exercise() -> None:
        await store.save_credentials('client', 'token', 'open-id')
        backend.fail_write = True
        with pytest.raises(PasswordSetError):
            await store.save_credentials('client', 'new-token', 'open-id')
        assert (await store.load_credentials()).access_token == 'token'

    asyncio.run(exercise())
