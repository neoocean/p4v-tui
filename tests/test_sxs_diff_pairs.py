"""Unit test for SideBySideDiffModal.for_cl pair-building.

The pure pair logic (which files get a ``rev-1`` left side vs an empty
one) is exercised without a running Textual app — ``for_cl`` only builds
specs and constructs the modal. Pins the move/add edge case fixed
alongside ``utils.is_creation_action``: a rename destination has no real
predecessor under its path, even at rev > 1 (resurrected path), so its
left side must be empty rather than the delete tombstone at ``rev-1``.
"""
from __future__ import annotations

from p4v_tui.widgets.sxs_diff_modal import SideBySideDiffModal


def _pairs(files):
    m = SideBySideDiffModal.for_cl("123", files, p4_service=None)
    # (left_spec, right_spec, label) per file, in order
    return m._pairs


def test_edit_gets_rev_minus_one_left():
    (left, right, _), = _pairs([("//d/a.py", 3, "edit")])
    assert left == "//d/a.py#2"
    assert right == "//d/a.py#3"


def test_add_and_rev1_have_empty_left():
    pairs = _pairs([
        ("//d/new.py", 1, "add"),
        ("//d/br.py", 1, "branch"),
    ])
    assert all(p[0] == "" for p in pairs)


def test_move_add_at_high_rev_has_empty_left():
    # the fix: move/add onto a resurrected path lands at rev > 1, but
    # rev-1 is only a delete tombstone — not a predecessor of the
    # moved-in content. Must be empty, not "//d/c.py#3".
    (left, right, _), = _pairs([("//d/c.py", 4, "move/add")])
    assert left == ""
    assert right == "//d/c.py#4"


def test_move_delete_keeps_its_predecessor():
    # the rename SOURCE is a real edit history — it DOES have a rev-1
    # and must not be swept up with the creations.
    (left, _, _), = _pairs([("//d/d.py", 2, "move/delete")])
    assert left == "//d/d.py#1"
