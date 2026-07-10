"""End-to-end TUI gestures for partial shelve and tree bulk actions.

Automates two more `docs/handoff-manual-tests.md` Priority-A checks:

* **Partial shelve** — the ShelvePickerModal lists the CL's open files
  all-checked; unchecking some shelves only the checked subset
  (explicit paths on the ``p4 shelve`` argv), while leaving everything
  checked omits the file list entirely (whole-CL shelve, the old
  behaviour).
* **Tree multi-select bulk** — ``Space``-marked workspace-tree files
  drive ONE bulk call: ``e`` opens them for edit in a single fresh
  *numbered* CL (never the default changelist), and ``r`` prompts
  exactly once with the full target list before reverting.

What these tests own is the wiring from gesture to p4 argv — the modal
selection round trip and the marked-set fan-in. The argv is captured by
a recording backend; job internals are covered elsewhere.

Same dependency-free pattern as ``test_e2e_gestures.py``.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from demo_backend import CLIENT, DemoBackend  # noqa: E402

from test_e2e_gestures import (  # noqa: E402
    _isolated_home, _make_app, _record_notifications,
    _wait_connected, _wait_until,
)

# DemoBackend CL 4231's open files, in `opened -c` order.
FILE_A = "//depot/demo/src/config.py"
FILE_B = "//depot/demo/docs/MANUAL.md"

# Two workspace files for the bulk gestures (client-syntax walk paths).
BULK_1 = "//depot/demo/src/app.py"
BULK_2 = "//depot/demo/src/config.py"


class _RecordingBackend(DemoBackend):
    """DemoBackend that records every tagged argv and fakes the mutating
    verbs (shelve / edit / revert) with believable per-file rows."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[list[str]] = []

    def run_tagged(self, args):
        a = [str(x) for x in args]
        self.calls.append(a)
        if a and a[0] in ("shelve", "edit", "revert"):
            return [{"depotFile": x, "action": a[0]}
                    for x in a[1:] if x.startswith("//depot")] \
                or [{"change": a[2] if len(a) > 2 else "", "action": a[0]}]
        return super().run_tagged(args)

    def calls_for(self, verb: str) -> list[list[str]]:
        return [c for c in self.calls if c and c[0] == verb]


# --- partial shelve -------------------------------------------------------

async def _open_shelve_picker(pilot, app):
    from p4v_tui.widgets.shelve_picker_modal import ShelvePickerModal
    app._run_shelve_interactive("4231")
    assert await _wait_until(
        pilot, lambda: isinstance(app.screen, ShelvePickerModal)), \
        "ShelvePickerModal never opened"
    return app.screen


def test_partial_shelve_shelves_only_checked_files(tmp_path, monkeypatch):
    from textual.widgets import SelectionList

    _isolated_home(tmp_path, monkeypatch)
    backend = _RecordingBackend()
    app = _make_app(backend)
    notes = _record_notifications(app)

    async def body():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            modal = await _open_shelve_picker(pilot, app)

            # All files start checked (all-selected == shelve everything).
            sel = modal.query_one("#files", SelectionList)
            assert set(sel.selected) == {FILE_A, FILE_B}

            # Uncheck everything, re-check only the first row (FILE_A).
            await pilot.press("n")
            assert list(sel.selected) == []
            await pilot.press("space")
            assert list(sel.selected) == [FILE_A]

            await pilot.press("enter")
            assert await _wait_until(
                pilot, lambda: backend.calls_for("shelve"))
            # Subset → explicit path list on the argv.
            assert backend.calls_for("shelve") == [
                ["shelve", "-c", "4231", FILE_A]]
            assert await _wait_until(
                pilot, lambda: any("Shelved 1 file" in n for n in notes))

    asyncio.run(body())


def test_shelve_all_checked_omits_file_list(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    backend = _RecordingBackend()
    app = _make_app(backend)
    _record_notifications(app)

    async def body():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            await _open_shelve_picker(pilot, app)
            # Everything left checked → whole-CL shelve, no file args.
            await pilot.press("enter")
            assert await _wait_until(
                pilot, lambda: backend.calls_for("shelve"))
            assert backend.calls_for("shelve") == [["shelve", "-c", "4231"]]

    asyncio.run(body())


# --- tree multi-select bulk ----------------------------------------------

async def _mark_two_workspace_files(pilot, app):
    """Space-mark BULK_1 and BULK_2 in the workspace tree; return it."""
    from textual.widgets import TabbedContent
    from p4v_tui.widgets.workspace_tree import WorkspaceTree

    app.query_one("#left_tabs", TabbedContent).active = "tab_workspace"
    await pilot.pause()
    tree = app.query_one(WorkspaceTree)
    app.set_focus(tree)
    tree.root.expand()

    for depot_path in (BULK_1, BULK_2):
        # Workspace dirs are client-syntax; the final depot-keyed leaf is
        # matched by the basename fallback (the CL 57867 navigation fix).
        client_path = depot_path.replace("//depot", f"//{CLIENT}")
        tree.navigate_to_path(client_path)
        assert await _wait_until(
            pilot,
            lambda: getattr(tree.cursor_node, "data", None) == depot_path), \
            f"cursor never reached {depot_path}"
        await pilot.press("space")
    assert tree.has_marks() and len(tree.marked_specs()) == 2
    return tree


def test_bulk_edit_lands_in_one_numbered_cl(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    backend = _RecordingBackend()
    app = _make_app(backend)
    notes = _record_notifications(app)

    async def body():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            await _mark_two_workspace_files(pilot, app)

            await pilot.press("e")
            assert await _wait_until(
                pilot, lambda: backend.calls_for("edit"))
            # ONE call, BOTH files, isolated in the fresh numbered CL the
            # DemoBackend's save_form mints ("Change 9999 created.").
            assert backend.calls_for("edit") == [
                ["edit", "-c", "9999", BULK_1, BULK_2]]
            assert await _wait_until(
                pilot,
                lambda: any("Bulk edit" in n and "(CL 9999)" in n
                            for n in notes))

    asyncio.run(body())


def test_bulk_revert_prompts_once_with_full_list(tmp_path, monkeypatch):
    from p4v_tui.widgets.confirm import ConfirmModal

    _isolated_home(tmp_path, monkeypatch)
    backend = _RecordingBackend()
    app = _make_app(backend)
    _record_notifications(app)

    async def body():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            await _mark_two_workspace_files(pilot, app)

            await pilot.press("r")
            assert await _wait_until(
                pilot, lambda: isinstance(app.screen, ConfirmModal)), \
                "bulk revert must confirm before running"
            modal = app.screen
            # One prompt, full target list in the message.
            assert "2 target(s)" in modal._title
            assert BULK_1 in modal._message and BULK_2 in modal._message

            # Confirm via the OK button (bare Enter would activate the
            # initially-focused Cancel button instead).
            from textual.widgets import Button
            app.set_focus(modal.query_one("#ok", Button))
            await pilot.press("enter")
            assert await _wait_until(
                pilot, lambda: backend.calls_for("revert"))
            # Revert is not CL-isolated — one call, both files, no -c.
            assert backend.calls_for("revert") == [
                ["revert", BULK_1, BULK_2]]

    asyncio.run(body())
