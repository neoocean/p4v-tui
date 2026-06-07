#!/usr/bin/env python3
"""Real screenshots for the manual/README — Textual headless SVG capture.

Mirrors the docker-monitor / pytmux approach: drive the **actual app**
(`P4VApp`) under Textual's headless `run_test()` pilot and save each scene
as an SVG via `app.save_screenshot()`, so the image is exactly what the app
paints — not a mockup. The app is fed a synthetic in-process backend
(`demo_backend.DemoBackend`) so **no live server is contacted and no
personal depot/workspace can appear**; a final regex scrub (`_scrub_svg`)
is defence-in-depth against any real identifier slipping through.

    python3 scripts/gen_screenshots.py            # all scenes → docs/image/*.svg
    python3 scripts/gen_screenshots.py 06-depot   # only scenes whose name matches

POSIX/desktop alike — headless, no real TTY needed. HOME is redirected to a
throwaway dir so the real ~/.p4v-tui state/index is never read or written.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

# Capture the invoking user's real home BEFORE we override it, so the scrub
# pass can flag it if it ever leaks — without hard-coding any real path in
# this (public) source file.
_REAL_HOME = os.environ.get("HOME", "")

# Redirect HOME *before* importing the app so state.py / search index point
# at a throwaway dir, never the real ~/.p4v-tui.
_TMP_HOME = tempfile.mkdtemp(prefix="p4v-shots-home-")
os.environ["HOME"] = _TMP_HOME
os.environ.setdefault("P4V_BACKEND", "cli")   # avoid importing P4Python

from p4v_tui.app import P4VApp                       # noqa: E402
from p4v_tui.config import Config, ConnectionConfig  # noqa: E402
from p4v_tui.p4client import P4Service               # noqa: E402
from demo_backend import (  # noqa: E402
    DemoBackend, CLIENT, OTHER_CLIENT, USER, PORT)

OUT_DIR = os.path.join(_ROOT, "docs", "image")
SIZE = (120, 38)          # wide enough to show both panes + detail
NARROW = (48, 40)         # very narrow terminal (mobile / split pane)
DETAIL_H = 9              # shorter detail pane → more table rows visible

# Defence-in-depth scrub uses an ALLOWLIST so this public source never has to
# name a real identifier. Anything depot-/email-shaped that isn't one of the
# synthetic demo values below is flagged. ``//@`` is the permalink namespace.
_ALLOWED_NS = ("//depot", f"//{CLIENT}", f"//{OTHER_CLIENT}", "//@")
_DEPOT_RE = re.compile(r"(?<![:/A-Za-z0-9])//[A-Za-z0-9_.@-]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _make_config() -> Config:
    cfg = Config.empty()
    cfg.connection = ConnectionConfig(
        port=PORT, user=USER, client=CLIENT, name="demo")
    return cfg


def _new_app() -> P4VApp:
    app = P4VApp(config=_make_config())
    # Swap in the synthetic backend before on_mount's connect worker runs.
    app.p4 = P4Service(cmd_log=app.cmd_log, backend=DemoBackend())
    # Shorter detail pane so the changelist tables show more rows.
    app.detail_pane_height = DETAIL_H
    return app


async def _wait_connected(pilot, app, tries=120):
    """Wait until the connect worker has run and the trees/tables filled."""
    for _ in range(tries):
        await pilot.pause(0.05)
        try:
            table = app.query_one("#pending_table")
            if table.row_count > 0:
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


async def _settle(pilot, n=6, dt=0.08):
    for _ in range(n):
        await pilot.pause(dt)


def _activate_tab(app, tabs_id, tab_id):
    from textual.widgets import TabbedContent
    app.query_one(tabs_id, TabbedContent).active = tab_id


# ─────────────────────────── scene drivers ──────────────────────────────
# Each returns after putting the app in the desired state; the caller saves.

async def overview(app, pilot):
    _activate_tab(app, "#right_tabs", "tab_pending")
    await _settle(pilot)
    app.query_one("#pending_table").focus()
    await _settle(pilot)


async def pending(app, pilot):
    _activate_tab(app, "#right_tabs", "tab_pending")
    await _settle(pilot)
    t = app.query_one("#pending_table")
    t.focus()
    t.move_cursor(row=0)        # row 2 stays the dim "↗" cross-workspace row
    await _settle(pilot)


async def submitted(app, pilot):
    _activate_tab(app, "#right_tabs", "tab_submitted")
    await _settle(pilot)
    app.query_one("#submitted_table").focus()
    await _settle(pilot)


async def history(app, pilot):
    # Load folder history for a demo dir into the History tab.
    app._load_folder_history("//depot/demo/src")
    await _settle(pilot, n=10)
    _activate_tab(app, "#right_tabs", "tab_history")
    await _settle(pilot)
    try:
        app.query_one("#history_table").focus()
    except Exception:  # noqa: BLE001
        pass
    await _settle(pilot)


async def _expand_tree(pilot, tree, depth=4):
    """Expand every directory node down to ``depth`` levels (lazy-load)."""
    tree.root.expand()
    await _settle(pilot, n=6)
    frontier = [tree.root]
    for _ in range(depth):
        nxt = []
        for node in frontier:
            for child in list(node.children):
                if child.allow_expand:
                    child.expand()
                    nxt.append(child)
        await _settle(pilot, n=6)
        frontier = nxt
    await _settle(pilot, n=6)


async def depot_tree(app, pilot):
    from p4v_tui.widgets.depot_tree import DepotTree
    _activate_tab(app, "#left_tabs", "tab_depot")
    await _settle(pilot)
    tree = app.query_one(DepotTree)
    tree.focus()
    await _expand_tree(pilot, tree)


async def workspace_tree(app, pilot):
    from p4v_tui.widgets.workspace_tree import WorkspaceTree
    _activate_tab(app, "#left_tabs", "tab_workspace")
    await _settle(pilot)
    tree = app.query_one(WorkspaceTree)
    tree.focus()
    await _expand_tree(pilot, tree)


async def file_viewer(app, pilot):
    app._open_file_viewer("//depot/demo/src/app.py")
    await _settle(pilot, n=14)


async def command_monitor(app, pilot):
    # Pre-seed a couple of running/finished entries so the tree isn't empty.
    app.action_show_cmd_monitor()
    await _settle(pilot, n=10)


async def fast_search(app, pilot):
    # Give the background index build a moment to ingest the demo files.
    await _settle(pilot, n=30, dt=0.1)
    app.action_open_search()
    await _settle(pilot, n=6)
    for ch in "config":          # type so the query runs on the built index
        await pilot.press(ch)
    await _settle(pilot, n=16, dt=0.1)


async def goto_path(app, pilot):
    app.action_goto_path()
    await _settle(pilot, n=8)
    for ch in "//depot/demo/src/app.py":
        await pilot.press("slash" if ch == "/" else
                          ("full_stop" if ch == "." else ch))
    await _settle(pilot, n=6)


async def context_menu(app, pilot):
    _activate_tab(app, "#right_tabs", "tab_pending")
    await _settle(pilot)
    t = app.query_one("#pending_table")
    t.focus()
    t.move_cursor(row=0)
    await _settle(pilot)
    await pilot.press("m")
    await _settle(pilot, n=8)


async def narrow_mode(app, pilot):
    # Built at NARROW size → app auto-switches to the page navigator.
    await _settle(pilot, n=6)
    app.query_one("#pending_table").focus()
    await _settle(pilot, n=6)


# (name, description, driver, size)
SCENES = [
    ("01-overview", "Main layout — trees, pending table, detail pane, log",
     overview, SIZE),
    ("02-pending", "Pending changelists — cross-workspace ↗ marker",
     pending, SIZE),
    ("03-submitted", "Submitted changelists", submitted, SIZE),
    ("04-history", "File/folder history tab", history, SIZE),
    ("05-depot-tree", "Depot tree (lazy-loaded)", depot_tree, SIZE),
    ("06-workspace-tree", "Workspace tree with status markers",
     workspace_tree, SIZE),
    ("07-file-viewer", "In-app file viewer (Enter on a leaf)",
     file_viewer, SIZE),
    ("08-command-monitor", "Command Monitor (F2)", command_monitor, SIZE),
    ("09-fast-search", "Fast Search (Ctrl+F)", fast_search, SIZE),
    ("10-goto-path", "Go to path (Ctrl+G)", goto_path, SIZE),
    ("11-context-menu", "Pending context menu (m)", context_menu, SIZE),
    ("12-narrow-mode", "Narrow mode page navigator (48 cells)",
     narrow_mode, NARROW),
]


def _scrub_svg(path):
    """Flag anything in the SVG that isn't a synthetic demo value.

    Returns True if clean. Uses an allowlist (synthetic namespaces + the
    demo email domain) so no real identifier is ever named in this file.
    """
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return True
    suspects = set()
    for tok in _DEPOT_RE.findall(svg):
        if tok != "//" and not any(tok.startswith(ns) for ns in _ALLOWED_NS):
            suspects.add(tok)
    for tok in _EMAIL_RE.findall(svg):
        if not (tok.endswith("example.com") or "alice" in tok):
            suspects.add(tok)
    if _REAL_HOME and _REAL_HOME in svg:
        suspects.add("<real $HOME>")
    if suspects:
        print(f"    !! non-synthetic token(s) in {os.path.basename(path)}: "
              f"{sorted(suspects)}")
        return False
    return True


async def _shoot(name, desc, drive, size):
    app = _new_app()
    path = os.path.join(OUT_DIR, name + ".svg")
    async with app.run_test(size=size) as pilot:
        ok = await _wait_connected(pilot, app)
        if not ok:
            print(f"  … {name}: never connected/loaded")
        await _settle(pilot, n=4)
        try:
            await drive(app, pilot)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}: driver error {type(e).__name__}: {e}")
        app.refresh()
        await _settle(pilot, n=3)
        os.makedirs(OUT_DIR, exist_ok=True)
        app.save_screenshot(path)
    clean = _scrub_svg(path)
    flag = "✓" if clean else "⚠ scrubbed"
    print(f"  {flag} {name}.svg  — {desc}")
    return path


async def main(filt=None):
    todo = [s for s in SCENES if not filt or filt in s[0]]
    if not todo:
        print(f"no scene matches {filt!r}. available: " +
              ", ".join(s[0] for s in SCENES))
        return 1
    print(f"generating screenshots → {OUT_DIR}")
    for name, desc, drive, size in todo:
        try:
            await _shoot(name, desc, drive, size)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(asyncio.run(main(arg)))
