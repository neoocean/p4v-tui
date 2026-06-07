"""Pick one file from a CL to navigate to in the workspace tree.

Used by the Submitted CL "Show Files in Tree…" action when the CL
touched more than one file. For a single-file CL the App skips the
picker and navigates straight away.
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class FileInCLPickerModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    FileInCLPickerModal { align: center middle; }
    FileInCLPickerModal > #dialog {
        width: 90%;
        height: 70%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    FileInCLPickerModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    FileInCLPickerModal #filter { margin-top: 1; }
    FileInCLPickerModal #file_list { height: 1fr; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self, change: str, files: list[str], *, title: str | None = None,
    ) -> None:
        super().__init__()
        self._change = change
        self._files = files
        # Default title keeps the original "Show in tree · CL N" wording;
        # callers reusing this as a generic file picker (e.g. the Go-to-path
        # fuzzy fallback, which has no CL) pass their own label.
        self._title = (
            title if title is not None
            else f" Show in tree · CL {change} "
        )

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(self._title, id="title")
            yield Input(placeholder="filter…", id="filter")
            yield OptionList(*self._build_opts(""), id="file_list")

    def _build_opts(self, filt: str) -> list[Option]:
        out: list[Option] = []
        f = filt.strip().lower()
        for path in self._files:
            if f and f not in path.lower():
                continue
            out.append(Option(path, id=path))
        return out

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter":
            return
        try:
            lst = self.query_one("#file_list", OptionList)
        except Exception:  # noqa: BLE001
            return
        lst.clear_options()
        for opt in self._build_opts(event.value):
            lst.add_option(opt)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
