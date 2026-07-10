"""Unit tests for the Pending/Submitted CL table filter+sort view model."""
from __future__ import annotations

from datetime import datetime

from p4v_tui.cl_table_filter import CLTableView, apply_view


def _epoch(date_str: str) -> str:
    return str(int(datetime.strptime(date_str, "%Y-%m-%d").timestamp()))


ROWS = [
    {"change": "100", "user": "alice", "time": _epoch("2026-01-10"),
     "desc": "WIP: refactor sync", "client": "alice-laptop"},
    {"change": "102", "user": "bob", "time": _epoch("2026-02-15"),
     "desc": "hotfix submit guard", "client": "bob-desktop"},
    {"change": "101", "user": "alice", "time": _epoch("2026-03-20"),
     "desc": "add image preview", "client": "alice-desktop"},
]


# --- default view is inert ----------------------------------------------

def test_default_view_is_noop():
    v = CLTableView()
    assert not v.is_active()
    assert not v.has_filter()
    out = apply_view(ROWS, v)
    assert [r["change"] for r in out] == ["100", "102", "101"]  # input order


# --- sorting ------------------------------------------------------------

def test_sort_by_change_desc():
    out = apply_view(ROWS, CLTableView(sort_key="change", descending=True))
    assert [r["change"] for r in out] == ["102", "101", "100"]


def test_sort_by_change_asc():
    out = apply_view(ROWS, CLTableView(sort_key="change", descending=False))
    assert [r["change"] for r in out] == ["100", "101", "102"]


def test_sort_by_user_then_date():
    out = apply_view(ROWS, CLTableView(sort_key="user", descending=False))
    assert [r["user"] for r in out][0] == "alice"


def test_sort_by_date():
    out = apply_view(ROWS, CLTableView(sort_key="date", descending=False))
    assert [r["change"] for r in out] == ["100", "102", "101"]


# --- filtering ----------------------------------------------------------

def test_filter_user_substring_ci():
    out = apply_view(ROWS, CLTableView(user="ALI"))
    assert {r["user"] for r in out} == {"alice"}
    assert len(out) == 2


def test_filter_workspace():
    out = apply_view(ROWS, CLTableView(workspace="desktop"))
    assert {r["change"] for r in out} == {"102", "101"}


def test_filter_text_ci():
    out = apply_view(ROWS, CLTableView(text="HOTFIX"))
    assert [r["change"] for r in out] == ["102"]


def test_filter_regex():
    out = apply_view(ROWS, CLTableView(regex=r"^(WIP|hotfix)"))
    assert {r["change"] for r in out} == {"100", "102"}


def test_invalid_regex_is_ignored_not_emptying():
    out = apply_view(ROWS, CLTableView(regex="("))  # invalid
    assert len(out) == len(ROWS)


def test_filter_date_range_inclusive():
    out = apply_view(
        ROWS, CLTableView(date_from="2026-02-01", date_to="2026-02-28"),
    )
    assert [r["change"] for r in out] == ["102"]


def test_filter_date_from_only():
    out = apply_view(ROWS, CLTableView(date_from="2026-03-01"))
    assert [r["change"] for r in out] == ["101"]


def test_combined_filter_and_sort():
    v = CLTableView(user="alice", sort_key="change", descending=True)
    out = apply_view(ROWS, v)
    assert [r["change"] for r in out] == ["101", "100"]


# --- persistence round trip ---------------------------------------------

def test_to_from_dict_round_trip():
    v = CLTableView(sort_key="user", descending=False, user="x", regex="y")
    v2 = CLTableView.from_dict(v.to_dict())
    assert v2 == v


def test_from_dict_tolerates_garbage():
    assert CLTableView.from_dict(None) == CLTableView()
    assert CLTableView.from_dict({"sort_key": "bogus"}).sort_key == "default"
    assert CLTableView.from_dict({"descending": 0}).descending is False


def test_summary_mentions_active_bits():
    v = CLTableView(sort_key="date", user="bob")
    s = v.summary()
    assert "sort:date" in s and "user~bob" in s
    assert CLTableView().summary() == "(none)"
