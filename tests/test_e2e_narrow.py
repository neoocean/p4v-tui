"""End-to-end TUI tests for the narrow-terminal page navigator.

Drives the app at a phone-sized viewport (``run_test(size=(80, 50))``)
so ``narrow_mode`` auto-engages, then scripts the real ``Tab`` /
``Shift+Tab`` keypresses a mobile tester makes. The pure sequencing core
is covered by ``test_narrow_nav.py``; this exercises the actual app
wiring — auto-entry, the page cycle, and the config-driven trimming of
disabled pages.

Same dependency-free pattern as the sibling e2e modules: a synthetic
``DemoBackend`` + Textual's headless pilot, each test a plain sync
function driving an async body via ``asyncio.run``.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from demo_backend import DemoBackend  # noqa: E402

from test_e2e_gestures import (  # noqa: E402
    _isolated_home, _make_app, _record_notifications,
    _wait_connected, _wait_until,
)

NARROW_SIZE = (80, 50)  # width < 100 -> narrow mode; height > 45 -> not short

# Full-cycle page -> direct-jump number key (see narrow_nav cycle).
_PAGE_KEY = {
    "tree": "1", "pending": "2", "history": "3",
    "submitted": "4", "log": "5",
}


async def _goto_page(pilot, app, page):
    """Navigate to a narrow page the way a user does — the number key.

    Why not just ``app.narrow_page = page``? Poking the reactive races
    the post-mount focus restore (``call_after_refresh(_restore_ui_state)``
    → tree gets focus → ``on_descendant_focus`` snaps ``narrow_page``
    back to ``"tree"``). Under load that async reset lands *after* the
    assignment and flakes the test (green in isolation). ``pilot.press``
    pumps the event loop first, so the restore has already settled and
    the keypress's focus move to the right pane is the last word —
    leaving ``narrow_page`` stable on the panel page.

    The number maps to the Nth *effective* page, so a panel page only
    exists once its table is populated. We re-press until the page
    sticks, which absorbs both the focus race and any lag in the table
    loads (idempotent — the same key always jumps to the same page).
    Returns True once it sticks, False if it never does (e.g. a disabled
    page not in the cycle).
    """
    key = _PAGE_KEY[page]
    for _ in range(40):
        await pilot.press(key)
        await pilot.pause()
        if app.narrow_page == page:
            return True
    return False


def test_narrow_mode_auto_engages_at_phone_width(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            # Starts on the tree page, full-screen (right pane hidden).
            assert app.narrow_page == "tree"
            assert app.query_one("#right_pane").display is False

    asyncio.run(_run())


def test_tab_walks_the_full_page_cycle(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            app.narrow_page = "tree"
            await pilot.pause()
            seen = [app.narrow_page]
            for _ in range(5):
                await pilot.press("tab")
                await pilot.pause()
                seen.append(app.narrow_page)
            # tree -> pending -> history -> submitted -> log -> tree
            assert seen == [
                "tree", "pending", "history", "submitted", "log", "tree"]

    asyncio.run(_run())


def test_shift_tab_walks_cycle_backwards(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            app.narrow_page = "tree"
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.pause()
            # Backwards from tree wraps to the last page (log).
            assert app.narrow_page == "log"

    asyncio.run(_run())


def test_disabled_pages_are_skipped_in_the_cycle(tmp_path, monkeypatch):
    from p4v_tui.config import NarrowConfig
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    app.config.narrow = NarrowConfig(disabled_pages=["history", "submitted"])
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            app.narrow_page = "tree"
            await pilot.pause()
            seen = [app.narrow_page]
            for _ in range(3):
                await pilot.press("tab")
                await pilot.pause()
                seen.append(app.narrow_page)
            # history + submitted are disabled, so the cycle is
            # tree -> pending -> log -> tree.
            assert seen == ["tree", "pending", "log", "tree"]
            assert "history" not in seen
            assert "submitted" not in seen

    asyncio.run(_run())


def test_breadcrumb_visible_in_narrow_and_tracks_page(tmp_path, monkeypatch):
    from textual.widgets import Static
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            bc = app.query_one("#narrow_breadcrumb", Static)
            assert bc.display is True
            assert await _goto_page(pilot, app, "submitted")
            # Static renders the markup into plain Content; the current
            # page is the reverse-highlighted one (wrapped in spaces).
            text = str(bc.render())
            assert "pending" in text and "log" in text  # full strip drawn
            assert " submitted " in text  # current page highlight padding

    asyncio.run(_run())


def test_breadcrumb_compacts_on_a_phone_width(tmp_path, monkeypatch):
    """At ~46 cols the full label strip overflows; it must compact so the
    last chip (5 log) isn't clipped off the edge (real-device finding)."""
    from textual.widgets import Static
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(46, 50)) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            assert await _goto_page(pilot, app, "submitted")
            text = str(app.query_one("#narrow_breadcrumb", Static).render())
            # current page keeps its label…
            assert " submitted " in text
            # …but non-current labels collapse to numbers (no clipping)
            assert "tree" not in text
            assert "log" not in text
            assert "5" in text  # log's jump number survives

    asyncio.run(_run())


def test_breadcrumb_full_labels_when_wide_enough(tmp_path, monkeypatch):
    from textual.widgets import Static
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        # 98 cols: still narrow mode (<100) but wide enough for full labels
        async with app.run_test(size=(98, 50)) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            assert await _goto_page(pilot, app, "submitted")
            text = str(app.query_one("#narrow_breadcrumb", Static).render())
            assert "tree" in text and "log" in text  # full labels shown

    asyncio.run(_run())


def test_breadcrumb_hidden_in_wide_mode(tmp_path, monkeypatch):
    from textual.widgets import Static
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        # width >= 100 -> wide layout, breadcrumb hidden
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_until(pilot, lambda: app.narrow_mode is False)
            bc = app.query_one("#narrow_breadcrumb", Static)
            assert bc.display is False

    asyncio.run(_run())


def test_page_is_preserved_across_a_rotation(tmp_path, monkeypatch):
    """Phone rotate portrait → landscape → portrait keeps your place.

    We drive the narrow⇄wide flip through the *layout pin* rather than
    by poking ``narrow_mode`` directly: at an 80-col size the app's own
    width-based ``on_resize`` wants narrow mode, so a manual override
    would race a spurious resize. Pinning wide overrides the width rule
    deterministically, which is exactly what a real rotation crossing the
    threshold does to the flag.
    """
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            # Navigate via the real gesture so the page sticks (a direct
            # narrow_page= would lose a race with the post-mount focus
            # restore — see _goto_page).
            assert await _goto_page(pilot, app, "submitted")
            # rotate to landscape (wide): leaving narrow resets to tree
            app._layout_mode = "wide"
            app._recompute_narrow_mode(app.size.width)
            await pilot.pause()
            assert app.narrow_mode is False
            assert app.narrow_page == "tree"
            # rotate back to portrait (narrow): restored to submitted
            app._layout_mode = "auto"
            app._recompute_narrow_mode(app.size.width)
            await pilot.pause()
            assert app.narrow_mode is True
            assert app.narrow_page == "submitted"

    asyncio.run(_run())


def test_number_keys_jump_directly_to_pages(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            # 1=tree 2=pending 3=history 4=submitted 5=log
            await pilot.press("5")
            await pilot.pause()
            assert app.narrow_page == "log"
            await pilot.press("3")
            await pilot.pause()
            assert app.narrow_page == "history"
            await pilot.press("1")
            await pilot.pause()
            assert app.narrow_page == "tree"

    asyncio.run(_run())


def test_number_jump_respects_disabled_pages(tmp_path, monkeypatch):
    from p4v_tui.config import NarrowConfig
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    app.config.narrow = NarrowConfig(disabled_pages=["history", "submitted"])
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            # effective cycle: 1=tree 2=pending 3=log
            await pilot.press("3")
            await pilot.pause()
            assert app.narrow_page == "log"
            # 4 is out of range now -> no-op (stays on log)
            await pilot.press("4")
            await pilot.pause()
            assert app.narrow_page == "log"

    asyncio.run(_run())


def test_narrow_footer_replaces_default_footer(tmp_path, monkeypatch):
    from textual.widgets import Static, Footer
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            nf = app.query_one("#narrow_footer", Static)
            footer = app.query_one(Footer)
            # narrow: curated hints shown, default Footer hidden
            assert nf.display is True
            assert footer.display is False
            assert await _goto_page(pilot, app, "pending")
            text = str(nf.render())
            assert "pages" in text and "menu" in text and "quit" in text

    asyncio.run(_run())


def test_narrow_footer_fits_phone_width_no_clip(tmp_path, monkeypatch):
    from textual.widgets import Static
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(46, 50)) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            assert await _goto_page(pilot, app, "submitted")
            text = str(app.query_one("#narrow_footer", Static).render())
            assert len(text) <= 46            # fits, no overflow
            assert "Tab" in text              # navigator survives
            assert text.rstrip().endswith("quit")  # exit hint not clipped

    asyncio.run(_run())


def test_default_footer_returns_in_wide_mode(tmp_path, monkeypatch):
    from textual.widgets import Static, Footer
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_until(pilot, lambda: app.narrow_mode is False)
            assert app.query_one("#narrow_footer", Static).display is False
            assert app.query_one(Footer).display is True

    asyncio.run(_run())


def test_layout_pin_narrow_forces_narrow_on_a_wide_terminal(
        tmp_path, monkeypatch):
    from p4v_tui.config import NarrowConfig
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    app.config.narrow = NarrowConfig(layout="narrow")
    _record_notifications(app)

    async def _run():
        # 120 cols would be wide under the auto rule, but the pin wins.
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.narrow_mode is True

    asyncio.run(_run())


def test_cycle_layout_mode_toggles_narrow_on_wide_terminal(
        tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_until(pilot, lambda: app.narrow_mode is False)
            # auto -> narrow: pins narrow despite the wide terminal
            app.action_cycle_layout_mode()
            await pilot.pause()
            assert app._layout_mode == "narrow"
            assert app.narrow_mode is True
            # narrow -> wide
            app.action_cycle_layout_mode()
            await pilot.pause()
            assert app._layout_mode == "wide"
            assert app.narrow_mode is False
            # wide -> auto (back to the width rule -> wide at 120 cols)
            app.action_cycle_layout_mode()
            await pilot.pause()
            assert app._layout_mode == "auto"
            assert app.narrow_mode is False

    asyncio.run(_run())


def _col_labels(app, selector):
    from textual.widgets import DataTable
    table = app.query_one(selector, DataTable)
    return [str(c.label) for c in table.columns.values()]


def test_pending_columns_trim_in_narrow_mode(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            labels = _col_labels(app, "#pending_table")
            # narrow keeps just Change + Description (the column you read)
            assert labels == ["Change", "Description"]

    asyncio.run(_run())


def test_pending_columns_full_in_wide_mode(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is False)
            labels = _col_labels(app, "#pending_table")
            assert labels == [
                "Change", "Workspace", "User", "Date", "Description"]

    asyncio.run(_run())


def test_pending_cell0_stays_change_number_in_narrow(tmp_path, monkeypatch):
    """Cursor-restore + menu lookups do str(row[0]) == change — the ↗
    marker must never land in column 0, even in the trimmed layout."""
    from textual.widgets import DataTable
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            table = app.query_one("#pending_table", DataTable)
            row0 = table.get_row_at(0)
            cell0 = str(row0[0])
            # identity cell: a CL number or the synthetic "default" row
            assert cell0 == "default" or cell0.isdigit()

    asyncio.run(_run())


def test_columns_rerender_live_on_layout_flip(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        # start wide
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is False)
            assert len(_col_labels(app, "#pending_table")) == 5
            # pin narrow -> columns should re-render from cached rows
            app._layout_mode = "narrow"
            app._recompute_narrow_mode(app.size.width)
            await pilot.pause()
            assert app.narrow_mode is True
            assert _col_labels(app, "#pending_table") == [
                "Change", "Description"]
            # and back to wide
            app._layout_mode = "wide"
            app._recompute_narrow_mode(app.size.width)
            await pilot.pause()
            assert app.narrow_mode is False
            assert len(_col_labels(app, "#pending_table")) == 5

    asyncio.run(_run())


def test_backspace_returns_to_tree(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=NARROW_SIZE) as pilot:
            assert await _wait_connected(pilot, app)
            assert await _wait_until(pilot, lambda: app.narrow_mode is True)
            assert await _goto_page(pilot, app, "submitted")
            await pilot.press("backspace")
            await pilot.pause()
            assert app.narrow_page == "tree"

    asyncio.run(_run())
