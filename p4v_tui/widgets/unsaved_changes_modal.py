"""Three-button "you have unsaved changes" confirmation.

Used by editable popups (currently :class:`PendingDetailModal`) to
intercept a Cancel that would discard work the user typed. The user
gets three explicit choices:

  * **Save**             — dismiss with ``"save"`` so the caller can
                           persist the edits before closing.
  * **Discard**          — dismiss with ``"discard"`` so the caller
                           closes the underlying modal as a normal
                           cancel (edits thrown away).
  * **Continue editing** — dismiss with ``None`` so the caller stays
                           in the underlying modal.
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class UnsavedChangesModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    UnsavedChangesModal { align: center middle; }
    UnsavedChangesModal > #dialog {
        width: 70;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1 2;
    }
    UnsavedChangesModal #title {
        text-style: bold;
        padding-bottom: 1;
    }
    UnsavedChangesModal #message {
        padding-bottom: 1;
    }
    UnsavedChangesModal #buttons {
        height: 3;
        align: right middle;
    }
    UnsavedChangesModal Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "continue_editing", "Continue editing",
                priority=True),
    ]

    def __init__(
        self,
        message: str = "You have unsaved changes. Save them first?",
    ) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static("Unsaved changes", id="title")
            yield Static(self._message, id="message")
            with Horizontal(id="buttons"):
                yield Button("Continue editing", id="continue")
                yield Button("Discard", id="discard",
                             variant="error")
                yield Button("Save", id="save",
                             variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "save":
            self.dismiss("save")
        elif bid == "discard":
            self.dismiss("discard")
        else:
            self.dismiss(None)

    def action_continue_editing(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
