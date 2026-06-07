"""Depot tree picker modal.

A read-only browser used by other modals (Rename/Move, future
Branch/Copy/Integrate refinements) to let the user pick a depot path
without typing it from scratch.

Returns the picked depot path string, or ``None`` if cancelled.

Navigation:
  * Arrow keys / Right (expand) / Left (collapse) — same as the
    main DepotTree.
  * Enter — dismiss with the cursored node's path.
  * Esc  — dismiss with None.
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static, Tree

from .depot_tree import DepotTree


class _BrowseDepotTree(DepotTree):
    """DepotTree variant for use inside a picker.

    Disables the context menu (`m` / `Shift+F10` / `ㅡ`) so a user
    browsing for a path can't accidentally fire side-effecting actions
    like sync or rename. Navigation bindings (arrows, expand/collapse)
    inherited from P4Tree stay intact.
    """

    def action_show_context_menu(self) -> None:
        # No-op in browse mode. Pick via Enter / click.
        pass


class DepotBrowserModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    DepotBrowserModal { align: center middle; }
    DepotBrowserModal > #dialog {
        width: 90%;
        height: 80%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    DepotBrowserModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    DepotBrowserModal #hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    DepotBrowserModal #browse_tree {
        height: 1fr;
        background: transparent;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, p4_service) -> None:
        super().__init__()
        self._p4 = p4_service

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Browse depot ", id="title")
            yield Static(
                " ↑↓ navigate · → expand / ← collapse · "
                "Enter = pick this path · Esc = cancel ",
                id="hint",
            )
            yield _BrowseDepotTree("//", self._p4, id="browse_tree")

    def on_mount(self) -> None:
        try:
            tree = self.query_one("#browse_tree", _BrowseDepotTree)
            tree.bootstrap()
            tree.focus()
        except Exception:  # noqa: BLE001
            pass

    def on_tree_node_selected(
        self, event: Tree.NodeSelected,
    ) -> None:
        # Prevent the App-level NodeSelected handler (which would open
        # the file viewer) from also firing.
        event.stop()
        node = event.node
        if node is None or not node.data:
            return
        self.dismiss(str(node.data))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
