"""Windows Credential Manager-backed secret storage."""

import asyncio

import keyring
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SERVICE_NAME = 'pist.tencent_docs'
DIRECT_CREDENTIALS_KEY = 'direct_credentials'


class TencentDocsCredentials(BaseModel):
    """Direct Tencent Docs credentials stored in Windows Credential Manager."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    client_id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    open_id: str = Field(min_length=1)


class CredentialStore:
    """Stores secrets outside the project directory in the current user's vault."""

    async def save_credentials(self, client_id: str, access_token: str, open_id: str) -> None:
        credentials = TencentDocsCredentials(
            client_id=client_id,
            access_token=access_token,
            open_id=open_id,
        )
        payload = credentials.model_dump_json()
        await asyncio.to_thread(keyring.set_password, SERVICE_NAME, DIRECT_CREDENTIALS_KEY, payload)

    async def load_credentials(self) -> TencentDocsCredentials:
        payload = await asyncio.to_thread(
            keyring.get_password, SERVICE_NAME, DIRECT_CREDENTIALS_KEY
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
