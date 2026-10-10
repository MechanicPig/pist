"""Exercise Windows storage semantics without reading or writing the system vault."""

import sys

import pytest

if sys.platform != 'win32':
    pytest.skip('Native Windows backend adapter', allow_module_level=True)

from keyring.backends.Windows import DecodingCredential, WinVaultKeyring
from keyring.errors import PasswordSetError

from pist.credentials.backend import credential_backend
from pist.credentials.windows import UpdatingWinVaultKeyring


class MemoryWinVault(UpdatingWinVaultKeyring):
    def __init__(self) -> None:
        super().__init__()
        self.entries: dict[str, DecodingCredential] = {}
        self.fail_write = False

    def _read_credential(self, target: str) -> DecodingCredential | None:
        return self.entries.get(target)

    def _set_password(self, target: str, username: str, password: str) -> None:
        if self.fail_write:
            raise PasswordSetError('Synthetic write failure')
        self.entries[target] = DecodingCredential(
            UserName=username, CredentialBlob=password.encode('utf-16')
        )

    def _delete_password(self, target: str) -> None:
        self.entries.pop(target, None)


def test_same_username_updates_without_compound_copy() -> None:
    backend = MemoryWinVault()
    backend.set_password('test-service', 'user', 'first')
    backend.set_password('test-service', 'user', 'second')
    backend.set_password('test-service', 'user', 'third')

    assert set(backend.entries) == {'test-service'}
    assert backend.get_password('test-service', 'user') == 'third'


def test_different_users_keep_upstream_multi_user_behavior() -> None:
    backend = MemoryWinVault()
    backend.set_password('test-service', 'alice', 'first')
    backend.set_password('test-service', 'bob', 'second')

    assert set(backend.entries) == {'test-service', 'alice@test-service'}
    assert backend.get_password('test-service', 'alice') == 'first'
    assert backend.get_password('test-service', 'bob') == 'second'


def test_same_user_failed_write_keeps_existing_value() -> None:
    backend = MemoryWinVault()
    backend.set_password('test-service', 'user', 'first')
    backend.fail_write = True

    with pytest.raises(PasswordSetError):
        backend.set_password('test-service', 'user', 'second')

    assert backend.get_password('test-service', 'user') == 'first'
    assert set(backend.entries) == {'test-service'}


def test_existing_stale_copy_is_not_changed_or_used_for_current_user() -> None:
    backend = MemoryWinVault()
    backend._set_password('user@test-service', 'user', 'stale')
    backend.set_password('test-service', 'user', 'current')
    backend.set_password('test-service', 'user', 'latest')

    assert backend.entries['user@test-service'].value == 'stale'
    assert backend.get_password('test-service', 'user') == 'latest'


def test_inherited_delete_keeps_other_users_credentials() -> None:
    backend = MemoryWinVault()
    backend.set_password('test-service', 'alice', 'first')
    backend.set_password('test-service', 'bob', 'second')
    backend.delete_password('test-service', 'alice')

    assert backend.get_password('test-service', 'alice') is None
    assert backend.get_password('test-service', 'bob') == 'second'


def test_native_backend_is_replaced_without_global_change(monkeypatch: pytest.MonkeyPatch) -> None:
    native = WinVaultKeyring()
    native.persist = 'local machine'
    monkeypatch.setattr('keyring.get_keyring', lambda: native)

    selected = credential_backend()

    assert type(selected) is UpdatingWinVaultKeyring
    assert selected.persist == native.persist
    import keyring

    assert keyring.get_keyring() is native


def test_custom_windows_backend_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    custom = MemoryWinVault()
    monkeypatch.setattr('keyring.get_keyring', lambda: custom)

    assert credential_backend() is custom
