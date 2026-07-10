"""Get Revision, diff (against-have / arbitrary), and cross-CL file
moves for :class:`P4VApp`.

Extracted verbatim from app.py (item 7, refactor 4/n). Calls back
into P4VApp via ``self`` through the MRO.
"""
from __future__ import annotations


from textual import work

from .p4client import P4Exception
from .sync_job import ChunkedSyncJob
from .utils import first_nonblank_line, is_creation_action, truncate_cells
from .widgets.confirm import ConfirmModal
from .widgets.file_viewer import FileViewerModal
from .widgets.move_change_modal import (
    MoveToChangelistModal,
    NEW_CL_SENTINEL,
)
from .widgets.new_change_modal import NewChangelistModal
from .widgets.arbitrary_diff_modal import ArbitraryDiffModal
from .widgets.get_revision_modal import (
    GetRevisionModal, GetRevisionRequest, build_sync_spec,
)
from .widgets.sxs_diff_modal import SideBySideDiffModal
from .widgets.workspace_tree import WorkspaceTree

from .app_shared import (
    _extract_qualifier,
)


class _DiffRevMixin:
    # --- Get Revision (multi-target picker) ----------------------------

    def _open_get_revision(self, target: str) -> None:
        """Open the full Get Revision dialog pre-loaded with ``target``
        as the first list entry. The user can Add/Remove more entries
        before firing."""
        initial = [target] if target else []

        def on_close(req: GetRevisionRequest | None) -> None:
            if req is None:
                return
            self._run_get_revision(req)

        self.push_screen(GetRevisionModal(initial), on_close)

    @work(thread=True, group="get_revision")
    def _run_get_revision(self, req: GetRevisionRequest) -> None:
        if req.preview:
            self._run_get_revision_preview(req)
            return
        # Real run — for each target, queue a ChunkedSyncJob with
        # the configured chunking strategy. Each one carries its own
        # state file so an interruption is recoverable.
        force = req.force
        # `safe_update` maps to `p4 sync -s`, which the existing
        # ChunkedSyncJob doesn't expose. For now, when safe_update is
        # set we run the entire batch via a single non-chunked
        # `p4 sync -s <spec>` per target (still resilient via
        # P4Service.run, just not split into batches).
        # only_files_in_cl: for "changelist" mode, fetch the file list
        # from `p4 files //...@CL` first and sync each file
        # individually instead of the whole tree.
        # remove_not_in_label: for "label" mode, after the regular
        # @label sync, walk the workspace below each target and sync
        # any file that's NOT in @label down to #none.
        for target in req.targets:
            self._sync_one_target(req, target, force=force)

    def _sync_one_target(
        self,
        req: GetRevisionRequest,
        target: str,
        *,
        force: bool,
    ) -> None:
        # If the target lacks the recursion suffix, leave it alone —
        # the user might want a single file. ChunkedSyncJob handles
        # both shapes via `p4 sync -n`.
        if req.rev_mode == "changelist" and req.only_files_in_cl:
            self._sync_files_in_cl(req, target, force=force)
            return

        spec = build_sync_spec(target, req.rev_mode, req.rev_value)

        if req.safe_update:
            # `-s` mode — run via direct p4.run rather than
            # ChunkedSyncJob since the chunked job doesn't currently
            # plumb -s. P4Service.run is still resilient.
            args = ["sync", "-s"]
            if force:
                args.append("-f")
            args.append(spec)
            try:
                res = self.p4.run(*args)
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(
                    self.notify,
                    f"sync -s {spec} failed: {e}",
                    severity="error", timeout=10,
                )
                return
            n = sum(1 for r in res if isinstance(r, dict))
            self.call_from_thread(
                self.notify,
                f"Sync (-s) {spec}: {n} file(s).",
                timeout=5,
            )
        else:
            job = ChunkedSyncJob(
                self.p4, spec, force=force,
                strategy=self._chunking_for("force_sync" if force else "sync"),
            )
            self.call_from_thread(self.jobs.submit_job, job)
            self.call_from_thread(
                self.notify,
                f"Queued chunked sync: {spec} "
                f"({job.strategy.describe()})",
                timeout=4,
            )

        # Label cleanup pass: for any file currently in workspace under
        # `target` that's NOT in the @label, sync to #none so the
        # workspace truly mirrors the label.
        if (req.rev_mode == "label"
                and req.remove_not_in_label
                and req.rev_value):
            self._remove_not_in_label(target, req.rev_value)

        self.call_from_thread(
            self.query_one(WorkspaceTree).refresh_root,
        )

    def _sync_files_in_cl(
        self,
        req: GetRevisionRequest,
        target: str,
        *,
        force: bool,
    ) -> None:
        """`Only files in CL` path — sync each file in the changelist
        individually rather than the whole tree."""
        try:
            rows = self.p4.run(
                "files", f"{target}@{req.rev_value.lstrip('@')}",
            )
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"List files in CL {req.rev_value} under {target} "
                f"failed: {e}",
                severity="error", timeout=10,
            )
            return
        files = [
            r["depotFile"] for r in rows
            if isinstance(r, dict) and r.get("depotFile")
        ]
        if not files:
            self.call_from_thread(
                self.notify,
                f"No files match {target}@{req.rev_value}.",
                severity="warning", timeout=5,
            )
            return
        # Build per-file specs at the requested CL.
        specs = [f"{f}@{req.rev_value.lstrip('@')}" for f in files]
        args: list = ["sync"]
        if force:
            args.append("-f")
        if req.safe_update:
            args.append("-s")
        args.extend(specs)
        try:
            res = self.p4.run(*args)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"sync of {len(specs)} file(s) failed: {e}",
                severity="error", timeout=10,
            )
            return
        n = sum(1 for r in res if isinstance(r, dict))
        self.call_from_thread(
            self.notify,
            f"Synced {n} file(s) in CL {req.rev_value} under {target}.",
            timeout=5,
        )

    def _remove_not_in_label(
        self, target: str, label: str,
    ) -> None:
        """Walk files under ``target`` that have a haveRev but aren't
        in ``@label``, and sync them to #none so the workspace
        matches the label."""
        try:
            in_label = self.p4.run(
                "files", f"{target}@{label.lstrip('@')}",
            )
        except Exception:  # noqa: BLE001
            return
        in_label_set = {
            r.get("depotFile") for r in in_label
            if isinstance(r, dict) and r.get("depotFile")
        }
        try:
            opened_or_have = self.p4.run("have", target)
        except Exception:  # noqa: BLE001
            return
        to_remove = [
            r["depotFile"] for r in opened_or_have
            if isinstance(r, dict)
            and r.get("depotFile")
            and r["depotFile"] not in in_label_set
        ]
        if not to_remove:
            return
        try:
            self.p4.run(
                "sync", *[f"{f}#none" for f in to_remove],
            )
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"Removing files not in label {label}: {e}",
                severity="error", timeout=10,
            )
            return
        self.call_from_thread(
            self.notify,
            f"Removed {len(to_remove)} file(s) not in label {label} "
            f"under {target}.",
            timeout=6,
        )

    def _run_get_revision_preview(
        self, req: GetRevisionRequest,
    ) -> None:
        """`p4 sync -n` for each target so the user sees what would
        happen without touching the workspace. Output collected and
        shown in the FileViewer."""
        lines: list[str] = []
        lines.append("Preview of Get Revision (no files changed):")
        lines.append("")
        for target in req.targets:
            spec = build_sync_spec(target, req.rev_mode, req.rev_value)
            args: list = ["sync", "-n"]
            if req.force:
                args.append("-f")
            if req.safe_update:
                args.append("-s")
            args.append(spec)
            lines.append(f"$ p4 {' '.join(args)}")
            try:
                rows = self.p4.run(*args)
            except Exception as e:  # noqa: BLE001
                lines.append(f"  ERROR: {e}")
                lines.append("")
                continue
            n = 0
            for r in rows:
                if isinstance(r, dict):
                    n += 1
                    df = r.get("depotFile", "?")
                    rev = r.get("rev", r.get("haveRev", ""))
                    action = r.get("action", "")
                    lines.append(f"  {action:<8} {df}#{rev}")
                elif isinstance(r, str):
                    lines.append(f"  {r}")
            if n == 0:
                lines.append("  (no files would change)")
            lines.append("")
            if req.rev_mode == "label" and req.remove_not_in_label:
                lines.append(
                    f"  Note: 'Remove files not in label' would also "
                    "delete files under this target whose path isn't "
                    f"in @{req.rev_value}."
                )
                lines.append("")
        body = "\n".join(lines)
        self.call_from_thread(
            self.push_screen,
            FileViewerModal("Get Revision · Preview", body),
        )

    # --- Diff Against Have (workspace shortcut) ------------------------

    def _diff_against_have(self, target: str) -> None:
        """Workspace tree shortcut — diff the local file (or tree)
        against its ``#have`` revision. Routes through the same
        Arbitrary Diff machinery; pre-fills the modal so the user
        can tweak before firing if they want a different right side."""
        # ``#have`` resolves on a per-client basis; let the server
        # do that. For directories we feed both sides as /...@have
        # vs /... (head-of-workspace-view).
        is_dir = target.endswith("/...") or target.endswith("/")
        left = target if is_dir else target
        right = (target + "#have") if not is_dir else (target + "@have")
        # Wrong way around — we want #have on the LEFT (older) and
        # the working copy on the RIGHT.
        left, right = right, left

        def on_close(picked: tuple[str, str] | None) -> None:
            if picked is None:
                return
            self._run_arbitrary_diff(*picked)

        self.push_screen(
            ArbitraryDiffModal(
                prefilled_left=left,
                prefilled_right=right,
            ),
            on_close,
        )

    # --- Arbitrary diff (file vs file / two folders / two CLs) ---------

    def action_diff_arbitrary(self) -> None:
        """Ctrl+Shift+D — open the two-spec entry modal. Pre-fills
        the Left input with the focused tree's cursor path so the
        common case ("compare this file with X") is one keystroke
        plus typing the right side."""
        prefilled_left = ""
        for tree_id in ("workspace_tree", "depot_tree"):
            try:
                tree = self.query_one(f"#{tree_id}")
            except Exception:  # noqa: BLE001
                continue
            node = getattr(tree, "cursor_node", None)
            data = getattr(node, "data", None) if node else None
            if data and isinstance(data, str) and data.startswith("//"):
                prefilled_left = data
                break

        def on_close(picked: tuple[str, str] | None) -> None:
            if picked is None:
                return
            left, right = picked
            self._run_arbitrary_diff(left, right)

        self.push_screen(
            ArbitraryDiffModal(
                prefilled_left=prefilled_left,
                prefilled_right="",
            ),
            on_close,
        )

    @work(thread=True, group="arbitrary_diff")
    def _run_arbitrary_diff(self, left: str, right: str) -> None:
        """Resolve the user's two specs into one or more (left_spec,
        right_spec) pairs and push the SxS viewer.

        Strategy:
          1. ``p4 diff2 -q <left> <right>`` returns one row per file
             pair the server compared (file vs file, or every
             matching pair under a tree pair).
          2. Filter to rows where the file actually differs.
          3. Build SxS pairs by re-applying the user's original
             qualifier (#rev / @CL) to the per-row depot paths.
          4. If no rows differ → toast and bail.
          5. Push :class:`SideBySideDiffModal` with the pair list.
        """
        try:
            rows = self.p4.run("diff2", "-q", left, right)
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"diff2 {left} {right} failed: {e}",
                severity="error", timeout=10,
            )
            return

        left_qual = _extract_qualifier(left)
        right_qual = _extract_qualifier(right)

        pairs: list[tuple[str, str, str]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            status = (r.get("status") or "").lower()
            # Skip identical pairs — they have nothing to show.
            if "identical" in status:
                continue
            ldf = r.get("depotFile") or ""
            rdf = r.get("depotFile2") or ""
            # diff2 reports "left only" / "right only" / "diff"; in the
            # one-side-only cases one path is empty.
            l_spec = (ldf + left_qual) if ldf else ""
            r_spec = (rdf + right_qual) if rdf else ""
            if not l_spec and not r_spec:
                continue
            label = ldf or rdf or "(unknown)"
            if "left only" in status:
                label = f"[only L]  {label}"
            elif "right only" in status:
                label = f"[only R]  {label}"
            pairs.append((l_spec, r_spec, label))

        if not pairs:
            self.call_from_thread(
                self.notify,
                f"No differences between {left} and {right}.",
                timeout=6,
            )
            return

        title = f"Diff · {left}  vs  {right}"

        def push_modal(p=pairs, t=title) -> None:
            self.push_screen(
                SideBySideDiffModal.for_pairs(
                    title=t, pairs=p, p4_service=self.p4,
                    left_col_label=f"Left  ({left})",
                    right_col_label=f"Right ({right})",
                ),
            )

        self.call_from_thread(push_modal)

    @work(thread=True, group="sxs_diff_open", exclusive=True)
    def _fetch_then_show_sxs_diff(self, change: str) -> None:
        """Build the (path, rev, action) tuples for every file in the
        CL and hand them to SideBySideDiffModal."""
        try:
            info = self.p4.describe(change)
        except Exception:  # noqa: BLE001
            info = {}
        depot_files = info.get("depotFile") or []
        revs = info.get("rev") or []
        actions = info.get("action") or []
        triples: list[tuple[str, int, str]] = []
        for i, df in enumerate(depot_files):
            try:
                rev_int = int(revs[i]) if i < len(revs) else 0
            except (TypeError, ValueError):
                rev_int = 0
            action = str(actions[i]) if i < len(actions) else ""
            triples.append((df, rev_int, action))
        if not triples:
            self.call_from_thread(
                self.notify,
                f"CL {change}: no files to diff.",
                severity="warning", timeout=4,
            )
            return
        self.call_from_thread(
            self.push_screen,
            SideBySideDiffModal.for_cl(change, triples, self.p4),
        )

    @work(thread=True, group="diff_prev_revs")
    def _run_diff_prev_revs(self, change: str) -> None:
        diff_text = self.p4.diff_describe(change)
        if not diff_text.strip():
            diff_text = (
                f"[CL {change} — no diff to display "
                "(empty CL, binary-only, or fetch failed)]"
            )
        self.call_from_thread(
            self.push_screen,
            FileViewerModal(
                f"Diff against previous · CL {change}",
                diff_text,
            ),
        )

    def _confirm_get_prev_revs_files(self, change: str) -> None:
        def on_close(yes: bool, c: str = change) -> None:
            if yes:
                self._run_get_prev_revs_files(c)

        self.push_screen(
            ConfirmModal(
                title=f"Sync files in CL {change} to PREVIOUS revision?",
                message=(
                    f"For each file in changelist {change}, sync to the "
                    "revision immediately before this changelist (rev-1).\n\n"
                    "Files added/branched in this changelist (no prior "
                    "revision) are skipped. Local copies of synced files "
                    "are replaced."
                ),
                ok_label="Sync to prev",
                ok_variant="primary",
            ),
            callback=on_close,
        )

    @work(thread=True, group="get_prev_revs_files")
    def _run_get_prev_revs_files(self, change: str) -> None:
        try:
            info = self.p4.describe(change)
            depot_files = info.get("depotFile") or []
            revs = info.get("rev") or []
            actions = info.get("action") or []
            targets: list[str] = []
            skipped_no_prior = 0
            for i, df in enumerate(depot_files):
                action = actions[i] if i < len(actions) else ""
                rev_str = revs[i] if i < len(revs) else ""
                try:
                    cur_rev = int(rev_str)
                except (TypeError, ValueError):
                    continue
                # A creation action (add / branch / import / move/add)
                # has no real predecessor; rev 1 likewise. move/add can
                # land at rev > 1 on a resurrected path, so the action
                # check is needed beyond the cur_rev guard.
                if cur_rev <= 1 or is_creation_action(action):
                    skipped_no_prior += 1
                    continue
                targets.append(f"{df}#{cur_rev - 1}")
            if not targets:
                self.call_from_thread(
                    self.notify,
                    f"CL {change}: nothing to revert "
                    f"(every file is add/branch or rev 1).",
                    timeout=5,
                )
                return
            self.p4.run("sync", *targets)
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Get Previous Revisions for files in CL {change} failed: {e}",
                severity="error", timeout=10,
            )
            return
        msg = (
            f"Synced {len(targets)} file(s) to revision before CL {change}"
        )
        if skipped_no_prior:
            msg += f"  ·  skipped {skipped_no_prior} (no prior revision)"
        self.call_from_thread(self.notify, msg, timeout=6)
        self.call_from_thread(
            self.query_one(WorkspaceTree).refresh_root,
        )

    @work(thread=True, group="get_revs_files")
    def _run_get_revs_files(self, change: str) -> None:
        try:
            info = self.p4.describe(change)
            files = info.get("depotFile") or []
            if not files:
                self.call_from_thread(
                    self.notify, f"CL {change}: no files to sync",
                    timeout=4,
                )
                return
            targets = [f"{f}@{change}" for f in files]
            self.p4.run("sync", *targets)
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Get Revisions for files in CL {change} failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.call_from_thread(
            self.notify,
            f"Synced {len(files)} file(s) at CL {change}",
            timeout=4,
        )
        self.call_from_thread(
            self.query_one(WorkspaceTree).refresh_root,
        )

    def _confirm_revert_cl(self, change: str) -> None:
        def on_close(yes: bool, c: str = change) -> None:
            if yes:
                self._run_revert_cl(c)

        self.push_screen(
            ConfirmModal(
                title=f"Revert all files in CL {change}?",
                message=(
                    f"Discard pending edits for every file currently in "
                    f"changelist {change}.\n\nFiles will be unopened and "
                    "their depot copy restored. Cannot be undone."
                ),
                ok_label="Revert",
                ok_variant="error",
            ),
            callback=on_close,
        )

    @work(thread=True, group="cl_revert")
    def _run_revert_cl(self, change: str) -> None:
        try:
            opened = self.p4.opened_in_change(change)
            files = [r["depotFile"] for r in opened
                     if isinstance(r, dict) and r.get("depotFile")]
            if not files:
                self.call_from_thread(
                    self.notify, f"CL {change}: no files to revert",
                    timeout=4,
                )
                return
            self.p4.run("revert", "-c", change, *files)
        except P4Exception as e:
            self.call_from_thread(
                self.notify, f"Revert CL {change} failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.call_from_thread(
            self.notify, f"Reverted {len(files)} file(s) in CL {change}",
            timeout=4,
        )
        self.call_from_thread(self._load_pending)
        self.call_from_thread(
            self.query_one(WorkspaceTree).refresh_root,
        )

    # --- move files between changelists ---------------------------------

    def _show_move_modal(self, source_cl: str) -> None:
        # Build the picker: default + every other pending CL of this
        # client + a sentinel for "create new". Source CL is excluded so
        # the user can't pick a no-op.
        choices: list[tuple[str, str]] = []
        if source_cl != "default":
            choices.append(("default", "default"))
        try:
            pending = self.p4.pending_changes(client=self.p4.client)
        except P4Exception:
            pending = []
        for r in pending:
            num = str(r.get("change", ""))
            if not num or num == source_cl:
                continue
            desc = first_nonblank_line(r.get("desc", "") or "")
            label = f"{num} — {desc}" if desc else num
            choices.append((num, truncate_cells(label, 80)))
        choices.append((NEW_CL_SENTINEL, "New changelist…"))

        def on_close(target_id: str | None, src: str = source_cl) -> None:
            if target_id is None:
                return
            if target_id == NEW_CL_SENTINEL:
                # Two-step: prompt for description, then create + reopen.
                def after_create(desc: str | None) -> None:
                    if desc:
                        self._create_then_move(src, desc)

                self.push_screen(NewChangelistModal(), after_create)
            else:
                self._move_files(src, target_id)

        self.push_screen(
            MoveToChangelistModal(source_cl, choices),
            on_close,
        )

    @work(thread=True, group="cl_move")
    def _move_files(self, source_cl: str, target_cl: str) -> None:
        try:
            opened = self.p4.opened_in_change(source_cl)
            files = [r["depotFile"] for r in opened
                     if isinstance(r, dict) and r.get("depotFile")]
            if not files:
                self.call_from_thread(
                    self.notify, f"CL {source_cl}: no files to move",
                    timeout=4,
                )
                return
            self.p4.run("reopen", "-c", target_cl, *files)
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Move from CL {source_cl} to {target_cl} failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.call_from_thread(
            self.notify,
            f"Moved {len(files)} file(s): CL {source_cl} → {target_cl}",
            timeout=4,
        )
        self.call_from_thread(self._load_pending)

    @work(thread=True, group="cl_move_create")
    def _create_then_move(self, source_cl: str, new_desc: str) -> None:
        try:
            new_cl = self.p4.create_changelist(new_desc)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify, f"Create new CL failed: {e}",
                severity="error", timeout=10,
            )
            return
        try:
            opened = self.p4.opened_in_change(source_cl)
            files = [r["depotFile"] for r in opened
                     if isinstance(r, dict) and r.get("depotFile")]
            if not files:
                self.call_from_thread(
                    self.notify,
                    f"Created CL {new_cl} (no files in {source_cl} to move)",
                    timeout=4,
                )
                self.call_from_thread(self._load_pending)
                return
            self.p4.run("reopen", "-c", new_cl, *files)
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Created CL {new_cl} but reopen failed: {e}",
                severity="error", timeout=10,
            )
            self.call_from_thread(self._load_pending)
            return
        self.call_from_thread(
            self.notify,
            f"Created CL {new_cl} and moved {len(files)} file(s) there",
            timeout=4,
        )
        self.call_from_thread(self._load_pending)
