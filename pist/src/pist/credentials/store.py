"""Store validated Tencent Docs credentials in the configured system credential backend."""

import asyncio

from keyring.backend import KeyringBackend
from pydantic import ValidationError

from berries.models import FrozenModel
from berries.types import NonEmptyStr
from pist.credentials.backend import credential_backend

SERVICE_NAME = 'pist.tencent_docs'
DIRECT_CREDENTIALS_KEY = 'direct_credentials'


class TencentDocsCredentials(FrozenModel):
    """Direct Tencent Docs credentials required to authenticate API requests."""

    client_id: NonEmptyStr
    access_token: NonEmptyStr
    open_id: NonEmptyStr


class CredentialStore:
    """Stores secrets outside the project directory in the current user's vault."""

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        self._backend = backend if backend is not None else credential_backend()

    async def save_credentials(self, client_id: str, access_token: str, open_id: str) -> None:
        credentials = TencentDocsCredentials(
            client_id=client_id,
            access_token=access_token,
            open_id=open_id,
        )
        payload = credentials.model_dump_json()
        await asyncio.to_thread(
            self._backend.set_password, SERVICE_NAME, DIRECT_CREDENTIALS_KEY, payload
        )

    async def load_credentials(self) -> TencentDocsCredentials:
        payload = await asyncio.to_thread(
            self._backend.get_password, SERVICE_NAME, DIRECT_CREDENTIALS_KEY
        )
        if not payload:
            raise RuntimeError(
                'No Tencent Docs credentials found. Run `pist credentials set` first.'
            )
        try:
            return TencentDocsCredentials.model_validate_json(payload)
        except ValidationError as error:
            raise RuntimeError(
                'Stored Tencent Docs credentials are invalid. Run `pist credentials set`.'
            ) from error
