"""More end-to-end TUI gesture tests — automating the rest of the
`docs/handoff-manual-tests.md` Priority-B checklist (and a couple of the
new features) that were previously click-through-only.

Same dependency-free pattern as ``test_e2e_gestures.py``: a synthetic
``DemoBackend`` + Textual's headless ``app.run_test()`` pilot, each test a
plain sync function driving an async body via ``asyncio.run``.
"""
from __future__ import annotations

import asyncio
import sys
from io import BytesIO
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from demo_backend import DemoBackend  # noqa: E402

# Reuse the shared helpers from the sibling e2e module.
from test_e2e_gestures import (  # noqa: E402
    _isolated_home, _make_app, _record_notifications,
    _wait_connected, _wait_until,
)

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

ASSET_PNG = "//depot/demo/assets/logo.png"


def _fresh_state(app, tmp_path):
    """Isolate permalink registry + bookmark store to throwaway files."""
    from p4v_tui.permalink import PermalinkRegistry
    from p4v_tui.bookmarks import BookmarkStore
    app._permalink_reg = PermalinkRegistry(
        tmp_path / "permalinks.json", after_write=None)
    app._bookmarks = BookmarkStore(
        tmp_path / "bookmarks.json", after_write=None)


# --- command palette disabled (trivial; no pilot) -----------------------

def test_command_palette_disabled():
    from p4v_tui.app import P4VApp
    # Ctrl+P is freed for Fast Search history; the Textual palette is off.
    assert P4VApp.ENABLE_COMMAND_PALETTE is False


# --- backend name in the title bar --------------------------------------

def test_backend_name_in_subtitle(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 38)) as pilot:
            assert await _wait_connected(pilot, app)
            # _on_connected sets sub_title to the active backend's name.
            assert await _wait_until(pilot, lambda: app.sub_title == "demo")

    asyncio.run(_run())


# --- tree multi-select mark glyph + Esc clears --------------------------

def test_space_marks_node_glyph_and_esc_clears(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 38)) as pilot:
            assert await _wait_connected(pilot, app)
            from textual.widgets import TabbedContent
            app.query_one("#left_tabs", TabbedContent).active = "tab_depot"
            await pilot.pause(0.1)
            tree = app.query_one("#depot_tree")
            tree.focus()
            tree.root.expand()
            await _wait_until(pilot, lambda: bool(tree.root.children))
            tree.navigate_to_path("//depot/demo/src/app.py")
            assert await _wait_until(
                pilot,
                lambda: getattr(tree.cursor_node, "data", None)
                == "//depot/demo/src/app.py")

            # GESTURE: Space marks the cursor node.
            await pilot.press("space")
            assert await _wait_until(pilot, lambda: tree.has_marks())
            assert "//depot/demo/src/app.py" in tree.marked_specs()
            # The mark glyph is rendered into the node label.
            from p4v_tui.widgets.p4_tree import P4Tree
            label = tree._plain(tree.cursor_node.label)
            assert P4Tree.MARK_GLYPH in label

            # GESTURE: Esc clears all marks.
            await pilot.press("escape")
            assert await _wait_until(pilot, lambda: not tree.has_marks())

    asyncio.run(_run())


# --- Go-to-path (Ctrl+G) ------------------------------------------------

def test_ctrl_g_goto_path_navigates(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 38)) as pilot:
            assert await _wait_connected(pilot, app)
            _fresh_state(app, tmp_path)

            await pilot.press("ctrl+g")
            from p4v_tui.widgets.goto_path_modal import GotoPathModal
            assert await _wait_until(
                pilot, lambda: isinstance(app.screen, GotoPathModal))
            from textual.widgets import Input
            app.screen.query_one("#path", Input).value = "//depot/demo/docs"
            await pilot.press("enter")
            # Lands on the docs directory in the depot tree (mapped path →
            # workspace tab; either way the modal closes and navigation runs).
            assert await _wait_until(
                pilot, lambda: not isinstance(app.screen, GotoPathModal))

    asyncio.run(_run())


# --- Bookmarks (Ctrl+B add, Ctrl+Shift+B picker) ------------------------

def test_bookmark_add_and_picker(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    notes = _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 38)) as pilot:
            assert await _wait_connected(pilot, app)
            _fresh_state(app, tmp_path)

            from textual.widgets import TabbedContent
            app.query_one("#left_tabs", TabbedContent).active = "tab_depot"
            await pilot.pause(0.1)
            tree = app.query_one("#depot_tree")
            tree.focus()
            tree.root.expand()
            await _wait_until(pilot, lambda: bool(tree.root.children))
            tree.navigate_to_path("//depot/demo/src/app.py")
            assert await _wait_until(
                pilot,
                lambda: getattr(tree.cursor_node, "data", None)
                == "//depot/demo/src/app.py")

            # GESTURE: Ctrl+B bookmarks the cursor node.
            await pilot.press("ctrl+b")
            assert await _wait_until(
                pilot, lambda: len(app._bookmark_store) == 1)
            assert any("Bookmarked" in n for n in notes)

            # GESTURE: Ctrl+Shift+B opens the picker listing it.
            await pilot.press("ctrl+shift+b")
            from p4v_tui.widgets.bookmark_picker_modal import (
                BookmarkPickerModal)
            assert await _wait_until(
                pilot, lambda: isinstance(app.screen, BookmarkPickerModal))

    asyncio.run(_run())


# --- image preview (Enter on an image leaf → ANSI art) ------------------

class _PngBackend(DemoBackend):
    """DemoBackend that returns real PNG bytes when printing the logo."""

    def run_tagged(self, args):
        a = [str(x) for x in args]
        if a and a[0] == "print" and a[-1].endswith("logo.png"):
            buf = BytesIO()
            Image.new("RGB", (8, 8), (10, 200, 30)).save(buf, format="PNG")
            return [{"depotFile": a[-1], "rev": "1", "type": "binary"},
                    buf.getvalue()]
        return super().run_tagged(args)


def test_enter_on_image_leaf_opens_ansi_preview(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(_PngBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 38)) as pilot:
            assert await _wait_connected(pilot, app)
            from textual.widgets import TabbedContent
            app.query_one("#left_tabs", TabbedContent).active = "tab_depot"
            await pilot.pause(0.1)
            tree = app.query_one("#depot_tree")
            tree.focus()
            tree.root.expand()
            await _wait_until(pilot, lambda: bool(tree.root.children))
            tree.navigate_to_path(ASSET_PNG)
            assert await _wait_until(
                pilot,
                lambda: getattr(tree.cursor_node, "data", None) == ASSET_PNG)

            # GESTURE: Enter opens the viewer; the image path renders as
            # pre-built half-block art (rendered=… set, not the text path).
            await pilot.press("enter")
            from p4v_tui.widgets.file_viewer import FileViewerModal
            assert await _wait_until(
                pilot, lambda: isinstance(app.screen, FileViewerModal))
            assert await _wait_until(
                pilot, lambda: app.screen._rendered is not None)
            # Title carries the image caption (format + dimensions).
            assert "PNG" in app.screen._title

    asyncio.run(_run())


# --- CL table filter (Pending) ------------------------------------------

def test_pending_filter_applies_and_reduces_rows(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 38)) as pilot:
            assert await _wait_connected(pilot, app)
            from textual.widgets import DataTable
            table = app.query_one("#pending_table", DataTable)
            before = table.row_count
            assert before > 0

            # GESTURE: open the filter dialog and filter by a user that
            # matches nothing → the table empties.
            app.open_pending_filter()
            from p4v_tui.widgets.cl_filter_modal import CLFilterModal
            assert await _wait_until(
                pilot, lambda: isinstance(app.screen, CLFilterModal))
            from textual.widgets import Input
            app.screen.query_one("#user", Input).value = "no-such-user-zzz"
            await pilot.click("#ok")
            assert await _wait_until(
                pilot, lambda: not isinstance(app.screen, CLFilterModal))
            assert await _wait_until(pilot, lambda: table.row_count < before)
            assert app._pending_view.is_active()

    asyncio.run(_run())
