"""Side-by-side diff viewer.

Two modes share the same widget:

* **Submitted CL** — for each file in the CL, fetch ``<file>#rev-1``
  vs ``<file>#rev`` and render aligned columns. Built via
  :meth:`SideBySideDiffModal.for_cl`.

* **Arbitrary pair(s)** — caller supplies any list of
  ``(left_spec, right_spec, label)`` triples; the widget fetches
  ``p4 print -q`` for each side. Built via :meth:`for_pairs`.

Both modes share the same alignment algorithm
(``difflib.SequenceMatcher.get_opcodes()``) and the same row-by-row
rendering — the difference is just where the specs come from.

Color coding
------------
  red    — line removed (left only)
  green  — line added   (right only)
  yellow — line replaced (both columns, but text differs)
  plain  — unchanged
"""
from __future__ import annotations

import difflib

from rich.text import Text

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Label, RichLog, Select, Static

from ..utils import is_creation_action


_PALETTE = {
    "del":   "red",
    "ins":   "green",
    "repl":  "yellow",
    "plain": "",
    "blank": "dim",
}


class SideBySideDiffModal(ModalScreen[None]):
    """Show two columns rendered from any (left_spec, right_spec) pair."""

    DEFAULT_CSS = """
    SideBySideDiffModal { align: center middle; }
    SideBySideDiffModal > #dialog {
        width: 98%;
        height: 95%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    SideBySideDiffModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    SideBySideDiffModal #file_picker {
        margin: 1 0 0 0;
    }
    SideBySideDiffModal Label.col_label {
        background: $boost;
        text-style: bold;
        padding: 0 1;
    }
    SideBySideDiffModal #left_col, SideBySideDiffModal #right_col {
        height: 1fr;
        width: 1fr;
        background: $surface;
    }
    SideBySideDiffModal #cols_row {
        height: 1fr;
    }
    SideBySideDiffModal #status {
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", priority=True),
        Binding("backspace", "cancel", "Close", priority=True),
        Binding("q", "cancel", "Close", priority=True),
        Binding("ㅂ", "cancel", "Close", priority=True),
    ]

    def __init__(
        self,
        title: str,
        pairs: list[tuple[str, str, str]],
        p4_service,
        *,
        left_col_label: str = "Left",
        right_col_label: str = "Right",
    ) -> None:
        """``pairs`` is a list of (left_spec, right_spec, picker_label).

        ``left_spec`` / ``right_spec`` are anything ``p4 print -q``
        accepts: ``//depot/path#rev``, ``//depot/path@CL``, etc. An
        empty string on either side means "render the other side as
        all-new" — use that for "added" / "deleted" pairs.
        """
        super().__init__()
        self._title = title
        self._pairs = pairs
        self._p4 = p4_service
        self._left_col_label = left_col_label
        self._right_col_label = right_col_label
        self._current_key: str | None = None

    # --- factory helpers ------------------------------------------------

    @classmethod
    def for_cl(
        cls,
        change: str,
        files: list[tuple[str, int, str]],
        p4_service,
    ) -> "SideBySideDiffModal":
        """Build pairs from a Submitted CL's file list.

        ``files`` is ``[(depot_path, current_rev, action), …]``.
        For each file the left side is ``depot_path#rev-1`` (or
        empty if it's a creation — add/branch/import/move/add — with no
        predecessor) and the right side is ``depot_path#rev``. The picker
        label is ``"<action>  <path>#<rev>"``.
        """
        pairs: list[tuple[str, str, str]] = []
        for path, rev, action in files:
            if rev <= 1 or is_creation_action(action):
                left_spec = ""
            else:
                left_spec = f"{path}#{rev - 1}"
            right_spec = f"{path}#{rev}"
            label = f"{action or '?'}  {path}#{rev}"
            pairs.append((left_spec, right_spec, label))
        return cls(
            title=f"Side-by-side diff · CL {change}",
            pairs=pairs,
            p4_service=p4_service,
            left_col_label="Previous revision (rev-1)",
            right_col_label=f"This revision (CL {change})",
        )

    @classmethod
    def for_pairs(
        cls,
        title: str,
        pairs: list[tuple[str, str, str]],
        p4_service,
        *,
        left_col_label: str = "Left",
        right_col_label: str = "Right",
    ) -> "SideBySideDiffModal":
        """Generic constructor — see ``__init__`` for the shape of
        ``pairs``. Used by Arbitrary Diff (file vs file, two folders,
        two CLs)."""
        return cls(
            title=title,
            pairs=pairs,
            p4_service=p4_service,
            left_col_label=left_col_label,
            right_col_label=right_col_label,
        )

    # --- compose --------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(f" {self._title} ", id="title")
            options = [
                (label, label) for _l, _r, label in self._pairs
            ]
            if not options:
                yield Static(
                    "  (no pairs to diff)", id="status",
                )
            else:
                yield Select(
                    options, id="file_picker",
                    allow_blank=False,
                    value=options[0][1],
                )
            with Horizontal(id="cols_row"):
                yield Container(
                    Label(self._left_col_label, classes="col_label"),
                    RichLog(highlight=False, markup=False,
                            wrap=False, id="left_col"),
                )
                yield Container(
                    Label(self._right_col_label, classes="col_label"),
                    RichLog(highlight=False, markup=False,
                            wrap=False, id="right_col"),
                )
            yield Static("  Loading…", id="status")

    def on_mount(self) -> None:
        if self._pairs:
            self._render_for(self._pairs[0][2])

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "file_picker":
            return
        label = event.value
        if isinstance(label, str) and label != self._current_key:
            self._render_for(label)

    def _render_for(self, key: str) -> None:
        self._current_key = key
        entry = next(
            (p for p in self._pairs if p[2] == key), None,
        )
        if entry is None:
            return
        left_spec, right_spec, _label = entry
        self._fetch_and_render(key, left_spec, right_spec)

    @work(thread=True, group="sxs_diff_fetch", exclusive=True)
    def _fetch_and_render(
        self, key: str, left_spec: str, right_spec: str,
    ) -> None:
        left_lines = self._print_lines(left_spec) if left_spec else []
        right_lines = self._print_lines(right_spec) if right_spec else []
        self.app.call_from_thread(
            self._render_columns, key, left_spec, right_spec,
            left_lines, right_lines,
        )

    def _print_lines(self, file_at_rev: str) -> list[str]:
        try:
            result = self._p4.run("print", "-q", file_at_rev)
        except Exception:  # noqa: BLE001
            return [f"[Could not read {file_at_rev}]"]
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
        # Heuristic: ≥1% NUL in the first 8 KiB → binary; show a stub.
        joined = "".join(text_parts)
        sample = joined[:8192]
        if sample and sample.count("\x00") > max(1, len(sample) // 100):
            return [
                f"[Binary file — {len(joined)} bytes]",
                "Cannot display in text diff.",
            ]
        return joined.splitlines()

    def _render_columns(
        self,
        key: str,
        left_spec: str,
        right_spec: str,
        old_lines: list[str],
        new_lines: list[str],
    ) -> None:
        if key != self._current_key:
            # User switched mid-fetch; drop the stale render.
            return
        try:
            left = self.query_one("#left_col", RichLog)
            right = self.query_one("#right_col", RichLog)
            status = self.query_one("#status", Static)
        except Exception:  # noqa: BLE001
            return
        left.clear()
        right.clear()
        diff_rows = _build_diff_rows(old_lines, new_lines)
        for left_kind, left_text, right_kind, right_text in diff_rows:
            left.write(_styled_line(left_text, left_kind))
            right.write(_styled_line(right_text, right_kind))
        n_del = sum(1 for r in diff_rows if r[0] == "del")
        n_ins = sum(1 for r in diff_rows if r[2] == "ins")
        n_repl = sum(1 for r in diff_rows
                     if r[0] == "repl" or r[2] == "repl")
        if not left_spec and right_spec:
            note = "  (new on right — no left side)"
        elif left_spec and not right_spec:
            note = "  (only on left — no right side)"
        else:
            note = ""
        status.update(
            f"  {key}: -{n_del} +{n_ins} ~{n_repl}{note}"
        )

    # --- close ----------------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "backspace", "q", "ㅂ"):
            event.stop()
            self.dismiss(None)


def _build_diff_rows(
    old_lines: list[str], new_lines: list[str],
) -> list[tuple[str, str, str, str]]:
    """Walk the SequenceMatcher opcodes and produce a list of
    aligned (left_kind, left_text, right_kind, right_text) rows.

    Replace-blocks are paired up to ``min(len_left, len_right)`` and
    the leftover side is filled with blanks on the other column.
    """
    if not old_lines and not new_lines:
        return []
    if not old_lines:
        return [
            ("blank", "", "ins", line) for line in new_lines
        ]
    if not new_lines:
        return [
            ("del", line, "blank", "") for line in old_lines
        ]
    matcher = difflib.SequenceMatcher(
        a=old_lines, b=new_lines, autojunk=False,
    )
    rows: list[tuple[str, str, str, str]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                rows.append((
                    "plain", old_lines[i1 + k],
                    "plain", new_lines[j1 + k],
                ))
        elif op == "replace":
            left_block = old_lines[i1:i2]
            right_block = new_lines[j1:j2]
            paired = min(len(left_block), len(right_block))
            for k in range(paired):
                rows.append((
                    "repl", left_block[k],
                    "repl", right_block[k],
                ))
            for k in range(paired, len(left_block)):
                rows.append((
                    "del", left_block[k],
                    "blank", "",
                ))
            for k in range(paired, len(right_block)):
                rows.append((
                    "blank", "",
                    "ins", right_block[k],
                ))
        elif op == "delete":
            for k in range(i2 - i1):
                rows.append((
                    "del", old_lines[i1 + k],
                    "blank", "",
                ))
        elif op == "insert":
            for k in range(j2 - j1):
                rows.append((
                    "blank", "",
                    "ins", new_lines[j1 + k],
                ))
    return rows


def _styled_line(text: str, kind: str) -> Text:
    style = _PALETTE.get(kind, "")
    if kind == "blank":
        return Text("·", style="dim")
    return Text(text, style=style or "")
