"""Unit tests for p4v_tui.permalink — grammar + JSON registry."""
from __future__ import annotations

from p4v_tui.permalink import (
    PermalinkRegistry,
    make_permalink,
    parse_permalink,
)


class TestGrammar:
    def test_make(self):
        assert make_permalink(98765) == "//@p/98765"

    def test_parse_valid(self):
        assert parse_permalink("//@p/98765") == "98765"
        assert parse_permalink("  //@p/12  ") == "12"

    def test_parse_legacy_v_prefix_accepted(self):
        # Addresses copied before the rename (//@v/) still resolve.
        assert parse_permalink("//@v/55") == "55"

    def test_parse_invalid(self):
        assert parse_permalink("//depot/foo") is None
        assert parse_permalink("//@p/abc") is None
        assert parse_permalink("") is None
        assert parse_permalink("//@p/") is None


class TestRegistry:
    def test_register_returns_id_and_persists(self, tmp_path):
        reg = PermalinkRegistry(tmp_path / "p.json")
        pid = reg.register("//d/foo/bar.txt")
        assert reg.lookup(pid) == "//d/foo/bar.txt"
        reg2 = PermalinkRegistry(tmp_path / "p.json")
        assert reg2.lookup(pid) == "//d/foo/bar.txt"

    def test_idempotent_per_path(self, tmp_path):
        reg = PermalinkRegistry(tmp_path / "p.json")
        assert reg.register("//d/x") == reg.register("//d/x")

    def test_distinct_paths_distinct_ids(self, tmp_path):
        reg = PermalinkRegistry(tmp_path / "p.json")
        assert reg.register("//d/a") != reg.register("//d/b")

    def test_lookup_unknown(self, tmp_path):
        reg = PermalinkRegistry(tmp_path / "p.json")
        assert reg.lookup(424242) is None

    def test_missing_file_starts_empty(self, tmp_path):
        reg = PermalinkRegistry(tmp_path / "does-not-exist.json")
        assert reg.lookup(1000) is None

    def test_corrupt_file_tolerated(self, tmp_path):
        p = tmp_path / "p.json"
        p.write_text("{ this is not json", encoding="utf-8")
        reg = PermalinkRegistry(p)  # must not raise
        pid = reg.register("//d/ok")
        assert reg.lookup(pid) == "//d/ok"

    def test_round_trip_through_address(self, tmp_path):
        reg = PermalinkRegistry(tmp_path / "p.json")
        pid = reg.register("//d/path")
        parsed = parse_permalink(make_permalink(pid))
        assert reg.lookup(parsed) == "//d/path"

    def test_after_write_hook_fires_with_path(self, tmp_path):
        seen = []
        reg = PermalinkRegistry(
            tmp_path / "p.json", after_write=lambda p: seen.append(p),
        )
        reg.register("//d/a")
        assert seen == [tmp_path / "p.json"]

    def test_after_write_hook_error_is_swallowed(self, tmp_path):
        def boom(_p):
            raise RuntimeError("p4 down")
        reg = PermalinkRegistry(tmp_path / "p.json", after_write=boom)
        # Save must still succeed even if tracking raises.
        pid = reg.register("//d/a")
        assert reg.lookup(pid) == "//d/a"

    def test_atomic_write_leaves_no_tmp_residue(self, tmp_path):
        import json
        reg = PermalinkRegistry(tmp_path / "p.json")
        reg.register("//d/a")
        reg.register("//d/b")
        # No leftover .tmp from the temp-file-then-rename write.
        assert list(tmp_path.glob("*.tmp")) == []
        # And the committed file is complete/valid JSON.
        data = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))
        assert set(data["map"].values()) == {"//d/a", "//d/b"}
