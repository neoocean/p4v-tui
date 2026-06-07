"""Picker for an existing Perforce label, used by Tag with Label.

Lists the result of ``p4 labels`` as ``label-name — description``.
Returns the picked label name, or ``None`` on cancel.
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


class LabelPickerModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    LabelPickerModal { align: center middle; }
    LabelPickerModal > #dialog {
        width: 80%;
        height: 70%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    LabelPickerModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    LabelPickerModal #filter { margin-top: 1; }
    LabelPickerModal #label_list { height: 1fr; }
    LabelPickerModal #status { color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, labels: list[dict], purpose: str) -> None:
        super().__init__()
        self._labels = labels
        self._purpose = purpose

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(f" Pick a label · {self._purpose} ", id="title")
            yield Input(placeholder="filter labels…", id="filter")
            opts = self._build_options(self._labels, "")
            if opts:
                yield OptionList(*opts, id="label_list")
            else:
                yield OptionList(id="label_list")
            yield Static(
                f"  {len(self._labels)} label(s) — Esc to cancel",
                id="status",
            )

    @staticmethod
    def _build_options(labels: list[dict], filt: str) -> list[Option]:
        out: list[Option] = []
        for r in labels:
            name = r.get("label", "") or ""
            if not name:
                continue
            if filt and filt.lower() not in name.lower():
                continue
            desc = (r.get("description") or "").strip().splitlines()
            first = desc[0] if desc else ""
            text = f"{name}    {first}" if first else name
            out.append(Option(text, id=name))
        return out

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter":
            return
        try:
            lst = self.query_one("#label_list", OptionList)
        except Exception:  # noqa: BLE001
            return
        lst.clear_options()
        for opt in self._build_options(self._labels, event.value.strip()):
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
