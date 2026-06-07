"""Unit tests for permalink move-following parsers in p4v_tui.app.

``_resolve_moved_path`` follows ``p4`` rename history so a permalink keeps
pointing at a file after it's moved. The two parser helpers it relies on
(:meth:`P4VApp._head_action`, :meth:`P4VApp._find_moved_into`) must read
filelog records from *both* backends, whose shapes differ:

* **P4Python** returns ``action`` / ``how`` / ``file`` as per-revision
  lists (``how`` and ``file`` are list-of-lists for integration data).
* **CLI ``-G``** emits flat numbered keys (``action0``, ``how1,0``,
  ``file1,0``).

The fixtures below are the *real* records captured from a live ``p4 move``
(CL 56812 probe: ``orig.txt`` → ``renamed.txt``). The non-obvious bit they
pin: the ``moved into`` integration sits on the revision *below* the
``move/delete`` head, so a head-only fetch would miss it.
"""
from __future__ import annotations

import types

from p4v_tui.app import P4VApp

RENAMED = "//depot/p4v-tui/tests/_probe_move/renamed.txt"
ORIG = "//depot/p4v-tui/tests/_probe_move/orig.txt"

# --- real captured filelog records (-m 2) ----------------------------------

# P4Python: lists indexed by revision (head first); how/file are
# list-of-lists carrying the integration records.
PYTHON_REC = {
    "action": ["move/delete", "add"],
    "how": [None, ["moved into"]],
    "file": [None, [RENAMED]],
    "rev": ["2", "1"],
    "change": ["56812", "56811"],
}

# CLI -G: flat numbered keys. The move target lives on revision row 1.
CLI_REC = {
    "action0": "move/delete",
    "action1": "add",
    "how1,0": "moved into",
    "file1,0": RENAMED,
    "rev0": "2",
    "rev1": "1",
}

# A file that was never moved (head action is a plain edit).
PYTHON_PLAIN = {"action": ["edit", "add"], "how": [None, None]}
CLI_PLAIN = {"action0": "edit", "action1": "add"}


class TestHeadAction:
    def test_python_list_shape(self):
        assert P4VApp._head_action(PYTHON_REC) == "move/delete"

    def test_cli_flat_shape(self):
        assert P4VApp._head_action(CLI_REC) == "move/delete"

    def test_plain_edit(self):
        assert P4VApp._head_action(PYTHON_PLAIN) == "edit"
        assert P4VApp._head_action(CLI_PLAIN) == "edit"

    def test_empty_record(self):
        assert P4VApp._head_action({}) == ""

    def test_string_action_defensive(self):
        assert P4VApp._head_action({"action": "delete"}) == "delete"

    def test_empty_action_list(self):
        assert P4VApp._head_action({"action": []}) == ""


class TestFindMovedInto:
    def test_python_shape(self):
        assert P4VApp._find_moved_into(PYTHON_REC) == RENAMED

    def test_cli_shape(self):
        assert P4VApp._find_moved_into(CLI_REC) == RENAMED

    def test_no_move_python(self):
        assert P4VApp._find_moved_into(PYTHON_PLAIN) is None

    def test_no_move_cli(self):
        assert P4VApp._find_moved_into(CLI_PLAIN) is None

    def test_empty_record(self):
        assert P4VApp._find_moved_into({}) is None

    def test_cli_picks_newest_move_by_rev_index(self):
        # Two 'moved into' records: row 1 (newest) must win over row 3,
        # regardless of dict order.
        rec = {
            "how3,0": "moved into", "file3,0": "//d/old_target.txt",
            "how1,0": "moved into", "file1,0": "//d/new_target.txt",
        }
        assert P4VApp._find_moved_into(rec) == "//d/new_target.txt"

    def test_python_picks_newest_move_by_index(self):
        rec = {
            "how": [None, ["moved into"], ["branch from"], ["moved into"]],
            "file": [None, ["//d/new.txt"], ["//d/b"], ["//d/old.txt"]],
        }
        assert P4VApp._find_moved_into(rec) == "//d/new.txt"

    def test_python_branch_only_is_not_a_move(self):
        rec = {"how": [None, ["branch from"]], "file": [None, ["//d/src"]]}
        assert P4VApp._find_moved_into(rec) is None


class _FakeP4:
    """Minimal p4 stub: maps a depot path to its captured filelog record."""

    def __init__(self, table):
        self._table = table

    def run(self, *args):
        # args like ("filelog", "-m", "2", <path>)
        path = args[-1]
        rec = self._table.get(path)
        return [rec] if rec is not None else []


def _app_with(table):
    app = types.SimpleNamespace(p4=_FakeP4(table))
    app._head_action = P4VApp._head_action
    app._find_moved_into = P4VApp._find_moved_into
    app._resolve_moved_path = types.MethodType(
        P4VApp._resolve_moved_path, app,
    )
    return app


class TestResolveMovedPath:
    def test_single_move_python(self):
        app = _app_with({ORIG: PYTHON_REC})
        assert app._resolve_moved_path(ORIG) == RENAMED

    def test_single_move_cli(self):
        app = _app_with({ORIG: CLI_REC})
        assert app._resolve_moved_path(ORIG) == RENAMED

    def test_unmoved_returns_origin(self):
        app = _app_with({ORIG: PYTHON_PLAIN})
        assert app._resolve_moved_path(ORIG) == ORIG

    def test_unknown_path_returns_origin(self):
        app = _app_with({})  # filelog returns []
        assert app._resolve_moved_path(ORIG) == ORIG

    def test_follows_multi_hop_chain(self):
        a, b, c = "//d/a.txt", "//d/b.txt", "//d/c.txt"
        table = {
            a: {"action": ["move/delete"], "how": [["moved into"]],
                "file": [[b]]},
            b: {"action": ["move/delete"], "how": [["moved into"]],
                "file": [[c]]},
            c: {"action": ["edit"], "how": [None]},
        }
        assert _app_with(table)._resolve_moved_path(a) == c

    def test_cycle_is_broken(self):
        # Pathological: a -> b -> a. Must terminate, not loop forever.
        a, b = "//d/a.txt", "//d/b.txt"
        table = {
            a: {"action": ["move/delete"], "how": [["moved into"]],
                "file": [[b]]},
            b: {"action": ["move/delete"], "how": [["moved into"]],
                "file": [[a]]},
        }
        result = _app_with(table)._resolve_moved_path(a)
        assert result in (a, b)  # terminates at one of them
