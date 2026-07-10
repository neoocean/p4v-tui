"""Unit tests for the workspace-tree status marker (`_status_marker`).

Pure function over one `p4 fstat` row dict. Pins the gone-at-head marker
fix: a file whose head action is `move/delete` (the old path of a rename)
/ `purge` / `archive` must show the stale "x", not a clean blank — the
old `head_action == "delete"` check missed the compound verbs.
"""
from __future__ import annotations

from p4v_tui.widgets.workspace_tree import _status_marker


def test_open_action_wins():
    assert _status_marker({"action": "edit"}) == "e"
    assert _status_marker({"action": "move/add"}) == "+"
    assert _status_marker({"action": "weird/new"}) == "?"  # unknown -> ?


def test_not_synced_returns_dot():
    assert _status_marker({"headRev": "3"}) == "·"  # no haveRev


def test_out_of_date_returns_star():
    assert _status_marker({"haveRev": "1", "headRev": "3"}) == "*"


def test_synced_clean_returns_blank():
    assert _status_marker(
        {"haveRev": "3", "headRev": "3", "headAction": "edit"}
    ) == " "


def test_plain_delete_at_head_marks_stale():
    assert _status_marker(
        {"haveRev": "3", "headRev": "3", "headAction": "delete"}
    ) == "x"


def test_move_delete_at_head_marks_stale():
    # the fix: a renamed-away path is gone at head and must read "x"
    assert _status_marker(
        {"haveRev": "5", "headRev": "5", "headAction": "move/delete"}
    ) == "x"


def test_purge_and_archive_at_head_mark_stale():
    for act in ("purge", "archive"):
        assert _status_marker(
            {"haveRev": "2", "headRev": "2", "headAction": act}
        ) == "x", act
