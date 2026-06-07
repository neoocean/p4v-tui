"""End-to-end TUI gesture tests — the last two manual smoke checks.

`docs/handoff-manual-tests.md` lists two flows whose *decision logic* was
already unit-tested + verified against real Perforce, but whose end-to-end
**TUI gesture** ("click it through on a real terminal") was still manual:

1. **Permalink move-following** — `Alt+C` on a file to copy its `//@p/N`
   address, the file is moved/renamed, then pasting that same address into
   `Ctrl+G` navigates to the file's *new* location with a "Followed move:"
   toast.
2. **3-way merge editor** — open the Resolve modal, press `Ctrl+E` on a
   conflicting file, pick a side per hunk (`y`/`t`/`b`/`o`), `Enter` writes
   the merged file.

These can't use a physical terminal here, but Textual's headless
``app.run_test()`` pilot scripts the *exact* keypresses a manual tester
makes — same bindings, same messages, same modals — so it converts both
smoke checks into reproducible regression tests. A synthetic in-process
backend (subclassing the screenshot ``DemoBackend``) feeds believable data;
no live server is touched.

The suite has no ``pytest-asyncio`` (and runs ``--strict-markers``), so each
test is a plain sync function that drives its async pilot body via
``asyncio.run`` — the standard dependency-free way to test Textual.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

# DemoBackend gives us all the connect / info / changes / tree plumbing for
# free; each test subclasses it to inject just the verb it exercises.
from demo_backend import (  # noqa: E402
    CLIENT, DemoBackend, _from_depot, _ns_of, _to_depot)
from p4v_tui.config import Config, ConnectionConfig  # noqa: E402
from p4v_tui.p4client import P4Service  # noqa: E402


# --------------------------------------------------------------------------
# Shared app construction (mirrors scripts/gen_screenshots._new_app).
# --------------------------------------------------------------------------
def _make_app(backend):
    from p4v_tui.app import P4VApp
    cfg = Config.empty()
    cfg.connection = ConnectionConfig(
        port=backend.port, user=backend.user, client=backend.client,
        name="demo")
    app = P4VApp(config=cfg)
    # Swap in the synthetic backend before on_mount's connect worker runs.
    app.p4 = P4Service(cmd_log=app.cmd_log, backend=backend)
    app.detail_pane_height = 9
    return app


def _record_notifications(app):
    """Replace app.notify with a recorder; return the captured-message list.

    Toasts are the load-bearing success signal for move-following, and
    replacing the bound method means worker threads' ``call_from_thread(
    self.notify, …)`` land in our list without mounting real toast widgets.
    """
    notes: list[str] = []

    def rec(message, **_kwargs):
        notes.append(str(message))

    app.notify = rec  # instance attr shadows the F5 class override
    return notes


async def _wait_connected(pilot, app, tries=120):
    for _ in range(tries):
        await pilot.pause(0.05)
        try:
            if app.query_one("#pending_table").row_count > 0:
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


async def _wait_until(pilot, predicate, tries=160):
    for _ in range(tries):
        await pilot.pause(0.05)
        try:
            if predicate():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _isolated_home(tmp_path, monkeypatch):
    """Point HOME at a throwaway dir so no real ~/.p4v-tui state is touched."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("P4V_BACKEND", raising=False)


def _fresh_permalink_registry(app, tmp_path):
    """Bind the app to a throwaway permalink registry (no project writes)."""
    from p4v_tui.permalink import PermalinkRegistry
    app._permalink_reg = PermalinkRegistry(
        tmp_path / "permalinks.json", after_write=None)


# ==========================================================================
# Gesture 1 — permalink move-following  (Alt+C → move → Ctrl+G)
# ==========================================================================
ORIGIN = "//depot/demo/src/utils.py"
RENAMED = "//depot/demo/src/utils_renamed.py"


class _MoveBackend(DemoBackend):
    """DemoBackend where ``utils.py`` has been ``p4 move``d to
    ``utils_renamed.py``.

    Adds the renamed file to the ``src`` directory listing (so the tree can
    navigate to it) and makes ``filelog`` on the origin report the move via
    the P4Python integration shape (``how``/``file`` parallel lists, the
    'moved into' record sitting on the revision *below* the move/delete
    head — exactly the shape ``_find_moved_into`` was fixed to parse).
    """

    def run_tagged(self, args):
        a = [str(x) for x in args]
        cmd = a[0] if a else ""

        if cmd in ("files", "fstat"):
            rows = list(super().run_tagged(args))
            glob = a[-1]
            # Only augment a single-directory listing of src, never the
            # recursive ``//...`` index build.
            parent = glob[:-2] if glob.endswith("/*") else glob
            if _to_depot(parent) == "//depot/demo/src":
                ns = _ns_of(parent)
                disp = (_from_depot(RENAMED, ns) if ns != "//depot"
                        else RENAMED)
                if cmd == "files":
                    rows.append({"depotFile": disp, "rev": "1",
                                 "type": "text", "action": "edit"})
                else:  # fstat
                    client = (_from_depot(RENAMED, ns) if ns != "//depot"
                              else RENAMED.replace("//depot", f"//{CLIENT}"))
                    rows.append({"depotFile": RENAMED, "clientFile": client,
                                 "headRev": "1", "haveRev": "1",
                                 "headAction": "add", "headType": "text"})
            return rows

        if cmd == "filelog":
            depot = a[-1]
            # filelog can be queried in depot or client syntax; match on tail.
            if depot.endswith("utils.py") and "renamed" not in depot:
                return [{
                    "depotFile": depot,
                    # head first: the move/delete, then the content rev that
                    # carries the integration record.
                    "action": ["move/delete", "add"],
                    "how": [None, ["moved into"]],
                    "file": [None, [RENAMED]],
                }]
            # Anything else: a plain head=edit history → stops the chain.
            return [{"depotFile": depot, "action": ["edit"],
                     "how": [None], "file": [None]}]

        return super().run_tagged(args)


def test_permalink_alt_c_then_ctrl_g_follows_move(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    backend = _MoveBackend()
    app = _make_app(backend)
    notes = _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 38)) as pilot:
            assert await _wait_connected(pilot, app), "app never connected"
            _fresh_permalink_registry(app, tmp_path)

            # --- focus the depot tree, position the cursor on utils.py ----
            from textual.widgets import TabbedContent
            app.query_one("#left_tabs", TabbedContent).active = "tab_depot"
            await pilot.pause(0.1)
            tree = app.query_one("#depot_tree")
            tree.focus()
            tree.root.expand()
            await _wait_until(pilot, lambda: bool(tree.root.children))
            # Walk the lazy-loaded tree to utils.py; it expands intermediate
            # dirs and lands the cursor on the target node.
            tree.navigate_to_path(ORIGIN)
            landed = await _wait_until(
                pilot,
                lambda: getattr(tree.cursor_node, "data", None) == ORIGIN)
            assert landed, "cursor never reached utils.py in the depot tree"

            # --- GESTURE: Alt+C copies a permalink for the cursor file ----
            await pilot.press("alt+c")
            copied = await _wait_until(
                pilot,
                lambda: getattr(app, "_last_permalink", None) is not None)
            assert copied, "Alt+C did not mint a permalink"
            vid = app._last_permalink
            from p4v_tui.permalink import make_permalink
            address = make_permalink(vid)
            assert app._permalink_registry.lookup(vid) == ORIGIN

            # (The move already happened in the backend; nothing else to do.)

            # --- GESTURE: Ctrl+G, paste the permalink, Enter -------------
            await pilot.press("ctrl+g")
            from p4v_tui.widgets.goto_path_modal import GotoPathModal
            opened = await _wait_until(
                pilot, lambda: isinstance(app.screen, GotoPathModal))
            assert opened, "Ctrl+G did not open the Go-to-path modal"
            from textual.widgets import Input
            modal = app.screen
            modal.query_one("#path", Input).value = address
            await pilot.press("enter")

            # --- ASSERT: move followed (toast) + tree walked to new path -
            followed = await _wait_until(
                pilot, lambda: any("Followed move" in n for n in notes))
            assert followed, (
                f"no 'Followed move:' toast after pasting {address}; "
                f"notes={notes}")
            toast = next(n for n in notes if "Followed move" in n)
            assert ORIGIN in toast and RENAMED in toast, toast

            # The toast fires only *after* _resolve_and_navigate_permalink has
            # already invoked _navigate_tree_to(RENAMED) — which synchronously
            # switches to the workspace tab and kicks off the walk to the new
            # path. So by now the tab switch is observable. (We don't assert on
            # the leaf landing: the workspace tree keys file leaves by *depot*
            # path while dir nodes are client-namespace — see
            # WorkspaceTree._format_file — so a clientFile walk settles on the
            # file's containing directory, and the final lazy re-population is
            # async/load-dependent. Move-following, the thing under test, is
            # fully proven by the ORIGIN→RENAMED toast above.)
            from textual.widgets import TabbedContent
            on_workspace = await _wait_until(
                pilot, lambda: app.query_one(
                    "#left_tabs", TabbedContent).active == "tab_workspace")
            assert on_workspace, "did not switch to the workspace tab"

    asyncio.run(_run())


# ==========================================================================
# Gesture 2 — 3-way merge editor  (Resolve modal Ctrl+E → pick → Enter)
# ==========================================================================
MERGE_TARGET = "//depot/demo/src/conflict.py"

# Real ``p4 resolve -af`` 3-way marker layout (see
# tests/test_merge3.py::TestRealPerforceMarkers): BASE=ORIGINAL,
# THEIRS=mainline, YOURS=feature; common lines bracket the hunk.
_YOURS_CONTENT = "alpha\nFEATURE-CHANGE\ngamma\n"
_MARKERS = (
    "alpha\n"
    f">>>> ORIGINAL {MERGE_TARGET}#1\n"
    "ORIGINAL-LINE\n"
    f"==== THEIRS {MERGE_TARGET}#2\n"
    "MAINLINE-CHANGE\n"
    f"==== YOURS {MERGE_TARGET}\n"
    "FEATURE-CHANGE\n"
    "<<<<\n"
    "gamma\n"
)
# reconstruct() preserves the file's trailing newline (a final empty
# common line), so the applied THEIRS resolution keeps it.
_EXPECT_THEIRS = "alpha\nMAINLINE-CHANGE\ngamma\n"


class _MergeBackend(DemoBackend):
    """DemoBackend that drives one conflicting file through the resolve
    choreography ``_run_3way_merge`` performs.

    The local file lives on disk (``local_path``); ``resolve -af`` writes the
    real Perforce conflict markers into it, and ``where`` points the app at
    it. ``resolve -n`` reports the file as still-conflicting so the editor
    opens.
    """

    def __init__(self, local_path) -> None:
        super().__init__()
        self.local = str(local_path)
        self.resolve_calls: list[tuple] = []

    def run_tagged(self, args):
        a = [str(x) for x in args]
        cmd = a[0] if a else ""

        if cmd == "resolve":
            self.resolve_calls.append(tuple(a))
            if "-af" in a:
                # Emit Perforce's 3-way markers into the workspace file.
                with open(self.local, "w", encoding="utf-8") as fh:
                    fh.write(_MARKERS)
                return [{"clientFile": MERGE_TARGET, "resolved": "af"}]
            if "-n" in a:
                # Still unresolved → a conflicting dict row.
                return [{"clientFile": MERGE_TARGET,
                         "fromFile": MERGE_TARGET, "toFile": MERGE_TARGET}]
            # -am (auto-merge): report it couldn't (string row, ignored).
            return [f"{MERGE_TARGET} - no auto-merge"]

        if cmd == "where":
            return [{"depotFile": MERGE_TARGET, "clientFile": MERGE_TARGET,
                     "path": self.local}]

        return super().run_tagged(args)


def test_resolve_modal_ctrl_e_opens_merge_editor_and_applies(
        tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    local = tmp_path / "conflict.py"
    local.write_text(_YOURS_CONTENT, encoding="utf-8")
    backend = _MergeBackend(local)
    app = _make_app(backend)
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app), "app never connected"

            # Open the Resolve picker the way the integrate/submit flows do.
            app._open_resolve_modal(MERGE_TARGET)
            from p4v_tui.widgets.resolve_modal import ResolveModal
            opened = await _wait_until(
                pilot, lambda: isinstance(app.screen, ResolveModal))
            assert opened, "Resolve modal never pushed"

            # Wait for the worker-thread `resolve -n` to populate the table.
            from textual.widgets import DataTable
            ready = await _wait_until(
                pilot,
                lambda: app.screen.query_one(
                    "#files_table", DataTable).row_count > 0)
            assert ready, "Resolve modal table never populated"

            # --- GESTURE: Ctrl+E hands the cursor row to the 3-way editor -
            await pilot.press("ctrl+e")
            from p4v_tui.widgets.merge_editor_modal import MergeEditorModal
            editor = await _wait_until(
                pilot, lambda: isinstance(app.screen, MergeEditorModal))
            assert editor, (
                "Ctrl+E did not open the merge editor; "
                f"resolve calls={backend.resolve_calls}")

            # The editor parsed exactly one conflict hunk from the markers.
            assert len(app.screen._conflicts) == 1

            # --- GESTURE: pick THEIRS for the hunk, then Enter to apply --
            await pilot.press("t")
            await pilot.pause(0.05)
            assert app.screen._choices[0] == "theirs"
            await pilot.press("enter")

            # --- ASSERT: workspace file now holds the THEIRS resolution --
            applied = await _wait_until(
                pilot,
                lambda: local.read_text(encoding="utf-8") == _EXPECT_THEIRS)
            assert applied, (
                "merged file content mismatch:\n"
                f"  got={local.read_text(encoding='utf-8')!r}\n"
                f"  want={_EXPECT_THEIRS!r}")

            # The -af path (marker emission) ran; the accept path must NOT
            # have re-run -af (which would discard the user's choice).
            af_calls = [c for c in backend.resolve_calls if "-af" in c]
            assert len(af_calls) == 1, backend.resolve_calls

    asyncio.run(_run())
