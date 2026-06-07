"""Log-entry detail viewer.

Thin subclass of :class:`FileViewerModal` that adds inter-entry
navigation (↑/↓, j/k) on top of the file viewer's proven render
pipeline.

Background — why a subclass of FileViewerModal
----------------------------------------------
The previous dedicated ``LogDetailModal`` reliably hung Textual 8.x at
``Visual.to_strips → render_strips`` on screen-resume. Six CLs (52580,
52583, 52585, 52587, 52589, 52590) chased the cause through Rich style
parsing, padding, deferred rendering, structural simplification, and
opaque-background workarounds — each fixed one layer and the next ran
into another. CL 52591 routed LogPanel Enter through ``FileViewerModal``
as a pragmatic resolution; the user lost ↑/↓ navigation between log
entries inside the popup.

This module restores that navigation by *being* a ``FileViewerModal``
(same compose, same render path, same widget tree — the only render
path Textual 8.x is happy with for our content shape) plus two
priority bindings that rebuild the body on entry change.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.widgets import RichLog, Static

from .file_viewer import FileViewerModal


if TYPE_CHECKING:
    from ..cmd_log import CmdLog


# How many entries to render above and below the focused one when
# building the detail body. Matches the radius the old LogDetailModal
# (and the FileViewerModal-routed fallback in LogPanel) used so the
# popup feels familiar after the rebuild.
_CONTEXT_RADIUS = 8


def _format_entry_detail(cmd_log: "CmdLog", entry_id: int) -> str:
    """Build the plain-text body for a single log entry.

    Header + window of ±``_CONTEXT_RADIUS`` surrounding entries + full
    detail block for the focused row. Plain string so the viewer stays
    read-only and the FileViewerModal render pipeline can handle it
    without any Rich-style parsing.
    """
    snap = cmd_log.snapshot()
    if not snap:
        return "(log is empty)"
    ids = [e.id for e in snap]
    try:
        i = ids.index(entry_id)
    except ValueError:
        # Entry was trimmed out of the ring — show the most recent.
        i = len(ids) - 1
    entry = snap[i]
    lines: list[str] = []
    lines.append(f"Log entry {i + 1} / {len(snap)} — {entry.name}")
    lines.append("")
    lo = max(0, i - _CONTEXT_RADIUS)
    hi = min(len(snap), i + _CONTEXT_RADIUS + 1)
    for j in range(lo, hi):
        e = snap[j]
        marker = (
            "●" if e.state == "running"
            else "✓" if e.state == "done"
            else "✗"
        )
        elapsed = (e.end_time or e.start_time) - e.start_time
        ts = time.strftime("%H:%M:%S", time.localtime(e.start_time))
        prefix = "  ►  " if j == i else "     "
        line = f"{prefix}{ts}  {marker}  {elapsed:6.2f}s   {e.name}"
        if e.error:
            line += f"  — {e.error[:120]}"
        lines.append(line)
    lines.extend(["", "— details —"])
    try:
        started = datetime.fromtimestamp(entry.start_time).strftime(
            "%Y-%m-%d %H:%M:%S",
        )
    except (TypeError, ValueError):
        started = str(entry.start_time)
    lines.append(f"start:  {started}")
    if entry.end_time:
        try:
            ended = datetime.fromtimestamp(entry.end_time).strftime(
                "%Y-%m-%d %H:%M:%S",
            )
        except (TypeError, ValueError):
            ended = str(entry.end_time)
        lines.append(f"end:    {ended}")
        duration = entry.end_time - entry.start_time
        lines.append(f"took:   {duration:.2f}s")
    else:
        lines.append("end:    (still running)")
    lines.append(f"state:  {entry.state}")
    if entry.is_job and entry.total is not None:
        lines.append(f"chunks: {entry.done or 0}/{entry.total}")
    if entry.parent_id is not None:
        lines.append(f"parent: job #{entry.parent_id}")
    if entry.error:
        lines.append("")
        lines.append("error / traceback:")
        for ln in entry.error.splitlines() or [entry.error]:
            lines.append(f"  {ln}")
    return "\n".join(lines)


def _compute_title(cmd_log: "CmdLog", entry_id: int) -> str:
    snap = cmd_log.snapshot()
    if not snap:
        return "Log entry — (empty)"
    ids = [e.id for e in snap]
    try:
        i = ids.index(entry_id)
    except ValueError:
        i = len(ids) - 1
    entry = snap[i]
    return f"Log entry {i + 1}/{len(snap)} · #{entry.id} · {entry.name}"


class LogEntryViewerModal(FileViewerModal):
    """File viewer rebound to a CmdLog entry, with ↑/↓ entry navigation.

    Inherits everything from :class:`FileViewerModal` — backdrop click-
    to-dismiss, batch-render pipeline, the close-key handlers — and
    layers prev/next-entry navigation on top. ``priority=True`` on the
    navigation bindings beats the focused ``RichLog``'s built-in line-
    scroll bindings, so the user gets entry-to-entry stepping with the
    arrow keys. PageUp/PageDown / Home / End still scroll the body for
    entries with long detail (long tracebacks).

    Placement
    ---------
    The modal hugs the top half of the screen (``place-top`` class from
    FileViewerModal — `align: center top`, `height: 55%`) so the
    LogPanel that opened it stays visible at the bottom. This follows
    the same "popup must not cover its trigger" rule the Pending /
    Submitted DataTable row pop-ups already obey (FileViewerModal hugs
    the edge opposite the highlighted row in that flow). The LogPanel
    lives at a fixed bottom-of-screen slot, so a single hard-coded
    `place-top` is enough; we don't need the row-Y heuristic the
    DataTable flow uses.
    """

    BINDINGS = [
        Binding("up",   "prev_entry", "Prev entry", priority=True),
        Binding("down", "next_entry", "Next entry", priority=True),
        Binding("k",    "prev_entry", show=False, priority=True),
        Binding("j",    "next_entry", show=False, priority=True),
        Binding("ㅏ",   "prev_entry", show=False, priority=True),
        Binding("ㅓ",   "next_entry", show=False, priority=True),
    ]

    def __init__(self, cmd_log: "CmdLog", entry_id: int) -> None:
        self._cmd_log = cmd_log
        self._entry_id = entry_id
        super().__init__(
            title=_compute_title(cmd_log, entry_id),
            content=_format_entry_detail(cmd_log, entry_id),
            # Log entries already have a row-by-row structure (see
            # `_format_entry_detail`: leading `►` marker + timestamp).
            # An additional left-margin line-number column would just
            # be noise. The user can still flip them on via `n` if
            # they want to reference a specific surrounding entry by
            # position.
            line_numbers=False,
        )
        # Hug the top of the screen so the LogPanel (always at the
        # bottom of the layout) stays visible behind the popup. The
        # place-top class is defined in FileViewerModal's CSS:
        # `align: center top` + `height: 55%`, which leaves a 45%
        # slice at the bottom uncovered — generous enough to keep
        # multiple LogPanel rows in view including the one we're
        # inspecting in the popup.
        self.add_class("place-top")

    def _footer_hint_text(self) -> str:
        # Mirrors the base class shape but leads with the entry-nav
        # hint (the primary affordance here) instead of the generic
        # scroll keys.
        on_off = "ON" if self._line_numbers else "OFF"
        return (
            f" ↑↓ entry · PgUp/PgDn scroll · "
            f"n line# ({on_off}) · Esc close "
        )

    def on_mount(self) -> None:
        super().on_mount()
        # FileViewerModal's compose() already calls our overridden
        # `_footer_hint_text`, so the entry-nav-aware hint is in
        # place from the first frame; nothing extra to do here.

    # --- navigation ----------------------------------------------------

    def action_prev_entry(self) -> None:
        self._step(-1)

    def action_next_entry(self) -> None:
        self._step(+1)

    def _step(self, delta: int) -> None:
        snap = self._cmd_log.snapshot()
        if not snap:
            return
        ids = [e.id for e in snap]
        try:
            i = ids.index(self._entry_id)
        except ValueError:
            # Selected entry was trimmed out of the ring — fall back
            # to whichever end of the buffer is closer.
            i = len(ids) - 1 if delta < 0 else 0
        i = max(0, min(len(ids) - 1, i + delta))
        if ids[i] == self._entry_id:
            return
        self._entry_id = ids[i]
        self._reload()

    def _reload(self) -> None:
        """Re-render the modal body around the current entry id.

        Re-uses FileViewerModal's existing batch-render pipeline rather
        than touching the RichLog directly — the same code path the
        viewer was proven on, just fed a new body. Re-runs the title
        widget update so the position counter / entry name stay in sync.
        """
        new_title = _compute_title(self._cmd_log, self._entry_id)
        new_body = _format_entry_detail(self._cmd_log, self._entry_id)
        self._title = new_title
        self._content = new_body
        try:
            self.query_one("#title", Static).update(f" {new_title} ")
        except Exception:  # noqa: BLE001
            pass
        try:
            log = self.query_one("#content", RichLog)
            log.clear()
        except Exception:  # noqa: BLE001
            return
        # Drive the same prepare → batched write pipeline FileViewerModal
        # uses on first mount. ``_pending_lines`` / ``_line_idx`` are
        # FileViewerModal internals — touching them keeps us on the
        # proven render path.
        self._pending_lines = self._prepare_lines(new_body)
        self._line_idx = 0
        self.call_after_refresh(self._write_next_batch)
