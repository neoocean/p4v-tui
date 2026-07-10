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
    """A `cancelled=True` callback must stop delivery — asserted by the
    *count* delivered, not the wall clock.

    Both backends poll `cancelled()` per match *before* delivering
    (python: `outputStat`; cli: the top of the read loop, plus the
    watcher thread). This `cancelled()` flips True on its 2nd call, so
    at most the first match is ever delivered: verified live as n=1
    (python) / n=0 (cli — its watcher may consume the first poll before
    any row arrives). Either way it's far below the thousands a full run
    of this hot pattern would yield.

    The old form asserted `elapsed < 15s`, but for a sparse-at-the-start
    scope like this that budget mostly measures the server's scan
    latency to the *first* match (seconds on a big depot) — unrelated to
    cancellation — and so flaked under CPU load. The count is
    load-independent.
    """
    matches: list[dict] = []
    cancel_after = [0]
    def cancelled():
        cancel_after[0] += 1
        return cancel_after[0] > 1  # bail after the first check
    n = live_backend.grep_stream(
        "import",
        "//.../*.py",
        on_match=lambda r: matches.append(r),
        cancelled=cancelled,
        case_insensitive=True,
        max_matches=5000,
    )
    assert n == len(matches), "n must equal callbacks delivered"
    assert n <= 1, f"cancel after the first check must stop delivery (got {n})"


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


def test_grep_stream_cancel_mid_stream_stops_at_that_point(live_backend):
    """A cancel raised partway through delivery stops the stream right
    there — asserted by the *count* delivered, load-independently.

    The previous form fired the cancel on a 0.3 s background timer and
    asserted an absolute wall-clock budget (≈ "the watcher killed the
    subprocess fast"). But the elapsed time is dominated by the server's
    scan latency to the first `import` match in `//.../*.py` (~seconds on
    this depot — verified), which has nothing to do with cancellation,
    and that flaked under CPU starvation. Worse, for the python backend
    there is no subprocess to kill, so cancellation can only take effect
    at a match boundary anyway.

    So instead we cancel from inside the match stream, exactly after the
    `K`-th delivery. Both backends poll `cancelled()` per match before
    delivering the next one, so the stream stops at precisely `K`
    (verified live: n=K on both python and cli). That's deterministic
    and CPU-load-independent, and still proves the cancel short-circuits
    a stream that would otherwise run to the `max_matches` cap.

    (The watcher thread's distinct job — a prompt kill while the cli
    main loop is *blocked* in marshal.load on a sparse grep — isn't
    deterministically reproducible against a live server; covering it
    would want a focused fake-subprocess unit test, noted as follow-up.)
    """
    K = 100
    MAXM = 10000
    matches: list[dict] = []
    cancel_flag = [False]

    def on_match(row):
        matches.append(row)
        if len(matches) >= K:
            cancel_flag[0] = True

    n = live_backend.grep_stream(
        "import",
        "//.../*.py",
        on_match=on_match,
        cancelled=lambda: cancel_flag[0],
        case_insensitive=True,
        max_matches=MAXM,
    )
    assert n == len(matches), "n must equal callbacks delivered"
    # Stops at the cancel point, never reaching the cap. A tiny slop
    # guards against any backend delivering one already-decoded row
    # before re-checking; the live-verified value is exactly K.
    assert K <= n <= K + 5, f"cancel mid-stream should stop near {K} (got {n})"
    assert n < MAXM, f"must stop short of the {MAXM} cap (got {n})"
