"""Bottom-anchored scrolling Log panel.

A scrollable view onto :class:`~p4v_tui.cmd_log.CmdLog`. Subscribes
to the log's listener channel so every ``begin_command`` /
``end_command`` / ``begin_job`` / ``end_job`` / ``update_job_progress``
event triggers a re-render. Plus a 1-second tick so a long-running
chunked job's elapsed time + ETA stay current between explicit
updates.

Replaces the previous one-line ``JobStatusBar`` at the top of the
screen — the p4v-style Log pane at the bottom is more useful
because it shows the *sequence* of commands, not just the current
chunked job.

Layout choice
-------------
The entire ``CmdLog`` snapshot is rendered chronologically; users
scroll vertically to inspect older entries. The panel grows /
shrinks via the bottom :class:`HorizontalSplitter` and the content
underneath stays put — Textual's ``ScrollView`` clamps the
viewport without dropping log lines.

Why ``LogPanel`` extends ``RichLog`` directly
---------------------------------------------
Earlier revisions wrapped a ``RichLog`` inside ``Widget`` (and then
``Container``) via ``compose()``. Both crashed on first paint in
Textual 8.x with ::

    AttributeError: 'NoneType' object has no attribute 'render_strips'

at ``Visual.to_strips`` — the combination of *border + padded
content region + no own visual on the parent* drives Textual into
a code path where ``Widget._render()`` returns ``None`` for the
parent's content slot. By making ``LogPanel`` *itself* a
``RichLog`` (one widget, no child composition), there's only one
render path and it has actual content. The bordered title still
hangs off the same widget.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.text import Text

from textual import events
from textual.binding import Binding
from textual.widgets import RichLog

from ..utils import format_eta


if TYPE_CHECKING:
    from ..cmd_log import CmdLog, CmdEntry


# 8 visible content rows + 2 cells of border = 10 total. The panel
# is freely resizable from the splitter above it; this is just the
# initial value before persisted UI state kicks in.
DEFAULT_HEIGHT = 10


class LogPanel(RichLog):
    DEFAULT_CSS = f"""
    LogPanel {{
        height: {DEFAULT_HEIGHT};
        border: solid $primary;
        background: $surface;
        scrollbar-size: 1 1;
    }}
    """

    BINDINGS = [
        # Mirror RichLog's intrinsic scroll keys but layer per-entry
        # selection on top: ↑/↓ move the selected entry instead of
        # the scrollbar when the panel is focused. Mouse wheel still
        # scrolls the underlying RichLog.
        Binding("up",    "select_prev", "Prev", show=False),
        Binding("down",  "select_next", "Next", show=False),
        Binding("k",     "select_prev", show=False),
        Binding("j",     "select_next", show=False),
        Binding("enter", "open_detail", "Detail"),
    ]

    def __init__(self, cmd_log: "CmdLog", **kw) -> None:
        # Surface ``id`` (and any other Widget kwarg) cleanly through
        # to RichLog; force the rendering knobs we care about.
        kw.setdefault("highlight", False)
        kw.setdefault("markup", True)
        kw.setdefault("wrap", False)
        # We manage scroll-to-bottom ourselves so a periodic re-render
        # doesn't fight the user when they've scrolled up to read history.
        kw.setdefault("auto_scroll", False)
        super().__init__(**kw)
        self._cmd_log = cmd_log
        # Tracks whether the user is currently anchored to the live
        # tail. Used both by the periodic re-render and by on_resize
        # to decide whether to re-pin to the bottom. Initially True
        # so the very first render lands at the bottom.
        self._follow_tail = True
        # ID of the currently highlighted entry (None = none — happens
        # on first paint and after the entry was trimmed out of the
        # ring). Click / Up / Down updates this; Enter dispatches to
        # the LogDetailModal anchored on it.
        self._selected_id: int | None = None
        # Border title slot — Textual renders it inline with the border
        # so the panel labels itself without a separate header row.
        self.border_title = "Log"
        # Focus is required to receive Enter / up / down; click sets
        # this too, but mark it explicitly so keyboard reaches us
        # whenever the panel has been clicked into.
        self.can_focus = True

    def on_mount(self) -> None:
        # Listener runs from arbitrary threads (JobRunner worker,
        # P4Service worker). Marshal onto the UI thread before
        # touching widget state.
        self._cmd_log.add_listener(self._on_log_change)
        # Periodic tick keeps elapsed time / ETA fresh for entries
        # whose underlying CmdEntry hasn't fired an update event.
        self.set_interval(1.0, self._render_safe)
        self._render_safe()

    def on_unmount(self) -> None:
        self._cmd_log.remove_listener(self._on_log_change)

    # --- update -------------------------------------------------------

    def _on_log_change(self) -> None:
        try:
            self.app.call_from_thread(self._render_safe)
        except Exception:  # noqa: BLE001
            # App may already be tearing down — best-effort.
            pass

    def _render_safe(self) -> None:
        try:
            self._render_log()
        except Exception:  # noqa: BLE001
            pass

    def _render_log(self) -> None:
        # Snapshot the user's intent (``_follow_tail``) BEFORE we
        # clear / rewrite — both operations move ``scroll_y`` and
        # would otherwise overwrite the flag via watch_scroll_y.
        follow = self._follow_tail
        prev_scroll_y = self.scroll_y
        self.clear()
        snapshot = self._cmd_log.snapshot()
        # Drop a stale selection if the underlying ring has trimmed
        # the entry. Keeps the highlight from "sticking" on a
        # phantom id after capacity wraparound.
        if (self._selected_id is not None
                and not any(e.id == self._selected_id for e in snapshot)):
            self._selected_id = None
        for e in snapshot:
            self.write(self._format(e))
        if follow:
            self.scroll_end(animate=False)
        else:
            self.scroll_to(y=prev_scroll_y, animate=False)

    # --- scroll / resize bookkeeping ----------------------------------

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        # Preserve the parent's scrollbar / anchor bookkeeping.
        super().watch_scroll_y(old_value, new_value)
        # Re-evaluate tail-following whenever scroll_y changes. If
        # the user scrolls up by even one line, we stop auto-scrolling
        # on the next render; if they scroll back to the bottom, we
        # resume.
        self._follow_tail = bool(self.is_vertical_scroll_end)

    def on_resize(self, event: events.Resize) -> None:
        # When the user drags the splitter to shrink the panel,
        # ``max_scroll_y`` grows but ``scroll_y`` is unchanged — so
        # a user who was watching the live tail suddenly finds
        # themselves "scrolled up" relative to the new viewport.
        # Re-pin to the bottom in that case. Growing the panel
        # naturally clamps ``scroll_y`` (watch_scroll_y fires and
        # keeps ``_follow_tail`` correct), so no special handling
        # there. When the user is scrolled up, leave them where they
        # are so the entries they were inspecting stay in view.
        if self._follow_tail:
            self.scroll_end(animate=False)

    # --- formatting --------------------------------------------------

    def _format(self, e: "CmdEntry") -> Text:
        now = time.time()
        if e.state == "running":
            marker, marker_style = "●", "cyan"
            elapsed = now - e.start_time
        elif e.state == "done":
            marker, marker_style = "✓", "green"
            elapsed = (e.end_time or e.start_time) - e.start_time
        else:
            marker, marker_style = "✗", "red"
            elapsed = (e.end_time or e.start_time) - e.start_time

        ts = time.strftime("%H:%M:%S", time.localtime(e.start_time))
        line = Text()
        line.append(f"{ts} ", style="dim")
        line.append(f"{marker} ", style=marker_style)
        line.append(f"{elapsed:6.2f}s  ", style="dim")

        # Job entries show progress + ETA; commands show their args.
        name_style = "bold" if e.is_job else ""
        line.append(e.name, style=name_style)

        if e.is_job and e.total is not None and e.done is not None:
            line.append(f"  [{e.done}/{e.total}]", style="dim")
            eta = e.eta_seconds()
            if eta is not None:
                line.append(f"  eta {format_eta(eta)}", style="dim")

        if e.error:
            line.append(f"  — {e.error[:80]}", style="red")
        if self._selected_id == e.id:
            # Inverse video for the focused row — readable on every
            # palette without needing a theme-specific colour.
            line.stylize("reverse")
        return line

    # --- selection + click + Enter ------------------------------------

    def on_click(self, event: events.Click) -> None:
        """Click highlights the entry under the pointer.

        The panel itself is a RichLog without a per-line cursor, so we
        reconstruct the entry index from the click's y coordinate
        within the widget plus the current scroll offset. Each entry
        renders on a single line (no wrap on the panel), so 1 line =
        1 entry.

        ``event.y`` is *widget-local* and counts the border too. With
        ``border: solid``, y == 0 is the top border row and the first
        content row sits at ``gutter.top``. Subtracting that offset
        before mapping to the snapshot index keeps the selection
        from sliding one entry below the touched row — a bug users
        on touch keyboards hit because tapping the first visible
        entry would always select the second.
        """
        try:
            gutter_top = self.gutter.top
            line_y = (
                int(event.y) - int(gutter_top) + int(self.scroll_y)
            )
        except Exception:  # noqa: BLE001
            return
        snap = self._cmd_log.snapshot()
        if not snap or line_y < 0 or line_y >= len(snap):
            return
        self._selected_id = snap[line_y].id
        # Stop auto-following the tail while the user is interacting —
        # otherwise the next periodic render would yank the viewport
        # back to the bottom and undo their selection.
        self._follow_tail = False
        self._render_safe()
        self.focus()

    def action_select_prev(self) -> None:
        self._step_selection(-1)

    def action_select_next(self) -> None:
        self._step_selection(+1)

    def _step_selection(self, delta: int) -> None:
        snap = self._cmd_log.snapshot()
        if not snap:
            return
        ids = [e.id for e in snap]
        try:
            i = ids.index(self._selected_id)
        except (ValueError, TypeError):
            # No selection yet — Up starts at the bottom, Down at the
            # top so the first keypress visibly moves the highlight.
            i = len(ids) if delta < 0 else -1
        i = max(0, min(len(ids) - 1, i + delta))
        self._selected_id = ids[i]
        self._follow_tail = False
        self._render_safe()

    def action_open_detail(self) -> None:
        # No explicit selection yet → pop the modal on the latest
        # entry so Enter is always meaningful.
        snap = self._cmd_log.snapshot()
        if not snap:
            return
        if self._selected_id is None:
            self._selected_id = snap[-1].id
            self._render_safe()

        # LogEntryViewerModal is a FileViewerModal subclass — it
        # keeps the proven render pipeline that survived the
        # ``Visual.to_strips → render_strips`` hang the dedicated
        # LogDetailModal kept hitting, and layers priority-bound
        # ↑/↓ entry navigation on top.
        from .log_entry_viewer import LogEntryViewerModal
        self.app.push_screen(
            LogEntryViewerModal(self._cmd_log, self._selected_id),
        )
