"""Modal that asks for a changelist description and returns it.

Result type is ``str | None`` — the entered description, or ``None`` if
cancelled. Empty / whitespace-only input is treated as cancellation so we
never create a CL with an empty description.
"""
from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class NewChangelistModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    NewChangelistModal { align: center middle; }
    NewChangelistModal > #dialog {
        width: 80;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1 2;
    }
    NewChangelistModal #title {
        text-style: bold;
        padding-bottom: 1;
    }
    NewChangelistModal #desc_input {
        margin-bottom: 1;
    }
    NewChangelistModal #buttons {
        height: 3;
        align: right middle;
    }
    NewChangelistModal Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static("New Pending Changelist", id="title")
            yield Static("Description:")
            yield Input(
                placeholder="Enter description…",
                id="desc_input",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Create", id="create", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#desc_input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            text = self.query_one("#desc_input", Input).value.strip()
            self.dismiss(text or None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Pressing Enter inside the Input is the same as clicking Create.
        text = event.value.strip()
        self.dismiss(text or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
