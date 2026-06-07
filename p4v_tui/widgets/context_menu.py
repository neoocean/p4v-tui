"""Generic context-menu modal.

Renders a list of :class:`ContextMenuItem` rows; the user picks one with
arrows + Enter, or dismisses with Escape. The screen result is the picked
item's ``action`` id, or ``None`` when cancelled.

This is a TUI surrogate for p4v's mouse-driven context menus. We open it on
a keyboard binding (``m`` or ``Shift+F10``) instead of right-click — though
clicking still works for users on a real terminal that forwards mouse events.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


@dataclass
class ContextMenuItem:
    label: str
    action: str
    shortcut: str = ""
    enabled: bool = True


# Sentinel to insert a horizontal separator inside the items list.
SEPARATOR = ContextMenuItem(label="__sep__", action="__sep__")


class ContextMenuModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    ContextMenuModal {
        align: center middle;
    }
    ContextMenuModal > #ctxmenu {
        width: auto;
        max-width: 80;
        height: auto;
        max-height: 32;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    ContextMenuModal #ctxmenu_title {
        text-style: bold;
        padding: 0 1;
        background: $boost;
        color: $text;
    }
    ContextMenuModal OptionList {
        background: transparent;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        items: list[ContextMenuItem],
        title: str = "",
    ) -> None:
        super().__init__()
        self._items = items
        self._title = title

    def compose(self) -> ComposeResult:
        with Container(id="ctxmenu"):
            if self._title:
                yield Static(f" {self._title} ", id="ctxmenu_title")
            options: list = []
            for it in self._items:
                if it is SEPARATOR or it.label == "__sep__":
                    # This Textual build's OptionList lacks a Separator type;
                    # render a disabled dashes-only row that visually divides.
                    options.append(Option("─" * 30, disabled=True))
                else:
                    options.append(self._build_option(it))
            yield OptionList(*options, id="ctxmenu_list")

    def _build_option(self, it: ContextMenuItem) -> Option:
        return Option(
            self._format_label(it),
            id=it.action,
            disabled=not it.enabled,
        )

    @staticmethod
    def _format_label(it: ContextMenuItem) -> str:
        if it.shortcut:
            return f"{it.label}  [dim]{it.shortcut}[/dim]"
        return it.label

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_click(self, event) -> None:
        """Tap / click outside the menu dismisses it.

        Matches the platform-standard right-click-menu UX: clicking
        somewhere other than a menu item closes the menu rather than
        keeping it parked over the screen. ``id="ctxmenu"`` is the
        outer Container — anything that doesn't trace its parent
        chain back to that id is considered "outside".
        """
        w = getattr(event, "widget", None)
        cur = w
        while cur is not None:
            if getattr(cur, "id", None) == "ctxmenu":
                return
            if cur is self:
                break
            cur = cur.parent
        event.stop()
        self.dismiss(None)
