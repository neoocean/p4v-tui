"""In-memory command log with parent/child tracking.

Every ``p4 …`` invocation that goes through :class:`P4Service` is recorded
as a leaf entry; every :class:`Job` running through :class:`JobRunner` is
recorded as a parent entry whose children are the commands its chunks
issued. The :class:`CmdMonitorModal` widget renders the current snapshot
as a tree on demand.

Parent association is via a thread-local "current job" id that the
JobRunner sets around each chunk invocation. Commands fired from
non-JobRunner threads (Textual @work workers, app handlers) end up as
top-level entries with ``parent_id = None``.

Older entries beyond ``capacity`` are pruned FIFO (and any of their still-
referenced children become orphans, which the UI shows under a synthetic
"(orphans)" branch — rare in practice because the cap is generous).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class CmdEntry:
    id: int
    parent_id: Optional[int]
    name: str
    is_job: bool
    state: str            # "running" | "done" | "failed"
    start_time: float
    end_time: Optional[float] = None
    error: Optional[str] = None
    # Progress on a job entry (None for command entries). Updated by
    # JobRunner after every chunk so the Command Monitor can render an
    # updated ETA.
    done: Optional[int] = None
    total: Optional[int] = None
    job_started: Optional[float] = None  # when first chunk actually ran

    def eta_seconds(self) -> Optional[float]:
        import time as _time
        if (not self.is_job or self.total is None
                or self.done is None or self.done <= 0
                or self.job_started is None):
            return None
        elapsed = _time.time() - self.job_started
        if elapsed <= 0:
            return None
        per = elapsed / self.done
        return per * max(0, self.total - self.done)


class CmdLog:
    def __init__(self, capacity: int = 500) -> None:
        self._lock = threading.RLock()
        self._next_id = 1
        self._entries: list[CmdEntry] = []
        self._capacity = max(50, capacity)
        self._listeners: list[Callable[[], None]] = []
        self._tls = threading.local()

    # --- thread-local current job association ---------------------------

    def set_current_job(self, jid: Optional[int]) -> None:
        if jid is None:
            self._tls.current_job = None
        else:
            self._tls.current_job = jid

    def get_current_job(self) -> Optional[int]:
        return getattr(self._tls, "current_job", None)

    # --- record begin / end ---------------------------------------------

    def begin_job(self, name: str) -> int:
        with self._lock:
            i = self._alloc_id()
            self._entries.append(CmdEntry(
                id=i, parent_id=None, name=name, is_job=True,
                state="running", start_time=time.time(),
            ))
            self._trim_locked()
        self._notify()
        return i

    def update_job_progress(
        self,
        jid: int,
        done: Optional[int],
        total: Optional[int],
        start_time: Optional[float],
    ) -> None:
        with self._lock:
            for e in self._entries:
                if e.id == jid:
                    e.done = done
                    e.total = total
                    e.job_started = start_time
                    break
        self._notify()

    def end_job(
        self, jid: int, failed: bool = False, error: Optional[str] = None,
    ) -> None:
        with self._lock:
            for e in self._entries:
                if e.id == jid:
                    e.state = "failed" if failed else "done"
                    e.end_time = time.time()
                    if error:
                        e.error = error[:200]
                    break
        self._notify()

    def begin_command(self, args: tuple) -> int:
        parent = self.get_current_job()
        with self._lock:
            i = self._alloc_id()
            self._entries.append(CmdEntry(
                id=i, parent_id=parent,
                name=self._format_args(args),
                is_job=False, state="running",
                start_time=time.time(),
            ))
            self._trim_locked()
        self._notify()
        return i

    def log_info(
        self,
        summary: str,
        details: Optional[str] = None,
    ) -> int:
        """Record a synthetic, already-completed *successful* entry.

        Same shape as :meth:`log_error` but ``state="done"`` so the
        Log panel renders it with a green ``✓`` marker. Used for
        startup / configuration messages that previously surfaced
        only as toasts — toasts cover the bottom of the screen for
        a few seconds and then vanish, leaving no scrollback. The
        Log panel keeps a history, so those messages stay reachable.
        """
        parent = self.get_current_job()
        with self._lock:
            i = self._alloc_id()
            now = time.time()
            e = CmdEntry(
                id=i, parent_id=parent,
                name=summary,
                is_job=False, state="done",
                start_time=now, end_time=now,
                error=(details[:200] if details else None),
            )
            self._entries.append(e)
            self._trim_locked()
        self._notify()
        return i

    def log_error(
        self,
        summary: str,
        details: Optional[str] = None,
    ) -> int:
        """Record a synthetic, already-completed failed entry.

        Used for one-shot issues that aren't tied to a specific p4
        command or JobRunner job — e.g. unhandled exceptions inside a
        widget. The entry renders in the Log panel marked failed
        (red ``✗``) so the user can see the error without it
        kicking the app into Textual's terminal-traceback exit.
        """
        parent = self.get_current_job()
        with self._lock:
            i = self._alloc_id()
            now = time.time()
            e = CmdEntry(
                id=i, parent_id=parent,
                name=summary,
                is_job=False, state="failed",
                start_time=now, end_time=now,
                error=(details[:200] if details else None),
            )
            self._entries.append(e)
            self._trim_locked()
        self._notify()
        return i

    def end_command(
        self, cid: int, failed: bool = False, error: Optional[str] = None,
    ) -> None:
        with self._lock:
            for e in self._entries:
                if e.id == cid:
                    e.state = "failed" if failed else "done"
                    e.end_time = time.time()
                    if error:
                        e.error = error[:200]
                    break
        self._notify()

    # --- snapshots / listeners ------------------------------------------

    def snapshot(self) -> list[CmdEntry]:
        with self._lock:
            return list(self._entries)

    def add_listener(self, fn: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(fn)
            except ValueError:
                pass

    # --- internals ------------------------------------------------------

    def _alloc_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def _trim_locked(self) -> None:
        if len(self._entries) > self._capacity:
            del self._entries[: len(self._entries) - self._capacity]

    def _notify(self) -> None:
        # Snapshot listeners under lock so removal mid-iter is safe.
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _format_args(args: tuple) -> str:
        # Keep the displayed name short — first ~4 args is plenty to
        # identify the operation; the rest is shown as "(+N more)".
        head = " ".join(str(a) for a in args[:4])
        if len(args) > 4:
            head += f"  (+{len(args) - 4} more)"
        return f"p4 {head}".rstrip()
