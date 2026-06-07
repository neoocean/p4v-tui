"""Tiny "Exiting…" popup shown while the app finishes shutting down.

Pressing Q (or Ctrl+Q) starts a teardown sequence that can take a
second or two — JobRunner cancellation, P4 disconnect, Textual cleanup.
Without visible feedback the user assumes the keypress was lost and
hits Q repeatedly. This modal pops up *immediately* on the first quit
keypress so they see the input was received.

The modal is informational only. It absorbs any further keypresses
while it's on screen so a second Q doesn't bubble back into the app
and trigger another quit attempt.
"""
from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static


class QuittingModal(ModalScreen[None]):
    DEFAULT_CSS = """
    QuittingModal { align: center middle; }
    QuittingModal > #quit_dialog {
        width: 40;
        height: 5;
        border: thick $primary;
        background: $panel;
        content-align: center middle;
        padding: 1 2;
    }
    QuittingModal #quit_message {
        text-style: bold;
        color: $text;
        content-align: center middle;
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="quit_dialog"):
            yield Static("Exiting…", id="quit_message")

    def on_key(self, event: events.Key) -> None:
        # Swallow every key so a frantic second Q doesn't propagate
        # back to the app and queue another quit attempt.
        event.stop()
        event.prevent_default()
