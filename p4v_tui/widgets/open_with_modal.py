"""Picker for the Open With… action.

Lists the user's configured ``[[external_editor]]`` entries (from
TOML, edited via Preferences). On pick, the App spawns the editor
with the file path filled into the entry's args template.

Returns the picked editor name, or ``None`` on cancel.
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

from ..config import ExternalEditor


class OpenWithModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    OpenWithModal { align: center middle; }
    OpenWithModal > #dialog {
        width: 70;
        height: auto;
        max-height: 80%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    OpenWithModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    OpenWithModal #subtitle { color: $text-muted; padding: 0 1 1 1; }
    OpenWithModal #editor_list { height: auto; max-height: 20; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        editors: list[ExternalEditor],
        target_path: str,
    ) -> None:
        super().__init__()
        self._editors = editors
        self._target_path = target_path

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Open With… ", id="title")
            yield Static(f"  {self._target_path}", id="subtitle")
            options = [
                Option(self._format_editor(ed), id=ed.name)
                for ed in self._editors
            ]
            yield OptionList(*options, id="editor_list")

    @staticmethod
    def _format_editor(ed: ExternalEditor) -> str:
        cmd = ed.command or "(no command)"
        args = ed.args or "{path}"
        return f"{ed.name}    {cmd}  {args}"

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
