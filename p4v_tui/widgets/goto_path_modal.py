"""Go-to-path popup (item 9).

A single-input modal: paste a Perforce depot path (``//...``) or a local
filesystem path and the app walks the active tree to it (expanding +
highlighting). Enter confirms, Escape cancels. Returns the raw string
or None — classification + navigation happen in the app.
"""
from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class GotoPathModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    GotoPathModal { align: center middle; }
    GotoPathModal > #dialog {
        width: 90%;
        max-width: 120;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1 2;
    }
    GotoPathModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
        margin-bottom: 1;
    }
    GotoPathModal #hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Go to path ", id="title")
            yield Input(
                value=self._initial,
                placeholder=(
                    "//depot/path/file · /local/abs/path · or a name fragment"
                ),
                id="path",
            )
            yield Static(
                "Depot (//…), local path, or fuzzy fragment · "
                "Enter: go · Esc: cancel",
                id="hint",
            )

    def on_mount(self) -> None:
        try:
            inp = self.query_one("#path", Input)
            inp.focus()
            inp.cursor_position = len(inp.value)
        except Exception:  # noqa: BLE001
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            self.dismiss(None)
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)
