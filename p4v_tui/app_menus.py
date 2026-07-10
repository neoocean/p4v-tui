"""Context-menu builders for :class:`P4VApp`.

Extracted verbatim from app.py (refactor 2/n). These methods build and
show the Pending / Submitted / History context menus and the Pending
panel-level (Shift+M) menu. They are pure UI-assembly methods that call
back into P4VApp via ``self`` through the MRO, so nothing here needs a
direct P4VApp import.
"""
from __future__ import annotations


from textual.widgets import (
    DataTable,
)

from .state import save_state
from .widgets.context_menu import ContextMenuItem, ContextMenuModal, SEPARATOR



def build_pending_menu(
    change: str,
    *,
    is_default: bool,
    is_remote: bool,
    row_client: str,
) -> tuple[list[ContextMenuItem], str]:
    """Build the Pending-CL context menu items + title for one row.

    Pure: depends only on the CL identity and its local/default/remote
    status, never on the running app. This is what makes the menu
    gating unit-testable. ``row_client`` is the owning workspace name
    (only used to annotate the title of a remote CL).
    """
    items: list[ContextMenuItem] = []

    if is_remote:
        # Remote CLs (owned by this user but living in another of
        # their workspaces) can only safely be inspected, have
        # their description edited (spec-level, server-side), have
        # an empty shell deleted, or be printed. Submit / revert /
        # shelve / unshelve / re-resolve / diff-have / move-files
        # all bind opened files in the current client and would
        # silently no-op or error. Hide them from the menu rather
        # than letting the user click into a confusing failure.
        items.append(ContextMenuItem(
            f"View Pending Changelist '{change}' (read-only)",
            "view_remote", "",
        ))
        items.append(ContextMenuItem(
            f"Edit Pending Changelist '{change}' Description…",
            "edit_desc", "",
        ))
        items.append(ContextMenuItem(
            f"Delete (empty) Pending Changelist '{change}'…",
            "delete_cl", "",
        ))
        # Swarm review URL for the remote (other-workspace) CL.
        if change and change != "default":
            items.extend([
                SEPARATOR,
                ContextMenuItem(
                    f"Copy Swarm Review URL for '{change}'",
                    "copy_swarm_cl", "",
                ),
                ContextMenuItem(
                    f"Open '{change}' in Swarm (browser)",
                    "open_swarm_cl", "",
                ),
            ])
        items.extend([
            SEPARATOR,
            ContextMenuItem("New Pending Changelist…",
                            "new_pending_cl", "Ctrl+N"),
            ContextMenuItem(
                f"Print Preview Pending Changelist '{change}'…",
                "print_preview", "",
            ),
            ContextMenuItem(
                f"Print Pending Changelist '{change}'…",
                "print", "Ctrl+P",
            ),
            SEPARATOR,
            ContextMenuItem("Refresh All Pending Changelists",
                            "refresh_pending", "F5"),
            ContextMenuItem(
                f"Refresh Pending Changelist '{change}'",
                "refresh_one", "",
            ),
        ])
        menu_title = (
            f"Pending CL {change} · ↗ remote workspace '{row_client}'"
        )
    else:
        # p4v shows Submit on the default CL too — clicking it tells
        # the user to promote first, but the entry is always present.
        items.append(ContextMenuItem(
            f"Submit Changelist '{change}'…", "submit", "Ctrl+S",
        ))
        if not is_default:
            items.append(ContextMenuItem(
                f"Submit & Resolve '{change}'…", "submit_resolve", "",
            ))
        items.append(ContextMenuItem(
            f"View Pending Changelist '{change}'", "view_cl", "",
        ))
        items.extend([
            ContextMenuItem(
                f"Revert Unchanged Files in '{change}'",
                "revert_unchanged", "",
            ),
            ContextMenuItem(
                f"Revert Files in '{change}'", "revert_cl", "Ctrl+R",
            ),
            ContextMenuItem(
                f"Re-resolve Previously Resolved Files in '{change}'…",
                "re_resolve", "",
            ),
            ContextMenuItem(
                f"Move All Files in '{change}' to Another Changelist…",
                "move_all_to", "",
            ),
            ContextMenuItem(
                f"Diff Files in '{change}' Against Have Revisions",
                "diff_have", "Ctrl+D",
            ),
        ])
        if not is_default:
            items.append(ContextMenuItem(
                f"Edit Pending Changelist '{change}' Description…",
                "edit_desc", "",
            ))
            items.append(ContextMenuItem(
                f"Delete (empty) Pending Changelist '{change}'…",
                "delete_cl", "",
            ))
        # --- Shelf operations
        if not is_default:
            items.extend([
                SEPARATOR,
                ContextMenuItem(
                    f"Shelve Files in '{change}'", "shelve", "",
                ),
                ContextMenuItem(
                    f"Update Shelved Files in '{change}' (force)",
                    "shelve_update", "",
                ),
                ContextMenuItem(
                    f"Unshelve Files from '{change}' into another CL…",
                    "unshelve", "",
                ),
                ContextMenuItem(
                    f"Delete Shelved Files in '{change}'",
                    "shelve_delete", "",
                ),
            ])
        # Swarm review URL for the local pending CL.
        if change and change != "default":
            items.extend([
                SEPARATOR,
                ContextMenuItem(
                    f"Copy Swarm Review URL for '{change}'",
                    "copy_swarm_cl", "",
                ),
                ContextMenuItem(
                    f"Open '{change}' in Swarm (browser)",
                    "open_swarm_cl", "",
                ),
            ])
        items.extend([
            SEPARATOR,
            ContextMenuItem("New Pending Changelist…",
                            "new_pending_cl", "Ctrl+N"),
            ContextMenuItem(
                f"Print Preview Pending Changelist '{change}'…",
                "print_preview", "",
            ),
            ContextMenuItem(
                f"Print Pending Changelist '{change}'…",
                "print", "Ctrl+P",
            ),
            SEPARATOR,
            ContextMenuItem("Refresh All Pending Changelists",
                            "refresh_pending", "F5"),
            ContextMenuItem(
                f"Refresh Pending Changelist '{change}'",
                "refresh_one", "",
            ),
        ])
        menu_title = f"Pending CL {change}"
    return items, menu_title


class _MenuMixin:
    # --- panel context menus ---------------------------------------------

    def action_show_panel_menu(self) -> None:
        """Open the right-side panel's context menu based on focus.

        Tree widgets own their own 'm' binding (which takes precedence
        over this app-level one when they're focused). This handler is
        meant for the pending / submitted tables; for anything else we
        surface a notification so the keypress isn't silently ignored.
        """
        f = self.focused
        wid = getattr(f, "id", None) if f is not None else None
        if wid == "pending_table":
            self._show_pending_menu()
            return
        if wid == "submitted_table":
            self._show_submitted_menu()
            return
        if wid == "history_table":
            self._show_history_menu()
            return
        # Depot tree has its own widget-level 'm' binding (see
        # DepotTree.action_show_context_menu), so this app-level handler
        # never sees it when the tree itself is focused. We don't need
        # a fallback here.
        # Catch-all: report what the focus is so users aren't confused
        # by a silent no-op.
        self.notify(
            f"No context menu available for {type(f).__name__ if f else 'this view'}.",
            timeout=3,
        )

    def action_show_panel_area_menu(self) -> None:
        """Open the right-pane *panel-level* (empty-area equivalent)
        menu — the p4v counterpart is right-clicking on empty space
        below the rows. Bound to ``Shift+M`` so it stays distinct
        from the row-level ``m`` menu.
        """
        f = self.focused
        wid = getattr(f, "id", None) if f is not None else None
        if wid == "pending_table":
            self._show_pending_panel_menu()
            return
        if wid == "submitted_table":
            self._show_submitted_panel_menu()
            return
        self.notify(
            "Panel menu (Shift+M) is wired for the Pending / Submitted tabs.",
            timeout=3,
        )

    def _show_submitted_panel_menu(self) -> None:
        """Panel-level menu for the Submitted tab — Filter/Sort + Refresh.

        The Submitted table has no per-workspace column, so its filter
        dialog hides the workspace field (``show_workspace=False``).
        """
        filt = "● " if self._submitted_view.is_active() else ""
        items = [
            ContextMenuItem(
                f"{filt}Filter / Sort Changelists…", "filter_submitted", "",
            ),
            ContextMenuItem(
                "Refresh Submitted Changelists", "refresh_submitted", "F5",
            ),
        ]

        def on_close(action_id: str | None) -> None:
            if action_id == "filter_submitted":
                self.open_submitted_filter()
            elif action_id == "refresh_submitted":
                self._load_submitted()

        self.push_screen(
            ContextMenuModal(items, title="Submitted Changelists"),
            on_close,
        )

    def _show_pending_menu(self) -> None:
        table = self.query_one("#pending_table", DataTable)
        if table.row_count == 0:
            self.notify("No pending changelists to show menu for.",
                        timeout=3)
            return
        if table.cursor_row is None or table.cursor_row < 0:
            self.notify("Move the cursor onto a row first.", timeout=3)
            return
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:  # noqa: BLE001 — RowDoesNotExist on stale cursor
            self.notify("Could not read selected pending row.", timeout=3)
            return
        change = str(row[0]) if row else ""
        if not change:
            return

        is_default = (change == "default")
        is_remote = self._is_remote_pending(change)
        row_client = self._pending_client_by_change.get(change, "")
        items, menu_title = build_pending_menu(
            change,
            is_default=is_default,
            is_remote=is_remote,
            row_client=row_client,
        )

        def on_close(action_id: str | None, c: str = change) -> None:
            if action_id is None:
                return
            if action_id == "submit":
                self.action_submit_pending()
            elif action_id == "submit_resolve":
                self._submit_and_resolve(c)
            elif action_id == "view_cl":
                self._view_pending_cl(c)
            elif action_id == "revert_cl":
                self._confirm_revert_cl(c)
            elif action_id == "revert_unchanged":
                self._run_revert_unchanged(c)
            elif action_id == "re_resolve":
                self._confirm_re_resolve_cl(c)
            elif action_id == "move_all_to":
                self._show_move_modal(c)
            elif action_id == "diff_have":
                self._run_diff_pending_against_have(c)
            elif action_id == "edit_desc":
                self._open_edit_desc_modal(c, force=False)
            elif action_id == "delete_cl":
                self._confirm_delete_cl(c)
            elif action_id == "shelve":
                self._run_shelve_interactive(c)
            elif action_id == "shelve_update":
                self._run_shelve(c, force=True)
            elif action_id == "shelve_delete":
                self._confirm_shelve_delete(c)
            elif action_id == "unshelve":
                self._open_unshelve_target_picker(c)
            elif action_id == "new_pending_cl":
                self.action_new_pending_cl()
            elif action_id == "copy_swarm_cl":
                self._copy_swarm_cl_url(c)
            elif action_id == "open_swarm_cl":
                self._open_swarm_cl_in_browser(c)
            elif action_id == "print_preview":
                self._print_cl(c, submitted=False, preview=True)
            elif action_id == "print":
                self._print_cl(c, submitted=False, preview=False)
            elif action_id == "refresh_pending":
                self._load_pending()
            elif action_id == "refresh_one":
                self._refresh_one_pending_cl(c)
            elif action_id == "view_remote":
                self._show_remote_pending_view(c)

        self.push_screen(
            ContextMenuModal(items, title=menu_title),
            on_close,
        )

    # --- Pending panel-level menu (Shift+M, empty-area equivalent) ------

    def _show_pending_panel_menu(self) -> None:
        """The right-click-on-empty-space menu for the Pending tab.

        Mirrors p4v's three-item panel menu: New Pending Changelist,
        a "Sort Files By" submenu (controls the detail-pane file
        ordering), and Refresh All. Distinct from the per-row menu
        on plain ``m`` — the row menu carries CL-specific operations
        and this one carries the panel-wide ones.
        """
        filt = "● " if self._pending_view.is_active() else ""
        items = [
            ContextMenuItem(
                "New Pending Changelist…", "new_pending_cl", "Ctrl+N",
            ),
            ContextMenuItem("Sort Files By  ▸", "sort_files", ""),
            ContextMenuItem(
                f"{filt}Filter / Sort Changelists…", "filter_pending", "",
            ),
            ContextMenuItem(
                "Refresh All Pending Changelists",
                "refresh_pending", "F5",
            ),
        ]

        def on_close(action_id: str | None) -> None:
            if action_id is None:
                return
            if action_id == "new_pending_cl":
                self.action_new_pending_cl()
            elif action_id == "sort_files":
                self._show_sort_files_submenu()
            elif action_id == "filter_pending":
                self.open_pending_filter()
            elif action_id == "refresh_pending":
                self._load_pending()

        self.push_screen(
            ContextMenuModal(items, title="Pending Changelists"),
            on_close,
        )

    def _show_sort_files_submenu(self) -> None:
        """Pick the sort key for the detail-pane file list.

        Stored on ``_ui_state["detail_files_sort"]`` and persisted to
        ``state.json`` so the choice survives across launches. The
        currently-selected option is prefixed with a filled bullet so
        the user can see the current setting at a glance.
        """
        cur = self._detail_files_sort

        def opt(label: str, key: str) -> ContextMenuItem:
            mark = "● " if cur == key else "  "
            return ContextMenuItem(f"{mark}{label}", key, "")

        items = [
            opt("Default (server order)",       "default"),
            opt("File Path",                    "path"),
            opt("Revision",                     "rev"),
            opt("Action (edit/add/delete/…)",   "action"),
            opt("Type (text/binary/symlink/…)", "type"),
        ]

        def on_close(action_id: str | None) -> None:
            if action_id:
                self._set_detail_files_sort(action_id)

        self.push_screen(
            ContextMenuModal(items, title="Sort Files By"),
            on_close,
        )

    def _set_detail_files_sort(self, key: str) -> None:
        """Apply + persist a detail_files sort key, then re-render
        the table in place using cached file data so the user sees
        the new order immediately without a server round-trip."""
        if key == self._detail_files_sort:
            return
        self._detail_files_sort = key
        self._ui_state["detail_files_sort"] = key
        save_state(self._ui_state)
        if self._last_detail_change is not None:
            self._render_detail(
                self._last_detail_change,
                self._last_detail_desc,
                self._last_detail_files,
            )

    @staticmethod
    def _sort_files(files: list[dict], key: str) -> list[dict]:
        """Return a new list of detail-pane file rows sorted by ``key``.

        Stable sort; falls back to the original order when the key is
        unknown (e.g. ``"default"`` — server-given order)."""
        if key == "path":
            return sorted(files, key=lambda f: (f.get("depotFile") or "").lower())
        if key == "action":
            return sorted(files, key=lambda f: f.get("action") or "")
        if key == "type":
            return sorted(files, key=lambda f: f.get("type") or "")
        if key == "rev":
            def rev_key(f: dict) -> int:
                v = f.get("rev") or f.get("haveRev") or 0
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return 0
            return sorted(files, key=rev_key)
        return list(files)

    # --- Submitted CL menu ----------------------------------------------

    def _show_submitted_menu(self) -> None:
        table = self.query_one("#submitted_table", DataTable)
        if table.row_count == 0:
            self.notify("No submitted changelists in view.", timeout=3)
            return
        if table.cursor_row is None or table.cursor_row < 0:
            self.notify("Move the cursor onto a row first.", timeout=3)
            return
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:  # noqa: BLE001
            self.notify("Could not read selected submitted row.", timeout=3)
            return
        change = str(row[0]) if row else ""
        if not change:
            return

        items = [
            ContextMenuItem(
                f"View Submitted Changelist '{change}'", "view", "",
            ),
            ContextMenuItem("Get Revision…", "get_revision", ""),
            ContextMenuItem(
                f"Get Revisions for Files in '{change}'",
                "get_revs_files", "",
            ),
            ContextMenuItem(
                f"Get Previous Revisions for Files in '{change}'",
                "get_prev_revs_files", "",
            ),
            SEPARATOR,
            ContextMenuItem(
                f"Merge/Integrate Files Using Submitted Changelist '{change}'…",
                "bci_integrate", "",
            ),
            ContextMenuItem(
                f"Copy Files Using Submitted Changelist '{change}'…",
                "bci_copy", "",
            ),
            ContextMenuItem("Branch Files…", "bci_branch", ""),
            SEPARATOR,
            ContextMenuItem(
                f"Undo Changes in Changelist '{change}'", "undo_cl", "",
            ),
            ContextMenuItem("Tag with Label…", "tag_label", ""),
            ContextMenuItem(
                f"Show Files in '{change}' in Tree…",
                "show_in_tree", "",
            ),
            SEPARATOR,
            ContextMenuItem(
                "Diff Files Against Previous Revisions",
                "diff_prev_revs", "Ctrl+D",
            ),
            ContextMenuItem(
                "Diff Files Against…", "diff_arbitrary", "Ctrl+Shift+D",
            ),
            ContextMenuItem(
                "Diff Files Against Previous Revisions (side-by-side)…",
                "diff_prev_revs_sxs", "",
            ),
            SEPARATOR,
            ContextMenuItem(
                f"Copy Swarm Review URL for '{change}'",
                "copy_swarm_cl", "",
            ),
            ContextMenuItem(
                f"Open '{change}' in Swarm (browser)",
                "open_swarm_cl", "",
            ),
            SEPARATOR,
            ContextMenuItem(
                f"Edit Submitted Changelist '{change}'", "edit_desc", "",
            ),
            ContextMenuItem(
                f"Print Preview Submitted Changelist '{change}'…",
                "print_preview", "",
            ),
            ContextMenuItem(
                f"Print Submitted Changelist '{change}'…",
                "print", "Ctrl+P",
            ),
            SEPARATOR,
            ContextMenuItem(
                "Refresh All Submitted Changelists",
                "refresh_submitted", "F5",
            ),
            ContextMenuItem(
                f"Refresh Submitted Changelist '{change}'",
                "refresh_one", "",
            ),
        ]

        def on_close(action_id: str | None, c: str = change) -> None:
            if action_id is None:
                return
            if action_id == "view":
                self._view_submitted_cl(c)
            elif action_id == "get_revision":
                self._open_get_revision("")
            elif action_id == "get_revs_files":
                self._confirm_get_revs_files(c)
            elif action_id == "get_prev_revs_files":
                self._confirm_get_prev_revs_files(c)
            elif action_id == "bci_integrate":
                self._open_bci_for_cl("integrate", c)
            elif action_id == "bci_copy":
                self._open_bci_for_cl("copy", c)
            elif action_id == "bci_branch":
                self._open_branch_flow("")
            elif action_id == "diff_prev_revs":
                self._run_diff_prev_revs(c)
            elif action_id == "diff_arbitrary":
                self.action_diff_arbitrary()
            elif action_id == "diff_prev_revs_sxs":
                self._open_sxs_diff(c)
            elif action_id == "undo_cl":
                self._confirm_undo_cl(c)
            elif action_id == "tag_label":
                self._tag_cl_with_label(c)
            elif action_id == "show_in_tree":
                self._show_cl_in_tree(c)
            elif action_id == "edit_desc":
                self._open_edit_desc_modal(c, force=True)
            elif action_id == "copy_swarm_cl":
                self._copy_swarm_cl_url(c)
            elif action_id == "open_swarm_cl":
                self._open_swarm_cl_in_browser(c)
            elif action_id == "print_preview":
                self._print_cl(c, submitted=True, preview=True)
            elif action_id == "print":
                self._print_cl(c, submitted=True, preview=False)
            elif action_id == "refresh_submitted":
                self._load_submitted()
            elif action_id == "refresh_one":
                self._load_change_detail(c, submitted=True)

        self.push_screen(
            ContextMenuModal(items, title=f"Submitted CL {change}"),
            on_close,
        )

    # --- History menu ----------------------------------------------------

    def _show_history_menu(self) -> None:
        """Build the History table's per-row context menu — mirrors
        p4v's right-click on a file-history / folder-history row.

        Every "...this changelist" item operates on the change number
        in the selected row; the panel-level refresh works off the
        per-tab target captured by ``_render_history`` /
        ``_render_folder_history``.
        """
        table = self.query_one("#history_table", DataTable)
        if table.row_count == 0:
            self.notify(
                "History table is empty — load a file or folder "
                "history first (Ctrl+T).",
                timeout=3,
            )
            return
        if table.cursor_row is None or table.cursor_row < 0:
            self.notify("Move the cursor onto a row first.", timeout=3)
            return
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:  # noqa: BLE001
            self.notify("Could not read selected history row.",
                        timeout=3)
            return
        # File-history columns: Rev | Change | Action | Date | User |
        # Description. Folder-history columns: Change | Date | User |
        # Description. ``_history_is_folder`` is True for the latter,
        # in which case Rev is absent.
        if self._history_is_folder:
            rev = ""
            change = str(row[0]) if len(row) > 0 else ""
        else:
            rev = str(row[0]) if len(row) > 0 else ""
            change = str(row[1]) if len(row) > 1 else ""
        if not change:
            self.notify("Selected row has no changelist number.",
                        timeout=3)
            return

        items = [
            ContextMenuItem(
                f"View Submitted Changelist '{change}'", "view", "",
            ),
            ContextMenuItem("Get Revision…", "get_revision", ""),
            ContextMenuItem(
                f"Merge/Integrate Files Using Submitted Changelist "
                f"'{change}'…",
                "bci_integrate", "",
            ),
            ContextMenuItem(
                f"Copy Files Using Submitted Changelist '{change}'…",
                "bci_copy", "",
            ),
            ContextMenuItem("Branch Files…", "bci_branch", ""),
            ContextMenuItem(
                f"Undo Changes in Changelist '{change}'", "undo_cl", "",
            ),
            ContextMenuItem("Tag with Label…", "tag_label", ""),
            SEPARATOR,
            ContextMenuItem(
                "Diff Files Against Previous Revisions",
                "diff_prev_revs", "Ctrl+D",
            ),
            ContextMenuItem(
                "Diff Files Against…", "diff_arbitrary", "Ctrl+Shift+D",
            ),
            SEPARATOR,
            ContextMenuItem(
                f"Edit Submitted Changelist '{change}'", "edit_desc", "",
            ),
            ContextMenuItem(
                f"Print Preview Submitted Changelist '{change}'…",
                "print_preview", "",
            ),
            ContextMenuItem(
                f"Print Submitted Changelist '{change}'…",
                "print", "Ctrl+P",
            ),
            SEPARATOR,
            ContextMenuItem(
                "Refresh Folder History" if self._history_is_folder
                else "Refresh File History",
                "refresh_history", "",
            ),
            ContextMenuItem(
                f"Refresh Revision '{rev}'" if rev
                else f"Refresh Changelist '{change}'",
                "refresh_one", "",
            ),
        ]

        def on_close(action_id: str | None, c: str = change) -> None:
            if action_id is None:
                return
            if action_id == "view":
                self._view_submitted_cl(c)
            elif action_id == "get_revision":
                self._open_get_revision(self._history_target or "")
            elif action_id == "bci_integrate":
                self._open_bci_for_cl("integrate", c)
            elif action_id == "bci_copy":
                self._open_bci_for_cl("copy", c)
            elif action_id == "bci_branch":
                self._open_branch_flow("")
            elif action_id == "undo_cl":
                self._confirm_undo_cl(c)
            elif action_id == "tag_label":
                self._tag_cl_with_label(c)
            elif action_id == "diff_prev_revs":
                self._run_diff_prev_revs(c)
            elif action_id == "diff_arbitrary":
                self.action_diff_arbitrary()
            elif action_id == "edit_desc":
                self._open_edit_desc_modal(c, force=True)
            elif action_id == "print_preview":
                self._print_cl(c, submitted=True, preview=True)
            elif action_id == "print":
                self._print_cl(c, submitted=True, preview=False)
            elif action_id == "refresh_history":
                self._refresh_history_view()
            elif action_id == "refresh_one":
                # History rows are summary lines; refreshing one row
                # means re-running the same fetch for the whole tab.
                self._refresh_history_view()

        self.push_screen(
            ContextMenuModal(items, title=f"History · CL {change}"),
            on_close,
        )

    def _refresh_history_view(self) -> None:
        """Re-fetch the history target the table is currently showing."""
        target = self._history_target
        if not target:
            self.notify("No history target loaded — open one via Ctrl+T.",
                        timeout=3)
            return
        if self._history_is_folder:
            self._load_folder_history(target)
        else:
            self._load_file_history(target)
