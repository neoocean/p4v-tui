"""Unit tests for the pure narrow-terminal page-navigation core."""

import pytest

from p4v_tui import narrow_nav as nn


def test_pages_are_unique_and_anchored():
    assert nn.NARROW_PAGES[0] == "tree"
    assert "log" in nn.NARROW_PAGES
    assert len(set(nn.NARROW_PAGES)) == len(nn.NARROW_PAGES)
    # The three panel pages are exactly the right-pane tables.
    assert nn.PANEL_PAGES == {"pending", "history", "submitted"}
    assert all(p in nn.NARROW_PAGES for p in nn.PANEL_PAGES)


@pytest.mark.parametrize("bad", [None, "", "nope", "TREE", "Pending"])
def test_normalize_falls_back_to_tree(bad):
    assert nn.normalize_page(bad) == "tree"


def test_cycle_forward_wraps_through_every_page_and_back():
    seq = []
    cur = "tree"
    for _ in range(len(nn.NARROW_PAGES)):
        seq.append(cur)
        cur = nn.cycle_page(cur, +1)
    assert seq == list(nn.NARROW_PAGES)
    # One more step wraps back to the start.
    assert cur == "tree"


def test_cycle_backward_is_inverse_of_forward():
    for page in nn.NARROW_PAGES:
        assert nn.cycle_page(nn.cycle_page(page, +1), -1) == page
        assert nn.cycle_page(nn.cycle_page(page, -1), +1) == page


def test_cycle_backward_from_tree_lands_on_last_page():
    assert nn.cycle_page("tree", -1) == nn.NARROW_PAGES[-1] == "log"


def test_cycle_handles_invalid_current():
    # Unknown current normalises to tree, so +1 lands on the 2nd page.
    assert nn.cycle_page("garbage", +1) == nn.NARROW_PAGES[1]


def test_is_panel_page():
    assert nn.is_panel_page("pending")
    assert nn.is_panel_page("submitted")
    assert not nn.is_panel_page("tree")
    assert not nn.is_panel_page("log")
    assert not nn.is_panel_page(None)


def test_right_tab_round_trip():
    for page in nn.PANEL_PAGES:
        tab = nn.right_tab_for_page(page)
        assert tab is not None
        assert nn.page_for_right_tab(tab) == page


def test_right_tab_for_non_panel_is_none():
    assert nn.right_tab_for_page("tree") is None
    assert nn.right_tab_for_page("log") is None
    assert nn.page_for_right_tab("tab_nonsense") is None
    assert nn.page_for_right_tab(None) is None


def test_toggle_from_tree_uses_last_panel():
    assert nn.toggle_target("tree", "history") == "history"
    assert nn.toggle_target("tree", "log") == "log"


def test_toggle_from_tree_without_last_defaults_to_pending():
    assert nn.toggle_target("tree", None) == nn.DEFAULT_PANEL_PAGE == "pending"
    assert nn.toggle_target("tree", "tree") == "pending"


@pytest.mark.parametrize("page", ["pending", "history", "submitted", "log"])
def test_toggle_from_any_non_tree_returns_to_tree(page):
    assert nn.toggle_target(page, "submitted") == "tree"
