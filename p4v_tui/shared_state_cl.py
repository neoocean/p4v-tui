"""Dedicated numbered changelist for shared-state JSON writes.

The TUI persists versioned shared-state files
(``shared-state/permalinks.json``, ``shared-state/bookmarks.json``, …) while
the app is running.  Each save flows through a ``p4 reconcile`` so the
change is opened in the workspace and submittable.

Earlier this used the default changelist.  The ``admin@shared`` client
is shared across concurrent sessions and the default CL is global, so
files reconciled into it could be swept into another session's
``p4 submit -d``.  See ``CLAUDE.md`` ("Critical: the `admin@shared`
client is shared by concurrent sessions") for the underlying invariant.

This module:

  * Creates one **numbered** changelist on first write of the session
    (lazy — sessions that never touch shared state pay nothing).
  * Routes every shared-state ``reconcile`` into that CL via
    ``reconcile -c <CL>``.
  * Records ``(path, action)`` for each reconciled file so the final
    submit can spell out *what* changed.
  * Submits the CL on app exit with a Korean description listing every
    changed file + action.

Pure logic (``build_description``) is exported separately so it can be
unit-tested without a live Perforce server.  Wiring into the app's quit
lifecycle lives in :mod:`p4v_tui.app`.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Protocol, Tuple


# Korean labels for the p4 action verbs that ``reconcile`` may surface.
# Anything not in this map is rendered verbatim — better to show the raw
# action than to silently swallow a verb we haven't translated.
_ACTION_LABELS = {
    "add":         "추가",
    "edit":        "수정",
    "delete":      "삭제",
    "move/add":    "이동(추가)",
    "move/delete": "이동(삭제)",
    "branch":      "분기",
    "integrate":   "병합",
}


def build_description(entries: Iterable[Tuple[str, str]]) -> str:
    """Build the Korean submit description for a list of tracked changes.

    ``entries`` is an iterable of ``(path, p4_action)`` tuples in the
    order they were recorded.  Multiple entries for the same path collapse
    to a single line whose action is the *most recent* one (so an
    ``add`` followed by an ``edit`` reports as ``edit``).

    An empty input returns a placeholder description rather than an empty
    string — callers can use ``has_changes()`` to skip submit entirely
    when there's nothing to record.
    """
    seen: "OrderedDict[str, str]" = OrderedDict()
    for path, action in entries:
        key = str(path)
        if key in seen:
            del seen[key]
        seen[key] = str(action)
    if not seen:
        return "p4v-tui: 공유 상태 변경 없음"
    lines = [
        "p4v-tui: 실행 중 공유 상태 자동 변경",
        "",
        "앱 실행 중 다음 공유 상태 파일이 변경되어 자동 서브밋되었습니다:",
        "",
    ]
    for raw_path, action in seen.items():
        label = _ACTION_LABELS.get(action, action)
        try:
            name = Path(raw_path).name or raw_path
        except (TypeError, ValueError):
            name = raw_path
        lines.append(f"  - {label}: {name}  ({raw_path})")
    return "\n".join(lines)


class _P4Like(Protocol):
    """Minimal subset of :class:`p4v_tui.p4client.P4Service` we depend on.

    Declared so the tests can substitute a plain stub without faking the
    full Perforce surface.
    """

    def create_changelist(self, description: str) -> str: ...

    def update_changelist_description(
        self, change: str, new_description: str,
    ) -> None: ...

    def run(self, *args: Any) -> list: ...


# Description stamped onto the CL the moment it's created.  Replaced with
# the detailed per-file list at submit time; only ever visible if the
# user kills the process between create + submit.
_PLACEHOLDER_DESCRIPTION = (
    "p4v-tui: 공유 상태 자동 변경 (실행 중)\n"
    "\n"
    "p4v-tui 실행 중 공유 상태 JSON(permalinks / bookmarks 등) 변경분이\n"
    "모이는 체인지리스트입니다. 앱 종료 시 변경된 파일 목록과 함께\n"
    "자동으로 서브밋됩니다.\n"
)


class SharedStateChangelist:
    """Lazy, thread-safe owner of the per-session shared-state CL.

    Usage:

        cl = SharedStateChangelist()
        cl.track(p4, "/path/to/shared-state/permalinks.json")
        ...
        cl.wait_idle(timeout=3.0)
        cl.submit_if_dirty(p4)

    All public methods are safe to call from any thread.  ``track`` runs
    the ``reconcile`` synchronously in the calling thread (callers that
    want to keep the UI thread free should call it from a worker — see
    :meth:`p4v_tui.app.P4VApp._track_state_file`).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._in_flight = 0
        self._cl: str | None = None
        # (path, action) in record order.  Duplicates are kept — the
        # description builder collapses them at submit time so the
        # ordering still reflects "most recent action wins".
        self._entries: list[tuple[str, str]] = []

    @property
    def cl_number(self) -> str | None:
        """The numbered CL hosting tracked changes, or ``None`` if not yet
        created this session."""
        return self._cl

    def has_changes(self) -> bool:
        """True if at least one ``reconcile`` has recorded a real action."""
        with self._lock:
            return bool(self._entries)

    def entries(self) -> list[tuple[str, str]]:
        """A snapshot of recorded ``(path, action)`` entries."""
        with self._lock:
            return list(self._entries)

    def _ensure_cl_locked(self, p4: _P4Like) -> str:
        if self._cl is not None:
            return self._cl
        cl = p4.create_changelist(_PLACEHOLDER_DESCRIPTION)
        self._cl = cl
        return cl

    def track(self, p4: _P4Like, path: str | Path) -> None:
        """Reconcile ``path`` into the dedicated CL, recording the action.

        Errors are swallowed (server down, file outside the client view,
        already-pristine file, …): the previous bare-reconcile call this
        replaces was best-effort too, so we don't regress UX by raising
        on the persistence side.
        """
        with self._lock:
            self._in_flight += 1
        try:
            try:
                with self._lock:
                    cl = self._ensure_cl_locked(p4)
            except Exception:  # noqa: BLE001 -- best-effort tracking
                return
            try:
                rows = p4.run("reconcile", "-c", cl, str(path))
            except Exception:  # noqa: BLE001 -- best-effort tracking
                return
            recorded: list[tuple[str, str]] = []
            for row in rows or ():
                if not isinstance(row, dict):
                    continue
                action = row.get("action") or row.get("headAction")
                if not action:
                    continue
                recorded.append((str(path), str(action)))
            if recorded:
                with self._lock:
                    self._entries.extend(recorded)
        finally:
            with self._idle:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._idle.notify_all()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until every in-flight :meth:`track` has returned.

        Returns True if idle was reached within ``timeout`` seconds,
        False on timeout.  Pass ``None`` to wait indefinitely.
        """
        with self._idle:
            return self._idle.wait_for(
                lambda: self._in_flight == 0, timeout=timeout,
            )

    def submit_if_dirty(self, p4: _P4Like) -> str | None:
        """Rewrite the CL description with per-file details and submit it.

        Returns the submitted CL number on success, ``None`` if there's
        nothing to submit.  Re-raises whatever ``p4 submit`` raises so the
        caller (the quit path) can log it — we'd rather leave a
        well-formed pending CL behind than swallow the failure silently.
        """
        with self._lock:
            if self._cl is None or not self._entries:
                return None
            cl = self._cl
            description = build_description(self._entries)
        try:
            p4.update_changelist_description(cl, description)
        except Exception:  # noqa: BLE001
            # Losing the detailed description is better than orphaning
            # the CL — fall through to submit with the placeholder.
            pass
        p4.run("submit", "-c", cl)
        with self._lock:
            self._cl = None
            self._entries.clear()
        return cl
