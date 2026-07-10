"""DB-backed tests for the one-time gone-at-head purge migration.

Indexes built before the ``move/delete`` ingest fix carry dead rows
(``move/delete`` / ``purge`` / ``archive``) as if live. `purge_gone_at_head`
evicts them once per index, guarded by a meta flag, and the ``files_ad``
trigger keeps the FTS mirror in sync so post-purge queries are clean too.
"""
from __future__ import annotations

import pytest

from p4v_tui.search_index import SearchIndex

SEED = [
    {"depotFile": "//depot/app/live.py",    "action": "edit",
     "user": "a", "type": "text", "time": 10, "change": 1},
    {"depotFile": "//depot/app/added.py",   "action": "add",
     "user": "a", "type": "text", "time": 11, "change": 2},
    {"depotFile": "//depot/app/del.py",     "action": "delete",
     "user": "a", "type": "text", "time": 12, "change": 3},
    {"depotFile": "//depot/app/moved.py",   "action": "move/delete",
     "user": "a", "type": "text", "time": 13, "change": 4},
    {"depotFile": "//depot/app/purged.py",  "action": "purge",
     "user": "a", "type": "text", "time": 14, "change": 5},
    {"depotFile": "//depot/app/arch.py",    "action": "archive",
     "user": "a", "type": "text", "time": 15, "change": 6},
    {"depotFile": "//depot/app/madd.py",    "action": "move/add",
     "user": "a", "type": "text", "time": 16, "change": 7},
]
SURVIVORS = {
    "//depot/app/live.py", "//depot/app/added.py", "//depot/app/madd.py",
}
DEAD = {
    "//depot/app/del.py", "//depot/app/moved.py",
    "//depot/app/purged.py", "//depot/app/arch.py",
}


@pytest.fixture()
def idx(tmp_path):
    ix = SearchIndex(tmp_path / "search.db")
    ix.open()
    ix.upsert_files(SEED)
    yield ix
    ix.close()


def _all_paths(ix):
    return {
        r[0] for r in ix._conn.execute(
            "SELECT depot_path FROM files"
        ).fetchall()
    }


def test_purge_removes_only_dead_rows(idx):
    deleted = idx.purge_gone_at_head()
    assert deleted == len(DEAD)
    remaining = _all_paths(idx)
    assert remaining == SURVIVORS
    assert not (remaining & DEAD)


def test_move_add_survives_purge(idx):
    # move/add is a LIVE creation — must not be swept with move/delete
    idx.purge_gone_at_head()
    assert "//depot/app/madd.py" in _all_paths(idx)


def test_purge_sets_flag_and_is_idempotent(idx):
    assert idx.purge_gone_at_head() == len(DEAD)
    assert idx.get_meta(SearchIndex._PURGE_FLAG) == "1"
    # second call scans nothing and deletes nothing
    assert idx.purge_gone_at_head() == 0


def test_purge_keeps_null_action_rows(idx):
    # a row with no head_action is unknown, not dead — keep it
    idx.upsert_files([{"depotFile": "//depot/app/noact.py"}])
    idx.purge_gone_at_head()
    assert "//depot/app/noact.py" in _all_paths(idx)


def test_purge_syncs_fts_mirror(idx):
    # the ``files_ad`` trigger must evict the dead rows from the FTS
    # virtual table too, or a stale FTS rowid would dangle. Assert the
    # mirror directly (the LIKE-based query methods read ``files``, so
    # only a direct FTS read proves the trigger fired).
    fts_before = {
        r[0] for r in idx._conn.execute(
            "SELECT depot_path FROM files_fts"
        ).fetchall()
    }
    assert DEAD <= fts_before          # dead paths start out mirrored
    idx.purge_gone_at_head()
    fts_after = {
        r[0] for r in idx._conn.execute(
            "SELECT depot_path FROM files_fts"
        ).fetchall()
    }
    assert fts_after == SURVIVORS
    assert not (fts_after & DEAD)


def test_purge_on_empty_index_is_noop(tmp_path):
    ix = SearchIndex(tmp_path / "empty.db")
    ix.open()
    assert ix.purge_gone_at_head() == 0
    assert ix.get_meta(SearchIndex._PURGE_FLAG) == "1"
    ix.close()
