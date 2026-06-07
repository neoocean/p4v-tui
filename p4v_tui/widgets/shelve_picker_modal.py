"""Partial-shelve file picker (item 2).

p4v's Shelve dialog lets you check a subset of a changelist's open files
to shelve. This multi-select modal mirrors that: all files start
selected (so confirming with no changes == "shelve everything", the old
behaviour), and the user can uncheck the ones to leave out. Returns the
selected depot paths, or None on cancel.
"""
from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import SelectionList, Static
from textual.widgets.selection_list import Selection


class ShelvePickerModal(ModalScreen[Optional[list[str]]]):
    DEFAULT_CSS = """
    ShelvePickerModal { align: center middle; }
    ShelvePickerModal > #dialog {
        width: 90%;
        height: 80%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    ShelvePickerModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    ShelvePickerModal #files { height: 1fr; margin-top: 1; }
    ShelvePickerModal #hint { color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("enter", "confirm", "Shelve", priority=True),
        Binding("a", "select_all", "All"),
        Binding("n", "select_none", "None"),
    ]

    def __init__(self, change: str, files: list[str]) -> None:
        super().__init__()
        self._change = change
        self._files = files

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(
                f" Shelve files · CL {self._change} ", id="title",
            )
            yield SelectionList[str](
                *[Selection(path, path, True) for path in self._files],
                id="files",
            )
            yield Static(
                "Space: toggle · a: all · n: none · Enter: shelve · Esc: cancel",
                id="hint",
            )

    def on_mount(self) -> None:
        try:
            self.query_one("#files", SelectionList).focus()
        except Exception:  # noqa: BLE001
            pass

    def _list(self) -> SelectionList:
        return self.query_one("#files", SelectionList)

    def action_select_all(self) -> None:
        self._list().select_all()

    def action_select_none(self) -> None:
        self._list().deselect_all()

    def action_confirm(self) -> None:
        selected = list(self._list().selected)
        if not selected:
            self.app.notify(
                "Select at least one file to shelve (or Esc to cancel).",
                severity="warning", timeout=4,
            )
            return
        self.dismiss(selected)

    def action_cancel(self) -> None:
        self.dismiss(None)
