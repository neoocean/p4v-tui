"""Modal to pick a target changelist for moving files into.

Returns:
  * the picked CL id as a string (numeric CL number, or ``"default"``),
  * the literal sentinel ``NEW_CL_SENTINEL`` if the user picked
    "New changelist…" — caller follows up with NewChangelistModal,
  * ``None`` if cancelled.
"""
from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


NEW_CL_SENTINEL = "__new__"


class MoveToChangelistModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    MoveToChangelistModal { align: center middle; }
    MoveToChangelistModal > #dialog {
        width: 90;
        height: 28;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    MoveToChangelistModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    MoveToChangelistModal OptionList {
        background: transparent;
        height: 1fr;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        source_cl: str,
        choices: list[tuple[str, str]],
    ) -> None:
        """choices is a list of (id, display_label) tuples in render order."""
        super().__init__()
        self._source = source_cl
        self._choices = choices

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(
                f" Move files from CL {self._source} to… ",
                id="title",
            )
            options = [
                Option(label, id=cid) for cid, label in self._choices
            ]
            yield OptionList(*options, id="cl_list")

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
