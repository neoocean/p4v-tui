"""Modal shown at startup when previous chunked jobs are still pending.

Per-item checkboxes via :class:`SelectionList` so the user can pick
exactly what to keep:

* Resume Selected → re-enqueue jobs for ticked items.
* Remove Selected → delete the state files for ticked items.
* Skip            → do nothing this run; state stays for next time.

Returns a dict with ``action`` ∈ {``resume``, ``remove``, ``skip``} and
``targets`` = list of state-file paths the user ticked.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, SelectionList, Static
from textual.widgets.selection_list import Selection

from ..pending_jobs import PendingJobInfo


class PendingJobsModal(ModalScreen[Optional[dict]]):
    DEFAULT_CSS = """
    PendingJobsModal { align: center middle; }
    PendingJobsModal > #dialog {
        width: 95%;
        height: 80%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    PendingJobsModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    PendingJobsModal #help {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    PendingJobsModal #job_list {
        height: 1fr;
        background: transparent;
    }
    PendingJobsModal #buttons {
        height: 3;
        align: right middle;
        padding: 0 1;
    }
    PendingJobsModal Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "skip", "Skip"),
    ]

    def __init__(self, items: list[PendingJobInfo]) -> None:
        super().__init__()
        self._items = items

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(
                f" Pending chunked jobs from previous run "
                f"({len(self._items)}) ",
                id="title",
            )
            yield Static(
                " Space toggles · Enter on Resume re-runs · "
                "Esc / Skip leaves state for next time ",
                id="help",
            )
            selections = [
                Selection(self._format(it), str(i), True)
                for i, it in enumerate(self._items)
            ]
            yield SelectionList[str](*selections, id="job_list")
            with Horizontal(id="buttons"):
                yield Button("Skip", id="skip")
                yield Button("Remove Selected", id="remove",
                             variant="error")
                yield Button("Resume Selected", id="resume",
                             variant="primary")

    def on_mount(self) -> None:
        try:
            self.query_one("#job_list", SelectionList).focus()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _format(it: PendingJobInfo) -> str:
        when = datetime.fromtimestamp(it.updated_at).strftime(
            "%Y-%m-%d %H:%M"
        ) if it.updated_at else "?"
        age = it.age_seconds
        age_str = (
            f"{age}s ago" if age < 60
            else f"{age//60}m ago" if age < 3600
            else f"{age//3600}h ago"
        )
        return (
            f"[b]{it.name}[/]\n"
            f"  [dim]target:[/] {it.target}\n"
            f"  [dim]completed so far:[/] {it.completed_count} files · "
            f"[dim]last update:[/] {when} ({age_str})"
        )

    # --- button actions --------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "skip":
            self.action_skip()
        elif bid == "remove":
            self._dismiss_with("remove")
        elif bid == "resume":
            self._dismiss_with("resume")

    def _dismiss_with(self, action: str) -> None:
        try:
            sel = self.query_one("#job_list", SelectionList).selected
        except Exception:  # noqa: BLE001
            sel = []
        targets = []
        for s in sel:
            try:
                idx = int(s)
            except ValueError:
                continue
            if 0 <= idx < len(self._items):
                targets.append(self._items[idx])
        self.dismiss({"action": action, "targets": targets})

    def action_skip(self) -> None:
        self.dismiss({"action": "skip", "targets": []})

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.action_skip()
