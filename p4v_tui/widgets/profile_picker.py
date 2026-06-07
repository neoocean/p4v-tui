"""Profile picker shown at startup when 2+ P4 servers are configured.

Returns the picked ``ConnectionConfig`` via the modal result, or ``None``
if the user cancels (caller treats cancel as "exit the app").
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..config import ConnectionConfig


class ProfilePickerModal(ModalScreen[Optional[ConnectionConfig]]):
    DEFAULT_CSS = """
    ProfilePickerModal { align: center middle; }
    ProfilePickerModal > #dialog {
        width: 80;
        height: auto;
        max-height: 28;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    ProfilePickerModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    ProfilePickerModal #help {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    ProfilePickerModal OptionList {
        background: transparent;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, profiles: list[ConnectionConfig]) -> None:
        super().__init__()
        self._profiles = profiles

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Pick a Perforce server ", id="title")
            yield Static(
                " ↑↓ Enter = pick · Esc = exit ", id="help",
            )
            options = []
            for i, p in enumerate(self._profiles):
                label = self._format(p)
                # Use the index as id so we can map back to the
                # ConnectionConfig on selection.
                options.append(Option(label, id=f"profile_{i}"))
            yield OptionList(*options, id="profile_list")

    def on_mount(self) -> None:
        try:
            self.query_one("#profile_list", OptionList).focus()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _format(p: ConnectionConfig) -> str:
        bits = []
        if p.name:
            bits.append(f"[b]{p.name}[/]")
        if p.port:
            bits.append(p.port)
        if p.user:
            bits.append(f"user={p.user}")
        if p.client:
            bits.append(f"client={p.client}")
        return "  ".join(bits) if bits else "(unnamed profile)"

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        oid = event.option.id or ""
        if not oid.startswith("profile_"):
            return
        try:
            idx = int(oid.split("_", 1)[1])
        except ValueError:
            return
        if 0 <= idx < len(self._profiles):
            self.dismiss(self._profiles[idx])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
