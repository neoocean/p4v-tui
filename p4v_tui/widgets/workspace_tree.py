"""Workspace tree: browses the depot under the current client's view.

Uses the ``//<client>/...`` path syntax so listings are scoped by the
workspace mapping. ``p4 fstat`` rows feed status overlays on file leaves.
"""
from __future__ import annotations

from textual.binding import Binding

from ..messages import FileActionRequested
from ..utils import is_deleted_at_head, truncate_cells
from .context_menu import ContextMenuItem, ContextMenuModal, SEPARATOR
from .p4_tree import LABEL_MAX_CELLS, P4Tree


# Single-character markers shown before each filename. Keep these short and
# distinctive — they end up packed beside long filenames in narrow trees.
_OPEN_ACTION_MARKER = {
    "edit": "e",
    "add": "+",
    "delete": "-",
    "branch": "B",
    "integrate": "I",
    "move/add": "+",
    "move/delete": "-",
    "purge": "P",
    "archive": "A",
}


def _status_marker(row: dict) -> str:
    action = row.get("action")  # currently-open action in this workspace
    if action:
        return _OPEN_ACTION_MARKER.get(action, "?")

    have = row.get("haveRev")
    head = row.get("headRev")
    head_action = row.get("headAction")

    if not have:
        # In depot but never synced into this workspace.
        return "·"
    try:
        if int(have) < int(head or 0):
            return "*"  # out of date
    except (TypeError, ValueError):
        pass
    if is_deleted_at_head(head_action):
        return "x"  # head is gone (delete / move/delete / …); copy is stale
    return " "  # synced, no pending action


class WorkspaceTree(P4Tree):
    BINDINGS = [
        # Single-letter aliases — fast for power users.
        Binding("s", "p4_action('sync')", "Sync", show=False),
        Binding("e", "p4_action('edit')", "Edit", show=False),
        Binding("r", "p4_action('revert')", "Revert", show=False),
        Binding("a", "p4_action('add')", "Mark for Add", show=False),
        # `g` = chunked sync: same target as 's' but goes through the
        # JobRunner so it can be split, resumed, and interleaved.
        Binding("g", "p4_action('chunked_sync')", "Chunked Sync"),
        # Hangul-IME aliases (2-beolsik): s/e/r/g/m -> ㄴ/ㄷ/ㄱ/ㅎ/ㅡ.
        # These let the single-letter shortcuts keep working when the
        # user has the Korean IME on (so the terminal sends the jamo
        # rather than the Latin key).
        Binding("ㄴ", "p4_action('sync')", show=False),
        Binding("ㄷ", "p4_action('edit')", show=False),
        Binding("ㄱ", "p4_action('revert')", show=False),
        Binding("ㅁ", "p4_action('add')", show=False),
        Binding("ㅎ", "p4_action('chunked_sync')", show=False),
        Binding("ㅡ", "show_context_menu", show=False),
        # p4v-style accelerators surfaced in the footer (Ctrl-combos
        # bypass IME composition on every terminal we care about).
        Binding("ctrl+shift+g", "p4_action('sync')", "Get Latest"),
        Binding("ctrl+e", "p4_action('edit')", "Check Out"),
        Binding("ctrl+r", "p4_action('revert')", "Revert"),
        Binding("ctrl+l", "p4_action('lock')", "Lock"),
        Binding("ctrl+u", "p4_action('unlock')", "Unlock"),
        # Context menu open.
        Binding("m", "show_context_menu", "Menu"),
        Binding("shift+f10", "show_context_menu", show=False),
    ]

    def __init__(self, p4, **kwargs) -> None:
        # Real root is set after connect via set_root_path(); start placeholder.
        super().__init__(root_path="//", p4=p4, **kwargs)

    def configure_for_client(self, client_name: str) -> None:
        if not client_name:
            return
        self.set_root_path(f"//{client_name}", label=f"//{client_name}/")

    def _fetch_node_data(self, path: str) -> tuple[list[str], list[dict]]:
        glob = f"{path}/*"
        return self._p4.dirs(glob), self._p4.fstat(glob)

    def _file_sort_key(self, file_row: dict) -> str:
        return (file_row.get("clientFile") or file_row.get("depotFile", "")).lower()

    # Filesystem hand-offs and clipboard copies operate on the path
    # itself; appending /... would break `p4 where`, the OS commands,
    # and the Swarm URL formula. Rename is also pre-recursion: the
    # worker appends /... after the user picks the new directory name.
    _NO_RECURSE_ACTIONS = frozenset({
        "show_in", "open_cmd", "open_with",
        "copy_path", "copy_swarm", "copy_permalink", "bookmark_add", "rename",
        "annotate", "timelapse", "rev_graph", "file_props",
    })

    def action_p4_action(self, action: str) -> None:
        # When nodes are marked, edit/revert/add/sync apply to the whole
        # selection via a single bulk request (item 4).
        if self.has_marks() and action in ("sync", "edit", "revert", "add"):
            from ..messages import BulkFileActionRequested
            self.post_message(
                BulkFileActionRequested(action, self.marked_specs())
            )
            self.clear_marks()
            return
        node = self.cursor_node
        if node is None or not node.data:
            self.app.notify(
                "Move the cursor onto a file or folder first.",
                timeout=3,
            )
            return
        # The root represents the entire workspace — actions on it target
        # the workspace recursively (//<client>/...).
        is_root = node is self.root
        is_dir = bool(node.allow_expand) or is_root
        if action in self._NO_RECURSE_ACTIONS:
            target = str(node.data)
        else:
            target = f"{node.data}/..." if is_dir else node.data
        self.post_message(
            FileActionRequested(
                action=action,
                target=target,
                source_node=node,
                is_directory=is_dir,
            )
        )

    # --- context menu -----------------------------------------------------

    def action_show_context_menu(self) -> None:
        node = self.cursor_node
        if node is None or not node.data:
            self.app.notify(
                "Move the cursor onto a file or folder first.",
                timeout=3,
            )
            return
        is_root = node is self.root
        target_label = (
            "(workspace root)" if is_root
            else (str(node.data).replace("\\", "/").rsplit("/", 1)[-1]
                  or str(node.data))
        )
        items = self._build_menu_items(
            is_directory=bool(node.allow_expand) or is_root,
        )

        def on_close(action_id: str | None, n=node) -> None:
            if not action_id:
                return
            if action_id == "search_in_folder":
                # Not a p4 verb — peel out to the App-level Fast
                # Search launcher with the cursor path as a seed.
                seed = "" if n is self.root else str(n.data or "")
                try:
                    self.app.action_open_search(initial_query=seed)
                except AttributeError:
                    # Older App build without the helper — fall back
                    # to the unparameterised open.
                    self.app.action_fast_search()
                return
            # Everything else flows through the same code path as the
            # keyboard shortcuts so behaviour (refresh, notifications,
            # confirm modal for revert) stays identical.
            self.action_p4_action(action_id)

        self.app.push_screen(
            ContextMenuModal(items, title=target_label),
            on_close,
        )

    def _build_menu_items(self, is_directory: bool) -> list[ContextMenuItem]:
        # Mirrors p4v's most-used items. The "(chunked …)" variants go
        # through the JobRunner so a long operation stays interruptible
        # and (for sync) resumable across launches.
        items: list[ContextMenuItem] = []
        if not is_directory:
            items.append(
                ContextMenuItem("Annotate / Blame…", "annotate", ""),
            )
            items.append(
                ContextMenuItem("Time-lapse View…", "timelapse", ""),
            )
            items.append(
                ContextMenuItem("Revision Graph…", "rev_graph", ""),
            )
            items.append(
                ContextMenuItem("File Properties…", "file_props", ""),
            )
            items.append(SEPARATOR)
        return items + [
            ContextMenuItem("Get Latest Revision", "sync", "Ctrl+Shift+G"),
            ContextMenuItem("Get Latest (chunked + resumable)",
                            "chunked_sync", "g"),
            ContextMenuItem("Force Get Latest (chunked)",
                            "chunked_force_sync", ""),
            ContextMenuItem("Get Revision…", "get_revision", ""),
            ContextMenuItem("Check Out", "edit", "Ctrl+E"),
            ContextMenuItem("Mark for Add", "add", ""),
            ContextMenuItem("Mark for Delete", "delete", ""),
            SEPARATOR,
            ContextMenuItem("Revert Files", "revert", "Ctrl+R"),
            ContextMenuItem("Revert Files (chunked)", "chunked_revert", ""),
            ContextMenuItem("Undo Latest Change…", "undo", ""),
            ContextMenuItem("Diff Against Have…", "diff_have", ""),
            SEPARATOR,
            ContextMenuItem("Lock", "lock", "Ctrl+L"),
            ContextMenuItem("Unlock", "unlock", "Ctrl+U"),
            SEPARATOR,
            ContextMenuItem("Reconcile Offline Work…",
                            "chunked_reconcile", ""),
            ContextMenuItem("Clean…", "chunked_clean", ""),
            SEPARATOR,
            ContextMenuItem("Merge/Integrate Files…", "integrate", ""),
            ContextMenuItem("Copy Files…", "copy", ""),
            ContextMenuItem("Branch Files…", "branch", ""),
            ContextMenuItem("Resolve Files…", "resolve", ""),
            ContextMenuItem("Rename/Move…", "rename", ""),
            SEPARATOR,
            ContextMenuItem("Open With…", "open_with", ""),
            ContextMenuItem("Show In…", "show_in", ""),
            ContextMenuItem("Open Command Window Here", "open_cmd", ""),
            SEPARATOR,
            ContextMenuItem("Copy Depot Path", "copy_path", ""),
            ContextMenuItem("Copy Swarm URL", "copy_swarm", ""),
            ContextMenuItem("Copy Permalink", "copy_permalink", "Alt+C"),
            ContextMenuItem("Bookmark This Path", "bookmark_add", "Ctrl+B"),
            ContextMenuItem(
                "Search In This Folder…", "search_in_folder", "Ctrl+F",
            ),
        ]

    def _format_file(self, file_row: dict) -> tuple[str, str]:
        depot_file = file_row.get("depotFile", "")
        client_file = file_row.get("clientFile", "")
        source = client_file or depot_file
        name = source.replace("\\", "/").rsplit("/", 1)[-1] if source else "?"
        marker = _status_marker(file_row)
        rev = file_row.get("haveRev") or file_row.get("headRev") or ""
        rev_str = f"#{rev}" if rev else ""
        # Truncate the variable filename portion only; keep the marker prefix
        # and rev suffix intact so status info doesn't get eaten by the cap.
        prefix = f"{marker} "
        budget = max(1, LABEL_MAX_CELLS - len(prefix) - len(rev_str))
        label = f"{prefix}{truncate_cells(name, budget)}{rev_str}"
        return label, depot_file
