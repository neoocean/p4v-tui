"""Interactive Reconcile / Clean file picker.

Mirrors p4v's Reconcile/Clean dialog: a dry-run preview is shown with
every file checked, and the user unchecks the ones to leave out. Returns
the selected file *specs* (client paths) to operate on, or ``None`` on
cancel. The caller compares the returned count against the full preview
to decide whether to run the original all-or-nothing subdir job (all
selected) or the explicit-files job (a subset).

Each row shows the proposed action (add / edit / delete / …) so the user
can tell at a glance what each entry will do — a delete is destructive,
an add merely opens a new file.
"""
from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import SelectionList, Static
from textual.widgets.selection_list import Selection

from ..reconcile_preview import PreviewEntry


class ReconcilePickerModal(ModalScreen[Optional[list[str]]]):
    DEFAULT_CSS = """
    ReconcilePickerModal { align: center middle; }
    ReconcilePickerModal > #dialog {
        width: 90%;
        height: 80%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    ReconcilePickerModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    ReconcilePickerModal #files { height: 1fr; margin-top: 1; }
    ReconcilePickerModal #hint { color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("enter", "confirm", "Run", priority=True),
        Binding("a", "select_all", "All"),
        Binding("n", "select_none", "None"),
    ]

    def __init__(
        self,
        op_label: str,
        target: str,
        entries: list[PreviewEntry],
    ) -> None:
        super().__init__()
        self._op_label = op_label
        self._target = target
        self._entries = entries

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(
                f" {self._op_label} · {len(self._entries)} file(s) · "
                f"{self._target} ",
                id="title",
            )
            # Selection value is the operable spec (client path); the
            # visible label carries the action + path so a delete is
            # obvious before the user confirms.
            yield SelectionList[str](
                *[
                    Selection(e.display, e.spec, True)
                    for e in self._entries
                ],
                id="files",
            )
            yield Static(
                f"Space: toggle · a: all · n: none · "
                f"Enter: {self._op_label.lower()} selected · Esc: cancel",
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
                "Select at least one file (or Esc to cancel).",
                severity="warning", timeout=4,
            )
            return
        self.dismiss(selected)

    def action_cancel(self) -> None:
        self.dismiss(None)
