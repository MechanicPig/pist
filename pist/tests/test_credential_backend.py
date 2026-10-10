"""Unrelated backend selection stays under keyring's configuration."""

import pytest
from keyring.backends.null import Keyring

from pist.credentials import backend


def test_other_backend_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = Keyring()
    monkeypatch.setattr(backend.keyring, 'get_keyring', lambda: selected)

    assert backend.credential_backend() is selected
