"""Modal that shows a live tree of running + recent P4 commands.

Top-level entries are either:
  * A Job container (from JobRunner) — its chunks' p4 commands appear
    nested below.
  * A standalone p4 command (fired outside any Job) — leaf at the top.

State markers prefixing each row:
    [cyan]●[/]   running
    [green]✓[/]  done
    [red]✗[/]    failed

The modal polls every 0.5s as a refresh fallback and also subscribes to
``CmdLog.add_listener`` for instant updates when something completes.
"""
from __future__ import annotations

import time
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static, Tree

from ..cmd_log import CmdEntry, CmdLog
from ..utils import format_eta


class CmdMonitorModal(ModalScreen[None]):
    DEFAULT_CSS = """
    CmdMonitorModal { align: center middle; }
    CmdMonitorModal > #dialog {
        width: 95%;
        height: 90%;
        border: thick $primary;
        background: $panel;
    }
    CmdMonitorModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    CmdMonitorModal #cmd_tree {
        height: 1fr;
        background: transparent;
    }
    CmdMonitorModal #footer_hint {
        height: 1;
        padding: 0 1;
        background: $boost;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", show=False),
        Binding("ㅂ", "close", show=False),
    ]

    def __init__(self, log: CmdLog) -> None:
        super().__init__()
        self._log = log
        self._listener_attached = False

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Command Monitor ", id="title")
            yield Tree("Commands", id="cmd_tree")
            yield Static(" Esc to close · auto-refresh 0.5s ",
                         id="footer_hint")

    def on_mount(self) -> None:
        # Subscribe + initial render + periodic fallback refresh.
        self._log.add_listener(self._on_log_change)
        self._listener_attached = True
        self.set_interval(0.5, self._refresh)
        self._refresh()
        # Pre-expand the root for visibility.
        try:
            tree = self.query_one("#cmd_tree", Tree)
            tree.root.expand()
        except Exception:  # noqa: BLE001
            pass

    def on_unmount(self) -> None:
        if self._listener_attached:
            self._log.remove_listener(self._on_log_change)
            self._listener_attached = False

    # --- refresh ---------------------------------------------------------

    def _on_log_change(self) -> None:
        # Listener fires on a worker thread — marshal to UI thread.
        try:
            self.app.call_from_thread(self._refresh)
        except Exception:  # noqa: BLE001
            pass

    def _refresh(self) -> None:
        try:
            tree = self.query_one("#cmd_tree", Tree)
        except Exception:  # noqa: BLE001
            return
        entries = self._log.snapshot()
        by_parent: dict[Optional[int], list[CmdEntry]] = {}
        ids = {e.id for e in entries}
        for e in entries:
            # If parent_id refers to an entry that's been pruned, treat as orphan.
            pid = e.parent_id if (e.parent_id is None or e.parent_id in ids) else None
            by_parent.setdefault(pid, []).append(e)
        # Newest first at every level so live activity stays visible.
        for k in by_parent:
            by_parent[k].sort(key=lambda x: x.start_time, reverse=True)

        tree.clear()
        tree.root.label = (
            f"Commands  [{len(entries)} entries, "
            f"{sum(1 for e in entries if e.state == 'running')} running]"
        )
        for top in by_parent.get(None, []):
            self._add_entry(tree.root, top, by_parent)
        tree.root.expand()

    def _add_entry(self, parent_node, entry: CmdEntry, by_parent) -> None:
        marker = {
            "running": "[cyan]●[/]",
            "done":    "[green]✓[/]",
            "failed":  "[red]✗[/]",
        }.get(entry.state, "?")
        end = entry.end_time if entry.end_time is not None else time.time()
        elapsed = max(0.0, end - entry.start_time)
        suffix = f"  [dim]({elapsed:.1f}s)[/]"
        # Job entries show progress + ETA when available. The eta updates
        # on every refresh tick so it tracks the current rate of work.
        if entry.is_job:
            if entry.done is not None and entry.total is not None:
                suffix += f"  [{entry.done}/{entry.total}]"
            if entry.state == "running":
                eta = format_eta(entry.eta_seconds())
                if eta:
                    suffix += f"  [dim]eta {eta}[/]"
        if entry.error:
            suffix += f"  [red]err: {entry.error[:80]}[/]"
        label = f"{marker} {entry.name}{suffix}"
        children = by_parent.get(entry.id, [])
        if entry.is_job or children:
            node = parent_node.add(label, expand=True)
            for child in children:
                self._add_entry(node, child, by_parent)
        else:
            parent_node.add_leaf(label)

    def action_close(self) -> None:
        self.dismiss()
