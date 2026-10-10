"""Select credential storage without changing keyring's process-wide configuration."""

import keyring
from keyring.backend import KeyringBackend
from keyring.backends.Windows import WinVaultKeyring


def credential_backend() -> KeyringBackend:
    """Retain the selected backend, correcting native Windows same-user updates."""
    backend = keyring.get_keyring()
    if type(backend) is WinVaultKeyring:
        from pist.credentials.windows import UpdatingWinVaultKeyring

        corrected = UpdatingWinVaultKeyring()
        corrected.persist = backend.persist
        return corrected
    return backend
