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


# --- effective_pages (disabled / empty trimming) --------------------------

def test_effective_pages_default_is_full_cycle():
    assert nn.effective_pages() == nn.NARROW_PAGES


def test_effective_pages_drops_disabled_panel_pages():
    assert nn.effective_pages(disabled={"history", "submitted"}) == (
        "tree", "pending", "log")


def test_effective_pages_drops_empty_panel_pages():
    assert nn.effective_pages(empty={"pending"}) == (
        "tree", "history", "submitted", "log")


def test_effective_pages_unions_disabled_and_empty():
    assert nn.effective_pages(disabled=["history"], empty=["pending"]) == (
        "tree", "submitted", "log")


def test_effective_pages_never_drops_tree_or_log():
    # Even asked to disable/empty the always-on pages, they survive.
    assert nn.effective_pages(
        disabled={"tree", "log"}, empty={"tree", "log"}) == nn.NARROW_PAGES


def test_effective_pages_preserves_canonical_order():
    eff = nn.effective_pages(disabled={"pending"})
    assert eff == ("tree", "history", "submitted", "log")
    # order is a subsequence of NARROW_PAGES
    idxs = [nn.NARROW_PAGES.index(p) for p in eff]
    assert idxs == sorted(idxs)


# --- cycle_page / toggle_target over an effective page list ---------------

def test_cycle_over_trimmed_list_skips_dropped_pages():
    pages = nn.effective_pages(disabled={"history", "submitted"})
    # tree -> pending -> log -> tree, never visiting history/submitted
    assert nn.cycle_page("tree", +1, pages) == "pending"
    assert nn.cycle_page("pending", +1, pages) == "log"
    assert nn.cycle_page("log", +1, pages) == "tree"
    assert nn.cycle_page("tree", -1, pages) == "log"


def test_cycle_resolves_current_that_fell_off_the_list():
    # submitted is no longer in the cycle (e.g. went empty); stepping
    # from it is still well-defined (resolves to the first page, then
    # advances) and always lands on a page that's in the list.
    pages = nn.effective_pages(disabled={"submitted"})
    nxt = nn.cycle_page("submitted", +1, pages)
    assert nxt in pages
    # resolves to tree (pages[0]) then +1 -> pending
    assert nxt == "pending"


def test_toggle_from_tree_skips_disabled_last_panel():
    # last panel was history but it's now disabled -> fall back to pending
    pages = nn.effective_pages(disabled={"history"})
    assert nn.toggle_target("tree", "history", pages) == "pending"


def test_toggle_from_tree_falls_back_when_pending_disabled():
    # pending disabled and no usable last-panel -> first surviving non-tree
    pages = nn.effective_pages(disabled={"pending"})
    assert nn.toggle_target("tree", None, pages) == "history"


def test_toggle_with_only_always_on_pages_returns_tree():
    pages = nn.effective_pages(disabled={"pending", "history", "submitted"})
    assert pages == ("tree", "log")
    # last-panel "log" is valid and present, so it's honoured
    assert nn.toggle_target("tree", "log", pages) == "log"
    # but with no usable last panel the only non-tree survivor is log
    assert nn.toggle_target("tree", None, pages) == "log"


# --- breadcrumb (page indicator) ------------------------------------------

def test_breadcrumb_segments_marks_exactly_the_current_page():
    segs = nn.breadcrumb_segments("history")
    assert [label for label, _ in segs] == list(nn.NARROW_PAGES)
    current = [label for label, is_cur in segs if is_cur]
    assert current == ["history"]


def test_breadcrumb_segments_follow_effective_pages():
    pages = nn.effective_pages(disabled={"history", "submitted"})
    segs = nn.breadcrumb_segments("pending", pages)
    assert [label for label, _ in segs] == ["tree", "pending", "log"]
    assert [is_cur for _, is_cur in segs] == [False, True, False]


def test_breadcrumb_segments_resolves_stale_current():
    # current fell off the effective list -> first page is highlighted
    pages = nn.effective_pages(disabled={"submitted"})
    segs = nn.breadcrumb_segments("submitted", pages)
    assert [label for label, is_cur in segs if is_cur] == ["tree"]


def test_render_breadcrumb_highlights_current_and_dims_rest():
    s = nn.render_breadcrumb("pending", ("tree", "pending", "log"))
    # current page is reverse-highlighted, others dimmed
    assert "[b reverse] pending [/]" in s
    assert "[dim]tree[/]" in s
    assert "[dim]log[/]" in s
    assert s.count("[b reverse]") == 1


def test_render_breadcrumb_numbered_prefixes_positions():
    s = nn.render_breadcrumb(
        "pending", ("tree", "pending", "log"), numbered=True)
    assert "[dim]1 tree[/]" in s
    assert "[b reverse] 2 pending [/]" in s
    assert "[dim]3 log[/]" in s


def test_render_breadcrumb_full_when_width_allows():
    # generous width -> full labels, no compaction
    s = nn.render_breadcrumb(
        "submitted", numbered=True, width=200)
    assert "[dim]1 tree[/]" in s
    assert "[b reverse] 4 submitted [/]" in s
    assert "[dim]5 log[/]" in s  # the chip that was clipping on a phone


def test_render_breadcrumb_compacts_when_too_narrow():
    # 46-col phone: full strip (~52 cells) overflows -> compact form.
    s = nn.render_breadcrumb("submitted", numbered=True, width=46)
    # current page keeps its label + highlight
    assert "[b reverse] 4 submitted [/]" in s
    # non-current pages collapse to bare jump numbers (label dropped)
    assert "[dim]1[/]" in s
    assert "[dim]5[/]" in s          # log's jump number stays visible
    assert "[dim]1 tree[/]" not in s
    assert "log" not in s            # only the number remains for log


def test_render_breadcrumb_compact_fits_the_width():
    # the compact form actually fits a narrow phone
    segs = nn.breadcrumb_segments("submitted")
    assert nn._breadcrumb_plain_width(segs, " · ", True, compact=True) <= 46


def test_render_breadcrumb_width_none_is_always_full():
    s = nn.render_breadcrumb("submitted", numbered=True)  # no width
    assert "[dim]5 log[/]" in s


# --- number-key direct jump -----------------------------------------------

def test_jump_target_by_index_maps_position_to_page():
    assert nn.jump_target_by_index(1) == "tree"
    assert nn.jump_target_by_index(2) == "pending"
    assert nn.jump_target_by_index(5) == "log"


def test_jump_target_by_index_follows_effective_pages():
    pages = nn.effective_pages(disabled={"history", "submitted"})
    # cycle is now tree(1) pending(2) log(3)
    assert nn.jump_target_by_index(1, pages) == "tree"
    assert nn.jump_target_by_index(2, pages) == "pending"
    assert nn.jump_target_by_index(3, pages) == "log"
    # 4 is out of range for the trimmed cycle
    assert nn.jump_target_by_index(4, pages) is None


@pytest.mark.parametrize("bad", [0, -1, 99, "2", None, 1.5])
def test_jump_target_by_index_rejects_out_of_range_and_non_int(bad):
    assert nn.jump_target_by_index(bad) is None


# --- page-aware footer hints ----------------------------------------------

def test_footer_hints_always_offer_navigator_keys_and_quit():
    for page in nn.NARROW_PAGES:
        keys = [k for k, _ in nn.footer_hints(page)]
        assert keys[0] == "Tab"
        assert "q" in keys


def test_footer_hints_jump_range_follows_page_count():
    keys = dict(nn.footer_hints("tree", n_pages=3))
    assert "1-3" in keys  # key -> label dict, jump key reflects trimmed count


def test_footer_hints_no_jump_when_single_page():
    keys = [k for k, _ in nn.footer_hints("tree", n_pages=1)]
    assert not any(k.startswith("1-") for k in keys)


def test_footer_hints_are_page_specific():
    tree = [lbl for _, lbl in nn.footer_hints("tree")]
    pending = [lbl for _, lbl in nn.footer_hints("pending")]
    # tree offers search/open; pending offers the row menu + submit
    assert "search" in tree
    assert "menu" in pending and "submit" in pending
    assert "submit" not in tree


def test_footer_hints_offer_back_to_tree_only_when_away():
    assert "tree" not in [lbl for _, lbl in nn.footer_hints("tree")]
    assert "tree" in [lbl for _, lbl in nn.footer_hints("log")]


def test_render_footer_hints_is_bold_key_dim_label():
    s = nn.render_footer_hints("pending", n_pages=5)
    assert "[b]Tab[/] [dim]pages[/]" in s
    assert "[b]m[/] [dim]menu[/]" in s


def _footer_plain(markup: str) -> str:
    # crude markup strip for width assertions in tests
    import re
    return re.sub(r"\[/?[^\]]*\]", "", markup)


def test_footer_hints_full_when_width_allows():
    s = nn.render_footer_hints("pending", n_pages=5, width=200)
    # nothing dropped
    for word in ("pages", "jump", "detail", "menu", "submit", "tree", "quit"):
        assert word in s


def test_footer_hints_fit_a_phone_width_without_clipping():
    # ~46-col phone: the full submitted strip (~53) overflows, so the
    # most-droppable hint (⌫ tree) goes — and it never clips mid-word.
    s = nn.render_footer_hints("submitted", n_pages=5, width=46)
    plain = _footer_plain(s)
    assert len(plain) <= 46
    assert "Tab" in plain and "quit" in plain
    assert not plain.endswith("q")     # "q quit" not chopped to "q"
    assert "tree" not in plain         # the lowest-priority hint dropped


def test_footer_hints_drop_both_low_priority_when_tighter():
    # tighter: both prio-4 hints (1-N jump, ⌫ tree) drop before the
    # page action / navigator / exit.
    s = nn.render_footer_hints("submitted", n_pages=5, width=40)
    plain = _footer_plain(s)
    assert len(plain) <= 40
    assert "jump" not in plain
    assert "tree" not in plain
    assert "Tab" in plain and "quit" in plain
    assert "detail" in plain and "menu" in plain  # page action kept


def test_footer_hints_keep_navigator_and_exit_under_pressure():
    # even very tight, Tab (navigator) + q (exit) are the last to go
    s = nn.render_footer_hints("pending", n_pages=5, width=18)
    plain = _footer_plain(s)
    assert len(plain) <= 18
    assert "Tab" in plain
    assert "quit" in plain


def test_footer_hints_width_none_is_full():
    s = nn.render_footer_hints("pending", n_pages=5)
    assert "jump" in s and "tree" in s


# --- layout-mode pin ------------------------------------------------------

@pytest.mark.parametrize("bad", [None, "", "Narrow", "off", "auto "])
def test_normalize_layout_mode_falls_back_to_auto(bad):
    assert nn.normalize_layout_mode(bad) == "auto"


def test_resolve_narrow_mode_auto_uses_width_threshold():
    assert nn.resolve_narrow_mode("auto", 80, 100) is True
    assert nn.resolve_narrow_mode("auto", 120, 100) is False
    # exactly at the threshold is wide (strict <)
    assert nn.resolve_narrow_mode("auto", 100, 100) is False


def test_resolve_narrow_mode_pins_override_width():
    # pinned narrow stays narrow even on a very wide terminal
    assert nn.resolve_narrow_mode("narrow", 999, 100) is True
    # pinned wide stays wide even on a tiny terminal
    assert nn.resolve_narrow_mode("wide", 10, 100) is False
    # unknown mode behaves as auto
    assert nn.resolve_narrow_mode("bogus", 80, 100) is True


def test_cycle_layout_mode_round_trips():
    assert nn.cycle_layout_mode("auto") == "narrow"
    assert nn.cycle_layout_mode("narrow") == "wide"
    assert nn.cycle_layout_mode("wide") == "auto"
    assert nn.cycle_layout_mode(None) == "narrow"  # normalises then steps


# --- responsive table columns ---------------------------------------------

@pytest.mark.parametrize(
    "table", ["pending", "submitted", "history_file", "history_folder"])
def test_narrow_profile_is_a_subsequence_of_wide(table):
    wide = nn.column_fields(table, narrow=False)
    narrow = nn.column_fields(table, narrow=True)
    # narrow keeps a subset, in the same relative order, and is shorter
    assert set(narrow) <= set(wide)
    assert len(narrow) < len(wide)
    assert [f for f in wide if f in narrow] == list(narrow)


@pytest.mark.parametrize(
    "table", ["pending", "submitted", "history_file", "history_folder"])
def test_narrow_profile_always_keeps_description_and_an_identity_col(table):
    narrow = nn.column_fields(table, narrow=True)
    assert "desc" in narrow  # the column you actually read survives
    # first column is an identity field (change / rev) for cursor restore
    assert narrow[0] in ("change", "rev")


def test_narrow_pending_drops_workspace_user_date():
    narrow = nn.column_fields("pending", narrow=True)
    assert narrow == ("change", "desc")
    for dropped in ("workspace", "user", "date"):
        assert dropped not in narrow


def test_column_headers_map_fields_to_labels():
    assert nn.column_headers("pending", narrow=False) == (
        "Change", "Workspace", "User", "Date", "Description")
    assert nn.column_headers("pending", narrow=True) == (
        "Change", "Description")


def test_column_fields_unknown_table_is_empty():
    assert nn.column_fields("bogus", narrow=True) == ()


def test_select_cells_picks_and_orders_active_profile():
    cells = {"change": "123", "workspace": "ws", "user": "me",
             "date": "2026", "desc": "fix bug"}
    assert nn.select_cells("pending", False, cells) == [
        "123", "ws", "me", "2026", "fix bug"]
    assert nn.select_cells("pending", True, cells) == ["123", "fix bug"]


def test_select_cells_missing_field_defaults_to_blank():
    # desc absent -> "" rather than KeyError
    assert nn.select_cells("submitted", True, {"change": "9"}) == ["9", ""]
