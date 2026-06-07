"""Generic Yes/No modal that returns a bool via screen result."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmModal(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal > #dialog {
        width: 64;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1 2;
    }
    ConfirmModal #dialog_title {
        text-style: bold;
        padding-bottom: 1;
    }
    ConfirmModal #dialog_message {
        padding-bottom: 1;
    }
    ConfirmModal #dialog_buttons {
        height: 3;
        align: right middle;
    }
    ConfirmModal Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "OK"),
    ]

    def __init__(self, title: str, message: str, ok_label: str = "OK",
                 ok_variant: str = "primary") -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._ok_label = ok_label
        self._ok_variant = ok_variant

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(self._title, id="dialog_title")
            yield Static(self._message, id="dialog_message")
            with Horizontal(id="dialog_buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self._ok_label, id="ok", variant=self._ok_variant)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
