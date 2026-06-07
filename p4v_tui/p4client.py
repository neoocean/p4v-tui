"""Thread-safe Perforce wrapper with pluggable P4Python / p4-CLI backend.

Historically this module assumed P4Python (`import P4`) was available.
Some environments can't install the C-extension wheel (older Linux,
non-x86 sidecars, SSH-only servers with no compiler) but do have the
`p4` command on PATH. To keep the TUI usable there, we wrap two
interchangeable backends behind one `P4Service` façade:

  * `_PythonBackend` — the original path. One in-process `P4.P4()`
    connection re-used across calls. Lowest latency per call.
  * `_CLIBackend` — spawns a short-lived `p4` subprocess per call,
    reads tagged output as Python marshal-2 (`p4 -G ...`), pipes form
    text to stdin for `*-i` write commands. Slower per call (fork +
    exec + TCP + auth), but needs only the `p4` binary.

Selection precedence (highest first):
  1. `P4V_BACKEND` env var (`python` | `cli` | unset)
  2. P4Python import succeeds → Python backend
  3. `p4` on PATH → CLI backend
  4. Neither → :class:`SetupError` (handled by `p4v.py` entry point)

`P4Service` still owns:
  * the `_lock` that serialises calls (P4Python's connection is not
    thread-safe; for CLI it provides backpressure so a busy UI doesn't
    fork 100 parallel `p4` processes),
  * the `_run_resilient` retry / reconnect / cmd-log loop,
  * every high-level wrapper method (`info`, `depots`, `pending_changes`,
    …) that callers already use.

Callers see the same surface; backend swap is transparent except for
the one-line `cmd_log.log_info("Backend: …")` the App emits at startup.

Both backends raise the local :class:`P4Exception` on failure (the
Python backend translates `P4.P4Exception` into it). The resilient
runner inspects the message for the same fragment list that the
original P4Python implementation used to decide retry-vs-fail.

Trust boundary (CLI backend)
----------------------------
The CLI backend feeds `marshal.load()` the stdout of a `p4` subprocess
the user themselves connected to. Python's docs warn:

    Never unmarshal data received from an untrusted or unauthenticated
    source. (https://docs.python.org/3/library/marshal.html)

In our threat model the trust boundary is **the p4d server the user
explicitly configured** — same boundary the P4Python backend trusts
when it reads protocol bytes off its own socket. CPython's marshal
module has no eval-like behaviour, but it has had occasional CVEs
around malformed input causing memory corruption / crash. Mitigations
in place:
  * We only feed marshal bytes that arrive on `Popen.stdout` of the
    `p4` binary we ourselves spawned (not arbitrary network input).
  * Read errors are caught (`EOFError` / `ValueError`) and treated
    as "end of stream" rather than propagating as crashes — a
    corrupt tail loses rows but doesn't take down the worker.
  * The subprocess has a per-call timeout (`_DEFAULT_CLI_TIMEOUT_S`)
    so a server returning a slow / infinite stream surfaces as a
    `P4Exception` instead of a hang.
If you ever pipe externally-sourced bytes through `_read_marshal_stream`
or the `grep_stream` loop, switch to a hardened parser (`json`,
`msgpack`) first — marshal is appropriate only for the p4d-trust
case.

See `docs/p4-cli-fallback-scenario.md` for the full design contract.
"""
from __future__ import annotations

import io
import marshal
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence


# Subprocess flags applied to every `p4` spawn. On Windows we set
# CREATE_NO_WINDOW so a TUI parent doesn't flash a console window each
# time we fork `p4.exe` (the binary is GUI-subsystem-aware but still
# allocates a console when spawned from a non-console parent). Empty
# dict on POSIX so the spread `**_SUBPROCESS_FLAGS` stays free.
if sys.platform == "win32":
    _SUBPROCESS_FLAGS: dict = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
else:
    _SUBPROCESS_FLAGS = {}


# Default per-call timeout for the CLI backend's `_invoke()` round
# trip. A hung p4d (network partition, dead TCP that hasn't surfaced as
# RST yet) would otherwise block the worker thread forever — the
# resilient runner only retries on raised exceptions, not on hangs.
# 1800 s (30 min) is generous enough for a single chunk of a large
# sync (the only legitimately long operation that goes through
# `_invoke`); anything taking longer is almost certainly a real hang.
# Operators can override via the P4V_CLI_TIMEOUT env var when
# debugging an environment with unusual latency characteristics, and
# in-tree callers can pass `timeout=N` to override per call.
try:
    _DEFAULT_CLI_TIMEOUT_S: float = float(
        os.environ.get("P4V_CLI_TIMEOUT", "1800")
    )
except ValueError:
    _DEFAULT_CLI_TIMEOUT_S = 1800.0


# How many CLI `p4` subprocesses may run in parallel.
#
# Background: true connection reuse (one TCP session → many commands)
# isn't achievable with the vanilla `p4` binary — it has no REPL /
# script-mode for arbitrary commands (`-s` is per-line tag output, not
# a multi-command loop). Real reuse needs P4Python, `p4-broker`, or a
# re-implementation of the wire protocol — all out of scope for the
# CLI-fallback path.
#
# What we CAN do is amortise the fork+exec+TCP+SSL handshake across
# *concurrent* calls instead of serialising every backend call through
# one lock. With N parallel permits the user's tree expand (which
# fans out into `dirs` + `files` + `fstat` per visible subdir) no
# longer queues those sequentially; cold-cache cost drops roughly by
# a factor of N.
#
# Default 4 is a balance — high enough for typical fan-outs, low
# enough that a typing-storm against Fast Search doesn't fork 50
# `p4` processes at once. Operators override via the env var.
try:
    _CLI_CONCURRENCY: int = max(
        1, int(os.environ.get("P4V_CLI_CONCURRENCY", "4")),
    )
except ValueError:
    _CLI_CONCURRENCY = 4


# TTL (seconds) for the CLI backend's idempotent-read cache. A handful
# of reads (`info`, `client -o <self>`, `p4 set -q`) change at most
# once per session for a typical user, but the UI hits them on every
# refresh — caching them for 30 s amortises the spawn cost without
# making stale data linger long enough to matter. Set to 0 to disable.
try:
    _CLI_READ_CACHE_TTL_S: float = max(
        0.0, float(os.environ.get("P4V_CLI_READ_CACHE_TTL", "30")),
    )
except ValueError:
    _CLI_READ_CACHE_TTL_S = 30.0


# How often the CLI grep_stream cancellation watcher checks
# `cancelled()`. Tight enough that the user feels the cancellation
# (~100 ms is the perceptual "instant" boundary), loose enough that an
# idle watcher costs effectively nothing. The watcher wakes
# immediately on a successful finish via a `threading.Event` so this
# sleep is the only cost it imposes on the cancelled-mid-stream path.
_GREP_CANCEL_POLL_S: float = 0.1


# Substrings observed in p4 failure messages (from both P4Python and
# the CLI) when the failure is transport-level rather than command-
# level. When any of these matches, the resilient runner reconnects
# and retries; otherwise the exception is raised unchanged so callers
# see the real error.
_CONNECTION_ERROR_FRAGMENTS = (
    "connect to server failed",
    "connection refused",
    "tcp receive failed",
    "tcp send failed",
    "disconnect signaled",
    "server timeout",
    "operation timed out",
    "read failed",
    "write failed",
    "broken pipe",
    "no route to host",
    "ssl receive failed",
    "ssl send failed",
    "rpc",  # generic RPC framing failures
)


class P4Exception(Exception):
    """Backend-agnostic Perforce error.

    Both `_PythonBackend` and `_CLIBackend` raise this class on
    failure. The Python backend translates `P4.P4Exception` into it so
    callers don't need to know which backend is active.

    `str(exc)` is the message text (for the CLI backend, that's
    stderr + in-band `code: error` rows concatenated; for the Python
    backend, P4Python's own message). The `_is_connection_error`
    helper inspects this string to decide retry-vs-fail.
    """


class P4SetupError(Exception):
    """Raised by `_select_backend()` when no backend can be activated.

    The `p4v.py` entry point catches this and renders a Korean install
    hint covering both backends instead of dumping a traceback.
    """


def _is_connection_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(frag in msg for frag in _CONNECTION_ERROR_FRAGMENTS)


# Type alias for the optional retry-progress callback.
# Signature: (attempt_number, max_attempts, exception_seen) -> None.
RetryCallback = Callable[[int, int, BaseException], None]

# Streaming-grep callbacks. ``GrepMatchCallback`` is invoked once per
# matching row the server emits (worker thread; UI marshalling is the
# caller's job). ``CancelledFn`` is polled to support cooperative
# cancellation when a newer keystroke has replaced the query.
GrepMatchCallback = Callable[[dict], None]
CancelledFn = Callable[[], bool]


@dataclass
class P4Info:
    user: str = ""
    client: str = ""
    port: str = ""
    server_address: str = ""
    server_version: str = ""
    server_uptime: str = ""
    client_root: str = ""
    client_host: str = ""


# ---------------------------------------------------------------------------
# Backend interface — both implementations agree on this shape
# ---------------------------------------------------------------------------

class _Backend:
    """Implementation contract for Perforce backends.

    Concrete subclasses don't lock or retry — that's the façade's job.
    They just translate one call into one (or one streaming) `p4`
    invocation and surface results / failures uniformly.

    Connection params (port/user/client/charset) are exposed as
    properties so each backend can choose its source of truth:
    P4Python reads them off the live `P4.P4()` instance (so env /
    P4CONFIG resolution is visible); the CLI backend snapshots them
    from `p4 set -q` at startup and reflects whatever the user typed
    into the profile picker on top.
    """

    #: Human-readable name shown in the startup `Backend: …` log line.
    name: str = "?"

    #: How many backend calls may run in parallel. P4Python's
    #: in-process connection isn't thread-safe so the Python backend
    #: must serialise (1). The CLI backend's per-call subprocess is
    #: independent, so multiple can run concurrently (default 4).
    #: P4Service builds a BoundedSemaphore(this) to gate concurrent
    #: `run_tagged` / `run_text` / form calls.
    max_concurrent_calls: int = 1

    @property
    def port(self) -> str:
        return ""

    @property
    def user(self) -> str:
        return ""

    @property
    def client(self) -> str:
        return ""

    @property
    def charset(self) -> str:
        return ""

    @property
    def connected(self) -> bool:  # noqa: D401
        raise NotImplementedError

    def configure(
        self,
        *,
        port: str | None = None,
        user: str | None = None,
        client: str | None = None,
        charset: str | None = None,
    ) -> None:
        raise NotImplementedError

    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    # --- core invocation primitives ----------------------------------------

    def run_tagged(self, args: Sequence[str]) -> list:
        """Run a p4 command and return tagged result rows.

        ``args`` is the argv tail (everything after the connection
        flags and the optional ``-G``) — all elements are already
        strings; the façade (`P4Service.run`) `str()`-converts on
        the public boundary so backends don't have to.

        Each row is either:
          * a dict (the common case — `code: stat` records that
            P4Python returns as plain dicts, with `code` stripped),
          * a string (`code: info` / `code: text` payload),
          * bytes (`code: binary` payload — only `print` on binary
            file types produces these).

        Raises :class:`P4Exception` on any failure (both transport and
        command level — the façade's resilient runner inspects the
        message to decide retry vs. propagate).
        """
        raise NotImplementedError

    def run_text(self, args: Sequence[str]) -> str:
        """Run a p4 command whose useful output is *un*tagged text.

        Used for ``describe -du`` and ``diff -du`` whose unified-diff
        sections aren't emitted in tagged form. Returns the raw stdout
        text (empty on failure rather than raising — callers want a
        "no diff" placeholder, not an exception).
        """
        raise NotImplementedError

    # --- form + login + grep (specialised paths) --------------------------

    def login_status(self) -> dict | None:
        """`p4 login -s` — single shot, no retry, no exception.

        Returns the parsed status dict, or ``None`` if the call fails
        or the user isn't logged in. Used at startup to warn before
        the first real command racks up retries against an expired
        ticket.
        """
        raise NotImplementedError

    def fetch_form(self, kind: str, key: str | None = None) -> dict:
        """Fetch a p4 spec form (``p4 <kind> -o [<key>]``).

        ``kind`` is the spec name (``"change"``, ``"client"``,
        ``"label"``, …). ``key`` is the spec-specific identifier
        (changelist number, client name, label name); pass ``None``
        to fetch the "new" template.

        Returns a flattened dict — multi-value fields like ``Files``,
        ``Jobs``, ``View`` arrive as lists, matching P4Python's
        ``fetch_change()`` / ``fetch_client()`` shape.

        Raises :class:`P4Exception` on failure.
        """
        raise NotImplementedError

    def save_form(
        self,
        kind: str,
        form: dict,
        *,
        force: bool = False,
    ) -> list:
        """Save a p4 spec form (``p4 <kind> -i [-f]``).

        ``kind`` matches the :meth:`fetch_form` ``kind`` argument.
        ``form`` is the dict shape :meth:`fetch_form` returns;
        ``force=True`` adds the ``-f`` flag for admin-only edits
        (e.g., updating a submitted CL description).

        Returns the info-row list (e.g. ``["Change 12345 created."]``)
        so ``create_changelist()`` can pull the new number out.
        """
        raise NotImplementedError

    def grep_stream(
        self,
        pattern: str,
        scope: str,
        on_match: GrepMatchCallback,
        cancelled: CancelledFn,
        *,
        case_insensitive: bool,
        max_matches: int,
    ) -> int:
        """Stream ``p4 grep`` match rows via ``on_match(row)``.

        Returns the number of rows actually delivered. ``cancelled``
        is a no-arg callable; backends check it between rows so a
        stale query gets dropped within ~one row of latency.
        """
        raise NotImplementedError

    def version_info(self) -> str:
        """One-line version banner for the LogPanel startup entry."""
        return self.name


# ---------------------------------------------------------------------------
# Python backend — wraps P4Python in its historical shape
# ---------------------------------------------------------------------------

class _PythonBackend(_Backend):
    name = "P4Python"

    def __init__(self) -> None:
        # P4 import is required *inside* this constructor — the whole
        # point of the CLI fallback is that p4v-tui still runs when
        # this module is missing. Callers (`_select_backend`) only
        # instantiate _PythonBackend after a successful `import P4`.
        import P4  # noqa: PLC0415
        self._P4 = P4
        self._p4 = P4.P4()
        self._p4.exception_level = 1  # warnings -> result; errors -> raise
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port(self) -> str:
        # Read straight off the P4 instance so env / P4CONFIG resolved
        # values are visible to the façade even when the user hasn't
        # overridden anything via configure().
        return self._p4.port or ""

    @property
    def user(self) -> str:
        return self._p4.user or ""

    @property
    def client(self) -> str:
        return self._p4.client or ""

    @property
    def charset(self) -> str:
        return self._p4.charset or ""

    def configure(
        self,
        *,
        port: str | None = None,
        user: str | None = None,
        client: str | None = None,
        charset: str | None = None,
    ) -> None:
        if port is not None:
            self._p4.port = port
        if user is not None:
            self._p4.user = user
        if client is not None:
            self._p4.client = client
        if charset is not None:
            self._p4.charset = charset

    def connect(self) -> None:
        if self._connected:
            return
        try:
            self._p4.connect()
        except self._P4.P4Exception as e:
            raise P4Exception(str(e)) from e
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        try:
            self._p4.disconnect()
        except Exception:  # noqa: BLE001 — disconnect must be tolerant
            pass
        self._connected = False

    # --- core invocation ---------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self._connected:
            self.connect()

    def run_tagged(self, args: Sequence[str]) -> list:
        self._ensure_connected()
        try:
            return list(self._p4.run(*args))
        except self._P4.P4Exception as e:
            # Forget the broken connection so the resilient runner
            # rebuilds it on the next attempt.
            if _is_connection_error(e):
                self._drop_connection()
            raise P4Exception(str(e)) from e

    def run_text(self, args: Sequence[str]) -> str:
        self._ensure_connected()
        prev_tagged = self._p4.tagged
        self._p4.tagged = False
        try:
            try:
                result = self._p4.run(*args)
            except self._P4.P4Exception as e:
                if _is_connection_error(e):
                    self._drop_connection()
                # Untagged callers (diff_describe etc.) historically
                # caught and turned this into "" — preserve that shape.
                raise P4Exception(str(e)) from e
        finally:
            self._p4.tagged = prev_tagged
        parts: list[str] = []
        for item in result:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, (bytes, bytearray)):
                parts.append(bytes(item).decode("utf-8", errors="replace"))
        return "".join(parts)

    def _drop_connection(self) -> None:
        if self._connected:
            try:
                self._p4.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._connected = False

    # --- specialised paths -------------------------------------------------

    def login_status(self) -> dict | None:
        # No retry — startup probe; an expired ticket isn't fixed by
        # trying again.
        try:
            self._ensure_connected()
            rows = self._p4.run_login("-s")
        except self._P4.P4Exception:
            return None
        if not rows:
            return None
        first = rows[0]
        return first if isinstance(first, dict) else None

    def fetch_form(self, kind: str, key: str | None = None) -> dict:
        """Delegate to P4Python's ``fetch_<kind>(key)`` accessor."""
        self._ensure_connected()
        method_name = f"fetch_{kind}"
        try:
            method = getattr(self._p4, method_name)
        except AttributeError as e:
            raise P4Exception(
                f"P4Python has no {method_name}; cannot fetch form"
            ) from e
        try:
            return method(key) if key is not None else method()
        except self._P4.P4Exception as e:
            if _is_connection_error(e):
                self._drop_connection()
            raise P4Exception(str(e)) from e

    def save_form(
        self,
        kind: str,
        form: dict,
        *,
        force: bool = False,
    ) -> list:
        """Push form back via P4Python's `input=` + `run("<kind>", "-i" [, "-f"])`."""
        self._ensure_connected()
        self._p4.input = form
        args: list[str] = [kind, "-i"]
        if force:
            args.insert(1, "-f")
        try:
            return list(self._p4.run(*args))
        except self._P4.P4Exception as e:
            if _is_connection_error(e):
                self._drop_connection()
            raise P4Exception(str(e)) from e

    def grep_stream(
        self,
        pattern: str,
        scope: str,
        on_match: GrepMatchCallback,
        cancelled: CancelledFn,
        *,
        case_insensitive: bool,
        max_matches: int,
    ) -> int:
        # Replicates the original P4Python OutputHandler path. The
        # handler class is built once per backend instance and stashed
        # at `self._grep_handler_cls` so a typing-storm of grep calls
        # doesn't pay the class-construction cost N times. Per-call
        # state (count, callbacks, cap) lives on the handler instance,
        # not the class, so re-use is safe.
        flags = ["-s", "-n"]
        if case_insensitive:
            flags.append("-i")
        try:
            self._ensure_connected()
        except P4Exception:
            return 0
        handler_cls = self._get_or_build_grep_handler_cls()
        handler = handler_cls(on_match, cancelled, max_matches)
        prev = getattr(self._p4, "handler", None)
        self._p4.handler = handler
        try:
            try:
                self._p4.run("grep", *flags, "-e", pattern, scope)
            except self._P4.P4Exception:
                pass
        finally:
            self._p4.handler = prev
        return handler.count

    def _get_or_build_grep_handler_cls(self):
        """Lazily build a P4Python OutputHandler subclass once per
        backend instance.

        We can't define the class at module load — P4Python may not be
        importable in the CLI-only install path, so the `P4.OutputHandler`
        base type doesn't exist there. Tied to the instance (not the
        class) so a future second `_PythonBackend` (per-profile?) still
        gets its own handler-class with its own captured `P4` reference.
        """
        cached = getattr(self, "_grep_handler_cls", None)
        if cached is not None:
            return cached
        P4 = self._P4

        class _Handler(P4.OutputHandler):
            def __init__(self_h, on_match, cancelled, max_matches):
                super().__init__()
                self_h._on_match = on_match
                self_h._cancelled = cancelled
                self_h._max = max_matches
                self_h.count = 0

            def outputStat(self_h, stat):
                if self_h._cancelled():
                    return P4.OutputHandler.CANCEL
                try:
                    self_h._on_match(stat)
                except Exception:  # noqa: BLE001
                    return P4.OutputHandler.CANCEL
                self_h.count += 1
                if self_h.count >= self_h._max:
                    return P4.OutputHandler.CANCEL
                return P4.OutputHandler.HANDLED

            def outputMessage(self_h, msg):
                # "no such file(s)" etc. — swallow, never raise.
                return P4.OutputHandler.HANDLED

        self._grep_handler_cls = _Handler
        return _Handler

    def version_info(self) -> str:
        try:
            ver = self._p4.api_level
        except Exception:  # noqa: BLE001
            ver = ""
        return f"P4Python (api {ver})" if ver else "P4Python"


# ---------------------------------------------------------------------------
# CLI backend — subprocess + `-G` (Python marshal) tagged output
# ---------------------------------------------------------------------------

def _decode_marshal(obj):
    """Recursively decode marshal bytes → str.

    `p4 -G` emits bytes for both keys and values (marshal v2). We render
    them to UTF-8 strings to match the dict shape the Python backend
    hands callers. A value that won't decode (rare — binary blob inside
    a `code: binary` row's `data` field) flows through as bytes so the
    caller can still detect / handle it.
    """
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return obj
    if isinstance(obj, dict):
        return {_decode_marshal(k): _decode_marshal(v)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode_marshal(x) for x in obj]
    return obj


def _read_marshal_stream(buf: bytes) -> list:
    """Parse the contiguous stream of marshal-2 dicts `p4 -G` emits.

    One dict per result row. Loop until EOFError. Truncated tail bytes
    (rare — happens when p4 gets killed mid-write) are silently
    ignored so we don't lose rows that did make it through.

    Trust boundary: ``buf`` is the stdout of a `p4` subprocess this
    process just spawned, talking to the user-configured p4d. See the
    module-level "Trust boundary" note for the threat model that
    justifies using `marshal` here. Do not call this function on bytes
    sourced from anywhere else.
    """
    out: list = []
    stream = io.BytesIO(buf)
    while True:
        try:
            obj = marshal.load(stream)
        except (EOFError, ValueError):
            break
        out.append(_decode_marshal(obj))
    return out


def _project_tagged_rows(rows: list) -> list:
    """Reshape `p4 -G` marshalled rows into P4Python's `run()` format.

    P4Python's tagged ``run()`` returns:
      * ``code: stat`` rows as plain dicts (with ``code`` stripped),
      * ``code: info`` / ``code: text`` rows as bare strings (the
        ``data`` field),
      * ``code: binary`` rows as bare bytes,
      * ``code: error`` rows raised as a P4Exception (already
        extracted by :func:`_extract_error_text` before this projection
        runs — we drop them defensively here in case any leak through),
      * ``code: warning`` rows surfaced via a separate ``warnings``
        attribute (we keep them as dicts in-line — rare path).
    """
    out: list = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        code = r.get("code")
        if code == "error":
            continue
        if code in ("info", "text"):
            out.append(r.get("data", ""))
            continue
        if code == "binary":
            out.append(r.get("data", b""))
            continue
        if code == "stat":
            r = {k: v for k, v in r.items() if k != "code"}
        out.append(r)
    return out


def _extract_error_text(rows: list) -> str | None:
    """Concatenate every ``code: error`` row's `data` into one message.

    ``severity`` ≥ 3 (error / fatal) is included; ``severity`` 2
    (warning) is skipped, matching P4Python's default
    ``exception_level=1`` (warnings → result, errors → raise).
    """
    parts: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("code") != "error":
            continue
        sev_raw = r.get("severity")
        try:
            sev = int(sev_raw) if sev_raw is not None else 3
        except (TypeError, ValueError):
            sev = 3
        if sev < 3:
            continue
        data = r.get("data") or r.get("message") or ""
        parts.append(str(data).rstrip())
    return "\n".join(parts) if parts else None


# --- spec-form helpers (flatten ⇄ list, dict ⇄ text)

_NUMBERED_FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*?)(\d+)$")


def _flatten_numbered(d: dict) -> dict:
    """Collapse ``Files0`` / ``Files1`` / … into ``Files: [..]``.

    p4 -G emits multi-value spec fields as numbered keys; collapsing
    them matches the shape P4Python's ``fetch_change()`` returns.
    """
    out: dict = {}
    groups: dict[str, list[tuple[int, Any]]] = {}
    for k, v in d.items():
        m = _NUMBERED_FIELD_RE.match(str(k))
        if m is None:
            out[k] = v
            continue
        name = m.group(1)
        idx = int(m.group(2))
        groups.setdefault(name, []).append((idx, v))
    for name, pairs in groups.items():
        pairs.sort(key=lambda x: x[0])
        out[name] = [v for _, v in pairs]
    return out


# Fields whose value spans multiple lines in the text form even when
# the value is a plain string (not a list). Anything not in this set
# AND not a list is rendered as a single-line `Key: value`.
_MULTILINE_TEXT_FIELDS = {"Description"}


def _form_dict_to_text(form: dict) -> str:
    """Serialise a flattened spec dict back to the text form `p4` reads.

    The text form rules p4 expects (see `p4 help jobspec`):
      * Single-line field: ``Key: value\\n``
      * Multi-line block (Description, lists): ``Key:\\n\\tline1\\n\\tline2\\n``
      * Empty value: ``Key:\\n``
      * Sections separated by a blank line for readability (not required
        by the parser, but matches what ``p4 ... -o`` emits).
      * Lines starting with ``#`` are comments — we don't emit any.

    Insertion order is preserved (Python dict ordering since 3.7), so a
    round-trip of ``change -o`` → modify Description → ``change -i``
    keeps the rest of the spec intact and in its original layout.
    """
    chunks: list[str] = []
    for key, value in form.items():
        k = str(key)
        if k.startswith("#") or k.lower() in ("code", "severity"):
            # Drop the meta keys p4 -G adds; the parser would
            # otherwise warn about "unknown field code".
            continue
        if isinstance(value, list):
            if not value:
                chunks.append(f"{k}:\n")
            else:
                lines = "".join(f"\t{item}\n" for item in value)
                chunks.append(f"{k}:\n{lines}")
        else:
            text = "" if value is None else str(value)
            if "\n" in text or k in _MULTILINE_TEXT_FIELDS:
                # Even a single-line Description is conventionally
                # rendered as a multi-line block so it's obvious in
                # the form text where the body would start.
                if not text:
                    chunks.append(f"{k}:\n")
                else:
                    # ``text.splitlines()`` is guaranteed non-empty
                    # here because we've already filtered ``not text``
                    # above. A previous revision had ``or [""]`` as a
                    # safety net but that branch was unreachable.
                    indented = "".join(
                        f"\t{line}\n" for line in text.splitlines()
                    )
                    chunks.append(f"{k}:\n{indented}")
            else:
                chunks.append(f"{k}: {text}\n")
        chunks.append("\n")
    return "".join(chunks)


class _CLIBackend(_Backend):
    name = "p4 CLI"
    max_concurrent_calls = _CLI_CONCURRENCY

    def __init__(self) -> None:
        # Resolved at construction so a later PATH mutation can't yank
        # the binary out from under us mid-run.
        p4_path = shutil.which("p4")
        if not p4_path:
            raise P4SetupError(
                "`p4` command-line client not found on PATH"
            )
        self._p4_bin = p4_path
        # Cache the `p4 set` snapshot so connection params reflect what
        # the user has in P4CONFIG / env *at startup* — later env
        # changes in the same process don't randomly affect commands.
        env = self._snapshot_p4_set()
        self._port = env.get("P4PORT", "")
        self._user = env.get("P4USER", "")
        self._client = env.get("P4CLIENT", "")
        self._charset = env.get("P4CHARSET", "")
        # CLI has no socket to keep open; "connected" tracks whether
        # the user has called connect() so the façade can mirror the
        # original lifecycle.
        self._connected = False
        self._version_str = self._probe_version()
        # Idempotent-read cache: keys are the argv tuple, values are
        # (timestamp, payload). Sized small (a few entries) so we
        # don't bother with eviction beyond the per-entry TTL. The
        # mutation lock keeps the dict safe under the relaxed
        # concurrency model (multiple backend calls can hit
        # `run_tagged` in parallel; only one mutates the cache at a
        # time). _CLI_READ_CACHE_TTL_S=0 disables caching entirely.
        self._read_cache: dict[tuple, tuple[float, Any]] = {}
        self._read_cache_lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port(self) -> str:
        return self._port

    @property
    def user(self) -> str:
        return self._user

    @property
    def client(self) -> str:
        return self._client

    @property
    def charset(self) -> str:
        return self._charset

    def configure(
        self,
        *,
        port: str | None = None,
        user: str | None = None,
        client: str | None = None,
        charset: str | None = None,
    ) -> None:
        if port is not None:
            self._port = port
        if user is not None:
            self._user = user
        if client is not None:
            self._client = client
        if charset is not None:
            self._charset = charset

    def connect(self) -> None:
        # No persistent socket — flip the flag so the resilient runner
        # treats us as live. Auth is handled per-call via ~/.p4tickets.
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    # --- subprocess plumbing -----------------------------------------------

    def _build_argv(
        self,
        args: Sequence[str],
        *,
        tagged: bool,
    ) -> list[str]:
        argv: list[str] = [self._p4_bin]
        if tagged:
            argv.append("-G")
        if self.port:
            argv += ["-p", self.port]
        if self.user:
            argv += ["-u", self.user]
        if self.client:
            argv += ["-c", self.client]
        if self.charset:
            argv += ["-C", self.charset]
        argv += [str(a) for a in args]
        return argv

    def _invoke(
        self,
        args: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        tagged: bool = True,
        timeout: float | None = None,
    ) -> tuple[Any, str]:
        """One subprocess round-trip. Returns ``(payload, stderr_text)``.

        ``tagged=True`` → payload is the list of `_decode_marshal`-ed
        dicts / strings / bytes from `_read_marshal_stream`. ``tagged
        =False`` → payload is raw stdout bytes (caller decodes).

        ``stdin`` is forced to DEVNULL when no ``input_bytes`` is
        supplied so a stale ticket can't trigger an interactive
        password prompt that would hang the worker forever.

        ``timeout`` caps how long we wait on ``communicate()``.
        ``None`` (the default) means "use :data:`_DEFAULT_CLI_TIMEOUT_S`"
        (env-overridable via ``P4V_CLI_TIMEOUT``). Pass an explicit
        float to widen / tighten for a specific call site (sync chunks
        with hundreds of files may want a longer ceiling; a heartbeat
        probe wants a tighter one). On expiry we kill the child and
        raise :class:`P4Exception` so the resilient runner can decide
        retry-vs-propagate the same way it does for transport errors.
        """
        argv = self._build_argv(args, tagged=tagged)
        effective_timeout = (
            timeout if timeout is not None else _DEFAULT_CLI_TIMEOUT_S
        )
        try:
            proc = subprocess.Popen(
                argv,
                stdin=(subprocess.PIPE if input_bytes is not None
                       else subprocess.DEVNULL),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **_SUBPROCESS_FLAGS,
            )
        except FileNotFoundError as e:
            raise P4Exception(
                "`p4` command-line client not found on PATH"
            ) from e
        try:
            stdout, stderr = proc.communicate(
                input=input_bytes, timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired:
            # Kill the hung child and drain whatever did arrive so the
            # error message can mention partial stderr (often the most
            # useful hint about why it hung). `communicate()` after
            # `kill()` is the documented cleanup pattern.
            proc.kill()
            try:
                _stdout, _stderr = proc.communicate(timeout=2)
            except Exception:  # noqa: BLE001
                _stderr = b""
            tail = (
                _stderr.decode("utf-8", errors="replace").strip()
                if _stderr else ""
            )
            hint = (
                "  (raise P4V_CLI_TIMEOUT or pass timeout=… per-call "
                "to widen)"
            )
            msg = (
                f"p4 timed out after {effective_timeout:.0f}s: "
                f"`p4 {' '.join(str(a) for a in args[:4])}`"
            )
            if tail:
                msg = f"{msg}\nstderr: {tail}"
            raise P4Exception(msg + hint)
        except Exception as e:  # noqa: BLE001
            # subprocess-level explosion. Treat as a transport failure
            # so the resilient runner retries.
            raise P4Exception(f"p4 subprocess failed: {e}") from e
        stderr_text = (
            stderr.decode("utf-8", errors="replace") if stderr else ""
        )
        if tagged:
            return _read_marshal_stream(stdout or b""), stderr_text
        return stdout or b"", stderr_text

    def _check_and_extract_error(
        self,
        payload: Any,
        stderr: str,
        *,
        tagged: bool,
    ) -> str:
        err_parts: list[str] = []
        if stderr.strip():
            err_parts.append(stderr.strip())
        if tagged and isinstance(payload, list):
            inband = _extract_error_text(payload)
            if inband:
                err_parts.append(inband)
        return "\n".join(err_parts).strip()

    # --- core invocation ---------------------------------------------------

    # Idempotent reads worth caching for `_CLI_READ_CACHE_TTL_S` so a
    # repeated UI refresh doesn't pay the spawn cost N times. Kept
    # small / explicit — when in doubt, don't cache. Tuples are
    # whole-args; partial matching is intentionally not supported.
    _CACHEABLE_ARG_HEADS: tuple[tuple[str, ...], ...] = (
        ("info",),
        ("client", "-o"),
    )

    @classmethod
    def _args_is_cacheable(cls, args: tuple[str, ...]) -> bool:
        for head in cls._CACHEABLE_ARG_HEADS:
            if args[: len(head)] == head:
                return True
        return False

    def _cache_get(self, key: tuple[str, ...]) -> list | None:
        with self._read_cache_lock:
            hit = self._read_cache.get(key)
        if hit is None:
            return None
        ts, payload = hit
        if time.time() - ts > _CLI_READ_CACHE_TTL_S:
            # Stale — drop it (best effort, no lock needed since the
            # next put() under lock will overwrite either way).
            with self._read_cache_lock:
                self._read_cache.pop(key, None)
            return None
        return payload

    def _cache_put(self, key: tuple[str, ...], payload: list) -> None:
        with self._read_cache_lock:
            self._read_cache[key] = (time.time(), payload)

    def invalidate_read_cache(self) -> None:
        """Drop every cached idempotent-read entry.

        Called by the façade after a write that might affect a cached
        spec (e.g., `client -i` invalidates `client -o`'s cache).
        """
        with self._read_cache_lock:
            self._read_cache.clear()

    def run_tagged(self, args: Sequence[str]) -> list:
        args_tuple = tuple(args)
        use_cache = (
            _CLI_READ_CACHE_TTL_S > 0
            and self._args_is_cacheable(args_tuple)
        )
        if use_cache:
            cached = self._cache_get(args_tuple)
            if cached is not None:
                # Return a fresh list — defensive against a caller
                # mutating the result in place and corrupting the cache.
                return list(cached)
        payload, stderr = self._invoke(args, tagged=True)
        error_msg = self._check_and_extract_error(
            payload, stderr, tagged=True,
        )
        if error_msg:
            raise P4Exception(error_msg)
        rows = _project_tagged_rows(payload)
        if use_cache:
            self._cache_put(args_tuple, list(rows))
        return rows

    def run_text(self, args: Sequence[str]) -> str:
        stdout, stderr = self._invoke(args, tagged=False)
        if not stdout and stderr.strip():
            # No data + stderr → command-level failure; callers of
            # run_text already swallow exceptions into "", so raise so
            # the resilient runner can decide retry vs not.
            raise P4Exception(stderr.strip())
        try:
            return stdout.decode("utf-8")
        except UnicodeDecodeError:
            return stdout.decode("utf-8", errors="replace")

    # --- specialised paths -------------------------------------------------

    def login_status(self) -> dict | None:
        try:
            payload, stderr = self._invoke(("login", "-s"))
        except P4Exception:
            return None
        if stderr.strip():
            return None
        rows = _project_tagged_rows([
            r for r in payload
            if not (isinstance(r, dict) and r.get("code") == "error")
        ])
        for r in rows:
            if isinstance(r, dict):
                return r
        return None

    def fetch_form(self, kind: str, key: str | None = None) -> dict:
        # `p4 [-G] <kind> -o [<key>]` — marshalled output is the natural
        # shape for tagged forms, so this path stays inside `run_tagged`.
        args: list[Any] = [kind, "-o"]
        if key is not None:
            args.append(key)
        rows = self.run_tagged(args)
        if not rows or not isinstance(rows[0], dict):
            raise P4Exception(
                f"unexpected `p4 {' '.join(str(a) for a in args)}` "
                f"response: {rows!r}"
            )
        return _flatten_numbered(rows[0])

    def save_form(
        self,
        kind: str,
        form: dict,
        *,
        force: bool = False,
    ) -> list:
        # Scenario §5.2 — pipe text form to `p4 ... -i`. Critically,
        # we must NOT pass `-G` on save_form: marshalled output mode
        # also expects marshalled *input*, and feeding text-form bytes
        # to `p4 -G change -i` triggers "Invalid marshalled data
        # supplied as input." Drop `-G` for the response too — p4
        # emits a single text line like "Change 12345 created."
        # which we wrap as one `code: info` row so the façade's
        # info-row parser (looking for "Change <N> created.") still
        # finds it.
        args: list[Any] = [kind, "-i"]
        if force:
            args.insert(1, "-f")
        text = _form_dict_to_text(form)
        stdout, stderr = self._invoke(
            args,
            input_bytes=text.encode("utf-8"),
            tagged=False,
        )
        # Any successful save *might* have changed a spec we cached
        # (`client -o` is the live example today). Blow the cache so
        # the next read fetches fresh — cheaper than a per-kind
        # invalidation map and a save_form is rare enough that the
        # full flush is negligible.
        self.invalidate_read_cache()
        if stderr.strip() and not stdout:
            raise P4Exception(stderr.strip())
        body = stdout.decode("utf-8", errors="replace").strip()
        if not body:
            # An empty response with no stderr usually means the form
            # was a no-op (no changes); surface as an empty list so
            # callers that don't care just see no info rows.
            return []
        # p4 may emit multiple status lines (one per affected field /
        # object). Each non-empty line becomes one info row in the
        # shape callers expect ("Change 12345 created." -> bare str).
        return [line for line in body.splitlines() if line.strip()]

    def grep_stream(
        self,
        pattern: str,
        scope: str,
        on_match: GrepMatchCallback,
        cancelled: CancelledFn,
        *,
        case_insensitive: bool,
        max_matches: int,
    ) -> int:
        # Scenario §5.3 — Popen + `-G` line parser. The original
        # design hinted at `-ztag` text mode, but reading marshalled
        # dicts off `Popen.stdout` is symmetrical with run_tagged and
        # avoids reparsing the `... key value` text format.
        flags = ["-s", "-n"]
        if case_insensitive:
            flags.append("-i")
        argv = self._build_argv(
            ("grep", *flags, "-e", pattern, scope),
            tagged=True,
        )
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                **_SUBPROCESS_FLAGS,
            )
        except FileNotFoundError:
            return 0

        # Cancellation watcher — a tiny daemon that polls cancelled()
        # every _GREP_CANCEL_POLL_S and kills the subprocess as soon
        # as the user types a new keystroke.
        #
        # Without it, marshal.load(proc.stdout) blocks until the
        # *next* match arrives, so on a slow grep with sparse hits the
        # cancellation could take seconds to take effect (the user
        # would see a stale earlier query's results trickling in
        # behind the new query's). Killing the child from the watcher
        # makes the main loop's marshal.load raise EOFError within
        # one poll tick, even mid-blob.
        #
        # The watcher uses `threading.Event` for its own sleep so a
        # successful finish (we set _stop_watcher) wakes it
        # immediately instead of holding a 100 ms sleep we don't
        # need any more — keeps the test wall-clock honest.
        stop_watcher = threading.Event()

        def _watch() -> None:
            while not stop_watcher.wait(_GREP_CANCEL_POLL_S):
                try:
                    if cancelled():
                        proc.kill()
                        return
                except Exception:  # noqa: BLE001
                    return
                if proc.poll() is not None:
                    return

        watcher = threading.Thread(
            target=_watch,
            name=f"p4-grep-cancel-watcher-{proc.pid}",
            daemon=True,
        )
        watcher.start()
        count = 0
        try:
            while True:
                if cancelled():
                    break
                try:
                    # Trust boundary: proc.stdout is the user-trusted
                    # p4d's response stream proxied by `p4`. See the
                    # module-level "Trust boundary" note.
                    obj = marshal.load(proc.stdout)
                except (EOFError, ValueError):
                    break
                row = _decode_marshal(obj)
                if not isinstance(row, dict):
                    continue
                if row.get("code") == "error":
                    continue
                # Treat `code: stat` rows as match records; anything
                # else (info / warning) we just skip.
                if row.get("code") == "stat" or "depotFile" in row:
                    row.pop("code", None)
                    try:
                        on_match(row)
                    except Exception:  # noqa: BLE001
                        break
                    count += 1
                    if count >= max_matches:
                        break
        finally:
            # Wake the watcher so it doesn't sit in its sleep for
            # another _GREP_CANCEL_POLL_S window after we've already
            # exited the read loop.
            stop_watcher.set()
            # Kill the subprocess so a cancellation doesn't leave a
            # rogue `p4 grep` chewing through the depot in the
            # background. (The watcher may have done this already;
            # kill() on a dead child is harmless.)
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
            # Best-effort watcher join; daemon=True means we don't
            # hang the worker if it stalled.
            watcher.join(timeout=1)
        return count

    # --- internals ---------------------------------------------------------

    def _snapshot_p4_set(self) -> dict[str, str]:
        """Parse `p4 set -q` into a flat env-like dict.

        `p4 set -q` prints one ``KEY=value`` per line listing the
        effective P4 environment (env vars + P4CONFIG file). We snapshot
        it once at startup so a later mutation of the process env can't
        change connection target underneath an in-flight command.
        Failures fall back to an empty dict — the configure()/connect()
        path will then rely on whatever the user passes explicitly.
        """
        try:
            cp = subprocess.run(
                [self._p4_bin, "set", "-q"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
                **_SUBPROCESS_FLAGS,
            )
        except Exception:  # noqa: BLE001
            return {}
        out: dict[str, str] = {}
        for line in cp.stdout.decode("utf-8", "replace").splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # p4 set -q sometimes appends " (config)" / " (set)" / etc.
            # markers after the value; strip them so we get the raw
            # value the user actually intended.
            value = value.strip()
            value = re.sub(r"\s+\([^)]+\)\s*$", "", value).strip()
            if key:
                out[key] = value
        return out

    def _probe_version(self) -> str:
        """`p4 -V` first informative line → "2024.1 (MACOSX1015X86_64)" form."""
        try:
            cp = subprocess.run(
                [self._p4_bin, "-V"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
                **_SUBPROCESS_FLAGS,
            )
        except Exception:  # noqa: BLE001
            return ""
        text = cp.stdout.decode("utf-8", "replace")
        # Looking for a line like "Rev. P4/LINUX26X86_64/2024.1/2503000 (2024/05/01)."
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Rev. P4/"):
                m = re.match(r"Rev\. P4/(\S+)/(\S+)/", line)
                if m:
                    platform, version = m.group(1), m.group(2)
                    return f"{version} ({platform})"
                return line
        return ""

    def version_info(self) -> str:
        return f"p4 CLI {self._version_str}".strip()


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------

def _select_backend() -> _Backend:
    """Pick the active backend per the precedence in this module's docstring.

    Raises :class:`P4SetupError` when no backend can be activated.
    """
    forced = (os.environ.get("P4V_BACKEND") or "").strip().lower()
    if forced == "cli":
        return _CLIBackend()  # raises P4SetupError if `p4` missing
    if forced == "python":
        try:
            return _PythonBackend()
        except ImportError as e:
            raise P4SetupError(
                "P4V_BACKEND=python but P4Python (`pip install p4python`) "
                "is not available."
            ) from e
    if forced and forced not in ("auto", ""):
        raise P4SetupError(
            f"P4V_BACKEND={forced!r}: expected 'python', 'cli' or unset"
        )
    # Auto — prefer Python, fall back to CLI.
    try:
        return _PythonBackend()
    except ImportError:
        try:
            return _CLIBackend()
        except P4SetupError as e:
            raise P4SetupError(
                "Neither P4Python nor the `p4` CLI is available. "
                "Install one of:\n"
                "  • P4Python — `pip install p4python`\n"
                "  • p4 CLI   — https://www.perforce.com/downloads"
            ) from e


# ---------------------------------------------------------------------------
# Façade — same surface callers had under the P4Python-only layout
# ---------------------------------------------------------------------------

class P4Service:
    def __init__(
        self,
        cmd_log=None,
        backend: _Backend | None = None,
    ) -> None:
        # Backend resolved lazily so tests can inject a custom one,
        # and so a fatal SetupError surfaces at the caller (`p4v.py`)
        # rather than at import time.
        self._backend: _Backend = backend or _select_backend()
        # Two-tier serialisation:
        #
        # `_connect_lock` (always mutex): protects connect() /
        # disconnect() / configure() — these mutate connection state
        # that the backend (especially P4Python) assumes is touched
        # by one thread at a time. Held only for the brief
        # state-change window.
        #
        # `_call_sem` (BoundedSemaphore sized by backend): guards
        # `run_tagged` / `run_text` / form calls. Python backend uses
        # 1 permit (P4Python's connection isn't thread-safe);
        # CLI backend uses N (each subprocess is independent, so we
        # let parallel UI fan-outs run concurrently). N is
        # configurable via P4V_CLI_CONCURRENCY.
        #
        # Why two locks: a long-running call shouldn't block a quick
        # connection state check, and a connect-time race shouldn't
        # block legitimate parallel reads on the CLI backend.
        self._connect_lock = threading.Lock()
        self._call_sem = threading.BoundedSemaphore(
            max(1, self._backend.max_concurrent_calls),
        )
        # Optional CmdLog for the Command Monitor — when present, every
        # _run_resilient call records start/end so users can inspect
        # what's running and what just ran.
        self._cmd_log = cmd_log

    # --- introspection -----------------------------------------------------

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def backend_version(self) -> str:
        return self._backend.version_info()

    # --- connection lifecycle ---------------------------------------------

    def connect(
        self,
        port: str | None = None,
        user: str | None = None,
        client: str | None = None,
        charset: str | None = None,
    ) -> None:
        # Configure first, then open the socket only if we haven't
        # already. Re-configure on an already-connected service is
        # supported on purpose — the profile picker can flip target
        # without a disconnect/reconnect dance — and the previous
        # two-branch version of this method emitted the same
        # `configure()` call twice (once in each branch), which made
        # the intent harder to read than the behaviour warranted.
        with self._connect_lock:
            self._backend.configure(
                port=port, user=user, client=client, charset=charset,
            )
            if not self._backend.connected:
                self._backend.connect()

    def disconnect(self) -> None:
        with self._connect_lock:
            self._backend.disconnect()

    @property
    def connected(self) -> bool:
        return self._backend.connected

    @property
    def port(self) -> str:
        return self._backend.port

    @property
    def user(self) -> str:
        return self._backend.user

    @property
    def client(self) -> str:
        return self._backend.client

    def _ensure_connected_locked(self) -> None:
        """Double-checked lazy connect under `_connect_lock`.

        Called by every operation that needs the backend live but
        doesn't itself go through `_run_resilient`. Cheap when
        already connected (no lock acquisition); takes the connect
        mutex only on the cold path.
        """
        if self._backend.connected:
            return
        with self._connect_lock:
            if not self._backend.connected:
                self._backend.connect()

    # --- resilient runner --------------------------------------------------

    def _run_resilient(
        self,
        args: Sequence[str],
        *,
        on_retry: RetryCallback | None = None,
        max_attempts: int = 10,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        text_mode: bool = False,
    ) -> Any:
        """Execute ``p4 <args>`` with auto-reconnect on connection failures.

        Lock is acquired per attempt and released during the backoff
        sleep, so other commands can interleave while we wait for the
        server to come back. Command-level errors (e.g. "no such file")
        are re-raised immediately without retry — only failures that
        match :data:`_CONNECTION_ERROR_FRAGMENTS` are retried.

        ``text_mode=True`` routes through ``run_text`` for the rare
        commands (`describe -du`, `diff -du`) whose useful output isn't
        tagged. Otherwise we use ``run_tagged`` and return the row list.

        Raises the last :class:`P4Exception` if every attempt failed
        with a connection-level error.
        """
        last_exc: BaseException | None = None
        delay = base_delay
        log_id: int | None = None
        if self._cmd_log is not None:
            log_id = self._cmd_log.begin_command(tuple(args))
        for attempt in range(1, max_attempts + 1):
            try:
                # Ensure connection under the short mutex first, then
                # acquire a call permit (which is BoundedSemaphore(N)
                # — N=1 for Python backend, N=_CLI_CONCURRENCY for
                # CLI). The two-tier split lets parallel CLI calls
                # proceed concurrently without racing on the connect
                # state flip.
                if not self._backend.connected:
                    with self._connect_lock:
                        if not self._backend.connected:
                            self._backend.connect()
                with self._call_sem:
                    if text_mode:
                        result = self._backend.run_text(args)
                    else:
                        result = self._backend.run_tagged(args)
                if log_id is not None:
                    self._cmd_log.end_command(log_id, failed=False)
                return result
            except P4Exception as e:
                last_exc = e
                if not _is_connection_error(e):
                    # Genuine command error — surface immediately.
                    if log_id is not None:
                        self._cmd_log.end_command(
                            log_id, failed=True, error=str(e),
                        )
                    raise
                if on_retry is not None:
                    try:
                        on_retry(attempt, max_attempts, e)
                    except Exception:  # noqa: BLE001
                        # Never let a UI callback take down the worker.
                        pass
                if attempt < max_attempts:
                    # Sleep WITHOUT the lock so other queued commands
                    # can try the connection during this lull.
                    time.sleep(min(delay, max_delay))
                    delay = min(delay * 2, max_delay)
        if log_id is not None:
            self._cmd_log.end_command(
                log_id, failed=True,
                error=str(last_exc) if last_exc else "retry exhausted",
            )
        if last_exc is not None:
            raise last_exc
        raise P4Exception("retry exhausted")  # unreachable safety net

    # --- queries -----------------------------------------------------------

    def login_status(self) -> dict | None:
        with self._call_sem:
            return self._backend.login_status()

    def info(self) -> P4Info:
        rows = self._run_resilient(("info",))
        d = rows[0] if rows else {}
        if not isinstance(d, dict):
            d = {}
        return P4Info(
            user=d.get("userName", ""),
            client=d.get("clientName", ""),
            port=self._backend.port,
            server_address=d.get("serverAddress", ""),
            server_version=d.get("serverVersion", ""),
            server_uptime=d.get("serverUptime", ""),
            client_root=d.get("clientRoot", ""),
            client_host=d.get("clientHost", ""),
        )

    def run(
        self,
        *args: Any,
        on_retry: RetryCallback | None = None,
        max_attempts: int = 10,
    ) -> list:
        # Permissive public surface: callers may pass ints / Paths /
        # whatever — we str()-cast at the boundary so the typed
        # internal pipeline (`_run_resilient` / backend.run_tagged)
        # gets a clean `Sequence[str]`. This matches the historical
        # behaviour where `_build_argv` did the stringification, but
        # promotes it from "happens later, somewhere" to "happens
        # here, visibly".
        argv = tuple(str(a) for a in args)
        return self._run_resilient(
            argv, on_retry=on_retry, max_attempts=max_attempts,
        )

    def depots(self) -> list[dict]:
        """Return all depots on the server."""
        try:
            return self._run_resilient(("depots",))
        except P4Exception:
            return []

    def dirs(self, depot_glob: str) -> list[str]:
        """List depot subdirectories matching ``depot_glob`` (e.g. ``//depot/*``)."""
        try:
            rows = self._run_resilient(("dirs", depot_glob))
        except P4Exception:
            return []
        return [r["dir"] for r in rows
                if isinstance(r, dict) and "dir" in r]

    def files(self, depot_glob: str) -> list[dict]:
        """List files matching ``depot_glob``. Deleted-at-head are filtered out.

        ``-e`` makes the server return only files that exist at head, which
        covers ``delete``, ``move/delete``, ``purge`` and ``archive`` in one
        shot — a manual ``action != "delete"`` filter misses ``move/delete``
        so renamed files would otherwise linger in the depot tree.
        """
        try:
            return self._run_resilient(("files", "-e", depot_glob))
        except P4Exception:
            return []

    def fstat(self, path_glob: str) -> list[dict]:
        """Return ``p4 fstat`` rows for files matching ``path_glob``.

        ``path_glob`` may be a depot path (``//depot/foo/*``) or a client
        path (``//clientname/foo/*``). Each row may contain ``haveRev``,
        ``headRev``, ``headAction``, ``action``, ``depotFile``, ``clientFile``.
        """
        try:
            return self._run_resilient(("fstat", path_glob))
        except P4Exception:
            return []

    def get_changelist_form(self, change: str) -> dict:
        """Return the parsed form for ``p4 change -o <change>``.

        Raises :class:`P4Exception` on failure (caller surfaces the message).
        """
        self._ensure_connected_locked()
        with self._call_sem:
            return self._backend.fetch_form("change", str(change))

    def update_changelist_description(
        self,
        change: str,
        new_description: str,
        *,
        force: bool = False,
    ) -> None:
        """Replace the Description of an existing changelist.

        ``force=True`` adds the ``-f`` flag (admin-only) which is required
        to edit a *submitted* changelist's description. Pending CLs
        owned by the current user save without ``-f``.
        """
        self._ensure_connected_locked()
        with self._call_sem:
            form = self._backend.fetch_form("change", str(change))
            form["Description"] = new_description
            self._backend.save_form("change", form, force=force)

    def create_changelist(self, description: str) -> str:
        """Create an empty pending changelist and return its number.

        Goes through the backend's form API (`fetch_form` + `save_form`).
        The backend serialises and dispatches appropriately (P4Python via
        `input=` + `run("change", "-i")`; CLI via text-form stdin pipe).
        Both end up returning the same info-row list that
        ``["Change 12345 created."]`` is parsed out of.
        """
        self._ensure_connected_locked()
        with self._call_sem:
            # No key → fetch the "new" template.
            form = self._backend.fetch_form("change")
            form["Description"] = description
            form.pop("Files", None)
            result = self._backend.save_form("change", form)
        for item in result:
            if isinstance(item, str):
                tokens = item.split()
                if len(tokens) >= 2 and tokens[1].isdigit():
                    return tokens[1]
        raise P4Exception(f"unexpected `change -i` result: {result!r}")

    def where(self, depot_path: str) -> dict | None:
        """Resolve a depot or client path to its local filesystem path.

        Returns a dict with depotFile / clientFile / path keys, or None
        if the path isn't mapped in the current client view.
        """
        try:
            rows = self._run_resilient(("where", depot_path))
        except P4Exception:
            return None
        if not rows or not isinstance(rows[0], dict):
            return None
        r = rows[0]
        return {
            "depotFile":  r.get("depotFile"),
            "clientFile": r.get("clientFile"),
            "path":       r.get("path"),
        }

    def grep(
        self,
        pattern: str,
        scope: str = "//...",
        *,
        case_insensitive: bool = True,
        max_matches: int = 200,
    ) -> list[dict]:
        """``p4 grep`` over the depot, returning matching lines.

        Used by Fast Search's ``?<query>`` content mode. The server-
        side grep walks the head revisions of every file under
        ``scope`` (default ``//...``) so it can be costly — callers
        should pass a narrower scope when they have one (e.g.
        ``//depot/foo/...``) and pair this with the modal's
        debounce + exclusive-worker so a typing storm doesn't
        queue up parallel scans.

        Each result row is a dict with at least:
            depotFile, rev, line, matchedLine

        Rows are returned in the order the server emitted them;
        ``max_matches`` is applied client-side because ``p4 grep -m``
        is per-file, not global.
        """
        if not pattern:
            return []
        flags = ["-s", "-n"]
        if case_insensitive:
            flags.append("-i")
        # ``-e`` makes ``pattern`` a literal regex argument so leading
        # ``-`` characters can't get mis-parsed as flags.
        try:
            rows = self._run_resilient(
                ("grep", *flags, "-e", pattern, scope),
            )
        except P4Exception:
            return []
        out: list[dict] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            out.append(r)
            if len(out) >= max_matches:
                break
        return out

    def grep_stream(
        self,
        pattern: str,
        scope: str,
        on_match: GrepMatchCallback,
        cancelled: CancelledFn,
        *,
        case_insensitive: bool = True,
        max_matches: int = 500,
    ) -> int:
        """Stream ``p4 grep`` matches via ``on_match(row)`` callback.

        Delegates to the active backend. P4Python uses an OutputHandler
        so each match arrives the moment the server emits it; the CLI
        backend reads marshalled rows off a ``Popen.stdout`` for the
        same effect.

        ``on_match(row)`` is called from this worker thread (not the
        Textual UI thread); the caller is responsible for marshalling
        the row onto the UI via ``app.call_from_thread``. ``row`` is
        a dict with at least ``depotFile`` / ``rev`` / ``line`` /
        ``matchedLine`` keys.
        ``cancelled()`` is a no-arg callable returning ``True`` when
        the caller wants the stream stopped (e.g. a newer keystroke
        replaced the query) — checked between rows so cancellation is
        at-worst one row latent (CLI backend) or one server emit
        (Python backend).

        Returns the number of rows actually delivered.
        """
        if not pattern:
            return 0
        try:
            self._ensure_connected_locked()
        except P4Exception:
            return 0
        with self._call_sem:
            # Grep can be long-running; holding one permit for its
            # duration is intentional (so we don't fork a parallel
            # `p4 grep` on top of the current one — the user almost
            # always wants the *latest* query, and the watcher thread
            # in `_CLIBackend.grep_stream` kills the previous when
            # `cancelled` flips). Other commands keep the remaining
            # N-1 permits.
            return self._backend.grep_stream(
                pattern,
                scope,
                on_match,
                cancelled,
                case_insensitive=case_insensitive,
                max_matches=max_matches,
            )

    def fetch_client_view(self) -> list[str]:
        """Return the current client's ``View`` lines (raw mapping pairs).

        Empty list if no client is configured or the spec fetch failed.
        Each entry is one mapping line, e.g. ``//depot/foo/... //cli/foo/...``
        — depot side first, optionally prefixed with ``-`` (exclude) or
        ``+`` (override). Used by the Depot tree to dim paths that the
        current workspace's view doesn't include.
        """
        name = self._backend.client
        if not name:
            return []
        try:
            self._ensure_connected_locked()
            with self._call_sem:
                spec = self._backend.fetch_form("client", name)
        except P4Exception:
            return []
        raw = spec.get("View") if isinstance(spec, dict) else None
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(x) for x in raw]
        return [str(raw)]

    def opened_in_change(self, change: str) -> list[dict]:
        """List files opened in changelist ``change`` (number or ``"default"``).

        Scoped to the current client.
        """
        try:
            return self._run_resilient(("opened", "-c", change))
        except P4Exception:
            return []

    def pending_changes(
        self,
        client: str | None = None,
        user: str | None = None,
    ) -> list[dict]:
        """Pending changelists on the server, optionally filtered.

        - ``client=X`` restricts to a single workspace (what p4v's
          Pending tab traditionally shows).
        - ``user=Y`` restricts to a single user across *all* their
          workspaces — useful for "show me all my outstanding CLs no
          matter which machine they were created on".

        Pass both to AND-filter; pass neither for the unfiltered
        server-wide list (rare — usually noisy). Every returned row
        includes a ``client`` field, so callers can tell which
        workspace a CL belongs to and tag remote vs. local rows.
        """
        args: list[str] = ["changes", "-s", "pending", "-L"]
        if client:
            args += ["-c", client]
        if user:
            args += ["-u", user]
        try:
            return self._run_resilient(tuple(args))
        except P4Exception:
            return []

    def submitted_changes(
        self,
        client: str | None = None,
        max_count: int = 100,
    ) -> list[dict]:
        """Most recent submitted changelists (server-wide unless ``client`` set)."""
        args: list[str] = ["changes", "-s", "submitted", "-L",
                           "-m", str(max_count)]
        if client:
            args += ["-c", client]
        try:
            return self._run_resilient(tuple(args))
        except P4Exception:
            return []

    def describe(self, change: str) -> dict:
        """Return ``p4 describe -s <change>`` as a single dict (or {} on error).

        For submitted changelists the result includes ``desc`` plus parallel
        arrays ``depotFile`` / ``rev`` / ``action`` / ``type`` covering every
        file in the change.

        Backend parity: P4Python natively returns those file fields as
        lists, but the CLI ``-G`` backend emits them as numbered keys
        (``depotFile0``, ``depotFile1``, …) with no flat ``depotFile``. We
        run :func:`_flatten_numbered` so every caller sees the parallel-array
        shape on both backends — otherwise ``info.get("depotFile")`` is
        ``None`` on the CLI backend and the whole file list silently
        vanishes. (No-op on the already-flat P4Python dict.)
        """
        try:
            rows = self._run_resilient(("describe", "-s", str(change)))
        except P4Exception:
            return {}
        if rows and isinstance(rows[0], dict):
            return _flatten_numbered(rows[0])
        return {}

    def diff_describe(self, change: str) -> str:
        """Return ``p4 describe -du <change>`` as a single text blob.

        Tagged output strips the unified-diff section, so this routes
        through the backend's text mode. Empty string on failure —
        caller can show a "no diff" placeholder rather than crashing.
        Connection errors are caught here too; for a viewer popup,
        retrying isn't important enough to wrap with the resilient
        runner.
        """
        try:
            return self._run_resilient(
                ("describe", "-du", str(change)),
                text_mode=True,
                max_attempts=1,
            )
        except P4Exception:
            return ""

    def diff_against_have(self, change: str) -> str:
        """Return unified diff of every opened file in pending ``change``
        against its #have revision (``p4 diff -du -c <change>``).

        Same untagged-text dance as :meth:`diff_describe` — the unified-
        diff sections only come through as raw text. Empty string on any
        failure.
        """
        try:
            return self._run_resilient(
                ("diff", "-du", "-c", str(change)),
                text_mode=True,
                max_attempts=1,
            )
        except P4Exception:
            return ""

    def filelog(self, depot_file: str, max_revs: int = 50) -> list[dict]:
        """Per-revision history for ``depot_file``.

        Both backends return one row per file with parallel arrays
        (P4Python natively; CLI via `-G` marshal with `_flatten_numbered`
        applied to numbered keys). We flatten into a list of per-
        revision dicts so the UI can render row-wise.
        """
        try:
            rows = self._run_resilient(
                ("filelog", "-L", "-m", str(max_revs), depot_file)
            )
        except P4Exception:
            return []
        if not rows:
            return []
        row = rows[0]
        if not isinstance(row, dict):
            return []
        # `_flatten_numbered` is a no-op when the dict already has list
        # values (Python backend path); for the CLI backend it
        # collapses `rev0` / `rev1` / … into a `rev: [...]` list.
        row = _flatten_numbered(row)
        revs = row.get("rev") or []
        if not isinstance(revs, list):
            revs = [revs]
        keys = ("rev", "change", "action", "time", "user",
                "client", "type", "desc")
        out: list[dict] = []
        for i in range(len(revs)):
            entry = {}
            for k in keys:
                arr = row.get(k) or []
                if not isinstance(arr, list):
                    arr = [arr]
                entry[k] = arr[i] if i < len(arr) else ""
            out.append(entry)
        return out
