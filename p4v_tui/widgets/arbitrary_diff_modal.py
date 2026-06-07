"""Entry picker for the Arbitrary Diff feature.

The user types two specs (left + right). Each spec is anything
``p4 diff2`` accepts:

  * ``//depot/path/file.txt``         (head rev)
  * ``//depot/path/file.txt#5``       (specific rev)
  * ``//depot/path/file.txt@CL``      (version at that CL)
  * ``//depot/path/...``              (folder tree)
  * ``//depot/path/...@CL``           (folder tree at CL)
  * Local workspace paths             (translated by p4)

On OK, dismisses with ``(left_spec, right_spec)``. The App is
responsible for routing — single-file pair → straight to
SideBySideDiffModal; tree pair → DifferingPairsPickerModal first.
Cancel returns ``None``.
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class ArbitraryDiffModal(ModalScreen[Optional[tuple[str, str]]]):
    DEFAULT_CSS = """
    ArbitraryDiffModal { align: center middle; }
    ArbitraryDiffModal > #dialog {
        width: 90;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1;
    }
    ArbitraryDiffModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    ArbitraryDiffModal Static.hint {
        color: $text-muted;
        padding: 0 1 1 1;
    }
    ArbitraryDiffModal Label.field {
        margin-top: 1;
        text-style: bold;
    }
    ArbitraryDiffModal Input { margin-bottom: 0; }
    ArbitraryDiffModal #buttons {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    ArbitraryDiffModal Button { margin-left: 2; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        prefilled_left: str = "",
        prefilled_right: str = "",
    ) -> None:
        super().__init__()
        self._left = prefilled_left
        self._right = prefilled_right

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Diff two paths / revisions / changelists ",
                         id="title")
            yield Static(
                "  Each side accepts file or folder paths, optionally "
                "with #rev or @CL. Use //path/... for folder diff.",
                classes="hint",
            )
            yield Label("Left", classes="field")
            yield Input(value=self._left, id="left",
                        placeholder="//depot/path/file.txt#5  · "
                                    "//depot/path/...@1234")
            yield Label("Right", classes="field")
            yield Input(value=self._right, id="right",
                        placeholder="//depot/path/file.txt  · "
                                    "//depot/path/...@5678")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Diff", id="ok", variant="primary")

    def on_mount(self) -> None:
        try:
            inp = self.query_one("#left", Input)
            inp.focus()
            inp.cursor_position = len(inp.value)
        except Exception:  # noqa: BLE001
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter on either input is the same as clicking Diff.
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        left = self.query_one("#left", Input).value.strip()
        right = self.query_one("#right", Input).value.strip()
        if not left or not right:
            self.app.notify(
                "Both Left and Right specs are required.",
                severity="warning", timeout=4,
            )
            return
        if left == right:
            self.app.notify(
                "Left and Right are identical — nothing to diff.",
                severity="warning", timeout=4,
            )
            return
        self.dismiss((left, right))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
