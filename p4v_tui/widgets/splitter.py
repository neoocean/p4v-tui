"""Draggable splitter widgets.

A splitter is a 1-cell-thick widget that sits between two panes and
emits a drag-delta message when the user mouses down on it and drags.
The parent decides which dimension to adjust — splitters know
nothing about the panes around them.

Two variants
------------
* :class:`VerticalSplitter` — 1 cell wide. Drag horizontally to
  resize the pane to its left (or right, caller's choice).
* :class:`HorizontalSplitter` — 1 cell tall. Drag vertically to
  resize the pane above it (or below it).

Both emit the same :class:`Splitter.Dragged` message; the
``axis`` attribute distinguishes them so a single handler in the
App can route to the right reactive.
"""
from __future__ import annotations

from rich.text import Text

from textual import events
from textual.message import Message
from textual.widget import Widget


class SplitterDragged(Message):
    """Emitted on every MouseMove during an active splitter drag.

    Module-level (not nested) so Textual's
    ``on_splitter_dragged`` handler name resolves cleanly — a
    nested ``_SplitterBase.Dragged`` would name itself
    ``on__splitter_base_dragged``, which is awkward and easy to
    misspell.

    ``delta`` is the *incremental* movement since the previous
    event — the App applies it on top of the current pane size.
    ``axis`` is ``"x"`` or ``"y"`` so a single message handler
    can route to the right reactive.
    """

    def __init__(self, splitter, delta: int, axis: str) -> None:
        self.splitter = splitter
        self.delta = delta
        self.axis = axis
        super().__init__()


class _SplitterBase(Widget):
    """Common drag-capture machinery for vertical / horizontal
    splitters. Subclasses set the axis hint and the CSS dimensions."""

    can_focus = False

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._dragging = False

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._dragging = True
        self.capture_mouse()
        event.stop()
        event.prevent_default()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()
            event.stop()
            event.prevent_default()


class VerticalSplitter(_SplitterBase):
    """1 cell wide. Drag → ``Dragged(delta=Δx, axis='x')``.

    Renders a single small right-pointing triangle (▸, U+25B8) at the
    vertical center of the bar. The "small" variant is used over the
    full-size ▶ (U+25B6) because the small one is classified Narrow
    in East Asian Width, so it occupies exactly one cell in Korean /
    Japanese / Chinese terminal fonts. The full-size triangle is
    Ambiguous Width and a CJK locale renders it 2 cells wide, which
    would overflow this 1-column widget.
    """

    DEFAULT_CSS = """
    VerticalSplitter {
        width: 1;
        background: $primary 30%;
        color: $text;
    }
    VerticalSplitter:hover {
        background: $primary 70%;
    }
    """

    def render(self) -> Text:
        h = self.size.height
        if h <= 0:
            return Text("")
        mid = h // 2
        lines = [" "] * h
        lines[mid] = "▸"
        return Text("\n".join(lines))

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        # Textual delivers delta_x / delta_y on captured MouseMove
        # events as the movement since the previous event — exactly
        # the incremental value the parent wants to apply.
        if event.delta_x:
            self.post_message(
                SplitterDragged(self, event.delta_x, "x"),
            )


class HorizontalSplitter(_SplitterBase):
    """1 cell tall. Drag → ``Dragged(delta=Δy, axis='y')``.

    Renders a single small down-pointing triangle (▾, U+25BE) at the
    horizontal center. Same Narrow-vs-Ambiguous-width reasoning as
    :class:`VerticalSplitter` — the small variant guarantees a
    single cell in any locale.
    """

    DEFAULT_CSS = """
    HorizontalSplitter {
        height: 1;
        background: $primary 30%;
        color: $text;
    }
    HorizontalSplitter:hover {
        background: $primary 70%;
    }
    """

    def render(self) -> Text:
        w = self.size.width
        if w <= 0:
            return Text("")
        mid = w // 2
        return Text(" " * mid + "▾" + " " * (w - mid - 1))

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        if event.delta_y:
            self.post_message(
                SplitterDragged(self, event.delta_y, "y"),
            )
