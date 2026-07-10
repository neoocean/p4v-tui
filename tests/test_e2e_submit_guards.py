"""End-to-end TUI gestures for the pre-submit confirm surface (Ctrl+S).

Automates the `docs/handoff-manual-tests.md` Priority-A "Submit guards"
and "Jira at submit" checks that were still manual:

* a CL with an unresolved file and a ≥ 25 MB file lists the matching
  ⛔ / ⚠ warnings in the confirm dialog (and the OK button demotes to
  "Submit anyway" / warning variant),
* an empty CL shows the ⛔ empty-changelist block,
* a clean CL shows no warnings and a plain "Submit" button,
* with ``[jira] base_url`` set, a description carrying an issue key gets
  the "🔗 Jira: KEY → browse-url" note and one without gets the
  "⚠ No Jira issue referenced" warning; with no ``[jira]`` config there
  is no Jira line at all (covered implicitly by every other test here),
* a *remote* workspace's pending CL refuses Ctrl+S with a toast instead
  of opening the confirm modal.

The pure guard logic is already pinned by ``test_submit_guards.py`` /
``test_jira.py``; what these tests own is the **wiring**: Ctrl+S →
``_gather_submit_files`` (real ``opened`` / ``fstat -Ol`` / ``fstat -Ru``
calls against the backend) → ``evaluate_submit_guards`` +
``_jira_submit_note`` → ConfirmModal content.

Same dependency-free pattern as ``test_e2e_gestures.py``: synthetic
``DemoBackend`` + Textual's headless ``run_test()`` pilot.
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

# DemoBackend's CL 4231 opens exactly these two files.
BIG = "//depot/demo/src/config.py"       # made oversized (30 MB)
UNRES = "//depot/demo/docs/MANUAL.md"    # made unresolved
EMPTY_CL = "4228"                        # opened -c 4228 forced to []
REMOTE_CL = "4219"                       # lives on alice-linux, not alice-mbp


class _GuardBackend(DemoBackend):
    """DemoBackend whose fstat probes report guard-triggering state.

    ``_gather_submit_files`` sizes files via ``fstat -Ol`` (no clientFile
    here, so it falls back to the depot ``fileSize``) and detects
    unresolved ones via ``fstat -Ru``.
    """

    def run_tagged(self, args):
        a = [str(x) for x in args]
        if a[:2] == ["fstat", "-Ol"]:
            return [{"depotFile": BIG, "fileSize": str(30 * 1024 * 1024)}]
        if a[:2] == ["fstat", "-Ru"]:
            return [{"depotFile": UNRES}]
        if a[:3] == ["opened", "-c", EMPTY_CL]:
            return []
        return super().run_tagged(args)


class _JiraDescBackend(DemoBackend):
    """DemoBackend whose describe() carries a Jira issue key."""

    def run_tagged(self, args):
        rows = super().run_tagged(args)
        a = [str(x) for x in args]
        if a and a[0] == "describe" and rows:
            rows[0]["desc"] = "DEMO-42: validate config input early"
        return rows


async def _open_submit_confirm(pilot, app, change):
    """Put the cursor on ``change`` in the Pending table and press Ctrl+S."""
    from textual.widgets import DataTable, TabbedContent
    tabs = app.query_one("#right_tabs", TabbedContent)
    tabs.active = "tab_pending"
    await pilot.pause()
    table = app.query_one("#pending_table", DataTable)
    for i in range(table.row_count):
        if str(table.get_row_at(i)[0]) == change:
            table.move_cursor(row=i)
            break
    else:
        raise AssertionError(f"CL {change} not in the pending table")
    app.set_focus(table)
    await pilot.pause()
    await pilot.press("ctrl+s")


async def _wait_confirm(pilot, app):
    from p4v_tui.widgets.confirm import ConfirmModal
    assert await _wait_until(
        pilot, lambda: isinstance(app.screen, ConfirmModal)), \
        "Ctrl+S did not open the submit ConfirmModal"
    return app.screen


def test_submit_confirm_lists_unresolved_and_oversized(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(_GuardBackend())
    notes = _record_notifications(app)

    async def body():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            await _open_submit_confirm(pilot, app, "4231")
            modal = await _wait_confirm(pilot, app)

            msg = modal._message
            # ⛔ unresolved block names the file; ⚠ large-file warn names
            # the size threshold and the file.
            assert "⛔" in msg and "still need resolve" in msg
            assert UNRES in msg
            assert "⚠" in msg and "≥ 25 MB" in msg
            assert BIG in msg
            # Button demotes to the warning form.
            assert modal._ok_label == "Submit anyway"
            assert modal._ok_variant == "warning"

            # Back out — nothing must be queued.
            await pilot.press("escape")
            await pilot.pause()
            assert not any("Queued resilient submit" in n for n in notes)

    asyncio.run(body())


def test_submit_confirm_blocks_empty_changelist(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(_GuardBackend())
    _record_notifications(app)

    async def body():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            await _open_submit_confirm(pilot, app, EMPTY_CL)
            modal = await _wait_confirm(pilot, app)
            assert "⛔" in modal._message
            assert "no open files" in modal._message
            assert modal._ok_label == "Submit anyway"
            await pilot.press("escape")

    asyncio.run(body())


def test_submit_confirm_clean_cl_has_no_warnings(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    # Base DemoBackend: sizes unknown, nothing unresolved → no warnings.
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def body():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            await _open_submit_confirm(pilot, app, "4231")
            modal = await _wait_confirm(pilot, app)
            assert "⛔" not in modal._message
            assert "⚠" not in modal._message
            # No [jira] config → no Jira line of either kind.
            assert "Jira" not in modal._message
            assert modal._ok_label == "Submit"
            assert modal._ok_variant == "primary"
            await pilot.press("escape")

    asyncio.run(body())


def test_submit_confirm_jira_note_with_and_without_key(tmp_path, monkeypatch):
    from p4v_tui.config import JiraConfig

    _isolated_home(tmp_path, monkeypatch)

    # (a) description carries DEMO-42 → linked note with the browse URL.
    app = _make_app(_JiraDescBackend())
    app.config.jira = JiraConfig(base_url="https://jira.example")
    _record_notifications(app)

    async def body_linked():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            await _open_submit_confirm(pilot, app, "4231")
            modal = await _wait_confirm(pilot, app)
            assert "🔗 Jira: DEMO-42 → https://jira.example/browse/DEMO-42" \
                in modal._message
            await pilot.press("escape")

    asyncio.run(body_linked())

    # (b) same config, key-less demo description → the warning form.
    app2 = _make_app(DemoBackend())
    app2.config.jira = JiraConfig(base_url="https://jira.example")
    _record_notifications(app2)

    async def body_warned():
        async with app2.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app2)
            await _open_submit_confirm(pilot, app2, "4231")
            modal = await _wait_confirm(pilot, app2)
            assert "⚠ No Jira issue referenced" in modal._message
            await pilot.press("escape")

    asyncio.run(body_warned())


def test_submit_refuses_remote_workspace_cl(tmp_path, monkeypatch):
    from p4v_tui.widgets.confirm import ConfirmModal

    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    notes = _record_notifications(app)

    async def body():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            await _open_submit_confirm(pilot, app, REMOTE_CL)
            # No modal — a refusal toast naming the owning workspace.
            assert await _wait_until(
                pilot,
                lambda: any("workspace" in n for n in notes)), \
                "remote-CL Ctrl+S should refuse with a workspace toast"
            assert not isinstance(app.screen, ConfirmModal)

    asyncio.run(body())
