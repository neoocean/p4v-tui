"""Preview the files a Branch Files (``p4 populate``) op would create.

p4v previews the branch set before committing. ``populate`` auto-submits,
so this confirm step is the last chance to back out. Shows the target
depot paths from the ``populate -n`` dry run; ``Branch`` proceeds, ``Esc``
/ ``Cancel`` aborts. Returns ``True`` to proceed, ``False`` otherwise.

(A branch creates files that don't exist on the target yet, so there's
nothing to *diff* against — the meaningful preview is the list of paths
that will be created.)
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option


class BranchPreviewModal(ModalScreen[bool]):
    DEFAULT_CSS = """
    BranchPreviewModal { align: center middle; }
    BranchPreviewModal > #dialog {
        width: 90%;
        height: 80%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    BranchPreviewModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    BranchPreviewModal #files { height: 1fr; margin-top: 1; }
    BranchPreviewModal #buttons { height: 3; align: right middle; }
    BranchPreviewModal Button { margin-left: 2; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("enter", "confirm", "Branch", priority=True),
    ]

    def __init__(self, files: list[str], summary: str) -> None:
        super().__init__()
        self._files = files
        self._summary = summary

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(
                f" Branch preview · {len(self._files)} file(s) · "
                f"{self._summary} ",
                id="title",
            )
            # Disabled options — this is a read-only preview, not a picker.
            yield OptionList(
                *[Option(f, disabled=True) for f in self._files],
                id="files",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Branch (submit)", id="ok", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
