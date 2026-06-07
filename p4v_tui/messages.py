"""Custom Textual messages used to bridge widgets and the App."""
from __future__ import annotations

from textual.message import Message
from textual.widget import Widget
from textual.widgets.tree import TreeNode


class FileActionRequested(Message):
    """A workspace-tree action (sync/edit/revert) was requested.

    The App handles the actual ``p4`` invocation so the originating widget
    stays focused on UI concerns.
    """

    def __init__(
        self,
        action: str,
        target: str,
        source_node: TreeNode | None = None,
        is_directory: bool = False,
    ) -> None:
        self.action = action
        self.target = target
        self.source_node = source_node
        self.is_directory = is_directory
        super().__init__()


class BulkFileActionRequested(Message):
    """A workspace-tree action requested over a multi-selection (item 4).

    Emitted instead of :class:`FileActionRequested` when the user has
    marked one or more nodes (Space) and triggers edit / revert / add /
    sync. ``targets`` are already resolved depot/local specs (a marked
    directory becomes ``<path>/...``). The App runs a single multi-file
    ``p4`` call so an edit/add lands the whole set in one numbered CL.
    """

    def __init__(self, action: str, targets: list[str]) -> None:
        self.action = action
        self.targets = targets
        super().__init__()


class TreeFilterRequested(Message):
    """A P4Tree (Workspace or Depot) asked the App to surface its
    floating filter input. The App holds a single overlay widget
    that routes typed text back to ``tree.apply_filter``."""

    def __init__(self, tree: Widget) -> None:
        self.tree = tree
        super().__init__()


class P4ClipboardAction(Message):
    """Tree-level Ctrl+C / Ctrl+X / Ctrl+V — Perforce-aware
    "copy this path", "cut this path", "paste here". The App holds
    the single-slot clipboard state and dispatches the actual
    p4 copy / p4 move on paste.

    ``op`` is one of ``"copy"`` / ``"cut"`` / ``"paste"``. For copy
    and cut, ``path`` is the source the user marked. For paste,
    ``path`` is wherever the cursor is sitting in the destination
    tree — the App computes the actual destination by combining
    that with the stored source's leaf.
    """

    def __init__(
        self, op: str, path: str, is_directory: bool,
    ) -> None:
        self.op = op
        self.path = path
        self.is_directory = is_directory
        super().__init__()
