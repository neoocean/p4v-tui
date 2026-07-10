"""Picker for an existing Perforce branch mapping, used by Branch Files.

Lists ``p4 branches`` as ``branch-name — description`` plus a "manual"
escape hatch (enter source/target by hand instead of using a mapping).
Returns the picked branch name, the empty string for manual mode, or
``None`` on cancel.
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

# Sentinel option id for "don't use a mapping, I'll type source/target".
MANUAL_ID = "__manual__"


class BranchPickerModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    BranchPickerModal { align: center middle; }
    BranchPickerModal > #dialog {
        width: 80%;
        height: 70%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    BranchPickerModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    BranchPickerModal #filter { margin-top: 1; }
    BranchPickerModal #branch_list { height: 1fr; }
    BranchPickerModal #status { color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, branches: list[dict]) -> None:
        super().__init__()
        self._branches = branches

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Pick a branch mapping · Branch Files ", id="title")
            yield Input(placeholder="filter branch mappings…", id="filter")
            yield OptionList(*self._build_options(""), id="branch_list")
            yield Static(
                f"  {len(self._branches)} branch mapping(s) · "
                "first row = enter source/target manually · Esc cancel",
                id="status",
            )

    def _build_options(self, filt: str) -> list[Option]:
        out: list[Option] = [
            Option("→ Enter source / target manually", id=MANUAL_ID),
        ]
        for r in self._branches:
            name = r.get("branch", "") or ""
            if not name:
                continue
            if filt and filt.lower() not in name.lower():
                continue
            desc = (r.get("Description") or r.get("description") or "")
            first = desc.strip().splitlines()
            tail = f"    {first[0]}" if first else ""
            out.append(Option(f"{name}{tail}", id=name))
        return out

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter":
            return
        try:
            lst = self.query_one("#branch_list", OptionList)
        except Exception:  # noqa: BLE001
            return
        lst.clear_options()
        for opt in self._build_options(event.value.strip()):
            lst.add_option(opt)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        oid = event.option.id
        self.dismiss("" if oid == MANUAL_ID else oid)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
