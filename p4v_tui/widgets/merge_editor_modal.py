"""Interactive 3-way merge editor (item 1).

Walks the conflict hunks of a ``p4 resolve -am``-marked file and lets the
user pick a resolution per hunk — Yours / Theirs / Base / Both — then
returns the reconstructed file text. The merge maths live in
:mod:`p4v_tui.merge3` (pure + tested); this modal is just the chooser.

Returns the merged text (str) on confirm, or None on cancel.
"""
from __future__ import annotations

from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..merge3 import BASE, BOTH, THEIRS, YOURS, conflicts, reconstruct

_CHOICE_NEXT = {YOURS: THEIRS, THEIRS: BASE, BASE: BOTH, BOTH: YOURS}


class MergeEditorModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    MergeEditorModal { align: center middle; }
    MergeEditorModal > #dialog {
        width: 95%;
        height: 90%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    MergeEditorModal #title { text-style: bold; background: $boost; padding: 0 1; }
    MergeEditorModal #cols { height: 1fr; margin-top: 1; }
    MergeEditorModal #hunks { width: 40%; height: 1fr; }
    MergeEditorModal #detail { width: 60%; height: 1fr; padding: 0 1; }
    MergeEditorModal #hint { color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("y", "set_choice('yours')", "Yours"),
        Binding("t", "set_choice('theirs')", "Theirs"),
        Binding("b", "set_choice('base')", "Base"),
        Binding("o", "set_choice('both')", "Both"),
        Binding("enter", "confirm", "Apply merge", priority=True),
        Binding("ctrl+s", "confirm", "Apply merge", priority=True),
    ]

    def __init__(self, path: str, segments: list) -> None:
        super().__init__()
        self._path = path
        self._segments = segments
        self._conflicts = conflicts(segments)
        # Default every hunk to "yours" — the least-surprising start, and
        # matches reconstruct()'s own default.
        self._choices: list[str] = [YOURS] * len(self._conflicts)
        self._cur = 0

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(
                f" 3-way merge · {self._path} "
                f"({len(self._conflicts)} conflict(s)) ",
                id="title",
            )
            with Horizontal(id="cols"):
                yield OptionList(*self._hunk_options(), id="hunks")
                yield Static(id="detail")
            yield Static(
                "↑↓ hunk · y Yours · t Theirs · b Base · o Both · "
                "Enter apply · Esc cancel",
                id="hint",
            )

    def _hunk_options(self) -> list[Option]:
        return [
            Option(self._hunk_label(i), id=str(i))
            for i in range(len(self._conflicts))
        ]

    def _hunk_label(self, i: int) -> str:
        return f"[{self._choices[i]:>6}] Hunk {i + 1}"

    def on_mount(self) -> None:
        try:
            self.query_one("#hunks", OptionList).focus()
        except Exception:  # noqa: BLE001
            pass
        self._render_detail()

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted,
    ) -> None:
        if event.option_list.id != "hunks":
            return
        self._cur = event.option_index or 0
        self._render_detail()

    def _render_detail(self) -> None:
        if not self._conflicts:
            return
        c = self._conflicts[self._cur]
        choice = self._choices[self._cur]
        body = Text()
        for label, lines, key in (
            ("YOURS", c.yours, YOURS),
            ("THEIRS", c.theirs, THEIRS),
            ("BASE", c.base, BASE),
        ):
            mark = "▶ " if choice == key or (choice == BOTH and key in (YOURS, THEIRS)) else "  "
            style = "bold green" if mark == "▶ " else "dim"
            body.append(f"{mark}{label}\n", style=style)
            for ln in (lines or ["(empty)"]):
                body.append(f"    {ln}\n")
            body.append("\n")
        try:
            self.query_one("#detail", Static).update(body)
        except Exception:  # noqa: BLE001
            pass

    def action_set_choice(self, choice: str) -> None:
        if not self._conflicts:
            return
        self._choices[self._cur] = choice
        try:
            lst = self.query_one("#hunks", OptionList)
            lst.replace_option_prompt_at_index(self._cur, self._hunk_label(self._cur))
        except Exception:  # noqa: BLE001
            pass
        self._render_detail()

    def action_confirm(self) -> None:
        self.dismiss(reconstruct(self._segments, self._choices))

    def action_cancel(self) -> None:
        self.dismiss(None)
