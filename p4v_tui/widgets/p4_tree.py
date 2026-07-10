"""Base class for lazy-loaded P4 namespace trees.

Subclasses implement ``_fetch_node_data`` to return ``(dirs, files)`` for a
given path. Directories become expandable child nodes; files become leaves.

Adds keyboard navigation that mirrors common file-explorer behavior:
  * Right arrow: expand current node, or step to its first child if already
    expanded.
  * Left arrow: collapse current node, or step to its parent if already
    collapsed (or a leaf).
"""
from __future__ import annotations

from rich.text import Text

from textual import work
from textual.binding import Binding
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from ..p4client import P4Service
from ..utils import truncate_cells

# Hard cap on label width inside the tree, in display cells. The default left
# pane width is 60; subtract ~10 cells for guides/indent/expand-arrow at deep
# nesting and we have ~50 cells of usable label space.
LABEL_MAX_CELLS = 50

# Loading marker appended to the right of a node's label while its
# children are being fetched from the server. Restored on populate.
# Animated via a one-off interval so the marker visibly turns —
# users on slow connections need a "we're alive, not frozen" signal.
_LOADING_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_LOADING_STYLE = "bold yellow"
_LOADING_INTERVAL = 0.12  # seconds — matches a typical CLI spinner rate


def _next_segment(parent: str, target: str) -> str | None:
    """Return the immediate child path of ``parent`` that lies on the
    walk down to ``target`` — or None if target isn't a descendant.

    Handles the special ``//`` root: ``_next_segment("//", "//d/x/y")``
    returns ``"//d"``, not ``"///d"``.
    """
    if not parent or not target or parent == target:
        return None
    if parent == "//":
        if not target.startswith("//"):
            return None
        rest = target[2:]
        if not rest:
            return None
        head = rest.split("/", 1)[0]
        return f"//{head}"
    if not target.startswith(parent + "/"):
        return None
    rest = target[len(parent) + 1:]
    head = rest.split("/", 1)[0]
    return f"{parent}/{head}"


def _basename(path: str) -> str:
    """Last path component, tolerant of both ``/`` and ``\\`` separators."""
    return (path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


class P4Tree(Tree[str]):
    BINDINGS = [
        Binding("right", "expand_node", "Expand", show=False),
        Binding("left", "collapse_node", "Collapse", show=False),
        Binding("slash", "start_filter", "Filter"),
        # 2-beolsik "?" → "/" position is shifted, so the IME-friendly
        # alternate is plain ASCII "/" which Hangul mode passes through
        # (it's not a jamo). No second alias needed.
        Binding("ctrl+c", "p4_clipboard_copy", "Copy", priority=True),
        Binding("ctrl+x", "p4_clipboard_cut",  "Cut",  priority=True),
        Binding("ctrl+v", "p4_clipboard_paste", "Paste", priority=True),
        # Copy a stable permalink for the cursor path (item 11).
        # Avoid ctrl+shift+c: Windows Terminal binds it to its own
        # "Copy" by default, and at the VT level it's indistinguishable
        # from ctrl+c (which is already bound above to p4_clipboard_copy).
        # alt+c sidesteps both — Alt+letter passes through Korean IME
        # too, so no Hangul alias is needed.
        Binding("alt+c", "copy_permalink", "Copy permalink",
                priority=True),
        # Bookmark the cursor path (permalink-backed, survives moves).
        Binding("ctrl+b", "bookmark_add", "Bookmark", priority=True),
        # F2 — quick in-place rename of the cursor leaf, auto-submitted
        # in its own changelist. Overrides the App-level F2 (Commands)
        # only while the tree is focused; the Command Monitor stays on
        # F2 everywhere else. Function key, no IME aliasing needed.
        Binding("f2", "quick_rename", "Rename"),
        # Multi-select: Space toggles a mark on the cursor node; marked
        # nodes drive the bulk edit/revert/add path (item 4).
        Binding("space", "toggle_mark", "Mark"),
        # Escape clears all marks (no-op when none; Escape is otherwise
        # unbound on the tree, and modals keep their own priority Escape).
        Binding("escape", "clear_marks", "Clear marks", show=False),
    ]

    # Leading glyph rendered on a marked node's label.
    MARK_GLYPH = "● "

    def __init__(self, root_path: str, p4: P4Service, **kwargs) -> None:
        super().__init__(label=root_path, data=root_path, **kwargs)
        self._p4 = p4
        self._loaded: set[str] = set()
        # Marked node paths → is_directory. Keyed by path (not TreeNode)
        # so a mark survives a subtree reload; re-applied in _populate.
        self._marked: dict[str, bool] = {}
        self.show_root = True
        self.guide_depth = 2
        # Map of nodes currently waiting for ``_fetch_children`` to
        # finish, keyed by id(node) (TreeNode isn't reliably hashable
        # across reloads). Stores the pre-loading label so we can
        # restore it once children arrive.
        self._loading_originals: dict[int, object] = {}
        # Animation timer — created lazily on the first concurrent
        # load, paused when the loading set drains.
        self._loading_timer = None
        self._loading_frame = 0

    # --- public API --------------------------------------------------------

    def bootstrap(self) -> None:
        """Trigger initial load of the root."""
        self.root.expand()

    def set_root_path(self, path: str, label: str | None = None) -> None:
        """Re-root this tree at ``path`` and clear its cached load state."""
        self._loaded.discard(self.root.data or "")
        self.root.data = path
        self.root.set_label(label if label is not None else path)
        self.root.remove_children()

    def refresh_root(self) -> None:
        """Wipe + re-fetch the root while preserving the user's view.

        ``r`` and ``F5`` both go through this. Losing what the user
        had open on every refresh is jarring — especially deep inside
        a nested directory after a sync — so before nuking children we
        snapshot:

        * every currently-expanded path (so ``_populate`` can re-expand
          each one as the lazy reload reaches its level)
        * the cursor's depot path (so ``navigate_to_path`` walks the
          cursor back to where it was)

        Together these make refresh idempotent from the user's POV:
        the open subtrees and cursor return automatically once the
        server data has been refetched.
        """
        cursor = self.cursor_node
        prev_cursor_data = (
            cursor.data if cursor is not None
            and cursor is not self.root else None
        )
        # ``set`` is fine — _populate matches by exact path equality;
        # duplicates can't happen because a node only has one data.
        self._pending_expansions = self._collect_expanded_paths(self.root)
        # remove_children() throws away every TreeNode under root, so
        # the cache of "already fetched paths" must be cleared in lock
        # step — otherwise a re-expand of `//depot` skips the server
        # fetch (it's still in _loaded) and lands as an empty node.
        self._loaded.clear()
        self.root.remove_children()
        self.root.expand()
        if prev_cursor_data:
            self.navigate_to_path(prev_cursor_data)

    def _collect_expanded_paths(self, node: TreeNode) -> set[str]:
        """Walk loaded subtree of ``node``, returning every data path
        whose node is currently expanded. Excludes the root itself —
        :meth:`refresh_root` always re-expands the root unconditionally.
        """
        out: set[str] = set()
        for child in node.children:
            if child.is_expanded and child.data:
                out.add(str(child.data))
            # Children of an expanded node may be expanded too.
            out.update(self._collect_expanded_paths(child))
        return out

    def reload_node(self, node: TreeNode) -> None:
        """Re-fetch the contents of an already-loaded node.

        Children are cleared and re-populated via the same lazy-load path,
        so callers can use this after mutating actions (sync, edit, revert).
        The cursor's data path is recorded before the wipe so we can hop
        back to the equivalent new node once children are repopulated.
        """
        path = node.data
        if not path:
            return
        cursor = self.cursor_node
        prev_cursor_data = cursor.data if cursor is not None else None
        self._loaded.discard(path)
        node.remove_children()
        self._loaded.add(path)
        self._fetch_children(node, path)
        if prev_cursor_data:
            self._pending_cursor_path = prev_cursor_data
        else:
            self._pending_cursor_path = None

    def navigate_to_path(self, target: str) -> None:
        """Move cursor to ``target`` (or its closest visible ancestor).

        Walks from root, expanding intermediate directories as needed.
        Lazy-load is async, so the walk resumes from :meth:`_populate`
        each time a needed level finishes loading.

        If a path segment isn't reachable in this tree (e.g. it's not
        under the workspace's view), the cursor lands on the deepest
        reachable ancestor — never silently no-ops.
        """
        if not target:
            return
        self._navigation_target = target
        self._navigate_step()

    def _navigate_step(self) -> None:
        target = getattr(self, "_navigation_target", None)
        if not target:
            return
        node = self._find_deepest_loaded(target)
        if (node.data or "") == target:
            self._move_cursor_to(node)
            self._navigation_target = None
            return
        next_path = _next_segment(node.data or "", target)
        if next_path is None:
            # Can't go deeper from this node — settle here.
            self._move_cursor_to(node)
            self._navigation_target = None
            return
        if (node.data or "") not in self._loaded:
            # Trigger async load; on_tree_node_expanded → _populate
            # will call us back once children land.
            if not node.is_expanded:
                node.expand()
            return
        # Loaded but no matching child → target absent from this tree.
        # Settle for the closest ancestor we *do* have.
        self._move_cursor_to(node)
        self._navigation_target = None

    def _find_deepest_loaded(self, target: str) -> TreeNode:
        """Walk down loaded nodes whose data is a prefix of target."""
        node = self.root
        while True:
            next_path = _next_segment(node.data or "", target)
            if next_path is None:
                return node
            match = self._match_child(node, next_path, target)
            if match is None:
                return node
            node = match
            if (node.data or "") == target:
                return node

    def _match_child(self, node: TreeNode, next_path: str, target: str):
        """Find the child of ``node`` on the walk toward ``target``.

        Exact ``data == next_path`` first. Then a final-segment fallback:
        the workspace tree keys directory nodes by *client* syntax
        (``//<client>/…``) but file leaves by *depot* path
        (``//depot/…``), so the last segment of a client-syntax target
        never exact-matches its depot-keyed leaf. When ``next_path`` is
        the final segment (``== target``) we match a leaf by basename so
        navigation lands on the file, not its containing directory. Dir
        nodes always match exactly (same namespace), so this never
        mis-routes mid-walk, and the depot tree (uniform namespace) hits
        the exact path first and never reaches the fallback.
        """
        for child in node.children:
            if (child.data or "") == next_path:
                return child
        if next_path == target:
            want = _basename(target)
            for child in node.children:
                if not child.allow_expand and _basename(child.data or "") == want:
                    return child
        return None

    def _move_cursor_to(self, node: TreeNode) -> None:
        """Move the cursor to ``node`` without firing NodeSelected.

        ``select_node`` would post NodeSelected which on a file leaf
        triggers the file viewer — exactly what we don't want during
        a programmatic position mirror. We use the line index instead;
        ``_tree_lines`` is private but stable across Textual 0.x. Falls
        back to ``select_node`` (and accepts the side effect) if that
        attribute isn't available.
        """
        try:
            lines = self._tree_lines  # type: ignore[attr-defined]
            for idx, line in enumerate(lines):
                line_node = getattr(line, "node", None)
                if line_node is node:
                    self.cursor_line = idx
                    self.scroll_to_node(node)
                    return
        except (AttributeError, TypeError):
            pass
        # Fallback — last-resort, may post NodeSelected.
        self.select_node(node)
        self.scroll_to_node(node)

    # --- subclass hook -----------------------------------------------------

    def _fetch_node_data(self, path: str) -> tuple[list[str], list[dict]]:
        """Return ``(subdir_paths, file_rows)`` for ``path``.

        Subdirectory entries are full P4 paths (e.g. ``//depot/foo``).
        File rows are dicts as returned by P4 — each subclass decides which
        keys it cares about in ``_format_file``.
        """
        raise NotImplementedError

    # --- expand / load -----------------------------------------------------

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node: TreeNode = event.node
        path = node.data
        if not path or path in self._loaded:
            return
        self._loaded.add(path)
        self._begin_loading_indicator(node)
        self._fetch_children(node, path)

    @work(thread=True)
    def _fetch_children(self, node: TreeNode, path: str) -> None:
        dirs, files = self._fetch_node_data(path)
        self.app.call_from_thread(self._populate, node, dirs, files)

    # --- loading indicator ----------------------------------------------

    def _begin_loading_indicator(self, node: TreeNode) -> None:
        """Append a spinner marker to ``node``'s label until populate().

        On a slow server, a depot expand can stall several seconds
        with no visible feedback; the user can't tell if the app is
        loading or hung. The marker animates on a shared timer that
        runs only while at least one node is loading.
        """
        key = id(node)
        if key in self._loading_originals:
            return
        try:
            self._loading_originals[key] = node.label
        except Exception:  # noqa: BLE001
            return
        self._render_loading_label(node)
        self._ensure_loading_timer()

    def _end_loading_indicator(self, node: TreeNode) -> None:
        key = id(node)
        original = self._loading_originals.pop(key, None)
        if original is None:
            return
        try:
            node.set_label(original)
        except Exception:  # noqa: BLE001
            pass
        if not self._loading_originals:
            self._stop_loading_timer()

    def _render_loading_label(self, node: TreeNode) -> None:
        frame_ch = _LOADING_FRAMES[
            self._loading_frame % len(_LOADING_FRAMES)
        ]
        original = self._loading_originals.get(id(node))
        if original is None:
            return
        try:
            if isinstance(original, Text):
                base = original.copy()
            else:
                base = Text(str(original))
            base.append(f"  {frame_ch}", style=_LOADING_STYLE)
            node.set_label(base)
        except Exception:  # noqa: BLE001
            pass

    def _ensure_loading_timer(self) -> None:
        if self._loading_timer is not None:
            return
        try:
            self._loading_timer = self.set_interval(
                _LOADING_INTERVAL, self._tick_loading_frames,
            )
        except Exception:  # noqa: BLE001
            # If we can't set a timer (no event loop, headless test),
            # the marker just stays on its first frame — still a
            # valid signal that loading is in progress.
            self._loading_timer = None

    def _stop_loading_timer(self) -> None:
        if self._loading_timer is None:
            return
        try:
            self._loading_timer.stop()
        except Exception:  # noqa: BLE001
            pass
        self._loading_timer = None

    def _tick_loading_frames(self) -> None:
        if not self._loading_originals:
            self._stop_loading_timer()
            return
        self._loading_frame = (
            self._loading_frame + 1
        ) % len(_LOADING_FRAMES)
        # Snapshot keys so a concurrent populate() removing entries
        # from the dict doesn't break iteration.
        for key in list(self._loading_originals.keys()):
            # Re-look up the node by walking the tree — we only kept
            # the id() above. In practice the loading nodes are still
            # part of the live tree, so just refresh the few that
            # are still recorded by walking from root.
            node = self._find_node_by_id(self.root, key)
            if node is not None:
                self._render_loading_label(node)

    def _find_node_by_id(
        self, node: TreeNode, target_id: int,
    ) -> TreeNode | None:
        if id(node) == target_id:
            return node
        for child in node.children:
            hit = self._find_node_by_id(child, target_id)
            if hit is not None:
                return hit
        return None

    def _populate(
        self,
        node: TreeNode,
        dirs: list[str],
        files: list[dict],
    ) -> None:
        # Lift the loading marker before children appear; restores the
        # pre-load label even if the worker came back with zero rows
        # (an empty folder must not stay stuck in the spinner state).
        self._end_loading_indicator(node)
        node.remove_children()
        for d in sorted(dirs):
            node.add(self._decorate(self._format_dir(d), d),
                     data=d, allow_expand=True)
        for f in sorted(files, key=self._file_sort_key):
            label, data = self._format_file(f)
            node.add_leaf(self._decorate(label, data), data=data)
        # When triggered via reload_node(), restore the cursor onto the new
        # node whose data matches the pre-reload cursor — otherwise the
        # cursor falls back to the root and subsequent shortcuts no-op.
        target = getattr(self, "_pending_cursor_path", None)
        if target:
            self._pending_cursor_path = None
            for child in node.children:
                if child.data == target:
                    self._move_cursor_to(child)
                    break

        # Refresh-root saved every previously-expanded path; re-expand
        # any child whose data is in that set so the user's open
        # subtrees survive the refresh. Each expand() triggers its own
        # lazy load → _populate → recursive expansion until the set is
        # drained (or the remaining paths no longer exist on the
        # server, in which case they harmlessly stay in the set).
        pending = getattr(self, "_pending_expansions", None)
        if pending:
            for child in node.children:
                data = child.data
                if data and data in pending and child.allow_expand:
                    pending.discard(data)
                    if not child.is_expanded:
                        child.expand()

        # If a programmatic navigation is mid-walk and this populate
        # delivered the level it was waiting on, resume the walk.
        nav_target = getattr(self, "_navigation_target", None)
        if nav_target:
            np = node.data or ""
            if nav_target == np or _next_segment(np, nav_target) is not None:
                self._navigate_step()

    # --- optimistic per-row action marker (perceived performance) --------

    # Transient glyph prefixed onto a file leaf the moment a
    # status-changing action is dispatched against it, so the row lights
    # up immediately on a laggy link instead of sitting unchanged until
    # the server confirms. We deliberately do NOT predict the end-state
    # marker (that would risk showing a lie) — this just means "an action
    # is in flight on this row". The post-action ``reload_node`` rebuilds
    # the leaf from fresh ``fstat``, which is the reconcile / rollback.
    PENDING_GLYPH = "⟳ "

    def mark_node_pending(self, node) -> None:
        """Flag a file-leaf node as 'action in flight'. No-op for
        folders / root (they reload wholesale) and for an already-flagged
        node."""
        try:
            if node is None or node is self.root or node.allow_expand:
                return
            plain = self._plain(node.label)
            if plain.startswith(self.PENDING_GLYPH):
                return
            node.set_label(self.PENDING_GLYPH + plain)
        except Exception:  # noqa: BLE001
            # Purely cosmetic — never let it disturb the action itself.
            pass

    def clear_node_pending(self, node) -> None:
        """Strip the pending glyph if present (used on an action path
        that returns before the reconciling reload, e.g. a failed
        pre-step)."""
        try:
            if node is None:
                return
            plain = self._plain(node.label)
            if plain.startswith(self.PENDING_GLYPH):
                node.set_label(plain[len(self.PENDING_GLYPH):])
        except Exception:  # noqa: BLE001
            pass

    # --- multi-select marks (item 4) --------------------------------------

    @staticmethod
    def _plain(label) -> str:
        return label.plain if hasattr(label, "plain") else str(label)

    def _decorate(self, label: str, data: str) -> str:
        """Prefix the mark glyph onto a label when its path is marked."""
        return f"{self.MARK_GLYPH}{label}" if data in self._marked else label

    def action_toggle_mark(self) -> None:
        node = self.cursor_node
        if node is None or node is self.root or not node.data:
            return
        data = str(node.data)
        plain = self._plain(node.label)
        if data in self._marked:
            del self._marked[data]
            if plain.startswith(self.MARK_GLYPH):
                node.set_label(plain[len(self.MARK_GLYPH):])
        else:
            self._marked[data] = bool(node.allow_expand)
            if not plain.startswith(self.MARK_GLYPH):
                node.set_label(self.MARK_GLYPH + plain)
        try:
            self.app.notify(f"{len(self._marked)} marked", timeout=2)
        except Exception:  # noqa: BLE001
            pass

    def action_clear_marks(self) -> None:
        """Escape — clear all marks (no-op + silent when there are none)."""
        if not self._marked:
            return
        n = len(self._marked)
        self.clear_marks()
        try:
            self.app.notify(f"Cleared {n} mark(s).", timeout=2)
        except Exception:  # noqa: BLE001
            pass

    def clear_marks(self) -> None:
        """Drop all marks and strip the glyph from every visible node."""
        if not self._marked:
            return
        self._marked.clear()

        def strip(n) -> None:
            plain = self._plain(n.label)
            if plain.startswith(self.MARK_GLYPH):
                n.set_label(plain[len(self.MARK_GLYPH):])
            for child in n.children:
                strip(child)

        strip(self.root)

    def marked_specs(self) -> list[str]:
        """Resolved targets for the marked set (dir → ``<path>/...``)."""
        return sorted(
            f"{path}/..." if is_dir else path
            for path, is_dir in self._marked.items()
        )

    def has_marks(self) -> bool:
        return bool(self._marked)

    # --- permalink (item 11) ----------------------------------------

    def action_copy_permalink(self) -> None:
        """Ask the app to mint + copy a permalink for the cursor path."""
        self._emit_path_action("copy_permalink")

    def action_bookmark_add(self) -> None:
        """Ask the app to bookmark the cursor path (permalink-backed)."""
        self._emit_path_action("bookmark_add")

    def _emit_path_action(self, action: str) -> None:
        node = self.cursor_node
        if node is None or node is self.root or not node.data:
            return
        from ..messages import FileActionRequested
        self.post_message(
            FileActionRequested(
                action=action,
                target=str(node.data),
                source_node=node,
                is_directory=bool(node.allow_expand),
            )
        )

    # --- formatting hooks (override as needed) ----------------------------

    def _format_dir(self, depot_dir: str) -> str:
        return truncate_cells(depot_dir.rsplit("/", 1)[-1], LABEL_MAX_CELLS)

    def _file_sort_key(self, file_row: dict) -> str:
        return file_row.get("depotFile", "") or file_row.get("clientFile", "")

    def _format_file(self, file_row: dict) -> tuple[str, str]:
        depot_file = file_row.get("depotFile", "")
        name = depot_file.rsplit("/", 1)[-1] if depot_file else "?"
        rev = file_row.get("rev", "")
        label = f"{name}#{rev}" if rev else name
        return truncate_cells(label, LABEL_MAX_CELLS), depot_file

    # --- arrow-key actions -------------------------------------------------

    def action_expand_node(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and not node.is_expanded:
            node.expand()
        elif node.is_expanded and node.children:
            # Already expanded: drop into the first child.
            self.select_node(node.children[0])
            self.scroll_to_node(node.children[0])

    def action_collapse_node(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.is_expanded:
            node.collapse()
        elif node.parent is not None and node.parent is not self.root.parent:
            # Leaf or already-collapsed dir: hop up to parent.
            self.select_node(node.parent)
            self.scroll_to_node(node.parent)

    # --- Perforce-aware clipboard (Ctrl+C / Ctrl+X / Ctrl+V) ----------
    #
    # The tree posts a P4ClipboardAction message to the App; the App
    # owns the single-slot clipboard state. Ctrl+C stores the cursor
    # path as "copy", Ctrl+X stores it as "cut" (= future move), and
    # Ctrl+V uses the cursor path as the destination — the App pairs
    # it with the stored source, runs the corresponding p4 verb, and
    # auto-submits the resulting changelist.

    def action_p4_clipboard_copy(self) -> None:
        self._post_clipboard_action("copy")

    def action_p4_clipboard_cut(self) -> None:
        self._post_clipboard_action("cut")

    def action_p4_clipboard_paste(self) -> None:
        self._post_clipboard_action("paste")

    def _post_clipboard_action(self, op: str) -> None:
        from ..messages import P4ClipboardAction
        node = self.cursor_node
        if node is None or not node.data:
            self.app.notify(
                "Move the cursor onto a depot path first.", timeout=3,
            )
            return
        path = str(node.data)
        if not path.startswith("//"):
            self.app.notify(
                "Clipboard works on depot / workspace paths only.",
                timeout=3,
            )
            return
        is_root = node is self.root
        is_dir = bool(node.allow_expand) or is_root
        self.post_message(
            P4ClipboardAction(op=op, path=path, is_directory=is_dir),
        )

    # --- quick rename (F2) ---------------------------------------------

    def action_quick_rename(self) -> None:
        """F2 — pop the lightweight rename modal for the cursor node
        and post a ``quick_rename`` FileActionRequested. The App's
        handler opens the modal, then on confirmation runs a worker
        that creates a CL, opens-for-edit, moves, and auto-submits.
        """
        from ..messages import FileActionRequested
        node = self.cursor_node
        if node is None or not node.data:
            self.app.notify(
                "Move the cursor onto a file or folder first.",
                timeout=3,
            )
            return
        if node is self.root:
            self.app.notify(
                "Can't rename the tree root.", timeout=3,
            )
            return
        path = str(node.data)
        # Block top-level depots (`//depot`) and the workspace root
        # (`//<client>`) — those are admin-only ops, not `p4 move`s.
        if not path.startswith("//") or "/" not in path[2:]:
            self.app.notify(
                "Top-level depots / workspace roots can't be renamed "
                "this way.", timeout=4,
            )
            return
        is_dir = bool(node.allow_expand)
        self.post_message(
            FileActionRequested(
                action="quick_rename",
                target=path,
                source_node=node,
                is_directory=is_dir,
            )
        )

    # --- filter ---------------------------------------------------------

    def action_start_filter(self) -> None:
        """Slash key — ask the App to show the floating filter input
        for this tree. The App composes a single TreeFilter overlay
        and routes apply_filter() back here as the user types."""
        from ..messages import TreeFilterRequested
        self.post_message(TreeFilterRequested(self))

    def apply_filter(self, query: str) -> None:
        """Hide loaded nodes whose label doesn't match ``query`` and
        whose descendants don't match either. Empty query restores
        every display flag.

        Walks only nodes already loaded — a hidden subtree's unloaded
        levels stay unevaluated; expanding them later re-shows them.
        Filter is a "live snapshot" view, not a long-running predicate.
        """
        q = (query or "").strip().lower()
        if not q:
            self._show_all(self.root)
            return
        self._apply_filter_recursive(self.root, q)
        # Always keep the root visible — filtering it would leave the
        # widget with nothing on screen.
        try:
            self.root.display = True
        except Exception:  # noqa: BLE001
            pass

    def _apply_filter_recursive(self, node: TreeNode, q: str) -> bool:
        """Returns True if ``node`` (or any descendant) matches."""
        try:
            label_text = str(node.label).lower()
        except Exception:  # noqa: BLE001
            label_text = ""
        self_match = q in label_text
        any_child_match = False
        for child in list(node.children):
            if self._apply_filter_recursive(child, q):
                any_child_match = True
        keep = self_match or any_child_match
        try:
            node.display = bool(keep)
        except Exception:  # noqa: BLE001
            pass
        if any_child_match and not node.is_expanded:
            # Auto-expand so matching descendants are actually visible.
            node.expand()
        return keep

    def _show_all(self, node: TreeNode) -> None:
        try:
            node.display = True
        except Exception:  # noqa: BLE001
            pass
        for child in list(node.children):
            self._show_all(child)
