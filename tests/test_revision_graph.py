"""Unit tests for the pure logic behind the Revision Graph view.

Covers the deterministic, backend-shaped pieces of
``p4v_tui/widgets/revision_graph_modal.py`` — the
parallel-array → per-revision collapse (``_extract_revs`` + the
``_idx`` / ``_idx_list`` ragged-array helpers), the ``srev..erev`` span
formatter (``_format_rev_span``) and the ``↙`` / ``↗`` edge-arrow rule
(``_edge_arrow``). Rendering itself needs a RichLog / running app and is
left to the manual checks in ``docs/handoff-manual-tests.md``; design +
rationale live in ``docs/revision-graph-scenario.md``.
"""
from __future__ import annotations

from p4v_tui.widgets.revision_graph_modal import (
    RevisionGraphModal,
    _idx,
    _idx_list,
)

extract = RevisionGraphModal._extract_revs
span = RevisionGraphModal._format_rev_span
arrow = RevisionGraphModal._edge_arrow


# --- _idx (scalar field) -------------------------------------------------

def test_idx_in_range():
    d = {"user": ["alice", "bob"]}
    assert _idx(d, "user", 0) == "alice"
    assert _idx(d, "user", 1) == "bob"


def test_idx_past_end_is_empty_string():
    assert _idx({"user": ["alice"]}, "user", 5) == ""


def test_idx_missing_key_is_empty_string():
    assert _idx({}, "user", 0) == ""


def test_idx_none_value_is_empty_string():
    # `d.get(key) or []` collapses an explicit None to []
    assert _idx({"user": None}, "user", 0) == ""


# --- _idx_list (nested integration field) --------------------------------

def test_idx_list_returns_inner_list():
    d = {"how": [["branch from", "merge from"], ["copy into"]]}
    assert _idx_list(d, "how", 0) == ["branch from", "merge from"]
    assert _idx_list(d, "how", 1) == ["copy into"]


def test_idx_list_wraps_lone_scalar():
    # some backends/servers return a single edge as a bare string,
    # not a 1-element list — normalise to a list[str]
    d = {"how": ["branch from", "merge from"]}
    assert _idx_list(d, "how", 0) == ["branch from"]
    assert _idx_list(d, "how", 1) == ["merge from"]


def test_idx_list_past_end_is_empty_list():
    assert _idx_list({"how": [["x"]]}, "how", 9) == []


def test_idx_list_missing_key_is_empty_list():
    assert _idx_list({}, "how", 0) == []


def test_idx_list_none_entry_is_empty_list():
    assert _idx_list({"how": [None]}, "how", 0) == []


def test_idx_list_coerces_non_str_elements():
    d = {"srev": [[5, 7]]}
    assert _idx_list(d, "srev", 0) == ["5", "7"]


# --- _edge_arrow (↙ incoming / ↗ outgoing) -------------------------------

def test_arrow_incoming_from_edges():
    # real filelog `how` strings (verified against the live server)
    assert arrow("branch from") == "↙"
    assert arrow("merge from") == "↙"
    assert arrow("copy from") == "↙"
    assert arrow("moved from") == "↙"


def test_arrow_outgoing_into_edges():
    # these are two-word strings with NO trailing space; the old
    # ' into ' (space-padded) test never matched them and drew ↙
    assert arrow("branch into") == "↗"
    assert arrow("merge into") == "↗"
    assert arrow("copy into") == "↗"
    assert arrow("moved into") == "↗"


def test_arrow_oddballs_default_incoming():
    # non-into/from relations fall through to ↙
    assert arrow("ignored") == "↙"
    assert arrow("ignored by") == "↙"
    assert arrow("undid") == "↙"
    assert arrow("undone by") == "↙"


def test_arrow_matches_token_not_substring():
    # a word merely *containing* "into" must not flip the arrow
    assert arrow("reintonate") == "↙"


def test_arrow_handles_non_string():
    assert arrow(None) == "↙"


# --- _format_rev_span ----------------------------------------------------

def test_span_empty_when_both_blank():
    assert span("", "") == ""
    assert span(None, None) == ""


def test_span_single_when_equal():
    assert span("5", "5") == "#5"


def test_span_single_when_only_srev():
    assert span("5", "") == "#5"


def test_span_single_when_only_erev():
    assert span("", "10") == "#10"


def test_span_range_when_distinct():
    # one leading '#', then 'srev..erev' (matches the module docstring's
    # `#7..10`) — NOT '#7..#10'
    assert span("7", "10") == "#7..10"


def test_span_strips_incoming_hash_prefix():
    # a backend that already hands back "#5" must not become "##5"
    assert span("#5", "#5") == "#5"
    assert span("#7", "#10") == "#7..10"


def test_span_coerces_ints():
    assert span(7, 10) == "#7..10"
    assert span(5, 5) == "#5"


# --- _extract_revs (parallel arrays → per-revision dicts) ----------------

def test_extract_basic_transpose():
    head = {
        "rev":    ["2", "1"],
        "change": ["4790", "4602"],
        "user":   ["alice", "alice"],
        "action": ["integrate", "branch"],
        "type":   ["text", "text"],
        "time":   ["1747555200", "1746147600"],
        "desc":   ["Merge bugfixes", "Branch release"],
        "how":    [["merge from"], ["branch from"]],
        "file":   [["//depot/dev/foo.cpp"], ["//depot/main/foo.cpp"]],
        "srev":   [["8"], ["42"]],
        "erev":   [["11"], ["42"]],
    }
    out = extract(head)
    assert len(out) == 2
    first = out[0]
    assert first["rev"] == "2"
    assert first["change"] == "4790"
    assert first["action"] == "integrate"
    assert first["how"] == ["merge from"]
    assert first["file"] == ["//depot/dev/foo.cpp"]
    assert first["srev"] == ["8"]
    assert first["erev"] == ["11"]
    # second revision's branch edge survives independently
    assert out[1]["how"] == ["branch from"]
    assert out[1]["file"] == ["//depot/main/foo.cpp"]


def test_extract_coerces_rev_to_str():
    head = {"rev": [3, 2, 1]}
    out = extract(head)
    assert [r["rev"] for r in out] == ["3", "2", "1"]


def test_extract_no_revs_is_empty():
    assert extract({}) == []
    assert extract({"rev": []}) == []


def test_extract_revision_without_edges():
    # a plain edit has no integration arrays at all
    head = {
        "rev":    ["1"],
        "change": ["100"],
        "action": ["add"],
    }
    out = extract(head)
    assert len(out) == 1
    assert out[0]["how"] == []
    assert out[0]["file"] == []
    assert out[0]["srev"] == []
    assert out[0]["erev"] == []


def test_extract_tolerates_ragged_scalar_edges():
    # edges delivered as bare scalars (not nested lists) still collapse
    head = {
        "rev": ["1"],
        "how": ["branch from"],
        "file": ["//depot/main/foo.cpp"],
        "srev": ["5"],
        "erev": ["5"],
    }
    out = extract(head)
    assert out[0]["how"] == ["branch from"]
    assert out[0]["file"] == ["//depot/main/foo.cpp"]
    assert out[0]["srev"] == ["5"]


def test_extract_missing_trailing_columns_padded():
    # `how` shorter than `rev`: revisions past the array get empty edges,
    # scalar fields past their array get ""
    head = {
        "rev":    ["3", "2", "1"],
        "change": ["30", "20"],          # missing the 3rd
        "how":    [["merge from"]],       # only the 1st rev has edges
    }
    out = extract(head)
    assert [r["change"] for r in out] == ["30", "20", ""]
    assert out[0]["how"] == ["merge from"]
    assert out[1]["how"] == []
    assert out[2]["how"] == []
