"""End-to-end TUI gestures for the Fast Search row actions (`d` / `g`).

The last "automatable but still manual" item on
``docs/handoff-manual-tests.md`` Priority B: inside the Ctrl+F Fast
Search modal, with focus on the *results list* (not the query Input),

* ``d`` diffs the highlighted hit against the have revision, and
* ``g`` get-latests it (chunked / resilient),

while the same letters typed into the focused query Input must go into
the query string instead of firing the action (single-letter bindings
only apply outside the Input — by design).

The blocker was the index fixture: SearchModal queries a real SQLite
``SearchIndex``, so these tests seed one in tmp_path (same pattern as
``test_search_index_delete_filter.py``) and swap it in after connect —
the modal itself can't tell it apart from the app-built index.

Same dependency-free pattern as ``test_e2e_gestures.py``: synthetic
``DemoBackend`` + Textual's headless ``run_test()`` pilot, plain sync
tests driving an async body via ``asyncio.run``.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from demo_backend import DemoBackend  # noqa: E402

# Reuse the shared helpers from the sibling e2e module.
from test_e2e_gestures import (  # noqa: E402
    _isolated_home, _make_app, _record_notifications,
    _wait_connected, _wait_until,
)

from p4v_tui.search_index import SearchIndex  # noqa: E402

# One row whose leaf uniquely matches the test query, plus noise rows so
# the modal renders a multi-row list (ranking must still put the exact
# leaf hit first — query_files ranks by leaf hits then recency).
TARGET = "//depot/demo/src/config.py"
SEED = [
    {"depotFile": TARGET, "action": "edit",
     "user": "alice", "type": "text", "time": 2000, "change": 4231},
    {"depotFile": "//depot/demo/src/app.py", "action": "edit",
     "user": "alice", "type": "text", "time": 1900, "change": 4205},
    {"depotFile": "//depot/demo/docs/MANUAL.md", "action": "add",
     "user": "alice", "type": "text", "time": 1800, "change": 4231},
]


def _install_seeded_index(app, tmp_path):
    """Swap the app's connect-time index for a deterministic seeded one."""
    idx = SearchIndex(tmp_path / "seeded-search.db")
    idx.open()
    idx.upsert_files(SEED)
    old = app._search_index
    if old is not None:
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass
    app._search_index = idx
    return idx


async def _open_search_with_hits(pilot, app):
    """Ctrl+F, type the query, wait for the seeded hit to render."""
    from p4v_tui.widgets.search_modal import SearchModal
    await pilot.press("ctrl+f")
    assert await _wait_until(
        pilot, lambda: isinstance(app.screen, SearchModal)), \
        "Ctrl+F did not open the SearchModal"
    modal = app.screen
    await pilot.press(*"config")
    assert await _wait_until(
        pilot,
        lambda: modal._hits
        and modal._hits[0].depot_path == TARGET), \
        f"query never returned the seeded hit (got {modal._hits!r})"
    return modal


def test_search_modal_d_diffs_highlighted_hit(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    diffs: list[str] = []
    app._diff_against_have = diffs.append  # instance attr shadows mixin

    async def body():
        from textual.widgets import Input
        from p4v_tui.widgets.search_modal import SearchModal
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            _install_seeded_index(app, tmp_path)
            modal = await _open_search_with_hits(pilot, app)

            # Gating: with the query Input focused, `d` is a keystroke,
            # not an action — the modal stays up, nothing dispatches,
            # and the letter lands in the query string.
            inp = modal.query_one("#query", Input)
            assert app.focused is inp, "query Input should own focus"
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, SearchModal)
            assert diffs == []
            assert inp.value.endswith("d")
            await pilot.press("backspace")   # restore "config"
            assert await _wait_until(
                pilot,
                lambda: modal._hits
                and modal._hits[0].depot_path == TARGET)

            # Move focus to the results list — now `d` is the action.
            app.set_focus(modal.query_one("#results"))
            await pilot.pause()
            await pilot.press("d")
            assert await _wait_until(
                pilot, lambda: not isinstance(app.screen, SearchModal)), \
                "d on the results list should dismiss the modal"
            assert diffs == [TARGET]

    asyncio.run(body())


def test_search_modal_g_get_latests_highlighted_hit(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    enqueued: list[tuple[object, str]] = []
    app._enqueue_chunked = lambda job, target: enqueued.append((job, target))

    async def body():
        from p4v_tui.widgets.search_modal import SearchModal
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_connected(pilot, app)
            _install_seeded_index(app, tmp_path)
            modal = await _open_search_with_hits(pilot, app)

            app.set_focus(modal.query_one("#results"))
            await pilot.pause()
            await pilot.press("g")
            assert await _wait_until(
                pilot, lambda: not isinstance(app.screen, SearchModal)), \
                "g on the results list should dismiss the modal"

            # The app must route the dismissal into the real chunked-sync
            # path: a ChunkedSyncJob built for the highlighted hit.
            from p4v_tui.sync_job import ChunkedSyncJob
            assert len(enqueued) == 1
            job, target = enqueued[0]
            assert target == TARGET
            assert isinstance(job, ChunkedSyncJob)

    asyncio.run(body())
