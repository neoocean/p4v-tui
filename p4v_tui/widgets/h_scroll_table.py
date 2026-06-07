"""DataTable that honors horizontal mouse-wheel events.

Textual's stock DataTable handles vertical wheel (MouseScrollUp/Down) but
does nothing for horizontal wheel events (MouseScrollLeft/Right) sent by
modern terminals (Windows Terminal, iTerm2, etc.). This subclass routes
those events to the underlying scroll-left / scroll-right methods so the
user can pan a wide table sideways with a horizontal trackpad gesture.
"""
from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.widgets import DataTable


class HScrollDataTable(DataTable):
    # Keyboard shortcuts mirror the horizontal-wheel handlers below so a
    # user without a horizontal trackpad gesture can still pan the table
    # sideways. We avoid bare Left/Right because those move the cell
    # cursor in the underlying DataTable.
    BINDINGS = [
        Binding("shift+left",  "h_scroll_left",  "← (h-scroll)", show=False),
        Binding("shift+right", "h_scroll_right", "→ (h-scroll)", show=False),
    ]

    def on_mouse_scroll_left(self, event: events.MouseScrollLeft) -> None:
        self.scroll_left(animate=False)
        event.stop()

    def on_mouse_scroll_right(self, event: events.MouseScrollRight) -> None:
        self.scroll_right(animate=False)
        event.stop()

    def action_h_scroll_left(self) -> None:
        self.scroll_left(animate=False)

    def action_h_scroll_right(self) -> None:
        self.scroll_right(animate=False)
