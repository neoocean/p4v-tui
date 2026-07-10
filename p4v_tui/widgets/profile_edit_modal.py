"""Edit one connection [[profile]] entry.

Sub-modal of the Preferences "Profiles" tab. Returns the edited
:class:`ConnectionConfig` on Save, or ``None`` on Cancel. Used for both
"Add" (empty initial) and "Edit" (pre-filled) — the only difference is
the title and which list slot the caller writes the result back into.

A profile needs at least a ``port`` to be useful (it's the server the
picker connects to); the other fields fall back to the ``p4`` environment
when blank, mirroring how ``[connection]`` already behaves.
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from ..config import ConnectionConfig


class ProfileEditModal(ModalScreen[Optional[ConnectionConfig]]):
    DEFAULT_CSS = """
    ProfileEditModal { align: center middle; }
    ProfileEditModal > #dialog {
        width: 80%;
        max-width: 80;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1;
    }
    ProfileEditModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
        margin-bottom: 1;
    }
    ProfileEditModal Label { margin-top: 1; }
    ProfileEditModal #buttons {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    ProfileEditModal Button { margin-left: 2; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        profile: ConnectionConfig | None = None,
        *,
        adding: bool = False,
    ) -> None:
        super().__init__()
        self._profile = profile or ConnectionConfig()
        self._adding = adding

    def compose(self) -> ComposeResult:
        p = self._profile
        verb = "Add" if self._adding else "Edit"
        with Container(id="dialog"):
            yield Static(f" {verb} connection profile ", id="title")
            with VerticalScroll():
                yield Label("Name (display label in the picker)")
                yield Input(value=p.name or "", id="p_name",
                            placeholder="e.g. Prod (Seoul)")
                yield Label("Port (required)")
                yield Input(value=p.port or "", id="p_port",
                            placeholder="ssl:host:1666")
                yield Label("User")
                yield Input(value=p.user or "", id="p_user",
                            placeholder="(blank → P4 env)")
                yield Label("Client / Workspace")
                yield Input(value=p.client or "", id="p_client",
                            placeholder="(blank → P4 env)")
                yield Label("Charset")
                yield Input(value=p.charset or "", id="p_charset",
                            placeholder="utf8")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#p_name", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        else:
            self.dismiss(None)

    def _val(self, wid: str) -> str | None:
        v = self.query_one(f"#{wid}", Input).value.strip()
        return v or None

    def _save(self) -> None:
        port = self._val("p_port")
        if not port:
            self.app.notify(
                "Port is required (it's the server the picker connects to).",
                severity="warning", timeout=4,
            )
            return
        self.dismiss(ConnectionConfig(
            name=self._val("p_name"),
            port=port,
            user=self._val("p_user"),
            client=self._val("p_client"),
            charset=self._val("p_charset"),
        ))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
