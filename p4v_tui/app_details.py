"""Pending-CL detail pane + Enter-popup logic for :class:`P4VApp`.

Extracted verbatim from app.py (refactor 3/n): the row-highlight detail
loading and the read-only pending/remote-CL popup. Calls back into
P4VApp via ``self`` through the MRO.
"""
from __future__ import annotations

from datetime import datetime

from textual import work
from textual.widgets import (
    DataTable,
    Static,
)

from .submit_job import ResilientSubmitJob
from .utils import truncate_cells
from .widgets.confirm import ConfirmModal
from .widgets.file_viewer import FileViewerModal
from .widgets.pending_detail_modal import PendingDetailModal



class _DetailMixin:
    # --- pending row Enter → info popup ---------------------------------

    def on_data_table_row_selected(
        self, event: DataTable.RowSelected,
    ) -> None:
        # Enter / click on a row in any of the three CL tables brings
        # up a popup with the full content:
        #   pending   → PendingDetailModal (editable description +
        #               selectable file list + Submit / Revert).
        #   submitted → FileViewerModal w/ `p4 describe -s` (read-only).
        #   history   → also FileViewerModal w/ `p4 describe -s`. The
        #               history rows are always submitted CLs (filelog
        #               for files, ``changes -L`` for folders), so the
        #               same read-only viewer fits — folder history's
        #               first column is the change number, file
        #               history puts it in column 1 (column 0 is rev).
        #   detail_files → open the selected depot file in FileViewerModal.
        table_id = event.data_table.id
        if table_id == "detail_files":
            try:
                row = event.data_table.get_row_at(event.cursor_row)
            except Exception:  # noqa: BLE001
                return
            if not row:
                return
            depot_file = str(row[0])
            if depot_file.startswith("//"):
                self._open_file_viewer(depot_file)
            return
        if table_id not in (
            "pending_table", "submitted_table", "history_table",
        ):
            return
        if event.cursor_row is None or event.cursor_row < 0:
            return
        try:
            row = event.data_table.get_row_at(event.cursor_row)
        except Exception:  # noqa: BLE001
            return
        if not row:
            return
        if table_id == "history_table":
            # Column layout swaps by mode — see ``_history_is_folder``
            # and ``_render_history`` / ``_render_folder_history``.
            change_idx = 0 if self._history_is_folder else 1
            if len(row) <= change_idx:
                return
            change = str(row[change_idx])
        else:
            change = str(row[0])
        if not change:
            return
        # Where on screen did the user trigger from? We avoid
        # covering the row that launched the popup — see
        # ``_placement_class_for_table``.
        placement = self._placement_class_for_table(table_id)
        if table_id in ("submitted_table", "history_table"):
            self._show_submitted_detail(change, placement=placement)
            return
        self._open_pending_detail(change, placement=placement)

    def _placement_class_for_table(self, table_id: str) -> str | None:
        """Pick a CSS class so the next popup doesn't cover the row.

        Returns ``"place-bottom"`` when the highlighted row sits in
        the upper half of the screen (so the popup should hug the
        bottom), ``"place-top"`` for the lower half, or ``None`` if
        the geometry can't be determined — in which case callers
        fall back to centred placement.
        """
        try:
            table = self.query_one(f"#{table_id}", DataTable)
        except Exception:  # noqa: BLE001
            return None
        coord = getattr(table, "cursor_coordinate", None)
        if coord is None or coord.row < 0:
            return None
        try:
            scroll_y = int(table.scroll_offset.y)
        except Exception:  # noqa: BLE001
            scroll_y = 0
        visible_row = coord.row - scroll_y
        try:
            region = table.region
        except Exception:  # noqa: BLE001
            return None
        # +1 for the header row inside the DataTable.
        cursor_screen_y = region.y + 1 + visible_row
        try:
            screen_h = self.size.height
        except Exception:  # noqa: BLE001
            return None
        if screen_h <= 0:
            return None
        return ("place-bottom" if cursor_screen_y < screen_h // 2
                else "place-top")

    @work(thread=True, group="submitted_detail_modal")
    def _show_submitted_detail(
        self, change: str, *, placement: str | None = None,
    ) -> None:
        """Read-only ``p4 describe -s`` viewer for a submitted CL.

        Triggered by Enter / mouse double-click on a row in the
        Submitted Changelists table. Reuses :class:`FileViewerModal`
        which already gives us Esc/q/Backspace close, RichLog scroll,
        and the chunked-render path for huge CL descriptions / file
        lists. We format the body identically to
        ``_show_remote_pending_view`` so the two read-only viewers
        feel consistent — header, description, affected files.
        """
        try:
            info = self.p4.describe(change)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"Could not load submitted CL {change}: {e}",
                severity="warning",
                timeout=4,
            )
            return

        desc = (info.get("desc") or "").rstrip()
        user = info.get("user", "") or ""
        client = info.get("client", "") or ""
        time_raw = info.get("time")
        when = ""
        if time_raw:
            try:
                when = datetime.fromtimestamp(int(time_raw)).strftime(
                    "%Y-%m-%d %H:%M:%S",
                )
            except (TypeError, ValueError):
                when = str(time_raw)

        depot_files = info.get("depotFile", []) or []
        revs        = info.get("rev",       []) or []
        actions     = info.get("action",    []) or []
        ftypes      = info.get("type",      []) or []

        lines: list[str] = [
            f"Change:    {change}  (submitted)",
            f"User:      {user}",
            f"Workspace: {client}",
            f"Date:      {when}",
            "",
            "Description:",
            "",
        ]
        for ln in desc.splitlines() or [""]:
            lines.append(f"    {ln}")
        lines.extend(["", f"Affected files ({len(depot_files)}):", ""])
        if depot_files:
            for i, df in enumerate(depot_files):
                rev    = revs[i]    if i < len(revs)    else ""
                action = actions[i] if i < len(actions) else ""
                ftype  = ftypes[i]  if i < len(ftypes)  else ""
                rev_s  = f"#{rev}" if rev else ""
                typ_s  = f" ({ftype})" if ftype else ""
                act_s  = f"[{action}] " if action else ""
                lines.append(f"    {act_s}{df}{rev_s}{typ_s}")
        else:
            lines.append("    <no files>")
        text = "\n".join(lines)

        def push(t: str = text, c: str = change,
                 cls: str | None = placement) -> None:
            modal = FileViewerModal(f"Submitted CL {c}", t)
            if cls:
                modal.add_class(cls)
            self.push_screen(modal)
        self.call_from_thread(push)

    def _open_pending_detail(
        self, change: str, *, placement: str | None = None,
    ) -> None:
        # Worker fetches description (already cached) + file list, then
        # we push the modal on the UI thread.
        # Fork early on remote CLs: PendingDetailModal exposes Submit /
        # Revert / Save buttons that all assume the CL's files are
        # opened in the currently connected client. For a CL that lives
        # in another workspace, `p4 opened -c <N>` returns nothing (it
        # only sees the current client's open files), so the modal
        # would render an empty file list and let the user click
        # Submit on an action that can't possibly succeed. Show a
        # read-only FileViewerModal instead.
        if self._is_remote_pending(change):
            self._show_remote_pending_view(change, placement=placement)
            return
        self._fetch_then_show_pending_detail(change, placement=placement)

    @work(thread=True, group="pending_detail_modal")
    def _show_remote_pending_view(
        self, change: str, *, placement: str | None = None,
    ) -> None:
        """Read-only viewer for a pending CL that belongs to another of
        the user's workspaces. Uses ``p4 describe -s`` because
        ``p4 opened -c <N>`` is scoped to the current client and would
        come back empty. Shows the CL header, description, and the
        affected-files list as plain text so there's no risk of an
        accidental write-action click."""
        desc = self._pending_desc.get(change, "") or "<no description>"
        row_client = self._pending_client_by_change.get(change, "")
        info = self.p4.describe(change)

        # ``p4 describe`` returns parallel arrays for the file list.
        depot_files = info.get("depotFile", []) or []
        revs        = info.get("rev", []) or []
        actions     = info.get("action", []) or []
        ftypes      = info.get("type", []) or []

        lines: list[str] = [
            f"Change:    {change}",
            f"Workspace: {row_client}  (remote — currently connected to "
            f"'{self.p4.client}')",
            f"User:      {info.get('user', '') or self.p4.user}",
            "",
            "Description:",
            "",
        ]
        for ln in desc.splitlines() or [""]:
            lines.append(f"    {ln}")
        lines.extend(["", f"Affected files ({len(depot_files)}):", ""])
        if depot_files:
            for i, df in enumerate(depot_files):
                rev    = revs[i]    if i < len(revs)    else ""
                action = actions[i] if i < len(actions) else ""
                ftype  = ftypes[i]  if i < len(ftypes)  else ""
                rev_s  = f"#{rev}" if rev else ""
                typ_s  = f" ({ftype})" if ftype else ""
                act_s  = f"[{action}] " if action else ""
                lines.append(f"    {act_s}{df}{rev_s}{typ_s}")
        else:
            lines.append("    <no files — empty pending CL>")
        lines.extend([
            "",
            "(read-only view — switch P4CLIENT to this workspace to "
            "submit / revert / shelve.)",
        ])
        text = "\n".join(lines)

        def push(t: str = text, c: str = change,
                 cls: str | None = placement) -> None:
            modal = FileViewerModal(f"Remote pending CL {c}", t)
            if cls:
                modal.add_class(cls)
            self.push_screen(modal)
        self.call_from_thread(push)

    @work(thread=True, group="pending_detail_modal")
    def _fetch_then_show_pending_detail(
        self, change: str, *, placement: str | None = None,
    ) -> None:
        desc = self._pending_desc.get(change, "") or ""
        try:
            files = self.p4.opened_in_change(change)
        except Exception:  # noqa: BLE001
            files = []
        is_default = (change == "default")

        def push_modal(c: str = change, d: str = desc,
                       f: list = files,
                       is_def: bool = is_default,
                       cls: str | None = placement) -> None:
            def on_close(result: dict | None, cc: str = c) -> None:
                if not result:
                    return
                action = result.get("action")
                if action == "revert":
                    # User picked Revert — discard everything in the
                    # CL. Description / file edits in the popup are
                    # ignored on this branch.
                    self._confirm_revert_cl(cc)
                    return
                if action == "submit":
                    self._apply_edits_then_submit(
                        cc,
                        result.get("new_description"),
                        result.get("unchecked_files") or [],
                    )
                    return
                if action == "save":
                    self._apply_edits_only(
                        cc,
                        result.get("new_description"),
                        result.get("unchecked_files") or [],
                    )

            modal = PendingDetailModal(c, d, f, is_def)
            if cls:
                modal.add_class(cls)
            self.push_screen(modal, on_close)

        self.call_from_thread(push_modal)

    def _apply_pending_edits(
        self,
        change: str,
        new_description: str | None,
        unchecked_files: list[str],
        *,
        promote_requires_description: bool,
    ) -> tuple[str, list[str]] | None:
        """Apply the user's description / file-selection edits.

        Shared between Submit and Save flows. For numbered CLs this is
        an in-place update; for the default CL it promotes to a fresh
        numbered CL and moves the checked files in.

        ``promote_requires_description`` controls the default-CL gate:
        Submit always needs one; Save can skip the gate when there is
        nothing to do (no edits at all).

        Returns ``(effective_change, summary)`` on success, or ``None``
        if the caller should stop (an error toast was already shown).
        Must be invoked from a worker thread — emits notifies via
        ``call_from_thread``.
        """
        applied: list[str] = []

        if change == "default":
            desc = (new_description or "").strip()
            if not desc:
                if promote_requires_description:
                    self.call_from_thread(
                        self.notify,
                        "Default changelist requires a description "
                        "before it can be submitted. Type one in the "
                        "popup and try again.",
                        severity="error", timeout=10,
                    )
                    return None
                # Save with no description on default: nothing
                # actionable, just bail quietly.
                self.call_from_thread(
                    self.notify,
                    "Nothing to save on the default changelist. Add a "
                    "description first to promote it to a numbered CL.",
                    severity="warning", timeout=6,
                )
                return None
            try:
                new_cl = self.p4.create_changelist(desc)
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(
                    self.notify,
                    f"Promote default → numbered failed: {e}",
                    severity="error", timeout=10,
                )
                return None
            try:
                # Snapshot the current default contents and move only
                # the user-checked files into the new CL. Anything in
                # the user's `unchecked_files` list stays in default.
                opened = self.p4.opened_in_change("default")
                excluded = set(unchecked_files)
                checked = [
                    r["depotFile"] for r in opened
                    if isinstance(r, dict)
                    and r.get("depotFile")
                    and r["depotFile"] not in excluded
                ]
                if checked:
                    self.p4.run("reopen", "-c", new_cl, *checked)
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(
                    self.notify,
                    f"Created CL {new_cl} but moving files in failed: "
                    f"{e}",
                    severity="error", timeout=10,
                )
                return None
            applied.append(f"default promoted to CL {new_cl}")
            if unchecked_files:
                applied.append(
                    f"{len(unchecked_files)} file(s) left in default"
                )
            change = new_cl
        else:
            # Step 1: save edited description if any.
            if new_description is not None:
                try:
                    self.p4.update_changelist_description(
                        change, new_description,
                    )
                except Exception as e:  # noqa: BLE001
                    self.call_from_thread(
                        self.notify,
                        f"Save description for CL {change} failed: {e}",
                        severity="error", timeout=10,
                    )
                    return None
                applied.append("description updated")

            # Step 2: move unchecked files out to default.
            if unchecked_files:
                try:
                    self.p4.run(
                        "reopen", "-c", "default", *unchecked_files,
                    )
                except Exception as e:  # noqa: BLE001
                    self.call_from_thread(
                        self.notify,
                        f"Move unchecked files out of CL {change} "
                        f"failed: {e}",
                        severity="error", timeout=10,
                    )
                    return None
                applied.append(
                    f"{len(unchecked_files)} file(s) moved to default"
                )

        return change, applied

    @work(thread=True, group="pending_detail_apply_then_submit")
    def _apply_edits_then_submit(
        self,
        change: str,
        new_description: str | None,
        unchecked_files: list[str],
    ) -> None:
        result = self._apply_pending_edits(
            change, new_description, unchecked_files,
            promote_requires_description=True,
        )
        if result is None:
            return
        change, applied_summary = result

        # Common path: confirm + queue the resilient submit job.
        def confirm_and_submit(c: str = change,
                               summary: list = applied_summary) -> None:
            preview_line = (
                "\n\nApplied: " + "  ·  ".join(summary)
                if summary else ""
            )

            def on_close(yes: bool, cc: str = c) -> None:
                if yes:
                    job = ResilientSubmitJob(self.p4, cc)
                    self.jobs.submit_job(job)
                    self.notify(
                        f"Queued resilient submit: CL {cc}",
                        timeout=4,
                    )

            self.push_screen(
                ConfirmModal(
                    title=f"Submit changelist {c}?",
                    message=(
                        f"Submit CL {c} to depot."
                        f"{preview_line}\n\nWill retry transparently on "
                        "connection drops; if the server commits but "
                        "the ack is lost, the next attempt is "
                        "recognized as already-submitted."
                    ),
                    ok_label="Submit",
                    ok_variant="primary",
                ),
                callback=on_close,
            )

        self.call_from_thread(confirm_and_submit)
        # Refresh the pending list so the modal flow's intermediate
        # state (moved files, edited description, default-promoted CL)
        # reflects in the UI.
        self.call_from_thread(self._load_pending)

    @work(thread=True, group="pending_detail_apply_only")
    def _apply_edits_only(
        self,
        change: str,
        new_description: str | None,
        unchecked_files: list[str],
    ) -> None:
        """Save-only path: persist description / file-selection edits
        but skip the submit confirm + ResilientSubmitJob queue."""
        result = self._apply_pending_edits(
            change, new_description, unchecked_files,
            promote_requires_description=False,
        )
        if result is None:
            return
        effective_change, applied = result
        if applied:
            self.call_from_thread(
                self.notify,
                f"Saved CL {effective_change}: " + "  ·  ".join(applied),
                timeout=5,
            )
        self.call_from_thread(self._load_pending)

    # --- detail loading ---------------------------------------------------

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        table_id = event.data_table.id
        if table_id not in (
            "pending_table", "submitted_table", "history_table",
        ):
            return
        if event.cursor_row is None or event.cursor_row < 0:
            return
        try:
            row = event.data_table.get_row_at(event.cursor_row)
        except IndexError:
            return
        if not row:
            return
        if table_id == "history_table":
            # Column layout depends on whether the current History
            # target is a file (Rev, Change, Action, Date, User,
            # Description) or a folder (Change, Date, User,
            # Description). Read the CL from whichever column owns it
            # in the active schema.
            change_idx = 0 if self._history_is_folder else 1
            if len(row) <= change_idx:
                return
            change = str(row[change_idx])
            if not change:
                return
            self._load_change_detail(change, submitted=True)
            return
        change = str(row[0])
        self._load_change_detail(change, submitted=table_id == "submitted_table")

    @work(thread=True, exclusive=True, group="detail")
    def _load_change_detail(self, change: str, submitted: bool = False) -> None:
        if submitted:
            info = self.p4.describe(change)
            files = self._files_from_describe(info)
            desc = info.get("desc", "") or ""
        elif self._is_remote_pending(change):
            # Pending CL in another workspace — `p4 opened -c <N>` is
            # scoped to the current client and would return nothing,
            # leaving the detail pane stuck on the previous row's
            # file list as the user cursors through. Fall back to
            # `p4 describe -s`, which is workspace-agnostic.
            info = self.p4.describe(change)
            files = self._files_from_describe(info)
            desc = self._pending_desc.get(change, "")
        else:
            files = self.p4.opened_in_change(change)
            desc = self._pending_desc.get(change, "")
        self.call_from_thread(self._render_detail, change, desc, files)

    @staticmethod
    def _files_from_describe(info: dict) -> list[dict]:
        names = info.get("depotFile") or []
        revs = info.get("rev") or []
        actions = info.get("action") or []
        types = info.get("type") or []
        out: list[dict] = []
        for i, depot_file in enumerate(names):
            out.append({
                "depotFile": depot_file,
                "rev": revs[i] if i < len(revs) else "",
                "action": actions[i] if i < len(actions) else "",
                "type": types[i] if i < len(types) else "",
            })
        return out

    def _render_detail(
        self, change: str, desc: str, files: list[dict]
    ) -> None:
        from rich.markup import escape as _escape
        desc = (desc or "").rstrip()
        header = f"[b]Change {change}[/b]"
        if desc and desc != "<default changelist>":
            # Escape user-supplied description text so brackets like
            # [/INST] (which crash Rich's markup parser) and [b] (which
            # would silently start bold) render literally.
            body = f"\n{_escape(desc)}"
        else:
            body = ""
        self.query_one("#detail_desc", Static).update(f" {header}{body}")

        # Cache the unsorted file list + desc so the Pending panel's
        # "Sort Files By" submenu can re-render in place without
        # re-fetching from p4 (sort is purely a view-side operation).
        self._last_detail_change = change
        self._last_detail_desc = desc
        self._last_detail_files = list(files)

        files = self._sort_files(files, self._detail_files_sort)

        table = self.query_one("#detail_files", DataTable)
        table.clear()
        for f in files:
            # Truncate the File path by display cells. Long CJK paths render
            # at 2 cells per glyph but Rich/Textual's row-clipping miscounts
            # them, so a long-untruncated path bleeds into the left pane.
            table.add_row(
                truncate_cells(f.get("depotFile", ""), 60),
                f.get("rev", "") or f.get("haveRev", ""),
                f.get("action", ""),
                f.get("type", ""),
            )
