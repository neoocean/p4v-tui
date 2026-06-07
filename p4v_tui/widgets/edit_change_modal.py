"""Modal for editing a changelist's description.

Used for both Pending CLs (any author) and Submitted CLs (admin-only,
``p4 change -f``). Returns the edited description string, or ``None`` if
cancelled. Empty / whitespace-only result is treated as cancellation —
P4 won't accept an empty description anyway.
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea


class EditChangelistDescModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    EditChangelistDescModal { align: center middle; }
    EditChangelistDescModal > #dialog {
        width: 90%;
        height: 80%;
        border: thick $primary;
        background: $panel;
    }
    EditChangelistDescModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    EditChangelistDescModal #desc_area {
        height: 1fr;
        margin: 1;
    }
    EditChangelistDescModal #buttons {
        height: 3;
        align: right middle;
        padding-right: 1;
        padding-bottom: 1;
    }
    EditChangelistDescModal Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        change: str,
        initial: str,
        *,
        force: bool = False,
    ) -> None:
        super().__init__()
        self._change = str(change)
        self._initial = initial
        self._force = force

    def compose(self) -> ComposeResult:
        scope = "submitted (admin -f)" if self._force else "pending"
        with Container(id="dialog"):
            yield Static(
                f" Edit description · {scope} CL {self._change} ",
                id="title",
            )
            yield TextArea(self._initial, id="desc_area", soft_wrap=True)
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#desc_area", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            text = self.query_one("#desc_area", TextArea).text.strip()
            self.dismiss(text or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        # Esc must close even when TextArea is focused — TextArea's own
        # bindings can otherwise consume it.
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
