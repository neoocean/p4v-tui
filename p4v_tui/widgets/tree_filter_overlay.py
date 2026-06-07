"""Floating filter input for the Workspace / Depot tree.

A single instance lives on the App's main screen (anchored at the
bottom of the left pane via CSS). Pressing ``/`` while a tree is
focused opens it; the App routes typed text back to the tree's
``apply_filter`` so loaded nodes are hidden / shown live.

Esc / Enter close the overlay and (Esc) restore the unfiltered view.
"""
from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Container
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label


class TreeFilterOverlay(Widget):
    """Bottom-of-pane Input + label, hidden by default."""

    DEFAULT_CSS = """
    TreeFilterOverlay {
        layer: overlay;
        dock: bottom;
        height: 3;
        background: $boost;
        padding: 0 1;
        display: none;
    }
    TreeFilterOverlay.visible { display: block; }
    TreeFilterOverlay #row {
        layout: horizontal;
        height: 1;
    }
    TreeFilterOverlay #label {
        width: auto;
        color: $text-muted;
    }
    TreeFilterOverlay #q { width: 1fr; }
    """

    class FilterChanged(Message):
        def __init__(self, query: str) -> None:
            self.query = query
            super().__init__()

    class FilterClosed(Message):
        def __init__(self, restored: bool) -> None:
            self.restored = restored
            super().__init__()

    def compose(self) -> ComposeResult:
        with Container(id="row"):
            yield Label("/", id="label")
            yield Input(placeholder="filter (esc=clear · enter=keep)",
                        id="q")

    def show_for(self) -> None:
        """Reveal the overlay and focus the input."""
        self.add_class("visible")
        try:
            inp = self.query_one("#q", Input)
            inp.value = ""
            inp.focus()
        except Exception:  # noqa: BLE001
            pass

    def hide(self) -> None:
        self.remove_class("visible")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "q":
            return
        self.post_message(self.FilterChanged(event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter — keep the filter applied and close.
        self.hide()
        self.post_message(self.FilterClosed(restored=False))

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            try:
                self.query_one("#q", Input).value = ""
            except Exception:  # noqa: BLE001
                pass
            self.hide()
            self.post_message(self.FilterClosed(restored=True))
