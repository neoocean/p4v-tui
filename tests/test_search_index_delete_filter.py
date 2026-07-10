"""DB-backed regression: gone-at-head rows must not surface in search.

A naive ``head_action != 'delete'`` filter (and the matching
``action == "delete"`` ingest filter) silently kept ``move/delete`` —
the *old* path of a rename, the dominant gone-at-head action on a busy
depot — plus ``purge`` / ``archive``. These would then show up as live
Fast Search results. This pins all three query methods against a small
real SQLite index seeded with one of each action.
"""
from __future__ import annotations

import pytest

from p4v_tui.search_index import SearchIndex

# depot_path leaf shares the token "widget" so one substring query hits
# every seeded row; only the live one should come back.
SEED = [
    {"depotFile": "//depot/app/widget_live.py",   "action": "edit",
     "user": "alice", "type": "text", "time": 1000, "change": 5},
    {"depotFile": "//depot/app/widget_del.py",    "action": "delete",
     "user": "alice", "type": "text", "time": 1001, "change": 6},
    {"depotFile": "//depot/app/widget_moved.py",  "action": "move/delete",
     "user": "alice", "type": "text", "time": 1002, "change": 7},
    {"depotFile": "//depot/app/widget_purged.py", "action": "purge",
     "user": "alice", "type": "text", "time": 1003, "change": 8},
    {"depotFile": "//depot/app/widget_arch.py",   "action": "archive",
     "user": "alice", "type": "text", "time": 1004, "change": 9},
]

LIVE = "//depot/app/widget_live.py"
GONE = {
    "//depot/app/widget_del.py",
    "//depot/app/widget_moved.py",
    "//depot/app/widget_purged.py",
    "//depot/app/widget_arch.py",
}


@pytest.fixture()
def idx(tmp_path):
    ix = SearchIndex(tmp_path / "search.db")
    ix.open()
    ix.upsert_files(SEED)
    yield ix
    ix.close()


def _paths(hits):
    return {h.depot_path for h in hits}


def test_query_files_excludes_gone_at_head(idx):
    paths = _paths(idx.query_files("widget"))
    assert LIVE in paths
    assert not (paths & GONE), f"dead paths surfaced: {paths & GONE}"


def test_query_files_filtered_excludes_gone_at_head(idx):
    paths = _paths(idx.query_files_filtered(substr="widget"))
    assert LIVE in paths
    assert not (paths & GONE), f"dead paths surfaced: {paths & GONE}"


def test_query_files_loose_excludes_gone_at_head(idx):
    paths = _paths(idx.query_files_loose("widget"))
    assert LIVE in paths
    assert not (paths & GONE), f"dead paths surfaced: {paths & GONE}"


def test_filtered_by_user_alone_still_excludes_gone(idx):
    # the "@user:alice" path (no substring) is the one that can spill
    # tens of thousands of rows on a real depot — make sure it filters
    paths = _paths(idx.query_files_filtered(user="alice"))
    assert LIVE in paths
    assert not (paths & GONE)
