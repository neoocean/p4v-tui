"""F2 quick-rename popup.

Single-input modal for renaming the leaf component of a file or
directory in place. Enter confirms (the caller then runs the
auto-submit pipeline), Escape cancels. Returns the new leaf string
or None.

Differs from :class:`RenameMoveModal`:

* No base-path / Browse field — F2 is a *rename in place*, not a
  move to a different directory. Path separators in the input are
  rejected with a toast so the user goes through the full
  Rename/Move dialog if they wanted to relocate.
* Auto-submits the rename when the caller wires it up — the popup
  itself just collects the new leaf, the App's worker creates a
  pending CL, opens + moves the files, and queues a resilient
  submit.
"""
from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class QuickRenameModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    QuickRenameModal { align: center middle; }
    QuickRenameModal > #dialog {
        width: 80%;
        max-width: 100;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1 2;
    }
    QuickRenameModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
        margin-bottom: 1;
    }
    QuickRenameModal #source_path {
        color: $text-muted;
        margin-bottom: 1;
    }
    QuickRenameModal #hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        source_path: str,
        *,
        is_directory: bool = False,
    ) -> None:
        super().__init__()
        self._source = source_path
        self._is_directory = is_directory
        if "/" in source_path:
            self._leaf = source_path.rsplit("/", 1)[-1]
        else:
            self._leaf = source_path

    def compose(self) -> ComposeResult:
        scope = "directory" if self._is_directory else "file"
        with Container(id="dialog"):
            yield Static(f" Rename {scope} ", id="title")
            yield Static(self._source, id="source_path")
            yield Input(value=self._leaf, id="leaf")
            yield Static(
                "Enter: rename + submit · Esc: cancel",
                id="hint",
            )

    def on_mount(self) -> None:
        try:
            inp = self.query_one("#leaf", Input)
            inp.focus()
            # Put the cursor at the end so the user can backspace +
            # type, or Ctrl+A to select all. Pre-selecting all text
            # behaves unpredictably across Textual's Input revisions.
            inp.cursor_position = len(inp.value)
        except Exception:  # noqa: BLE001
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        new = event.value.strip()
        if not new:
            self.app.notify(
                "New name is empty.",
                severity="warning", timeout=4,
            )
            return
        if new == self._leaf:
            # No change — quietly cancel.
            self.dismiss(None)
            return
        if "/" in new or "\\" in new:
            self.app.notify(
                "Path separators (/ \\) aren't allowed in the new "
                "name. Use the Rename/Move… menu item for relocation.",
                severity="warning", timeout=6,
            )
            return
        self.dismiss(new)

    def action_cancel(self) -> None:
        self.dismiss(None)
