"""Tests for the connection-profile editor: label formatting + the
write_config → load_config round trip that the Save path relies on."""
from __future__ import annotations

from p4v_tui.config import (
    Config, ConnectionConfig, SwarmConfig, load_config, write_config,
)
from p4v_tui.widgets.preferences_modal import PreferencesModal


def test_profile_label_named_with_detail():
    p = ConnectionConfig(name="Prod", port="ssl:h:1666", user="me",
                         client="ws1")
    label = PreferencesModal._profile_label(p)
    assert label.startswith("Prod")
    assert "ssl:h:1666" in label and "me" in label and "ws1" in label


def test_profile_label_unnamed_and_empty():
    assert PreferencesModal._profile_label(ConnectionConfig()) == "(unnamed)"
    p = ConnectionConfig(port="p:1")
    assert PreferencesModal._profile_label(p) == "(unnamed)  (p:1)"


def test_profiles_round_trip_through_config(tmp_path):
    cfg = Config(
        connection=ConnectionConfig(port="ssl:main:1666"),
        profiles=[
            ConnectionConfig(name="A", port="ssl:a:1666", user="alice"),
            ConnectionConfig(name="B", port="ssl:b:1666", client="ws-b"),
        ],
        swarm=SwarmConfig(base_url=""),
        chunking=None,
        source=None,
    )
    target = tmp_path / "p4v-tui.toml"
    write_config(cfg, target)

    loaded = load_config(explicit_path=str(target))
    assert len(loaded.profiles) == 2
    assert loaded.profiles[0].name == "A"
    assert loaded.profiles[0].user == "alice"
    assert loaded.profiles[1].name == "B"
    assert loaded.profiles[1].client == "ws-b"


def test_deleting_then_writing_drops_profile(tmp_path):
    cfg = Config(
        connection=ConnectionConfig(port="ssl:main:1666"),
        profiles=[
            ConnectionConfig(name="A", port="ssl:a:1666"),
            ConnectionConfig(name="B", port="ssl:b:1666"),
        ],
        swarm=None, chunking=None, source=None,
    )
    # Simulate the modal's working-copy delete of index 0.
    cfg.profiles.pop(0)
    target = tmp_path / "p4v-tui.toml"
    write_config(cfg, target)
    loaded = load_config(explicit_path=str(target))
    assert [p.name for p in loaded.profiles] == ["B"]
