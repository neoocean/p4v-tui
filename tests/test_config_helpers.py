"""Unit tests for p4v_tui.config — Swarm URL builders + TOML load/write.

Covers the pure URL helpers and the file-backed load/write round trip
(including the UTF-8 BOM tolerance documented in docs/MEMORY.md). No
Perforce server or Textual app needed; TOML I/O uses tmp_path.
"""
from __future__ import annotations

from p4v_tui.config import (
    Config,
    SwarmConfig,
    build_swarm_review_url,
    build_swarm_url,
    is_http_url,
    load_config,
    write_config,
)


class TestBuildSwarmUrl:
    def test_depot_double_slash_collapsed(self):
        assert (
            build_swarm_url("http://swarm", "//depot/foo/bar.txt", 42)
            == "http://swarm/files/depot/foo/bar.txt?v=42"
        )

    def test_trailing_slash_on_base_stripped(self):
        assert (
            build_swarm_url("http://swarm/", "//depot/a.txt")
            == "http://swarm/files/depot/a.txt"
        )

    def test_rev_zero_and_none_omit_query(self):
        assert build_swarm_url("http://s", "//d/a", 0) == "http://s/files/d/a"
        assert build_swarm_url("http://s", "//d/a", None) == "http://s/files/d/a"
        assert build_swarm_url("http://s", "//d/a", "") == "http://s/files/d/a"

    def test_string_rev_preserved(self):
        assert build_swarm_url("http://s", "//d/a", "head").endswith("?v=head")

    def test_relative_path_gets_leading_slash(self):
        assert build_swarm_url("http://s", "depot/a") == "http://s/files/depot/a"


class TestBuildSwarmReviewUrl:
    def test_changes_path(self):
        assert build_swarm_review_url("http://swarm", 12345) == "http://swarm/changes/12345"

    def test_trailing_slash_stripped(self):
        assert build_swarm_review_url("http://swarm/", "999") == "http://swarm/changes/999"


class TestIsHttpUrl:
    """Security gate (audit F3): only http/https go to the browser."""

    def test_http_and_https_ok(self):
        assert is_http_url("http://swarm/changes/1")
        assert is_http_url("https://swarm.example/changes/42")

    def test_non_http_schemes_rejected(self):
        assert not is_http_url("file:///etc/passwd")
        assert not is_http_url("javascript:alert(1)")
        assert not is_http_url("ftp://host/x")
        assert not is_http_url("data:text/html,<script>")

    def test_malformed_and_schemeless_rejected(self):
        assert not is_http_url("")
        assert not is_http_url("not a url")
        assert not is_http_url("http://")          # no netloc
        assert not is_http_url("//swarm/changes/1")  # scheme-relative


class TestLoadConfig:
    def test_missing_file_returns_empty_without_error(self, tmp_path):
        cfg = load_config(tmp_path / "nope.toml")
        assert cfg.source is None
        assert cfg.error is None
        assert cfg.swarm.base_url is None

    def test_parses_swarm_and_connection(self, tmp_path):
        p = tmp_path / "c.toml"
        p.write_text(
            '[connection]\nport = "ssl:host:1666"\nuser = "alice"\n'
            '[swarm]\nbase_url = "http://swarm"\n',
            encoding="utf-8",
        )
        cfg = load_config(p)
        assert cfg.connection.port == "ssl:host:1666"
        assert cfg.connection.user == "alice"
        assert cfg.swarm.base_url == "http://swarm"

    def test_utf8_bom_is_tolerated(self, tmp_path):
        # Perforce can prepend a BOM on sync; stdlib tomllib rejects it,
        # so load_config must strip it (docs/MEMORY.md).
        p = tmp_path / "bom.toml"
        p.write_bytes(b"\xef\xbb\xbf" + b'[swarm]\nbase_url = "http://swarm"\n')
        cfg = load_config(p)
        assert cfg.error is None
        assert cfg.swarm.base_url == "http://swarm"

    def test_malformed_toml_sets_error(self, tmp_path):
        p = tmp_path / "bad.toml"
        p.write_text("this is = = not valid", encoding="utf-8")
        cfg = load_config(p)
        assert cfg.error is not None
        assert "bad.toml" in cfg.error

    def test_profile_array_parsed(self, tmp_path):
        p = tmp_path / "p.toml"
        p.write_text(
            '[[profile]]\nname = "prod"\nport = "ssl:a:1666"\n'
            '[[profile]]\nname = "stg"\nport = "ssl:b:1666"\n',
            encoding="utf-8",
        )
        cfg = load_config(p)
        assert [pr.name for pr in cfg.profiles] == ["prod", "stg"]

    def test_profile_without_port_is_dropped(self, tmp_path):
        p = tmp_path / "p.toml"
        p.write_text('[[profile]]\nname = "noport"\n', encoding="utf-8")
        cfg = load_config(p)
        assert cfg.profiles == []


class TestWriteConfigRoundTrip:
    def test_swarm_round_trips(self, tmp_path):
        cfg = Config.empty()
        cfg.swarm = SwarmConfig(base_url="http://swarm-host")
        out = tmp_path / "written.toml"
        write_config(cfg, out)
        reloaded = load_config(out)
        assert reloaded.swarm.base_url == "http://swarm-host"


class TestJiraConfig:
    def test_default_is_empty(self, tmp_path):
        cfg = load_config(tmp_path / "nope.toml")
        assert cfg.jira.base_url is None
        assert cfg.jira.projects == []

    def test_parsed(self, tmp_path):
        p = tmp_path / "j.toml"
        p.write_text(
            '[jira]\nbase_url = "https://jira.example"\n'
            'projects = ["ABC", "XY"]\n',
            encoding="utf-8",
        )
        cfg = load_config(p)
        assert cfg.jira.base_url == "https://jira.example"
        assert cfg.jira.projects == ["ABC", "XY"]

    def test_round_trips(self, tmp_path):
        from p4v_tui.config import JiraConfig
        cfg = Config.empty()
        cfg.jira = JiraConfig(base_url="https://jira.example", projects=["ABC"])
        out = tmp_path / "w.toml"
        write_config(cfg, out)
        reloaded = load_config(out)
        assert reloaded.jira.base_url == "https://jira.example"
        assert reloaded.jira.projects == ["ABC"]

    def test_path_projects_parsed(self, tmp_path):
        p = tmp_path / "j.toml"
        p.write_text(
            '[jira]\nbase_url = "https://example.atlassian.net"\n'
            '[jira.path_projects]\n'
            '"//depot/alpha/" = "alpha"\n'
            '"//depot/gamma/" = "gamma"\n',
            encoding="utf-8",
        )
        cfg = load_config(p)
        assert cfg.jira.path_projects == {
            "//depot/alpha/": "alpha", "//depot/gamma/": "gamma",
        }

    def test_path_projects_round_trips(self, tmp_path):
        from p4v_tui.config import JiraConfig
        cfg = Config.empty()
        cfg.jira = JiraConfig(
            base_url="https://example.atlassian.net",
            path_projects={"//depot/beta/": "beta"},
        )
        out = tmp_path / "w.toml"
        write_config(cfg, out)
        assert load_config(out).jira.path_projects == {"//depot/beta/": "beta"}
