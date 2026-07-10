"""Unit tests for the pure helpers in p4v_tui.search_jobs.

`is_deleted_at_head` decides which `p4 files` rows are gone at head and
must be kept out of (or removed from) the Fast Search index. The raw
paged `files` call the indexer uses has no `-e`, so it returns
`move/delete` / `purge` / `archive` rows too — a naive `== "delete"`
test would index renamed-away old paths as live files.
"""
from __future__ import annotations

from p4v_tui.search_jobs import is_deleted_at_head


class TestIsDeletedAtHead:
    def test_plain_delete(self):
        assert is_deleted_at_head("delete") is True

    def test_move_delete_is_gone(self):
        # the old path of a rename — the dominant gone-at-head action,
        # and the one `action == "delete"` silently missed
        assert is_deleted_at_head("move/delete") is True

    def test_purge_and_archive_are_gone(self):
        assert is_deleted_at_head("purge") is True
        assert is_deleted_at_head("archive") is True

    def test_any_slash_delete_variant(self):
        # future-proofing: any `x/delete` reads as a delete
        assert is_deleted_at_head("branch/delete") is True

    def test_live_actions_are_not_gone(self):
        for live in ("add", "edit", "branch", "integrate",
                     "move/add", "import"):
            assert is_deleted_at_head(live) is False, live

    def test_move_add_is_not_a_delete(self):
        # guards against an over-broad `"delete" in action` /
        # substring rule — move/add must stay live
        assert is_deleted_at_head("move/add") is False

    def test_whitespace_and_empty(self):
        assert is_deleted_at_head("  move/delete  ") is True
        assert is_deleted_at_head("") is False
        assert is_deleted_at_head("   ") is False

    def test_none_safe(self):
        assert is_deleted_at_head(None) is False  # type: ignore[arg-type]
