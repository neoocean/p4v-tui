"""Live-server parity tests across the P4Python and CLI backends.

Skipped unless `p4 info` succeeds (see conftest.py). When both
backends are usable, each test runs twice via the parametrized
`live_backend` fixture; equivalent assertions are made against each
backend so a divergence (e.g. CLI returning a different key shape)
shows up as a test failure rather than a silent UX bug.

These tests are *read-only* against the server. The form-write CRUD
flow (`create_changelist` / `update_changelist_description`) is
exercised in `test_p4client_live_crud.py` and gated behind a
`PYTEST_ALLOW_WRITES=1` env var so a casual `pytest` doesn't litter
a shared depot with probe changelists.
"""
from __future__ import annotations

import pytest


def test_connect_and_info(live_backend):
    info = live_backend.info()
    assert info.user, "info.user must not be empty"
    assert info.client, "info.client must not be empty"
    assert info.server_version, "info.server_version must not be empty"
    # Backend name visible via the façade so the LogPanel banner is
    # honest about which path was taken.
    assert live_backend.backend_name in ("P4Python", "p4 CLI")


def test_depots_returns_dicts(live_backend):
    depots = live_backend.depots()
    assert isinstance(depots, list)
    if depots:
        first = depots[0]
        assert isinstance(first, dict)
        assert "name" in first


def test_dirs_or_files_against_root(live_backend):
    # Pick whichever has content under it — depending on the dev's
    # workspace this might be a streams depot (only dirs) or a
    # classic flat depot (only files). Either result is valid; the
    # assertion is just "the call doesn't crash and returns a list".
    dirs = live_backend.dirs("//*")
    assert isinstance(dirs, list)


def test_fetch_client_view_nonempty_when_configured(live_backend):
    # If the user has a real workspace, the view must come back.
    # Empty client name = unmapped/temporary; skip the assertion.
    if not live_backend.client:
        pytest.skip("no client configured")
    view = live_backend.fetch_client_view()
    assert isinstance(view, list)
    if view:
        # Each line is one mapping pair.
        assert " " in view[0] or "//" in view[0]


def test_login_status_returns_dict_or_none(live_backend):
    status = live_backend.login_status()
    assert status is None or isinstance(status, dict)


def test_pending_changes_shape(live_backend):
    rows = live_backend.pending_changes(user=live_backend.user)
    assert isinstance(rows, list)
    for r in rows:
        assert isinstance(r, dict)
        # `client` is the marker the UI uses to colour remote rows; both
        # backends must include it.
        assert "client" in r


def test_submitted_changes_shape(live_backend):
    rows = live_backend.submitted_changes(max_count=3)
    assert isinstance(rows, list)
    for r in rows:
        assert isinstance(r, dict)
        assert "change" in r and "user" in r


def test_where_resolves_workspace_root_file(live_backend):
    # Probe the entry script itself — should always be in any p4v-tui
    # contributor's workspace.
    w = live_backend.where("//.../p4v.py")
    # `None` is fine for an unmapped path; if mapped, the dict must
    # carry the three keys the depot tree relies on.
    if w is not None:
        assert "depotFile" in w
        assert "clientFile" in w
        assert "path" in w


def test_run_passthrough(live_backend):
    # `p4 counter change` — universal read; both backends must return
    # a list with at least one dict carrying a `value` field.
    rows = live_backend.run("counter", "change")
    assert isinstance(rows, list)
    assert rows, "counter change must return at least one row"
    assert isinstance(rows[0], dict)
    assert "value" in rows[0]


def test_describe_file_fields_are_parallel_lists(live_backend):
    """`describe()` must return file fields as parallel LISTS on both
    backends.

    P4Python returns them natively as lists; the CLI `-G` backend emits
    numbered keys (`depotFile0`, …) that `describe()` flattens. Without
    the flatten, `info.get("depotFile")` is None on the CLI backend and
    every describe-driven file list (pending detail, show-in-tree, the
    Jira path→project map) silently empties out. Pick the most recent
    submitted change that actually touched files so the lists are
    non-empty.
    """
    recent = live_backend.submitted_changes(max_count=10)
    if not recent:
        pytest.skip("no submitted changes on this server to describe")
    for row in recent:
        change = row.get("change")
        if not change:
            continue
        info = live_backend.describe(change)
        df = info.get("depotFile")
        if not df:
            continue  # description-only change; try the next one
        assert isinstance(df, list), (
            f"depotFile must be a list, got {type(df).__name__} "
            f"on {live_backend.backend_name}"
        )
        assert all(isinstance(p, str) and p.startswith("//") for p in df)
        # rev / action run parallel to depotFile when present.
        for key in ("rev", "action"):
            val = info.get(key)
            if val is not None:
                assert isinstance(val, list)
                assert len(val) == len(df)
        return
    pytest.skip("no file-bearing submitted change found to describe")


def test_grep_stream_delivers_matches(live_backend):
    """`def main` should match this very repo's `p4v.py`."""
    matches: list[dict] = []
    n = live_backend.grep_stream(
        "def main",
        "//.../p4v.py",
        on_match=lambda r: matches.append(r),
        cancelled=lambda: False,
        case_insensitive=False,
        max_matches=5,
    )
    # Don't hard-fail if the workspace doesn't have p4v.py mapped;
    # the contract under test is "non-zero matches => row shape".
    if n == 0:
        return
    assert n == len(matches), "n must equal callbacks delivered"
    first = matches[0]
    assert isinstance(first, dict)
    assert "depotFile" in first
    assert "line" in first


def test_grep_stream_cancellation_short_circuits(live_backend):
    """A `cancelled=True` callback must stop delivery quickly."""
    matches: list[dict] = []
    # Match a hot pattern likely to hit thousands of files — without
    # cancellation this would deliver many rows; with it, we should
    # see ≤ a handful before the loop breaks. Exact count is racy
    # (the first row may already be in flight), so we only assert
    # "≤ max_matches" and "didn't hang for ages".
    import time
    cancel_after = [0]
    def cancelled():
        cancel_after[0] += 1
        return cancel_after[0] > 1  # bail after the first check
    started = time.monotonic()
    n = live_backend.grep_stream(
        "import",
        "//.../*.py",
        on_match=lambda r: matches.append(r),
        cancelled=cancelled,
        case_insensitive=True,
        max_matches=5000,
    )
    elapsed = time.monotonic() - started
    assert n <= 5000, f"max_matches must cap delivery (got {n})"
    # Wall-clock budget — cancellation should fire well within a few
    # seconds even on a slow remote depot. With the watcher thread
    # (CL 4) the CLI backend kills the subprocess within
    # `_GREP_CANCEL_POLL_S` of cancellation, so the wall-clock budget
    # could be tightened, but we leave the generous bound to avoid
    # flakiness on slow CI / a slow first match arriving.
    assert elapsed < 15.0, f"cancellation didn't bail fast ({elapsed:.1f}s)"


def test_grep_stream_repeated_calls_isolate_state(live_backend):
    """Two back-to-back grep_stream calls must not leak state.

    The Python backend (after CL 9) caches its OutputHandler subclass
    at instance scope but creates a fresh instance per call; the CLI
    backend spawns a fresh subprocess each time. Either way, the
    second call's `count` must reflect ONLY the second call's matches
    — not the first call's count carried over.

    Pinning this catches a regression where someone, optimising
    further, accidentally hoists the handler *instance* to the class
    or to the backend (so `count` accumulates across calls).
    """
    first_matches: list[dict] = []
    n1 = live_backend.grep_stream(
        "def main",
        "//.../p4v.py",
        on_match=lambda r: first_matches.append(r),
        cancelled=lambda: False,
        case_insensitive=False,
        max_matches=5,
    )
    second_matches: list[dict] = []
    n2 = live_backend.grep_stream(
        "import",
        "//.../p4v.py",
        on_match=lambda r: second_matches.append(r),
        cancelled=lambda: False,
        case_insensitive=False,
        max_matches=5,
    )
    # Each call's count matches its own callback delivery — no spill-
    # over from the previous call.
    assert n1 == len(first_matches), f"first: {n1} vs {len(first_matches)}"
    assert n2 == len(second_matches), f"second: {n2} vs {len(second_matches)}"


def test_grep_stream_watcher_promptly_kills_on_late_cancel(live_backend):
    """Cancellation that fires AFTER the main loop is already blocked
    on marshal.load(proc.stdout) must still take effect promptly.

    Before the watcher thread (CL 4), this scenario could take seconds
    on a slow grep with sparse matches — marshal.load was blocked
    until the next row arrived. With the watcher, kill() races the
    next row and we see EOFError within ~`_GREP_CANCEL_POLL_S`.

    We can't easily reproduce "main loop blocked on marshal.load"
    deterministically against a live server (it depends on server
    response timing), so this test approximates by:
      1. Issuing a grep against a hot pattern that takes the server
         tens of ms per page.
      2. Setting cancelled=True after a short sleep on a background
         thread so the cancel happens while the main loop is most
         likely mid-marshal.load.
      3. Asserting overall wall-clock stays small.
    """
    import threading
    import time
    cancel_now = threading.Event()
    matches: list[dict] = []

    def fire_cancel():
        # Wait until the main loop has had time to start blocking on
        # marshal.load, then cancel.
        time.sleep(0.3)
        cancel_now.set()

    fire_thread = threading.Thread(target=fire_cancel, daemon=True)
    fire_thread.start()
    started = time.monotonic()
    live_backend.grep_stream(
        "import",
        "//.../*.py",
        on_match=lambda r: matches.append(r),
        cancelled=cancel_now.is_set,
        case_insensitive=True,
        max_matches=10000,
    )
    elapsed = time.monotonic() - started
    fire_thread.join(timeout=1)
    # Budget: 0.3 s for the fire_cancel sleep + at most ~0.2 s for
    # the watcher to notice + ~0.5 s slop for kill/wait. 5 s is a
    # very loose ceiling that still proves we're not waiting for the
    # whole grep to finish (which on a busy depot would be 10+ s).
    assert elapsed < 5.0, (
        f"watcher didn't kill subprocess fast enough ({elapsed:.2f}s)"
    )
