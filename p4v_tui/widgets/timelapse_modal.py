"""Time-lapse view for a single file.

Walks the file's revision history one rev at a time. ←/→ steps between
revisions; the body shows the file content at the current rev, with
each line marked ``+`` (new this rev), ``-`` (would-be-removed since
prev rev), or plain. The header shows rev / CL / user / date /
description-first-line.

This is the TUI analogue of p4v's slider-based time-lapse view —
keyboard-driven instead of mouse-scrubbed.

Bindings
--------
  ,  / k  — previous revision (older)
  .  / j  — next revision (newer)
  Home   — first (oldest)
  End    — latest (newest)
  Esc / Backspace / q — close

``,``/``.`` are used in place of ``←``/``→`` because the body widget
is a ``RichLog`` with horizontal-scrollable content; left/right
arrows are reserved for that scrolling so long lines stay
inspectable.
"""
from __future__ import annotations

import difflib
from datetime import datetime

from rich.text import Text

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static


class TimelapseModal(ModalScreen[None]):
    DEFAULT_CSS = """
    TimelapseModal { align: center middle; }
    TimelapseModal > #dialog {
        width: 95%;
        height: 90%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    TimelapseModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    TimelapseModal #header {
        padding: 0 1;
        color: $text;
    }
    TimelapseModal #status {
        padding: 0 1;
        color: $text-muted;
    }
    TimelapseModal #body {
        height: 1fr;
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", priority=True),
        Binding("backspace", "cancel", "Close", priority=True),
        Binding("q", "cancel", "Close", priority=True),
        Binding("ㅂ", "cancel", "Close", priority=True),
        # ``,`` and ``.`` step the revision timeline. Left/Right arrows
        # are intentionally left to RichLog's horizontal scroll — long
        # source lines need to be scannable. ``,`` and ``.`` are the
        # same characters in 2-beolsik Hangul mode, so no IME alias
        # needed. ``j``/``k`` are kept as vim-style hidden aliases
        # with their 2-beolsik jamo equivalents (``ㅓ`` / ``ㅏ``).
        # Textual's BINDINGS string is comma-split, so a literal ","
        # raises InvalidBinding. Use the Unicode-derived key names
        # ("comma", "full_stop") — they still match the actual ,/.
        # keystrokes via the same _character_to_key normalization.
        Binding("comma", "prev_rev", "Older"),
        Binding("full_stop", "next_rev", "Newer"),
        Binding("k", "prev_rev", show=False),
        Binding("j", "next_rev", show=False),
        Binding("ㅏ", "prev_rev", show=False),
        Binding("ㅓ", "next_rev", show=False),
        Binding("home", "first_rev", "Oldest"),
        Binding("end", "last_rev", "Newest"),
    ]

    def __init__(self, depot_path: str, p4_service) -> None:
        super().__init__()
        self._depot_path = depot_path
        self._p4 = p4_service
        # `_revs` is filelog rows, sorted oldest → newest after fetch.
        # Indexes match `_idx`.
        self._revs: list[dict] = []
        self._idx = 0
        self._content_cache: dict[int, list[str]] = {}

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(
                f" Time-lapse · {self._depot_path} ", id="title",
            )
            yield Static("  Loading revisions…", id="header")
            yield Static("", id="status")
            yield RichLog(highlight=False, markup=False,
                          wrap=False, id="body")

    def on_mount(self) -> None:
        self._fetch_revs()

    @work(thread=True, group="timelapse_revs", exclusive=True)
    def _fetch_revs(self) -> None:
        try:
            rows = self._p4.filelog(self._depot_path, max_revs=200)
        except Exception:  # noqa: BLE001
            rows = []
        # filelog returns newest-first; flip so left/right arrow
        # behaves like a timeline.
        rows = list(reversed(rows))
        self.app.call_from_thread(self._init_with_revs, rows)

    def _init_with_revs(self, rows: list[dict]) -> None:
        self._revs = rows
        if not rows:
            try:
                self.query_one("#header", Static).update(
                    "  No revision history for this file.",
                )
            except Exception:  # noqa: BLE001
                pass
            return
        # Start at the latest revision — that's what the user sees in
        # the tree, and it's the most useful default starting point.
        self._idx = len(rows) - 1
        self._render_current()

    # --- navigation -----------------------------------------------------

    def action_prev_rev(self) -> None:
        if self._idx > 0:
            self._idx -= 1
            self._render_current()

    def action_next_rev(self) -> None:
        if self._idx < len(self._revs) - 1:
            self._idx += 1
            self._render_current()

    def action_first_rev(self) -> None:
        if self._revs:
            self._idx = 0
            self._render_current()

    def action_last_rev(self) -> None:
        if self._revs:
            self._idx = len(self._revs) - 1
            self._render_current()

    # --- rendering ------------------------------------------------------

    def _render_current(self) -> None:
        if not self._revs:
            return
        rev = self._revs[self._idx]
        rev_n = self._rev_number(rev)
        user = str(rev.get("user", "") or "")
        cl = str(rev.get("change", "") or "")
        ts = rev.get("time", "")
        try:
            date = datetime.fromtimestamp(int(ts)).strftime(
                "%Y-%m-%d %H:%M",
            ) if ts else ""
        except (TypeError, ValueError):
            date = ""
        desc = (rev.get("desc") or "").splitlines()
        first = desc[0] if desc else ""
        try:
            header = self.query_one("#header", Static)
            status = self.query_one("#status", Static)
        except Exception:  # noqa: BLE001
            return
        header.update(
            f"  rev #{rev_n}  CL={cl}  {user}  {date}\n  {first}"
        )
        status.update(
            f"  {self._idx + 1} / {len(self._revs)}  "
            "(, older · . newer · Home/End edges · ←/→ scroll)"
        )
        # Fetch + diff in a worker so big files don't block the UI on
        # every arrow press.
        self._fetch_and_render_body(self._idx)

    @work(thread=True, group="timelapse_body", exclusive=True)
    def _fetch_and_render_body(self, target_idx: int) -> None:
        if target_idx < 0 or target_idx >= len(self._revs):
            return
        try:
            cur = self._content_for_idx(target_idx)
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(
                self._render_body_error, target_idx, str(e),
            )
            return
        prev_lines: list[str] = []
        if target_idx > 0:
            try:
                prev_lines = self._content_for_idx(target_idx - 1)
            except Exception:  # noqa: BLE001
                prev_lines = []
        self.app.call_from_thread(
            self._render_body, target_idx, prev_lines, cur,
        )

    def _content_for_idx(self, idx: int) -> list[str]:
        if idx in self._content_cache:
            return self._content_cache[idx]
        rev = self._revs[idx]
        rev_n = self._rev_number(rev)
        path_at = f"{self._depot_path}#{rev_n}"
        try:
            result = self._p4.run("print", "-q", path_at)
        except Exception:  # noqa: BLE001
            self._content_cache[idx] = []
            return []
        text_parts: list[str] = []
        for item in result:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, (bytes, bytearray)):
                try:
                    text_parts.append(bytes(item).decode("utf-8"))
                except UnicodeDecodeError:
                    text_parts.append(
                        bytes(item).decode("utf-8", errors="replace"),
                    )
        joined = "".join(text_parts)
        sample = joined[:8192]
        if sample and sample.count("\x00") > max(1, len(sample) // 100):
            self._content_cache[idx] = [
                f"[Binary file — {len(joined)} bytes]",
            ]
            return self._content_cache[idx]
        lines = joined.splitlines()
        self._content_cache[idx] = lines
        return lines

    def _render_body(
        self, target_idx: int,
        prev_lines: list[str],
        cur_lines: list[str],
    ) -> None:
        if target_idx != self._idx:
            # User stepped to a different rev mid-fetch.
            return
        try:
            body = self.query_one("#body", RichLog)
        except Exception:  # noqa: BLE001
            return
        body.clear()
        if not cur_lines:
            body.write(Text("  (empty file at this revision)", style="dim"))
            return
        # Diff against previous rev to color what changed.
        if prev_lines:
            sm = difflib.SequenceMatcher(
                a=prev_lines, b=cur_lines, autojunk=False,
            )
            # Determine which b-indices are "equal" so the rest are new.
            new_indices: set[int] = set(range(len(cur_lines)))
            for op, _i1, _i2, j1, j2 in sm.get_opcodes():
                if op == "equal":
                    for j in range(j1, j2):
                        new_indices.discard(j)
        else:
            # First revision in the timeline — every line is "new".
            new_indices = set(range(len(cur_lines)))
        for i, line in enumerate(cur_lines):
            if i in new_indices:
                body.write(Text("+ ", style="green").append(line))
            else:
                body.write(Text("  ").append(line))

    def _render_body_error(
        self, target_idx: int, message: str,
    ) -> None:
        if target_idx != self._idx:
            return
        try:
            body = self.query_one("#body", RichLog)
        except Exception:  # noqa: BLE001
            return
        body.clear()
        body.write(Text(f"Failed to load revision: {message}",
                        style="red"))

    @staticmethod
    def _rev_number(rev: dict) -> int:
        for key in ("rev", "haveRev", "headRev"):
            v = rev.get(key)
            if v is None:
                continue
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
        return 0

    # --- close ----------------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "backspace", "q", "ㅂ"):
            event.stop()
            self.dismiss(None)
