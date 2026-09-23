from pathlib import Path

import pytest

from pist.collab_lobbies import (
    CollabLobbyOverrideStore,
    load_collab_lobby_override_layers,
    load_collab_lobby_overrides,
)
from pist.game.levels import LevelSide


def test_local_collab_lobby_override_replaces_shared_side(tmp_path: Path) -> None:
    shared = tmp_path / 'shared.toml'
    shared.write_text(
        """
[[lobbies]]
lobby = "Example/0-Lobbies/1-Easy"
side = "A"
campaigns = ["Example/1-Easy"]

[[lobbies]]
lobby = "Example/0-Lobbies/1-Easy"
side = "B"
campaigns = ["Example/1-Easy-B"]
""",
        encoding='utf-8',
    )
    local = tmp_path / 'local.toml'
    local.write_text(
        """
[[lobbies]]
lobby = "Example/0-Lobbies/1-Easy"
side = "A"
campaigns = []
""",
        encoding='utf-8',
    )

    overrides = load_collab_lobby_override_layers(shared, local)

    assert overrides.campaigns_for('Example/0-Lobbies/1-Easy', LevelSide.A) == ()
    assert overrides.campaigns_for('Example/0-Lobbies/1-Easy', LevelSide.B) == ('Example/1-Easy-B',)
    assert overrides.campaigns_for('Example/0-Lobbies/2-Hard', LevelSide.A) is None


def test_collab_lobby_overrides_reject_duplicate_side(tmp_path: Path) -> None:
    path = tmp_path / 'collab_lobbies.toml'
    path.write_text(
        """
[[lobbies]]
lobby = "Example/0-Lobbies/1-Easy"
side = "A"
campaigns = ["Example/1-Easy"]

[[lobbies]]
lobby = "Example/0-Lobbies/1-Easy"
side = "A"
campaigns = ["Example/2-Hard"]
""",
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='Invalid Collab lobby overrides'):
        load_collab_lobby_overrides(path)


def test_collab_lobby_overrides_reject_backslash_lobby_sid(tmp_path: Path) -> None:
    path = tmp_path / 'collab_lobbies.toml'
    path.write_text(
        r"""
[[lobbies]]
lobby = 'Example\0-Lobbies\1-Easy'
side = 'A'
campaigns = ['Example/1-Easy']
""",
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='Invalid Collab lobby overrides'):
        load_collab_lobby_overrides(path)


def test_collab_lobby_overrides_reject_backslash_campaign_ref(tmp_path: Path) -> None:
    path = tmp_path / 'collab_lobbies.toml'
    path.write_text(
        r"""
[[lobbies]]
lobby = 'Example/0-Lobbies/1-Easy'
side = 'A'
campaigns = ['Example\1-Easy']
""",
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='Invalid Collab lobby overrides'):
        load_collab_lobby_overrides(path)


def test_collab_lobby_override_store_saves_and_removes_local_entry(tmp_path: Path) -> None:
    shared = tmp_path / 'shared.toml'
    shared.write_text(
        """
[[lobbies]]
lobby = "Example/0-Lobbies/1-Easy"
side = "A"
campaigns = ["Example/1-Easy"]
""",
        encoding='utf-8',
    )
    local = tmp_path / '.pist/collab_lobbies.toml'
    store = CollabLobbyOverrideStore(shared, local)

    saved = store.save_local(
        'Example/0-Lobbies/1-Easy',
        LevelSide.A,
        ('Example/1-Easy', 'Example/1-Extra'),
    )

    assert store.has_local_override('Example/0-Lobbies/1-Easy', LevelSide.A)
    assert saved.campaigns_for('Example/0-Lobbies/1-Easy', LevelSide.A) == (
        'Example/1-Easy',
        'Example/1-Extra',
    )
    restored = store.remove_local('Example/0-Lobbies/1-Easy', LevelSide.A)
    assert not store.has_local_override('Example/0-Lobbies/1-Easy', LevelSide.A)
    assert restored.campaigns_for('Example/0-Lobbies/1-Easy', LevelSide.A) == ('Example/1-Easy',)


def test_collab_lobby_override_store_can_promote_local_entry_to_shared(tmp_path: Path) -> None:
    shared = tmp_path / 'shared.toml'
    shared.write_text('lobbies = []\n', encoding='utf-8')
    local = tmp_path / '.pist/collab_lobbies.toml'
    store = CollabLobbyOverrideStore(shared, local, can_write_shared=True)
    store.save_local('Example/0-Lobbies/1-Easy', LevelSide.A, ('Example/1-Old',))

    saved = store.save_shared(
        'Example/0-Lobbies/1-Easy',
        LevelSide.A,
        ('Example/1-Easy', 'Example/1-Extra'),
    )

    assert not store.has_local_override('Example/0-Lobbies/1-Easy', LevelSide.A)
    assert load_collab_lobby_overrides(shared).campaigns_for(
        'Example/0-Lobbies/1-Easy', LevelSide.A
    ) == ('Example/1-Easy', 'Example/1-Extra')
    assert saved.campaigns_for('Example/0-Lobbies/1-Easy', LevelSide.A) == (
        'Example/1-Easy',
        'Example/1-Extra',
    )


def test_collab_lobby_override_store_rejects_shared_write_outside_source_checkout(
    tmp_path: Path,
) -> None:
    shared = tmp_path / 'shared.toml'
    shared.write_text('lobbies = []\n', encoding='utf-8')
    store = CollabLobbyOverrideStore(shared, tmp_path / 'local.toml')

    assert not store.can_write_shared
    with pytest.raises(ValueError, match='安装包模式'):
        store.save_shared('Example/0-Lobbies/1-Easy', LevelSide.A, ())


def test_collab_lobby_override_store_resets_shared_without_removing_local(
    tmp_path: Path,
) -> None:
    shared = tmp_path / 'shared.toml'
    shared.write_text(
        """
[[lobbies]]
lobby = "Example/0-Lobbies/1-Easy"
side = "A"
campaigns = ["Example/1-Shared"]
""",
        encoding='utf-8',
    )
    local = tmp_path / 'local.toml'
    store = CollabLobbyOverrideStore(shared, local, can_write_shared=True)
    store.save_local('Example/0-Lobbies/1-Easy', LevelSide.A, ('Example/1-Local',))

    restored = store.remove_shared('Example/0-Lobbies/1-Easy', LevelSide.A)

    assert not store.has_shared_override('Example/0-Lobbies/1-Easy', LevelSide.A)
    assert store.has_local_override('Example/0-Lobbies/1-Easy', LevelSide.A)
    assert restored.campaigns_for('Example/0-Lobbies/1-Easy', LevelSide.A) == ('Example/1-Local',)
