"""Depot tree: browses the server-side depot namespace.

Root is ``//`` and lists every depot via ``p4 depots``. Below each depot we
use ``p4 dirs`` and ``p4 files``.

Each label is rendered dim if the path is *not* covered by the current
client's View — that is, "shows in the depot tree but isn't mapped into
this workspace, so a normal sync would never bring it to local disk".
The full View is fetched once and matched client-side, so the dim
overlay costs no extra per-node RPC.
"""
from __future__ import annotations

import re

from rich.text import Text
from textual.binding import Binding

from ..messages import FileActionRequested
from ..utils import truncate_cells
from .context_menu import ContextMenuItem, ContextMenuModal, SEPARATOR
from .p4_tree import LABEL_MAX_CELLS, P4Tree


# -------------------- client View parsing & matching ---------------------
#
# A client View is a list of mapping lines:
#
#     [+|-]//depot/pattern  //client/pattern
#
# Patterns use ``...`` (matches any chars including slashes), ``*``
# (matches anything except slash within one segment), and ``%%N``
# (positional capture, treated like ``*`` for matching purposes).
#
# Last-match-wins is the canonical P4 semantics: a later ``-`` line
# excludes paths a previous include allowed; a later ``+`` line re-
# includes paths a previous ``-`` excluded.

_WILDCARD_RE = re.compile(r"(\.\.\.|\*|%%[1-9])")


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a P4 depot-side view pattern to a regex matching the
    whole depot path. ``...`` → ``.*``; ``*`` and ``%%N`` → ``[^/]*``.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("...", i):
            out.append(".*")
            i += 3
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif (pattern.startswith("%%", i)
              and i + 2 < n and pattern[i + 2].isdigit()):
            out.append("[^/]*")
            i += 3
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _fixed_prefix(pattern: str) -> str:
    """Literal leading prefix of ``pattern`` before any wildcard."""
    m = _WILDCARD_RE.search(pattern)
    return pattern[: m.start()] if m else pattern


def _parse_view(view_lines: list[str]) -> list[tuple[bool, str, re.Pattern[str]]]:
    """Parse raw View lines into ``(is_include, raw_depot_pattern, regex)``.

    Skips malformed or empty entries. The client-side half of the
    mapping is irrelevant for "is this depot path mapped?" — only the
    depot pattern matters.
    """
    rules: list[tuple[bool, str, re.Pattern[str]]] = []
    for line in view_lines:
        s = line.strip()
        if not s:
            continue
        # The depot side comes first. Handle quoted patterns that may
        # contain spaces; the common case is a bare ``//...`` token.
        if s.startswith('"'):
            end = s.find('"', 1)
            if end == -1:
                continue
            depot_part = s[1:end]
        else:
            depot_part = s.split(None, 1)[0]
        sign = ""
        if depot_part[:1] in ("-", "+"):
            sign, depot_part = depot_part[0], depot_part[1:]
        try:
            rx = _pattern_to_regex(depot_part)
        except re.error:
            continue
        rules.append((sign != "-", depot_part, rx))
    return rules


def _file_is_mapped(
    file_path: str,
    rules: list[tuple[bool, str, re.Pattern[str]]],
) -> bool:
    """Last-match-wins evaluation of the View for a file path."""
    matched = False
    for is_include, _pat, rx in rules:
        if rx.match(file_path):
            matched = is_include
    return matched


def _dir_overlaps_view(
    dir_path: str,
    rules: list[tuple[bool, str, re.Pattern[str]]],
) -> bool:
    """Conservative: True iff *any* include rule could match a path
    at or under ``dir_path/``.

    Compares the rule's literal fixed prefix to the dir path:

    * if the rule's prefix lives at-or-below ``dir_path`` → descendants
      of ``dir_path`` can match → True;
    * if ``dir_path`` lives at-or-below the rule's prefix and the rule
      still has wildcards after that prefix → those wildcards can
      absorb the deeper segments → True.

    May produce false positives (e.g. ``//depot/foo/*.cpp`` for the
    dir ``//depot/foo/bar``) which only mean "shows in normal color
    when it might still be empty" — acceptable for a visual hint
    that doesn't gate any action.
    """
    dp = dir_path.rstrip("/")
    if not dp or dp == "//":
        return any(inc for inc, _p, _r in rules)
    for is_include, pat, _rx in rules:
        if not is_include:
            continue
        fp = _fixed_prefix(pat).rstrip("/")
        if fp == dp or fp.startswith(dp + "/"):
            return True
        if dp == fp or dp.startswith(fp + "/"):
            rest = pat[len(fp):]
            if "..." in rest or "*" in rest or "%%" in rest:
                return True
    return False


class DepotTree(P4Tree):
    BINDINGS = [
        Binding("m", "show_context_menu", "Menu"),
        Binding("shift+f10", "show_context_menu", show=False),
        Binding("ㅡ", "show_context_menu", show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # View rules are pulled lazily on the first label render so
        # initial connect / set_root_path / bootstrap don't pay an
        # extra RPC before the tree is even visible. ``None`` =
        # "not fetched yet"; ``[]`` = fetched but empty (= unmapped
        # client) — treat both as "no dim" to avoid lighting the
        # whole tree up in dim on first paint while we wait.
        self._view_rules: list[tuple[bool, str, re.Pattern[str]]] | None = None

    def _ensure_view_rules(self) -> None:
        if self._view_rules is not None:
            return
        try:
            view = self._p4.fetch_client_view()
        except Exception:  # noqa: BLE001
            view = []
        self._view_rules = _parse_view(view)
        # Diagnostic: drop a single log entry so the user can verify
        # in the Log panel that the view was actually fetched (vs
        # silently falling back to "no dim"). One-shot — subsequent
        # calls hit the cache and won't re-log.
        try:
            from rich.text import Text as _T  # noqa: F401
            cmd_log = getattr(self.app, "cmd_log", None)
            if cmd_log is not None and hasattr(cmd_log, "log_info"):
                include_n = sum(1 for r in self._view_rules if r[0])
                exclude_n = sum(1 for r in self._view_rules if not r[0])
                cmd_log.log_info(
                    f"DepotTree: client View loaded — "
                    f"{include_n} include / {exclude_n} exclude rule(s)",
                )
        except Exception:  # noqa: BLE001
            pass

    def invalidate_view_cache(self) -> None:
        """Force a re-fetch of the client View on the next label render.

        Called when the workspace changes (e.g. via profile switch).
        Existing tree nodes keep their current labels until the next
        ``refresh_root`` / ``reload_node``.
        """
        self._view_rules = None

    def _fetch_node_data(self, path: str) -> tuple[list[str], list[dict]]:
        if path == "//":
            depots = self._p4.depots()
            return [f"//{d['name']}" for d in depots], []
        glob = f"{path}/*"
        return self._p4.dirs(glob), self._p4.files(glob)

    # --- label rendering: dim when path isn't in the client's View ----

    def _label_for(self, text: str, mapped: bool):
        """Return a tree-acceptable label — bright ``Text`` if mapped
        into the workspace's client View, faded ``grey50`` if not.

        We *always* wrap the label in a Rich ``Text``, even for the
        mapped branch, because some Textual / terminal combinations
        render a plain ``str`` returned next to a styled sibling
        slightly *less* prominently than the styled one (the layout
        engine quietly adopts the parent widget's faded base style
        in that case). Returning a Text for both keeps the rendering
        comparable: bright = no style override (inherits widget
        colour), dim = explicit grey foreground.

        ``grey50`` is intentionally more visibly faded than Rich's
        ``dim`` attribute — ``dim`` is a SGR flag terminals interpret
        loosely (Apple Terminal in particular renders it almost
        identically to bright), whereas ``grey50`` lands as a
        concrete 24-bit colour the user can see at a glance.
        """
        if mapped:
            return Text(text)
        return Text(text, style="grey50")

    def _format_dir(self, depot_dir: str):
        self._ensure_view_rules()
        text = truncate_cells(depot_dir.rsplit("/", 1)[-1], LABEL_MAX_CELLS)
        # If we haven't been able to fetch the View, render normally —
        # better than incorrectly dimming the entire tree.
        if not self._view_rules:
            return text
        return self._label_for(
            text, _dir_overlaps_view(depot_dir, self._view_rules),
        )

    def _format_file(self, file_row: dict) -> tuple:
        self._ensure_view_rules()
        depot_file = file_row.get("depotFile", "")
        name = depot_file.rsplit("/", 1)[-1] if depot_file else "?"
        rev = file_row.get("rev", "")
        text = truncate_cells(
            f"{name}#{rev}" if rev else name, LABEL_MAX_CELLS,
        )
        if not self._view_rules:
            return text, depot_file
        return (
            self._label_for(
                text, _file_is_mapped(depot_file, self._view_rules),
            ),
            depot_file,
        )

    # --- context menu ----------------------------------------------------

    def action_show_context_menu(self) -> None:
        node = self.cursor_node
        if node is None or not node.data:
            self.app.notify(
                "Move the cursor onto a depot path first.", timeout=3,
            )
            return

        path = str(node.data)
        is_dir = bool(node.allow_expand)
        is_root = (node is self.root) or path == "//"

        if is_root:
            target_label = "(depot root)"
            items = self._root_menu_items()
        else:
            target_label = path.rsplit("/", 1)[-1] or path
            items = self._node_menu_items(is_directory=is_dir)

        def on_close(action_id: str | None,
                     n=node, p=path, d=is_dir) -> None:
            if not action_id:
                return
            self._dispatch(action_id, n, p, d)

        self.app.push_screen(
            ContextMenuModal(items, title=target_label),
            on_close,
        )

    # --- menu definitions ------------------------------------------------

    def _root_menu_items(self) -> list[ContextMenuItem]:
        return [
            ContextMenuItem("Find File…", "find_file", "Ctrl+Shift+F"),
            ContextMenuItem(
                "Search In This Folder…", "search_in_folder", "Ctrl+F",
            ),
            ContextMenuItem("Refresh root", "refresh_root", "F5"),
        ]

    def _node_menu_items(self, is_directory: bool) -> list[ContextMenuItem]:
        items: list[ContextMenuItem] = []
        if not is_directory:
            items.append(ContextMenuItem(
                "View File", "view", "Enter",
            ))
            items.append(ContextMenuItem(
                "Annotate / Blame…", "annotate", "",
            ))
            items.append(ContextMenuItem(
                "Time-lapse View…", "timelapse", "",
            ))
            items.append(ContextMenuItem(
                "Revision Graph…", "rev_graph", "",
            ))
            items.append(ContextMenuItem(
                "File Properties…", "file_props", "",
            ))
        items.extend([
            ContextMenuItem(
                "Folder History" if is_directory else "File History",
                "history", "Ctrl+T",
            ),
            SEPARATOR,
            ContextMenuItem("Get Latest Revision", "sync",
                            "Ctrl+Shift+G"),
            ContextMenuItem("Get Latest (chunked + resumable)",
                            "chunked_sync", ""),
            ContextMenuItem("Get Revision…", "get_revision", ""),
            SEPARATOR,
            ContextMenuItem("Open With…", "open_with", ""),
            ContextMenuItem("Show In…", "show_in", ""),
            ContextMenuItem("Open Command Window Here", "open_cmd", ""),
            SEPARATOR,
            ContextMenuItem("Rename/Move…", "rename", ""),
            ContextMenuItem("Mark for Delete", "delete", ""),
            SEPARATOR,
            ContextMenuItem("Copy Depot Path", "copy_path", ""),
            ContextMenuItem("Copy Swarm URL", "copy_swarm", ""),
            ContextMenuItem("Copy Permalink", "copy_permalink", "Alt+C"),
            ContextMenuItem("Bookmark This Path", "bookmark_add", "Ctrl+B"),
            ContextMenuItem("Find File…", "find_file", "Ctrl+Shift+F"),
            ContextMenuItem(
                "Search In This Folder…", "search_in_folder", "Ctrl+F",
            ),
            ContextMenuItem("Refresh", "refresh_node", ""),
        ])
        return items

    # --- dispatch --------------------------------------------------------

    # Filesystem hand-offs, clipboard copies, and rename operate on the
    # path itself; sync / chunked_sync recurse with /... on directories.
    _NO_RECURSE_ACTIONS = frozenset({
        "show_in", "open_cmd", "open_with",
        "copy_path", "copy_swarm", "copy_permalink", "bookmark_add", "rename",
        "annotate", "timelapse", "rev_graph", "file_props",
    })

    # Actions handled by the App via FileActionRequested.
    # ``delete`` reuses the App-level confirm-then-`p4 delete` path
    # that WorkspaceTree already uses; on a directory the dispatcher
    # below appends ``/...`` so the delete recurses.
    _APP_ACTIONS = frozenset({
        "sync", "chunked_sync", "get_revision",
        "show_in", "open_cmd", "open_with",
        "copy_path", "copy_swarm", "copy_permalink", "bookmark_add",
        "rename", "delete",
        "annotate", "timelapse", "rev_graph", "file_props",
    })

    # Actions that make sense over a multi-selection as one p4 call
    # (Get Latest, Mark for Delete). Others are per-node (open a dialog,
    # a viewer, etc.) and stay single-target.
    _BULK_ELIGIBLE = frozenset({"sync", "delete"})

    # Annotate is path-as-is — no /... suffix even on directories
    # (the App rejects directory annotate before it reaches p4).

    def _dispatch(self, action_id: str, node, path: str,
                  is_directory: bool) -> None:
        app = self.app
        if action_id == "find_file":
            app.action_find_file()
            return
        if action_id == "search_in_folder":
            # Pre-fill Fast Search with the depot path so the user
            # immediately sees results scoped to (or anchored at) the
            # node they right-clicked. Root → empty query.
            seed = "" if (node is self.root) else path
            app.action_open_search(initial_query=seed)
            return
        if action_id in ("refresh_root", "refresh"):
            self.refresh_root()
            app.notify("Depot tree refreshed.", timeout=3)
            return
        if action_id == "refresh_node":
            self.reload_node(node)
            return
        if action_id == "history":
            # Reuse the App-level Ctrl+T handler. It reads cursor_node
            # from the focused tree, which is us, so just call it.
            app.action_show_folder_history()
            return
        if action_id == "view":
            if is_directory:
                app.notify("View applies to file leaves only.", timeout=3)
                return
            # Direct invocation of the App's worker — bypasses the
            # Tree.NodeSelected event which would otherwise also fire.
            app._open_file_viewer(path)
            return
        # When nodes are marked, the actions that are safe as a single
        # multi-file p4 call apply to the whole selection (item 4 bulk
        # routing, extended to the depot tree).
        if action_id in self._BULK_ELIGIBLE and self.has_marks():
            from ..messages import BulkFileActionRequested
            self.post_message(
                BulkFileActionRequested(action_id, self.marked_specs())
            )
            self.clear_marks()
            return
        if action_id in self._APP_ACTIONS:
            if action_id in self._NO_RECURSE_ACTIONS:
                target = path
            else:
                target = f"{path}/..." if is_directory else path
            self.post_message(
                FileActionRequested(
                    action=action_id,
                    target=target,
                    source_node=node,
                    is_directory=is_directory,
                )
            )
            return
        # Unknown action — no-op with a hint.
        app.notify(f"Unhandled depot menu action: {action_id}",
                   severity="warning", timeout=4)
