"""Deterministic unit tests for the CLI grep cancellation watcher.

Follow-up explicitly left by CL 59352: the watcher's distinct job —
promptly killing the ``p4 grep`` subprocess while the reader is
**blocked in ``marshal.load`` on a sparse grep** — is not reliably
reproducible against a live server (needs a grep that stays quiet for
seconds on cue), so the de-flaked live tests only pin the delivered
*count*. These tests pin the kill behaviour itself with a fake
subprocess whose stdout is a real OS pipe:

* nothing written → ``marshal.load`` genuinely blocks, exactly like a
  sparse depot grep between matches;
* ``kill()`` closes the write end → the blocked ``marshal.load`` raises
  ``EOFError``, mirroring how killing ``p4`` drops the pipe.

If the watcher thread were removed or broken, the mid-blob cancel test
would hang in ``marshal.load`` until the join timeout and fail — the
main read loop cannot see the cancel flag while blocked, so *only* the
watcher can unblock it.

No `p4` binary, no server: `_CLIBackend.__init__` is bypassed via
``object.__new__`` (the method under test touches only ``_build_argv``
fields and the module-level ``subprocess.Popen``).
"""
from __future__ import annotations

import marshal
import os
import threading
import time

import p4v_tui.p4client as pc


class _FakeGrepProc:
    """Popen stand-in: stdout is the read end of a real pipe."""

    def __init__(self) -> None:
        r, w = os.pipe()
        self.stdout = os.fdopen(r, "rb")
        self._w: int | None = w
        self.pid = 4242
        self.kill_calls = 0
        self._returncode: int | None = None

    # --- test helpers ---------------------------------------------------
    def feed_row(self, depot_file: str, line: str = "42") -> None:
        """Write one p4-shaped marshalled match row (bytes keys/values,
        marshal version 0 — what `p4 -G` actually emits)."""
        row = {b"code": b"stat", b"depotFile": depot_file.encode(),
               b"line": line.encode(), b"matchedLine": b"import os"}
        assert self._w is not None
        os.write(self._w, marshal.dumps(row, 0))

    def close_stdin_side(self) -> None:
        """Simulate the grep finishing normally (EOF on the pipe)."""
        if self._w is not None:
            os.close(self._w)
            self._w = None
            self._returncode = 0

    # --- Popen surface used by grep_stream ------------------------------
    def kill(self) -> None:
        self.kill_calls += 1
        self._returncode = -9
        if self._w is not None:
            os.close(self._w)          # unblocks a reader in marshal.load
            self._w = None

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        return self._returncode if self._returncode is not None else 0


def _bare_cli_backend() -> pc._CLIBackend:
    """A _CLIBackend without __init__ (no `p4` binary, no `p4 set` probe)."""
    b = object.__new__(pc._CLIBackend)
    b._p4_bin = "p4"
    b._port = b._user = b._client = b._charset = ""
    return b


def _run_grep(backend, fake, monkeypatch, cancelled, *, max_matches=100):
    """Drive grep_stream in a worker thread; return (thread, results)."""
    monkeypatch.setattr(pc.subprocess, "Popen", lambda *a, **k: fake)
    hits: list[dict] = []
    out: dict = {}

    def on_match(row):
        hits.append(row)

    def target():
        out["count"] = backend.grep_stream(
            "import", "//depot/...", on_match, cancelled,
            case_insensitive=False, max_matches=max_matches,
        )

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, hits, out


def test_watcher_kills_while_blocked_in_marshal_load(monkeypatch):
    """The CL 59352 gap: cancel lands mid-``marshal.load`` on a sparse
    grep → the watcher must kill the child within ~one poll tick; the
    reader is blocked and cannot do it itself."""
    fake = _FakeGrepProc()
    cancel = threading.Event()
    first_match = threading.Event()

    def cancelled() -> bool:
        return cancel.is_set()

    backend = _bare_cli_backend()
    monkeypatch.setattr(pc.subprocess, "Popen", lambda *a, **k: fake)
    hits: list[dict] = []
    out: dict = {}

    def on_match(row):
        hits.append(row)
        first_match.set()

    def target():
        out["count"] = backend.grep_stream(
            "import", "//depot/...", on_match, cancelled,
            case_insensitive=False, max_matches=100,
        )

    t = threading.Thread(target=target, daemon=True)
    t.start()

    # Deliver one match, then let the reader re-enter marshal.load and
    # block on the now-quiet pipe (the sparse-grep shape).
    fake.feed_row("//depot/a.py")
    assert first_match.wait(timeout=5), "first match never delivered"
    time.sleep(0.3)                    # reader is now inside marshal.load

    cancel.set()                       # ← the user typed a new keystroke
    t.join(timeout=5)
    assert not t.is_alive(), (
        "grep_stream stayed blocked in marshal.load after cancel — "
        "the watcher did not kill the subprocess"
    )
    assert fake.kill_calls >= 1
    assert out["count"] == 1           # the one delivered match, no more
    assert [h["depotFile"] for h in hits] == ["//depot/a.py"]


def test_normal_completion_decodes_rows_and_reaps_child(monkeypatch):
    """EOF path: rows flow through _decode_marshal (bytes → str), the
    stream returns the full count, and the child is still reaped."""
    fake = _FakeGrepProc()
    backend = _bare_cli_backend()
    t, hits, out = _run_grep(
        backend, fake, monkeypatch, cancelled=lambda: False)

    fake.feed_row("//depot/a.py", line="1")
    fake.feed_row("//depot/b.py", line="7")
    fake.close_stdin_side()
    t.join(timeout=5)
    assert not t.is_alive()
    assert out["count"] == 2
    # bytes keys/values from the marshal stream must arrive decoded.
    assert hits[0]["depotFile"] == "//depot/a.py"
    assert hits[1]["line"] == "7"
    # finally-block cleanup killed/reaped the (already finished) child.
    assert fake.kill_calls >= 1


def test_max_matches_stops_stream_and_kills_child(monkeypatch):
    fake = _FakeGrepProc()
    backend = _bare_cli_backend()
    t, hits, out = _run_grep(
        backend, fake, monkeypatch, cancelled=lambda: False, max_matches=2)

    for i in range(3):
        fake.feed_row(f"//depot/f{i}.py")
    t.join(timeout=5)
    assert not t.is_alive()
    assert out["count"] == 2           # capped before the third row
    assert len(hits) == 2
    assert fake.kill_calls >= 1        # rogue grep must not keep running
