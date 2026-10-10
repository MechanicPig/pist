"""Adapt Windows credential updates for upstream issue jaraco/keyring#545."""

from keyring.backends.Windows import WinVaultKeyring


class UpdatingWinVaultKeyring(WinVaultKeyring):
    """Update same-user credentials without creating a stale compound-name copy."""

    def set_password(self, service: str, username: str, password: str) -> None:
        # Private backend hooks are intentionally confined here. Recheck this adapter when
        # upgrading keyring: https://github.com/jaraco/keyring/issues/545
        existing = self._read_credential(service)
        if existing is not None and existing['UserName'] == username:
            self._set_password(service, username, password)
        else:
            super().set_password(service, username, password)
