"""End-to-end tests for the perceived-performance feel layer wiring.

The pure policy is covered by ``test_perf_feel.py``; this checks the app
wiring — the activity registry drives the ConnectionBar activity suffix,
honours the show-after-delay threshold, and clears cleanly. Timing is made
deterministic by back-dating the registry entry rather than sleeping.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from demo_backend import DemoBackend  # noqa: E402

from test_e2e_gestures import (  # noqa: E402
    _isolated_home, _make_app, _record_notifications, _wait_until,
)


def test_activity_indicator_hidden_until_delay_then_shows(tmp_path, monkeypatch):
    from p4v_tui.app_shared import ConnectionBar
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            bar = app.query_one(ConnectionBar)
            # idle: no activity text in the bar
            assert "Loading changelists" not in str(bar.render())

            tok = app._begin_activity("Loading changelists")
            # just started (< 150 ms) -> still hidden after a tick
            app._tick_activity()
            assert "Loading changelists" not in str(bar.render())

            # back-date so it's crossed the threshold, then tick
            app._activity[tok] = ("Loading changelists", time.monotonic() - 0.5)
            app._tick_activity()
            assert "Loading changelists" in str(bar.render())

            # ending the last activity clears it from the bar
            app._end_activity(tok)
            assert "Loading changelists" not in str(bar.render())

    asyncio.run(_run())


def test_activity_label_escalates_when_slow(tmp_path, monkeypatch):
    from p4v_tui.app_shared import ConnectionBar
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            bar = app.query_one(ConnectionBar)
            tok = app._begin_activity("Loading submitted")
            # 2 s in flight -> "still working" nudge
            app._activity[tok] = ("Loading submitted", time.monotonic() - 2.0)
            app._tick_activity()
            assert "Loading submitted" in str(bar.render())
            assert "still working" in str(bar.render())
            app._end_activity(tok)

    asyncio.run(_run())


def test_activity_oldest_op_drives_label(tmp_path, monkeypatch):
    from p4v_tui.app_shared import ConnectionBar
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            bar = app.query_one(ConnectionBar)
            t1 = app._begin_activity("Loading changelists")
            t2 = app._begin_activity("Loading history")
            # back-date the first so it's the oldest + over threshold
            app._activity[t1] = ("Loading changelists", time.monotonic() - 0.6)
            app._activity[t2] = ("Loading history", time.monotonic() - 0.2)
            app._tick_activity()
            assert "Loading changelists" in str(bar.render())
            # ending the oldest leaves the other still tracked
            app._end_activity(t1)
            assert app._activity  # t2 still in flight
            app._end_activity(t2)
            assert "Loading changelists" not in str(bar.render())
            assert "Loading history" not in str(bar.render())

    asyncio.run(_run())


def test_adaptive_refresh_backs_off_on_slow_latency(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # fix the configured base so the test is deterministic
            app._ui_state["auto_refresh_pending_seconds"] = 30
            # fast link: no/low latency -> ~base cadence
            app._recent_latencies_ms.clear()
            app._recent_latencies_ms.append(50.0)
            app._schedule_next_pending_refresh()
            fast = app._pending_auto_refresh_timer._interval
            # slow link: multi-second latency -> backed off, but capped
            app._recent_latencies_ms.clear()
            app._recent_latencies_ms.extend([3000.0, 3000.0])
            app._schedule_next_pending_refresh()
            slow = app._pending_auto_refresh_timer._interval
            assert slow > fast
            assert fast >= 30          # never faster than configured base
            assert slow <= 30 * 4      # capped at 4x base

    asyncio.run(_run())


def test_adaptive_refresh_disabled_when_base_zero(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._ui_state["auto_refresh_pending_seconds"] = 0  # disabled
            app._schedule_next_pending_refresh()
            assert app._pending_auto_refresh_timer is None

    asyncio.run(_run())


def test_connection_bar_shows_reconnecting_then_restores(tmp_path, monkeypatch):
    from p4v_tui.app_shared import ConnectionBar
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            # wait for connect so _p4_info is stashed + bar has the good line
            assert await _wait_until(pilot, lambda: app._p4_info is not None)
            bar = app.query_one(ConnectionBar)
            assert "Server:" in str(bar.render())
            # a reconnect attempt surfaces in the bar
            app._show_reconnecting(2, 10)
            await pilot.pause()
            assert "Reconnecting" in str(bar.render())
            assert "2/10" in str(bar.render())
            # recovery restores the normal Server/User/… line
            app._show_reconnected()
            await pilot.pause()
            assert "Server:" in str(bar.render())
            assert "Reconnecting" not in str(bar.render())

    asyncio.run(_run())


def test_service_reconnect_callbacks_wired_after_connect(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _wait_until(pilot, lambda: app._p4_info is not None)
            # the service-level reconnect hooks point at the app handlers
            assert app.p4._on_retry == app._p4_on_retry
            assert app.p4._on_recover == app._p4_on_recover

    asyncio.run(_run())


def test_app_routes_optimistic_marker_to_the_tree(tmp_path, monkeypatch):
    from p4v_tui.widgets.workspace_tree import WorkspaceTree
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tree = app.query_one(WorkspaceTree)
            # add a synthetic file leaf so we don't depend on fstat data
            node = tree.root.add_leaf("e foo.py#3", data="//c/foo.py")
            await pilot.pause()
            app._mark_node_pending(node)
            assert tree._plain(node.label).startswith(tree.PENDING_GLYPH)
            app._clear_node_pending(node)
            assert tree._plain(node.label) == "e foo.py#3"

    asyncio.run(_run())


def test_action_label_maps_known_and_falls_back(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    assert app._action_label("edit") == "Opening for edit"
    assert app._action_label("revert") == "Reverting"
    assert app._action_label("sync") == "Get latest"
    # unknown action -> humanised fallback, never crashes
    assert app._action_label("some_custom") == "Some custom"


def test_activity_timer_stops_when_idle(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    app = _make_app(DemoBackend())
    _record_notifications(app)

    async def _run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tok = app._begin_activity("x")
            assert app._activity_timer is not None  # timer running
            app._end_activity(tok)
            # a tick with nothing in flight stops + clears the timer
            app._tick_activity()
            assert app._activity_timer is None

    asyncio.run(_run())
