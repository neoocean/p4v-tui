"""Shared fixtures for the p4v-tui test suite.

Two kinds of tests live under `tests/`:

* **Unit tests** — exercise pure-Python helpers in `p4v_tui.p4client`
  (marshal decode, form text serializer, numbered-field flatten).
  Always run; no server / no `p4` binary required.

* **Live backend tests** — spawn the actual `_PythonBackend` /
  `_CLIBackend` against whatever Perforce server the developer is
  currently connected to. Auto-skipped via the `live_backend` fixture
  when the prerequisite (P4Python module or `p4` CLI) is missing, or
  when `p4 info` against the resolved server fails (the `p4_reachable`
  check — see :func:`_p4_reachable` for what "reachable" means here).

`P4V_BACKEND` is intentionally cleared at session scope so a developer
who sets it to test one backend doesn't accidentally taint other tests.
Per-test selection comes from the parametrized `backend_name` fixture.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True, scope="session")
def _scrub_backend_env():
    """Drop `P4V_BACKEND` for the duration of the session.

    Individual tests that need to force a backend re-set it under
    a `monkeypatch` fixture; this scrub just keeps an ambient env var
    from poisoning the default selection.
    """
    saved = os.environ.pop("P4V_BACKEND", None)
    yield
    if saved is not None:
        os.environ["P4V_BACKEND"] = saved


def _has_p4python() -> bool:
    try:
        import P4  # noqa: F401
        return True
    except ImportError:
        return False


def _has_p4_cli() -> bool:
    return shutil.which("p4") is not None


def _p4_reachable() -> bool:
    """Best-effort check that the resolved P4 server is responsive.

    Tries `p4 info`. This proves the server is reachable and the
    workstation is configured well enough to talk to it; it does NOT
    prove the user is logged in — `p4 info` succeeds against most
    servers even without a valid ticket (it returns server-side info
    without consulting the user's auth). The previous name
    `_p4_login_works` suggested otherwise, which set the wrong
    expectation when a test failed for an actually-expired ticket
    rather than for an unreachable server.

    When the `p4` binary is missing we return False so live tests
    are skipped. A pure-P4Python install could in theory still run
    them, but keeping the check uniform avoids matrix surprises:
    if `p4` isn't here, neither the CLI backend's per-call spawn
    nor the conftest's reachability probe can work, so live tests
    don't belong in that environment regardless.
    """
    if not _has_p4_cli():
        return False
    import subprocess
    try:
        cp = subprocess.run(
            ["p4", "info"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except Exception:  # noqa: BLE001
        return False
    return cp.returncode == 0


@pytest.fixture(scope="session")
def has_p4python() -> bool:
    return _has_p4python()


@pytest.fixture(scope="session")
def has_p4_cli() -> bool:
    return _has_p4_cli()


@pytest.fixture(scope="session")
def p4_reachable() -> bool:
    return _p4_reachable()


# Parametrize live-backend tests over both backends. Tests that depend
# on this fixture run twice (once with "python", once with "cli"),
# auto-skipping the variant whose prerequisite isn't installed.
@pytest.fixture(
    params=["python", "cli"],
    ids=["python", "cli"],
)
def backend_name(request, has_p4python, has_p4_cli, p4_reachable):
    if not p4_reachable:
        pytest.skip("no live Perforce server reachable via `p4 info`")
    if request.param == "python" and not has_p4python:
        pytest.skip("P4Python not installed")
    if request.param == "cli" and not has_p4_cli:
        pytest.skip("`p4` CLI not on PATH")
    return request.param


@pytest.fixture
def live_backend(backend_name):
    """Return a connected `P4Service` bound to the parametrized backend.

    Constructs the backend class directly (rather than going through
    `_select_backend()` via a `P4V_BACKEND` env var + `importlib.reload`
    of `p4v_tui.p4client`) for two reasons:

    1. **Trap avoidance** — `importlib.reload` rebinds every module-
       level object, including the `P4Exception` class. Tests that
       imported `P4Exception` *before* the reload see a different
       class object than tests that import it *after*; an
       `isinstance(exc, P4Exception)` then fails counter-intuitively.
       The reload version of this fixture worked only because every
       caller stuck to `except P4Exception` (which uses lookup-time
       name resolution, not the stale class binding). The direct
       construction path sidesteps the whole class.

    2. **Speed** — `reload` re-imports module dependencies; direct
       construction skips that overhead per test parametrisation.

    The two-line cost is verifying the prerequisite (`has_p4python` /
    `has_p4_cli`) before instantiating, which the `backend_name`
    fixture already did. So this fixture just maps `backend_name` to
    the matching concrete class.
    """
    import p4v_tui.p4client as pc
    backend_cls = (
        pc._PythonBackend if backend_name == "python" else pc._CLIBackend
    )
    backend = backend_cls()
    svc = pc.P4Service(backend=backend)
    svc.connect()
    yield svc
    svc.disconnect()
