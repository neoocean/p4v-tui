"""Unit tests for the Branch Files (populate) argv builder + preview parse."""
from __future__ import annotations

import pytest

from p4v_tui.branch_files import build_populate_args, parse_populate_preview


# --- pair mode ----------------------------------------------------------

def test_pair_mode_basic():
    args = build_populate_args(source="//d/main/...", target="//d/rel/...",
                               description="branch rel")
    assert args == ("populate", "-d", "branch rel",
                    "//d/main/...", "//d/rel/...")


def test_pair_mode_dry_run_inserts_n_first():
    args = build_populate_args(source="//d/a/...", target="//d/b/...",
                               description="x", dry_run=True)
    assert args[0] == "populate" and args[1] == "-n"
    assert args[-2:] == ("//d/a/...", "//d/b/...")


def test_pair_mode_requires_both_paths():
    with pytest.raises(ValueError):
        build_populate_args(source="//d/a/...", target="", description="x")
    with pytest.raises(ValueError):
        build_populate_args(source="", target="//d/b/...", description="x")


# --- branch-mapping mode ------------------------------------------------

def test_branch_mode_no_target():
    args = build_populate_args(branch="rel-branch", description="cut release")
    assert args == ("populate", "-d", "cut release", "-b", "rel-branch")


def test_branch_mode_with_optional_target_restriction():
    args = build_populate_args(branch="rel-branch", target="//d/rel/sub/...",
                               description="d")
    assert args == ("populate", "-d", "d", "-b", "rel-branch",
                    "//d/rel/sub/...")


def test_branch_mode_dry_run():
    args = build_populate_args(branch="b1", description="d", dry_run=True)
    assert args[:2] == ("populate", "-n")
    assert "-b" in args and "b1" in args


def test_branch_mode_ignores_source():
    # Source is meaningless in mapping mode; builder must not require it.
    args = build_populate_args(branch="b", source="ignored", description="d")
    assert "ignored" not in args


def test_no_description_omits_d_flag():
    args = build_populate_args(branch="b")
    assert "-d" not in args


# --- preview parse ------------------------------------------------------

def test_parse_preview_collects_depot_files():
    rows = [
        {"depotFile": "//d/rel/a.py", "action": "branch", "rev": "1"},
        {"depotFile": "//d/rel/b.py", "action": "branch", "rev": "1"},
        "noise",
        {"action": "branch"},  # no path → skipped
    ]
    assert parse_populate_preview(rows) == ["//d/rel/a.py", "//d/rel/b.py"]


def test_parse_preview_falls_back_to_tofile():
    rows = [{"toFile": "//d/rel/c.py", "action": "branch"}]
    assert parse_populate_preview(rows) == ["//d/rel/c.py"]


def test_parse_preview_empty():
    assert parse_populate_preview([]) == []
    assert parse_populate_preview([{"code": "info", "data": "nothing"}]) == []
