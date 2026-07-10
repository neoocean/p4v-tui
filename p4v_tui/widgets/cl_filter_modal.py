"""Filter + sort dialog for the Pending / Submitted CL tables.

Edits a :class:`p4v_tui.cl_table_filter.CLTableView` and returns the new
one (or ``None`` on cancel). The "Clear" button returns a fresh default
view so the user can reset to "show everything as before" in one press.

The workspace filter row is only shown for the Pending table (Submitted
rows have no owning-client column), gated by ``show_workspace``.
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static

from ..cl_table_filter import CLTableView, SORT_KEYS


class CLFilterModal(ModalScreen[Optional[CLTableView]]):
    DEFAULT_CSS = """
    CLFilterModal { align: center middle; }
    CLFilterModal > #dialog {
        width: 80%;
        max-width: 90;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1;
    }
    CLFilterModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
        margin-bottom: 1;
    }
    CLFilterModal Static.field_label { margin-top: 1; }
    CLFilterModal #sort_row { height: 3; }
    CLFilterModal Select { width: 30; }
    CLFilterModal Checkbox { margin-left: 2; }
    CLFilterModal #buttons {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    CLFilterModal Button { margin-left: 2; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        view: CLTableView,
        *,
        table_label: str = "Changelists",
        show_workspace: bool = True,
    ) -> None:
        super().__init__()
        self._view = view
        self._table_label = table_label
        self._show_workspace = show_workspace

    def compose(self) -> ComposeResult:
        v = self._view
        with Container(id="dialog"):
            yield Static(f" Filter / Sort — {self._table_label} ", id="title")
            with Vertical():
                with Horizontal(id="sort_row"):
                    yield Select(
                        [(k, k) for k in SORT_KEYS],
                        value=v.sort_key,
                        id="sort_key",
                        allow_blank=False,
                    )
                    yield Checkbox(
                        "Descending", value=v.descending, id="descending",
                    )
                yield Static("User contains:", classes="field_label")
                yield Input(value=v.user, placeholder="(any user)", id="user")
                if self._show_workspace:
                    yield Static("Workspace contains:", classes="field_label")
                    yield Input(
                        value=v.workspace,
                        placeholder="(any workspace)", id="workspace",
                    )
                yield Static("Description contains:", classes="field_label")
                yield Input(value=v.text, placeholder="(any text)", id="text")
                yield Static("Description regex:", classes="field_label")
                yield Input(
                    value=v.regex, placeholder="e.g. ^WIP|hotfix", id="regex",
                )
                yield Static("Date from / to (YYYY-MM-DD):",
                             classes="field_label")
                with Horizontal():
                    yield Input(
                        value=v.date_from, placeholder="from", id="date_from",
                    )
                    yield Input(
                        value=v.date_to, placeholder="to", id="date_to",
                    )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Clear", id="clear")
                yield Button("Apply", id="ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#user", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self._collect())
        elif event.button.id == "clear":
            self.dismiss(CLTableView())
        else:
            self.dismiss(None)

    def _field(self, wid: str) -> str:
        try:
            return self.query_one(f"#{wid}", Input).value.strip()
        except Exception:  # noqa: BLE001 — field absent (e.g. workspace)
            return ""

    def _collect(self) -> CLTableView:
        sort_sel = self.query_one("#sort_key", Select).value
        sort_key = str(sort_sel) if sort_sel in SORT_KEYS else "default"
        return CLTableView(
            sort_key=sort_key,
            descending=self.query_one("#descending", Checkbox).value,
            user=self._field("user"),
            workspace=self._field("workspace"),
            text=self._field("text"),
            regex=self._field("regex"),
            date_from=self._field("date_from"),
            date_to=self._field("date_to"),
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
