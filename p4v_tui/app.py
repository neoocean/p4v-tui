"""Main P4V-TUI application."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
    Tree,
)

from .cmd_log import CmdLog
from .config import (
    Config, ConnectionConfig, build_swarm_url,
    discover_profiles, load_config,
)
from .fs_actions import (
    open_command_window, open_with_external, show_in_filesystem,
)
from .jobs import Job, JobRunner
from .messages import (
    BulkFileActionRequested, FileActionRequested, P4ClipboardAction,
    TreeFilterRequested,
)
from .p4client import P4Exception, P4Info, P4Service
from . import pending_jobs as _pending_jobs_mod
from .bulk_jobs import (
    ChunkedCleanJob,
    ChunkedReconcileJob,
    ChunkedRevertJob,
)
from .shared_state_cl import SharedStateChangelist
from .state import load_state, save_state
from .search_index import SearchIndex, index_path_for
from .search_jobs import IndexBuildJob, IndexUpdateJob
from .submit_job import ResilientSubmitJob
from .submit_guards import (
    SubmitFile, evaluate_submit_guards, format_guard_warnings, has_blocking,
)
from .sync_job import ChunkedSyncJob
from .utils import first_nonblank_line, truncate_cells
from .widgets.bci_modal import BranchCopyIntegrateModal, OPERATION_LABEL
from .widgets.cmd_monitor import CmdMonitorModal
from .widgets.confirm import ConfirmModal
from .widgets.context_menu import ContextMenuItem, ContextMenuModal
from .widgets.depot_tree import DepotTree
from .widgets.edit_change_modal import EditChangelistDescModal
from .widgets.file_viewer import FileViewerModal
from .widgets.bookmark_picker_modal import BookmarkPickerModal
from .widgets.find_file_modal import FindFileModal
from .widgets.goto_path_modal import GotoPathModal
from .widgets.h_scroll_table import HScrollDataTable
from .widgets.log_panel import LogPanel, DEFAULT_HEIGHT as DEFAULT_LOG_HEIGHT
from .widgets.splitter import (
    HorizontalSplitter, SplitterDragged, VerticalSplitter,
)
from .widgets.move_change_modal import (
    MoveToChangelistModal,
    NEW_CL_SENTINEL,
)
from .widgets.new_change_modal import NewChangelistModal
from .widgets.open_with_modal import OpenWithModal
from .widgets.pending_jobs_modal import PendingJobsModal
from .widgets.preferences_modal import PreferencesModal
from .widgets.annotate_modal import AnnotateModal
from .widgets.file_in_cl_picker import FileInCLPickerModal
from .widgets.file_properties_modal import FilePropertiesModal
from .widgets.label_picker_modal import LabelPickerModal
from .widgets.revision_graph_modal import RevisionGraphModal
from .widgets.timelapse_modal import TimelapseModal
from .widgets.profile_picker import ProfilePickerModal
from .widgets.quick_rename_modal import QuickRenameModal
from .widgets.quitting_modal import QuittingModal
from .widgets.rename_modal import RenameMoveModal
from .widgets.resolve_modal import ResolveModal
from .widgets.search_modal import SearchModal
from .widgets.tree_filter_overlay import TreeFilterOverlay
from .widgets.workspace_tree import WorkspaceTree

from . import narrow_nav

from .app_shared import (
    DEFAULT_AUTO_REFRESH_PENDING_SEC,
    DEFAULT_DETAIL_HEIGHT,
    DEFAULT_LEFT_WIDTH,
    LEFT_WIDTH_STEP,
    MAX_DETAIL_HEIGHT,
    MAX_LEFT_WIDTH,
    MAX_LOG_HEIGHT,
    MIN_DETAIL_HEIGHT,
    MIN_LEFT_WIDTH,
    MIN_LOG_HEIGHT,
    NARROW_TERMINAL_WIDTH,
    ConnectionBar,
    _truncate_workspace,
)
from .app_menus import _MenuMixin
from .app_details import _DetailMixin
from .app_diffrev import _DiffRevMixin


class P4VApp(_MenuMixin, _DetailMixin, _DiffRevMixin, App):
    CSS_PATH = "styles.tcss"
    TITLE = "p4v-tui"
    # p4v-tui ships its own keymap; the generic Textual command palette
    # (Ctrl+P by default) is unwanted and collided with Fast Search's
    # Ctrl+P history binding. Disabling it also drops the palette icon.
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        # F5 owns refresh — Ctrl+R is reserved app-wide for "Revert Files"
        # to match p4v conventions (the WorkspaceTree consumes it when it
        # has focus).
        Binding("f5", "refresh", "Refresh"),
        Binding("f2", "show_cmd_monitor", "Commands"),
        Binding("f3", "toggle_narrow_panels", "Panels"),
        # Phone-friendly alias for F3. iPhone Blink / iSH / SSH apps
        # often expose Ctrl combos but not function keys. Ctrl+W
        # mirrors the vim / tmux "switch window" idiom and stays
        # easy to type on virtual keyboards (`^` modifier + `w`).
        # In wide mode it just focuses the right pane like F3 does;
        # in narrow mode it flips the panes overlay on / off.
        Binding("ctrl+w", "toggle_narrow_panels", "Switch pane"),
        # Desktop page cycle (narrow) / right-pane tab cycle (wide).
        # NOTE: iPhone Blink and most mobile terminals do NOT emit
        # Ctrl+Arrow escape sequences, so on a phone these never fire —
        # Tab / Shift+Tab below are the reliable narrow-mode page cycle
        # (the Tab key IS in Blink's accessory bar). Kept for desktop
        # terminals that do send Ctrl+Arrow.
        Binding("ctrl+right", "right_tab_next", "Next tab",
                show=False),
        Binding("ctrl+left",  "right_tab_prev", "Prev tab",
                show=False),
        # Tab / Shift+Tab. In narrow mode this is the primary page
        # navigator: cycle tree → pending → history → submitted → log →
        # tree (Shift+Tab reverses). In wide mode it's a curated focus
        # traversal (active tree → active right table → log panel) —
        # better than Textual's default, which stops on header /
        # underline widgets. See ``action_smart_tab``.
        Binding("tab", "smart_tab(+1)", show=False),
        Binding("shift+tab", "smart_tab(-1)", show=False),
        Binding("backspace", "narrow_back", show=False),
        Binding("ctrl+s", "submit_pending", "Submit"),
        Binding("ctrl+n", "new_pending_cl", "New CL"),
        Binding("ctrl+t", "show_folder_history", "Folder Hist"),
        Binding("ctrl+shift+f", "find_file", "Find File"),
        Binding("ctrl+g", "goto_path", "Go to path"),
        Binding("ctrl+shift+v", "paste_permalink", "Paste permalink"),
        Binding("ctrl+shift+b", "bookmarks", "Bookmarks"),
        Binding("ctrl+f", "fast_search", "Search"),
        Binding("ctrl+d", "diff_prev_revs", "Diff vs prev"),
        Binding("f6", "focus_next_panel", "Next pane"),
        Binding("shift+f6", "focus_prev_panel", "Prev pane"),
        Binding("[", "shrink_left", "<Pane"),
        Binding("]", "grow_left", "Pane>"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+q", "quit", show=False, priority=True),
        Binding("ctrl+comma", "show_preferences", "Prefs"),
        Binding("ctrl+shift+d", "diff_arbitrary", "Diff…"),
        Binding("ctrl+shift+m", "run_macro", "Macros"),
        # Open the context menu for the focused panel (pending /
        # submitted tables — trees own their own 'm' binding, which takes
        # precedence when the tree itself is focused).
        Binding("m", "show_panel_menu", "Menu"),
        # Panel-level (right-pane empty-area) menu — separate from
        # the row menu on `m`. Modifier+letter combos pass through
        # the Korean IME unchanged, so no Hangul alias is needed.
        Binding("shift+m", "show_panel_area_menu", "Panel Menu"),
        # Hangul-IME aliases (2-beolsik): q -> ㅂ, m -> ㅡ.
        Binding("ㅂ", "quit", show=False),
        Binding("ㅡ", "show_panel_menu", show=False),
    ]

    # Right-tab id -> the focusable widget id inside it. Used by the
    # F6 panel-cycle to know which DataTable is "active" right now.
    _RIGHT_TAB_TO_WIDGET = {
        "tab_pending": "#pending_table",
        "tab_history": "#history_table",
        "tab_submitted": "#submitted_table",
    }
    _LEFT_TAB_TO_WIDGET = {
        "tab_depot": "#depot_tree",
        "tab_workspace": "#workspace_tree",
    }

    left_pane_width = reactive(DEFAULT_LEFT_WIDTH)
    detail_pane_height = reactive(DEFAULT_DETAIL_HEIGHT)
    log_panel_height = reactive(DEFAULT_LOG_HEIGHT)
    # Auto-toggled when the terminal narrows past NARROW_TERMINAL_WIDTH.
    # In narrow mode the right pane is hidden and the tree fills the
    # screen — useful for iPhone Blink and similar small terminals.
    narrow_mode = reactive(False)
    # In narrow mode the layout collapses to a single full-screen "page"
    # navigator (see ``narrow_nav``). ``narrow_page`` is the visible page:
    # one of tree / pending / history / submitted / log. Ctrl+→ / Ctrl+←
    # cycle the whole set; F3 / Ctrl+W quick-toggle tree ↔ last panel;
    # Backspace returns to the tree. Has no effect outside narrow_mode.
    narrow_page = reactive("tree")

    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config = config if config is not None else load_config()
        # Surface user-defined macro keybindings as instance-level
        # Binding entries. Class-level BINDINGS is shared across
        # instances and we want each user's TOML to drive their own
        # set; the merged map lives on ``self._bindings`` which
        # Textual rebuilds from class BINDINGS + any instance-level
        # additions during ``__init_subclass__`` / mount. The
        # cleanest way to inject without subclassing per-config is
        # to register on the running app via ``bind()`` from
        # ``on_mount`` (Textual's public binding API). See
        # :meth:`_install_macro_bindings`.
        # Single CmdLog wired into both the resilient runner (per-command
        # tracking) and the JobRunner (parent association for chunked
        # jobs). The Command Monitor (F2) renders this as a tree.
        self.cmd_log = CmdLog()
        self.p4 = P4Service(cmd_log=self.cmd_log)
        self.jobs = JobRunner(cmd_log=self.cmd_log)
        # Every shared-state JSON write goes through this — it lazily
        # creates one numbered CL, routes ``p4 reconcile`` into it, and
        # auto-submits on app exit with a detailed Korean description.
        # Avoiding the default CL is mandatory: ``admin@shared`` is
        # shared across concurrent sessions, so the default CL is global
        # and a sibling session's ``p4 submit -d`` would sweep our files
        # in. See ``p4v_tui/shared_state_cl.py``.
        self._shared_state_cl = SharedStateChangelist()
        # Picked at startup by the profile-discovery logic; used by the
        # connect worker to set port/user/client/charset.
        self.active_profile: ConnectionConfig | None = None
        # change# -> long description, populated whenever pending list reloads.
        self._pending_desc: dict[str, str] = {}
        # change# -> client (workspace) that owns the CL. Populated alongside
        # ``_pending_desc`` when the pending list reloads. Lets the context
        # menu / Enter-handler tell apart CLs created in the currently
        # connected workspace ("local") from CLs created in another
        # workspace of the same user ("remote"). Remote CLs can be viewed
        # and have their description edited, but Submit / Revert / Shelve
        # require switching to that workspace, so we gate those actions.
        self._pending_client_by_change: dict[str, str] = {}
        # Persisted UI state from prior launch (which tabs were active,
        # and the user's customized pane sizes).
        self._ui_state = load_state()
        # Set True while we apply restored state so the resulting
        # TabActivated events / size watchers don't re-save the same
        # value back.
        self._restoring_state = True
        # Pull persisted pane sizes — clamp into legal ranges so a
        # corrupted state file can't push the layout off-screen.
        try:
            self.left_pane_width = max(
                MIN_LEFT_WIDTH,
                min(MAX_LEFT_WIDTH,
                    int(self._ui_state.get("left_pane_width",
                                           DEFAULT_LEFT_WIDTH))),
            )
            self.detail_pane_height = max(
                MIN_DETAIL_HEIGHT,
                min(MAX_DETAIL_HEIGHT,
                    int(self._ui_state.get("detail_pane_height",
                                           DEFAULT_DETAIL_HEIGHT))),
            )
            self.log_panel_height = max(
                MIN_LOG_HEIGHT,
                min(MAX_LOG_HEIGHT,
                    int(self._ui_state.get("log_panel_height",
                                           DEFAULT_LOG_HEIGHT))),
            )
        except (TypeError, ValueError):
            # Bad value in state file → fall through to defaults.
            pass
        # Search index — lazily opened on first use. ``_search_index_status``
        # is a human-readable snapshot ("fresh, 124k files" / "Updating
        # 42%" / "offline + last-known") drawn into SearchModal's status
        # line.
        self._search_index: SearchIndex | None = None
        self._search_index_status: str = "(not yet initialized)"
        # History tab state — what the table is currently showing, so
        # "Refresh Folder History" / "Refresh Revision" menu items
        # know what to re-fetch. Updated by ``_render_folder_history``
        # and ``_render_history``.
        self._history_target: str | None = None
        self._history_is_folder: bool = False
        # Detail-pane file sort — applied in ``_render_detail`` and
        # persisted across launches. Driven by the Pending panel's
        # Shift+M "Sort Files By" submenu. Cached files / desc let
        # us re-render on sort change without re-fetching from p4.
        self._detail_files_sort: str = str(
            self._ui_state.get("detail_files_sort", "default")
        )
        self._last_detail_change: str | None = None
        self._last_detail_desc: str = ""
        self._last_detail_files: list[dict] = []
        # Narrow-mode navigator: the most recently visited non-tree page,
        # so the F3 / Ctrl+W quick-toggle can flip back to where the user
        # was instead of always defaulting to Pending.
        self._narrow_last_panel: str = narrow_nav.DEFAULT_PANEL_PAGE

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ConnectionBar(" Connecting…", id="conn_bar")
        with Horizontal(id="main"):
            with Vertical(id="left_pane"):
                with TabbedContent(initial="tab_depot", id="left_tabs"):
                    with TabPane("Depot", id="tab_depot"):
                        yield DepotTree("//", self.p4, id="depot_tree")
                    with TabPane("Workspace", id="tab_workspace"):
                        yield WorkspaceTree(self.p4, id="workspace_tree")
                yield TreeFilterOverlay(id="tree_filter_overlay")
            yield VerticalSplitter(id="main_splitter")
            with Vertical(id="right_pane"):
                with TabbedContent(initial="tab_pending", id="right_tabs"):
                    with TabPane("Pending Changelists", id="tab_pending"):
                        yield HScrollDataTable(
                            id="pending_table",
                            cursor_type="row",
                            zebra_stripes=True,
                        )
                    with TabPane("History", id="tab_history"):
                        yield HScrollDataTable(
                            id="history_table",
                            cursor_type="row",
                            zebra_stripes=True,
                        )
                    with TabPane("Submitted Changelists", id="tab_submitted"):
                        yield HScrollDataTable(
                            id="submitted_table",
                            cursor_type="row",
                            zebra_stripes=True,
                        )
                yield HorizontalSplitter(id="detail_splitter")
                with Vertical(id="detail_pane"):
                    yield Static(
                        " Select a changelist to view details.",
                        id="detail_desc",
                    )
                    yield HScrollDataTable(
                        id="detail_files",
                        cursor_type="row",
                        zebra_stripes=True,
                    )
        yield HorizontalSplitter(id="log_splitter")
        yield LogPanel(self.cmd_log, id="log_panel")
        yield Footer()

    # --- exception routing --------------------------------------------

    def _handle_exception(self, error: Exception) -> None:
        """Route unhandled exceptions to the in-app Log panel.

        Textual's default :meth:`App._handle_exception` calls
        :meth:`App._fatal_error`, which marks the app for exit and
        prints the full traceback to the terminal after the alt-screen
        is torn down. That terminal dump is the giant "separate
        panel" of red traceback that we explicitly don't want — the
        user has the Log panel for problem reporting and that's
        where these should land.

        Behaviour here:

        * format a one-line summary + the full traceback,
        * push them through :meth:`CmdLog.log_error` so the Log
          panel renders a ``✗`` red entry the user can scroll up
          to,
        * write the full traceback to
          ``~/.p4v-tui/last-error.log`` for offline diagnostics,
        * raise a transient toast,
        * **do not** chain to ``super()`` — that would re-trigger
          the fatal-exit path we're trying to bypass.

        The price of staying alive is that the offending widget may
        keep failing and flood the log; we dedupe consecutive
        identical summaries to keep the spam bounded.
        """
        import traceback as _tb
        from pathlib import Path as _Path

        try:
            tb_str = "".join(_tb.format_exception(error))
        except Exception:  # noqa: BLE001
            tb_str = repr(error)
        first_line = str(error).splitlines()[0] if str(error) else ""
        summary = f"{type(error).__name__}: {first_line}".strip(": ").strip()

        # Rate-limit: even when the summary changes every frame
        # (e.g. a render-loop exception whose message includes a
        # tick counter), refuse to flood the log / toast queue. A
        # 1-second min interval still gives the user visible signal
        # without each paint adding a new entry, which is what
        # previously turned "bad style string in a modal" into a
        # full-app hang.
        now = _time.monotonic() if (
            _time := __import__("time")) else 0.0
        last_at = getattr(self, "_last_exception_at", 0.0)
        prev = getattr(self, "_last_exception_summary", None)
        self._last_exception_summary = summary
        if summary != prev and (now - last_at) >= 1.0:
            self._last_exception_at = now
            try:
                self.cmd_log.log_error(summary, details=tb_str)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.notify(summary, severity="error", timeout=6)
            except Exception:  # noqa: BLE001
                pass

        # Persist a full traceback regardless of dedupe — overwriting
        # is fine; the user can grep / pipe / re-open the file.
        try:
            err_dir = _Path.home() / ".p4v-tui"
            err_dir.mkdir(parents=True, exist_ok=True)
            (err_dir / "last-error.log").write_text(tb_str)
        except Exception:  # noqa: BLE001
            pass

    def on_mount(self) -> None:
        # Bind user-defined macro keys before the screen is fully
        # interactive — sooner is fine, this is just a normal Binding
        # registration on the App's runtime BindingsMap.
        self._install_macro_bindings()
        # "Workspace" is the client that owns the CL. For the current
        # client it's rendered plain; for another of the user's clients
        # it's rendered in a dim/italic style with a "↗" prefix on the
        # Change cell so the row is visually distinct at a glance.
        self.query_one("#pending_table", DataTable).add_columns(
            "Change", "Workspace", "User", "Date", "Description")
        self.query_one("#submitted_table", DataTable).add_columns(
            "Change", "User", "Date", "Description")
        self.query_one("#history_table", DataTable).add_columns(
            "Rev", "Change", "Action", "Date", "User", "Description")
        self.query_one("#detail_files", DataTable).add_columns(
            "File", "Rev", "Action", "Type")
        self.jobs.start(on_progress=self._on_job_progress)
        # Set narrow mode based on initial terminal size — on_resize
        # might not fire at startup on every Textual version.
        try:
            self.narrow_mode = self.size.width < NARROW_TERMINAL_WIDTH
        except Exception:  # noqa: BLE001
            pass
        # Apply persisted pane sizes to the actual widgets. The
        # reactives were set in __init__ but their watchers fired
        # before compose() ran, so query_one() returned nothing and
        # the styles never landed on the widgets. Do it explicitly
        # now that the widgets exist.
        self._apply_persisted_pane_sizes()
        # Apply persisted tab selection + focus after the layout has
        # settled.
        self.call_after_refresh(self._restore_ui_state)
        # 1Hz tick captures focus changes (clicks, Tab cycle, F6
        # cycle, etc.) into _ui_state so the next launch can restore
        # the highlighted widget. Cheap — only writes to state.json
        # when the focused id actually changes and only for known
        # main-layout widgets.
        self.set_interval(1.0, self._poll_focused_widget)
        # Profile discovery: 0 → message + exit; 1 → auto-connect;
        # 2+ → modal picker, then connect with the picked one.
        self.call_after_refresh(self._begin_profile_discovery)

    # Main-layout widget ids that are meaningful to remember as
    # "the highlighted panel". Anything else (modal inputs, tabbed
    # content's internal tab buttons, etc.) is transient and not
    # worth persisting.
    _PERSISTENT_FOCUS_IDS = frozenset({
        "depot_tree", "workspace_tree",
        "pending_table", "submitted_table", "history_table",
        "detail_files", "log_panel",
    })

    def _apply_persisted_pane_sizes(self) -> None:
        """Push the saved pane sizes onto the live widgets.

        Belongs to ``on_mount`` because ``__init__`` set the reactives
        before ``compose()`` ran — the watchers tried to ``query_one``
        the panes when they didn't exist yet and silently swallowed
        the resulting NoMatches. Re-apply once we know the widgets are
        in the DOM.
        """
        try:
            if not self.narrow_mode:
                self.query_one("#left_pane").styles.width = (
                    self.left_pane_width
                )
        except Exception:  # noqa: BLE001
            pass
        try:
            self.query_one("#detail_pane").styles.height = (
                self.detail_pane_height
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            # In narrow mode the navigator owns the log panel's height
            # (hidden on tree/panel pages, 1fr on the log page); don't
            # stomp it with the persisted wide-mode height.
            if not self.narrow_mode:
                self.query_one("#log_panel").styles.height = (
                    self.log_panel_height
                )
        except Exception:  # noqa: BLE001
            pass

    def on_descendant_focus(self, event) -> None:
        """In narrow mode, follow keyboard focus to the visible pane.

        Two things are tracked while walking up the focused widget's
        parent chain:

        1. **Which pane** (#left_pane / #right_pane) the widget lives
           in. If the pane isn't on the current ``narrow_page``, we
           flip the navigator to the matching page so the user can
           actually see what's focused.

        2. **Which TabPane** wraps the widget (and the enclosing
           TabbedContent's id). Tab traversal can land focus on a
           widget inside an *inactive* tab — e.g. ``workspace_tree``
           even when the Depot tab is active, or ``pending_table``
           when Submitted is active. The user sees the same screen
           as before but their keystrokes go to an invisible
           widget. We activate the wrapping tab so the focused
           widget is on screen.

        Both pieces are non-essential UX. Any exception is swallowed
        rather than allowed to interrupt the focus event itself.
        """
        try:
            if not self.narrow_mode:
                return
            w = getattr(event, "widget", None)
            if w is None:
                return

            target_side: str | None = None
            # innermost TabPane the focused widget sits inside, plus
            # the TabbedContent id that owns it.
            tab_pane_id: str | None = None
            tabs_id: str | None = None

            cur = w
            while cur is not None:
                cid = getattr(cur, "id", None)
                cls_name = cur.__class__.__name__
                # Innermost TabPane wins — overwriting on an outer
                # one would point at the wrong layer in nested tabs
                # (the app currently has none, but be safe).
                if cls_name == "TabPane" and tab_pane_id is None:
                    tab_pane_id = cid
                if cls_name == "TabbedContent" and tabs_id is None:
                    tabs_id = cid
                if cid == "left_pane":
                    target_side = "left"
                    break
                if cid == "right_pane":
                    target_side = "right"
                    break
                cur = cur.parent

            # 1) Flip the narrow navigator to the page that owns the
            #    newly-focused widget, so it's actually on screen.
            if target_side == "left":
                if self.narrow_page != "tree":
                    self.narrow_page = "tree"
            elif target_side == "right":
                p = narrow_nav.page_for_right_tab(tab_pane_id)
                if p and self.narrow_page != p:
                    self.narrow_page = p
                elif not narrow_nav.is_panel_page(self.narrow_page):
                    self.narrow_page = self._narrow_last_panel

            # 2) Activate the enclosing tab if focus landed on an
            #    invisible sibling tab. Done after the pane flip so
            #    we don't activate a tab that's about to be hidden
            #    by the same focus event in the other pane.
            if tab_pane_id and tabs_id:
                try:
                    tc = self.query_one(
                        f"#{tabs_id}", TabbedContent,
                    )
                    if tc.active != tab_pane_id:
                        tc.active = tab_pane_id
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            # Focus chasing is a UX nicety — never let it crash the
            # app or interfere with the actual focus event.
            pass

    def _poll_focused_widget(self) -> None:
        """Save the focused widget's id when it changes.

        Polled at 1Hz from ``on_mount`` — a hook on every
        focus/blur event would also work but cross-version Textual
        wiring for descendant focus is fragile, and this poll is
        cheap (a single attribute read on a steady state). Only
        widgets in ``_PERSISTENT_FOCUS_IDS`` get saved so transient
        modal inputs don't overwrite the last meaningful focus."""
        if self._restoring_state:
            return
        f = self.focused
        if f is None:
            return
        fid = getattr(f, "id", None)
        if not fid or fid not in self._PERSISTENT_FOCUS_IDS:
            return
        if self._ui_state.get("focused_widget") == fid:
            return
        self._ui_state["focused_widget"] = fid
        save_state(self._ui_state)

    def _begin_profile_discovery(self) -> None:
        profiles = discover_profiles(self.config)
        if not profiles:
            err = (self.config.error
                   or "No Perforce server is configured.")
            self.notify(
                f"{err}\n\nNo P4PORT, no [connection] in p4v-tui.toml, "
                "no P4CONFIG. Set one and try again.",
                severity="error", timeout=20,
            )
            # Give the user a moment to read the toast before the app
            # process tears down.
            self.set_timer(2.0, self.exit)
            return
        if len(profiles) == 1:
            self.active_profile = profiles[0]
            self._connect_and_load()
            return

        def on_pick(picked: ConnectionConfig | None) -> None:
            if picked is None:
                self.notify("Cancelled. Exiting.",
                            severity="warning", timeout=4)
                self.set_timer(1.0, self.exit)
                return
            self.active_profile = picked
            self._connect_and_load()

        self.push_screen(ProfilePickerModal(profiles), on_pick)

    def on_unmount(self) -> None:
        # Belt-and-braces: when ``action_quit`` runs we already do
        # the heavy teardown on a worker before calling ``exit()``,
        # so on_unmount usually has nothing to do. Keep the call
        # though for the abnormal exit paths (SIGINT, panic) so
        # JobRunner worker threads still get joined.
        try:
            self.jobs.stop(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
        # Best-effort submit of the shared-state CL on abnormal exits.
        # The normal quit path runs this earlier (action_quit._shutdown)
        # so the connection is still live when we submit; here we may be
        # tearing down after a panic where p4 has already gone away — in
        # that case the call no-ops and the user gets a pending CL to
        # submit manually.
        self._submit_shared_state_cl_on_exit()
        try:
            self.p4.disconnect()
        except Exception:  # noqa: BLE001
            pass

    def _submit_shared_state_cl_on_exit(self) -> None:
        """Finalize the dedicated shared-state CL.

        Waits briefly for any in-flight reconcile worker to land, then
        rewrites the CL description with a per-file breakdown and runs
        ``p4 submit -c <CL>``. Failure surfaces a warning notification
        (when the UI is still alive) and leaves the CL pending so the
        user can submit it by hand — losing the submit is preferable to
        crashing the shutdown path.
        """
        cl = self._shared_state_cl
        if not cl.has_changes():
            return
        try:
            cl.wait_idle(timeout=3.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            submitted = cl.submit_if_dirty(self.p4)
        except Exception as exc:  # noqa: BLE001
            try:
                self.cmd_log.log_info(
                    f"shared-state CL submit failed: {exc!r}",
                )
            except Exception:  # noqa: BLE001
                pass
            return
        if submitted is not None:
            try:
                self.cmd_log.log_info(
                    f"shared-state CL {submitted} submitted on exit",
                )
            except Exception:  # noqa: BLE001
                pass

    def _restore_ui_state(self) -> None:
        try:
            left = self.query_one("#left_tabs", TabbedContent)
            right = self.query_one("#right_tabs", TabbedContent)
        except Exception:  # noqa: BLE001
            self._restoring_state = False
            return

        saved_left = self._ui_state.get("left_tab")
        saved_right = self._ui_state.get("right_tab")
        if saved_left and saved_left in {p.id for p in left.query("TabPane")}:
            left.active = saved_left
        if saved_right and saved_right in {p.id for p in right.query("TabPane")}:
            right.active = saved_right

        # Restore the highlighted panel — must run AFTER the tab
        # restoration above, because some widgets only exist inside
        # a specific TabPane (e.g. pending_table only when tab_pending
        # is active). focus() on a hidden widget is a no-op.
        saved_focus = self._ui_state.get("focused_widget")
        if saved_focus and saved_focus in self._PERSISTENT_FOCUS_IDS:
            try:
                self.query_one(f"#{saved_focus}").focus()
            except Exception:  # noqa: BLE001
                pass

        self._restoring_state = False

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated,
    ) -> None:
        if self._restoring_state:
            return
        tabs = event.tabbed_content
        active = tabs.active
        if tabs.id == "left_tabs":
            prev = self._ui_state.get("left_tab")
            # When the user flips between Depot and Workspace, mirror
            # the cursor position into the newly-shown tree so they
            # don't lose their place. Captures the OUTGOING cursor
            # path now (before persisting active) so we know where
            # the user was looking.
            if (prev and prev != active
                    and prev in ("tab_depot", "tab_workspace")
                    and active in ("tab_depot", "tab_workspace")):
                self._mirror_tree_cursor(prev, active)
            self._ui_state["left_tab"] = active
        elif tabs.id == "right_tabs":
            self._ui_state["right_tab"] = active
        else:
            return
        save_state(self._ui_state)

    def _cursor_path_for_tab(self, tab_id: str) -> tuple[str, bool] | None:
        """Return ``(depot_path, is_directory)`` for the cursor of the
        tree under ``tab_id``, or None if there's nothing to mirror.
        """
        widget_id = self._LEFT_TAB_TO_WIDGET.get(tab_id)
        if not widget_id:
            return None
        try:
            tree = self.query_one(widget_id)
        except Exception:  # noqa: BLE001
            return None
        node = getattr(tree, "cursor_node", None)
        if node is None or not node.data:
            return None
        # Don't mirror from the root — there's nothing meaningful to
        # point at on the other side.
        try:
            if node is tree.root:
                return None
        except Exception:  # noqa: BLE001
            pass
        path = str(node.data)
        if not path.startswith("//"):
            return None
        return (path, bool(node.allow_expand))

    @work(thread=True, group="tree_mirror", exclusive=True)
    def _mirror_tree_cursor(self, from_tab: str, to_tab: str) -> None:
        """Translate the outgoing tree's cursor path into the incoming
        tree's namespace and walk it there.

        Workspace and Depot trees use different roots
        (``//<client>/...`` vs ``//depot/...``) so we route through
        ``p4 where`` whenever crossing between them. Falls back to
        the closest-ancestor parent when the exact path isn't
        reachable in the destination tree.
        """
        snapshot = self._cursor_path_for_tab(from_tab)
        if snapshot is None:
            return
        from_path, _is_dir = snapshot

        try:
            info = self.p4.where(from_path)
        except Exception:  # noqa: BLE001
            info = None

        if to_tab == "tab_depot":
            translated = (info or {}).get("depotFile") if info else None
        else:
            translated = (info or {}).get("clientFile") if info else None

        # If `where` couldn't translate (path not in the workspace's
        # view, or an error), still navigate using the original path —
        # the destination tree's _navigate_step will settle on the
        # closest ancestor that exists.
        target = translated or from_path

        def kick(t: str = target, tab: str = to_tab) -> None:
            widget_id = self._LEFT_TAB_TO_WIDGET.get(tab)
            if not widget_id:
                return
            try:
                tree = self.query_one(widget_id)
            except Exception:  # noqa: BLE001
                return
            navigate = getattr(tree, "navigate_to_path", None)
            if navigate is None:
                return
            navigate(t)

        self.call_from_thread(kick)

    def _on_job_progress(self, job: Job) -> None:
        # The LogPanel listens directly to the CmdLog feed (JobRunner
        # already calls update_job_progress for every chunk), so we
        # don't need to push job state into a status widget here.
        # This handler just covers post-finalization side effects.
        if (
            isinstance(job, ResilientSubmitJob)
            and job.finished
            and not job.failed
        ):
            self.call_from_thread(self._load_pending)
            self.call_from_thread(self._load_submitted)

    # --- workers -----------------------------------------------------------

    @work(thread=True, exclusive=True)
    def _connect_and_load(self) -> None:
        # Prefer the explicitly-picked profile; fall back to the legacy
        # single [connection] table if the picker hasn't run (covers
        # tests that pre-build the App without going through discovery).
        profile = self.active_profile
        if profile is None:
            profile = self.config.connection
        try:
            self.p4.connect(
                port=profile.port,
                user=profile.user,
                client=profile.client,
                charset=profile.charset,
            )
            info = self.p4.info()
            login = self.p4.login_status()
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify, f"Connect failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.call_from_thread(self._on_connected, info, login)

    def _on_connected(self, info: P4Info, login: dict | None) -> None:
        self.query_one(ConnectionBar).update_info(info)
        # Backend identity goes to the LogPanel first — so when a
        # report says "this worked in P4Python but not CLI" we always
        # have a scrollback line confirming which one was active.
        try:
            self.cmd_log.log_info(
                f"Backend: {self.p4.backend_version()}",
            )
        except Exception:  # noqa: BLE001
            pass
        # Surface the active backend (P4Python / p4 CLI) in the Header's
        # title bar at a glance; the Log line above keeps the full detail.
        try:
            self.sub_title = self.p4.backend_name
        except Exception:  # noqa: BLE001
            pass
        # Config-load outcome goes to the Log panel as a historable
        # entry instead of a toast. Toasts cover the bottom of the
        # screen and disappear after a few seconds; long config paths
        # got truncated and the user had no way to refer back. The
        # LogPanel keeps the entry around with timestamp + click-
        # to-open detail. Errors still raise a toast in addition —
        # they need immediate attention, not a quiet log row.
        if self.config.error:
            self.notify(self.config.error, severity="error", timeout=10)
            try:
                self.cmd_log.log_error(
                    "config parse error", details=self.config.error,
                )
            except Exception:  # noqa: BLE001
                pass
        elif self.config.source is not None:
            try:
                self.cmd_log.log_info(
                    f"Loaded config: {self.config.source}",
                )
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                self.cmd_log.log_info(
                    "No config file found — using P4 environment.",
                )
            except Exception:  # noqa: BLE001
                pass
        if login is None:
            self.notify(
                "Not logged in. Run `p4 login` and restart.",
                severity="warning", timeout=10,
            )

        ws = self.query_one(WorkspaceTree)
        ws.configure_for_client(info.client)
        ws.bootstrap()
        depot = self.query_one(DepotTree)
        depot.bootstrap()
        # Kick off the search-index lifecycle: open the per-(server,
        # client) DB, run an incremental update in the background, and
        # if there's no index yet, queue a full build behind it. All
        # of this is handled by JobRunner so it interleaves with
        # interactive work and survives quit / reconnect.
        self._init_search_index(info)
        # Focus the tree inside whichever left tab is currently active —
        # which respects the user's restored selection from prior launch.
        try:
            left_tabs = self.query_one("#left_tabs", TabbedContent)
            if left_tabs.active == "tab_workspace":
                ws.focus()
            else:
                depot.focus()
        except Exception:  # noqa: BLE001
            depot.focus()
        self._load_pending()
        self._load_submitted()
        # Surface any chunked jobs that were interrupted on the previous
        # run so the user can resume / discard them.
        self._maybe_show_pending_jobs()
        # Kick off the periodic Pending-Changelists auto-refresh so
        # new CLs from other clients (or this client via the p4 CLI)
        # appear without the user pressing F5.
        self._start_pending_auto_refresh()

    # Active timer for the Pending Changelists auto-refresh. Held so
    # we can stop / restart it without leaking ticks if the user
    # changes the interval at runtime in a future Preferences pass.
    _pending_auto_refresh_timer = None

    def _pending_auto_refresh_interval(self) -> int:
        """Lookup the configured period in seconds.

        Read from ``state.json["auto_refresh_pending_seconds"]`` to
        keep this tunable without a Preferences UI for now; users
        who want a different cadence (or to disable entirely) can
        edit the file directly. Clamped to the range [5, 3600] so
        a silly value can't either DoS the server (1 s) or hide
        new CLs for a full day.
        """
        raw = self._ui_state.get(
            "auto_refresh_pending_seconds",
            DEFAULT_AUTO_REFRESH_PENDING_SEC,
        )
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_AUTO_REFRESH_PENDING_SEC
        if n <= 0:
            return 0    # explicit disable
        return max(5, min(3600, n))

    def _start_pending_auto_refresh(self) -> None:
        if self._pending_auto_refresh_timer is not None:
            return
        interval = self._pending_auto_refresh_interval()
        if interval <= 0:
            return
        self._pending_auto_refresh_timer = self.set_interval(
            float(interval), self._tick_pending_auto_refresh,
        )

    def _tick_pending_auto_refresh(self) -> None:
        """Fire one auto-refresh, skipping conditions that would
        either crash (disconnected) or duplicate work (load already
        in flight)."""
        try:
            if not self.p4.connected:
                return
        except Exception:  # noqa: BLE001
            return
        if self._pending_load_in_flight:
            return
        self._load_pending()

    # Guard so multiple overlapping ``_load_pending`` workers don't
    # race on the same DataTable. Set True at the top of the worker
    # body, False in the finally. The periodic refresher checks this
    # before kicking off a new load and just skips a tick if one is
    # already running.
    _pending_load_in_flight: bool = False

    @work(thread=True)
    def _load_pending(self) -> None:
        self._pending_load_in_flight = True
        try:
            # Fetch all of the current user's pending CLs across every
            # workspace they own on this server, not just the currently
            # connected one. The connected client's CLs show up as
            # "local"; everything else is rendered as "remote" with a
            # dim style + ↗ marker so the user can tell at a glance
            # which workspace each CL lives in. This is the p4v-tui
            # answer to the common multi-machine workflow gripe (left
            # an edit open on the laptop, came back to the desktop,
            # had no way to see it without manually switching clients).
            pending = self.p4.pending_changes(user=self.p4.user)
            default_files = self.p4.opened_in_change("default")
            self.call_from_thread(
                self._render_pending, pending, default_files,
            )
        finally:
            self._pending_load_in_flight = False

    def _render_pending(
        self,
        pending: list[dict],
        default_files: list[dict],
    ) -> None:
        # Local import: rich.text.Text is only needed for the per-row
        # dim/italic styling on remote CL rows. Keeping it scoped here
        # avoids dragging rich.text into the module-level import block
        # for one render path.
        from rich.text import Text

        table = self.query_one("#pending_table", DataTable)
        # Remember which CL (column 0) the cursor was sitting on so we
        # can restore the highlight after a refresh — otherwise the
        # auto-refresh tick would yank the user's selection back to
        # the top every 30 s. ``str(row[0])`` handles both plain
        # strings (local rows) and rich.text.Text cells (remote rows,
        # whose __str__ returns the underlying plain change number).
        prev_change: str | None = None
        if table.row_count > 0 and table.cursor_row is not None:
            try:
                row = table.get_row_at(table.cursor_row)
                if row:
                    prev_change = str(row[0])
            except Exception:  # noqa: BLE001
                prev_change = None

        table.clear()
        self._pending_desc.clear()
        self._pending_client_by_change.clear()

        cur_client = self.p4.client

        # Synthesize a "default" row when there are opened-in-default files,
        # since `p4 changes -s pending` never lists the default changelist.
        # The default CL is always per-client, so it can only ever be local
        # (it belongs to whichever client is currently connected).
        if default_files:
            self._pending_desc["default"] = "<default changelist>"
            self._pending_client_by_change["default"] = cur_client
            table.add_row(
                "default",
                cur_client,
                self.p4.user,
                "",
                f"<default changelist — {len(default_files)} file(s)>",
            )

        # Sort: local CLs first (so the user's own workspace is at the
        # top of the list, matching the pre-change behavior), then
        # remote CLs grouped by workspace. Within each group keep the
        # server's order (descending CL # — already implied by p4's
        # default sort on `p4 changes`).
        def _sort_key(row: dict) -> tuple[int, str]:
            row_client = row.get("client", "") or ""
            is_remote = row_client != cur_client
            return (1 if is_remote else 0, row_client)
        pending_sorted = sorted(pending, key=_sort_key)

        for r in pending_sorted:
            change = str(r.get("change", ""))
            user = r.get("user", "")
            row_client = r.get("client", "") or ""
            is_remote = bool(row_client) and row_client != cur_client
            t = r.get("time", "")
            date = (
                datetime.fromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M")
                if t else ""
            )
            desc_full = r.get("desc", "") or ""
            self._pending_desc[change] = desc_full
            self._pending_client_by_change[change] = row_client
            # Truncate by display cells, not character count: a Korean glyph
            # is 2 cells, so [:80] of CJK text would overflow the column.
            desc_first = truncate_cells(first_nonblank_line(desc_full), 80)

            ws_disp = _truncate_workspace(row_client)
            if is_remote:
                # Whole row dim+italic so the eye treats it as
                # background context. Workspace cell additionally
                # gets a bold yellow ↗ marker — that's the
                # "this CL lives somewhere else" anchor. Plain
                # ``str(...)`` of a Text returns the raw text, so
                # existing row[0] lookups (cursor restore, context
                # menu) keep working unchanged. The displayed name
                # is truncated so a long workspace like
                # "team-alpha-document-processor" doesn't blow
                # out the column — the full name is still on
                # `_pending_client_by_change` for menu titles and
                # toasts.
                table.add_row(
                    Text(change, style="dim italic"),
                    Text(f"↗ {ws_disp}", style="yellow bold"),
                    Text(user, style="dim italic"),
                    Text(date, style="dim italic"),
                    Text(desc_first, style="dim italic"),
                )
            else:
                table.add_row(change, ws_disp, user, date, desc_first)

        # Restore cursor on the same CL if it still exists. If it's
        # gone (submitted / deleted by another client) the cursor
        # falls to row 0 by default and the detail pane gets cleared
        # below. If we restored, the table's RowHighlighted message
        # will fire and the existing handler re-populates detail.
        restored = False
        if prev_change is not None and table.row_count > 0:
            for i in range(table.row_count):
                try:
                    row = table.get_row_at(i)
                except Exception:  # noqa: BLE001
                    continue
                if row and str(row[0]) == prev_change:
                    try:
                        table.move_cursor(row=i)
                    except Exception:  # noqa: BLE001
                        pass
                    restored = True
                    break

        if not restored:
            self.query_one("#detail_desc", Static).update(
                " Select a changelist to view details."
            )
            self.query_one("#detail_files", DataTable).clear()

    def _is_remote_pending(self, change: str) -> bool:
        """True iff ``change`` is a pending CL owned by the user but
        living in a *different* workspace than the one we're currently
        connected to.

        Returns False for the synthetic "default" row (always local —
        the default CL is per-client and we only ever surface the
        current client's default), for unknown CLs (be conservative —
        treat as local so an existing action doesn't suddenly start
        refusing), and for empty/missing workspace metadata.
        """
        if not change or change == "default":
            return False
        row_client = self._pending_client_by_change.get(change)
        if not row_client:
            return False
        return row_client != self.p4.client

    def _remote_workspace_note(self, change: str) -> str:
        """One-line user-facing description of where a remote CL lives.
        Empty string for local CLs."""
        row_client = self._pending_client_by_change.get(change, "")
        if not row_client or row_client == self.p4.client:
            return ""
        return (
            f"CL {change} belongs to workspace '{row_client}'. "
            f"Switch to that workspace to submit / revert / shelve."
        )

    # --- submitted loading ------------------------------------------------

    @work(thread=True)
    def _load_submitted(self) -> None:
        rows = self.p4.submitted_changes(max_count=100)
        self.call_from_thread(self._render_submitted, rows)

    def _render_submitted(self, rows: list[dict]) -> None:
        table = self.query_one("#submitted_table", DataTable)
        table.clear()
        for r in rows:
            change = str(r.get("change", ""))
            user = r.get("user", "")
            t = r.get("time", "")
            date = (
                datetime.fromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M")
                if t else ""
            )
            desc_first = truncate_cells(
                first_nonblank_line(r.get("desc", "") or ""), 80,
            )
            table.add_row(change, user, date, desc_first)

    # --- file viewer (Enter on a tree leaf) ------------------------------

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        # Triggered by Enter / click on a tree node. We only handle leaf
        # nodes whose data looks like a depot path (filters out the
        # cmd-monitor's tree etc.).
        node = event.node
        if node is None or not node.data:
            return
        if node.allow_expand:
            return  # directory — let the tree handle expand/collapse
        depot_file = str(node.data)
        if not depot_file.startswith("//"):
            return
        self._open_file_viewer(depot_file)

    @work(thread=True, group="view_file")
    def _open_file_viewer(self, depot_file: str) -> None:
        try:
            result = self.p4.run("print", "-q", depot_file)
        except P4Exception as e:
            self.call_from_thread(
                self.notify, f"Read failed: {e}",
                severity="error", timeout=8,
            )
            return
        # `p4 print -q` returns the metadata dict followed by content
        # parts (str for text types, bytes for binary). Concatenate and
        # decode bytes leniently — replacement chars are fine for the
        # binary-detection heuristic below.
        parts: list[str] = []
        for item in result:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, (bytes, bytearray)):
                try:
                    parts.append(bytes(item).decode("utf-8"))
                except UnicodeDecodeError:
                    parts.append(bytes(item).decode("utf-8", errors="replace"))
        content = "".join(parts)

        # Heuristic: if the first 8KiB has more than 1% NUL bytes, treat
        # as binary and refuse to render the raw bytes (which would just
        # be noise). Most text/utf8 files have zero NULs.
        sample = content[:8192]
        if sample and sample.count("\x00") > max(1, len(sample) // 100):
            content = (
                f"[Binary file — {len(content)} bytes]\n"
                "Cannot display in text viewer."
            )

        # narrow=True so the modal sits on the right half of the
        # screen — the left-pane tree stays visible behind it and the
        # user keeps their navigation context. ``filename`` opts the
        # viewer into syntax highlighting based on the depot file's
        # extension (skipped for plain logs / binaries / huge files).
        self.call_from_thread(
            self.push_screen,
            FileViewerModal(
                depot_file, content, narrow=True, filename=depot_file,
            ),
        )

    # --- file history -----------------------------------------------------

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        node = event.node
        if node is None or not node.data:
            return
        path = str(node.data)
        if not path.startswith("//"):
            return
        # Skip the tree's root — its history would be every change in
        # the entire depot/workspace and would just spam the panel.
        try:
            if node is event.node.tree.root:
                return
        except Exception:  # noqa: BLE001
            pass
        if node.allow_expand:
            # Directory: folder history (one row per CL touching the
            # subtree). Same target shape as Ctrl+T.
            self._load_folder_history(f"{path}/...")
        else:
            self._load_file_history(path)

    @work(thread=True, exclusive=True, group="history")
    def _load_file_history(self, depot_file: str) -> None:
        rows = self.p4.filelog(depot_file, max_revs=50)
        self.call_from_thread(self._render_history, depot_file, rows)

    # --- folder history (Ctrl+T) ----------------------------------------

    def action_show_folder_history(self) -> None:
        """Switch to History tab and load it for the focused tree node.

        Files use ``p4 filelog`` (existing flow). Folders use ``p4 changes
        <path>/... -L -m 50`` and render the per-changelist rows in the
        same History table (Rev / Action columns left empty since they're
        per-file concepts).
        """
        f = self.focused
        node = getattr(f, "cursor_node", None)
        if node is None or not node.data:
            self.notify(
                "Select a file or folder in a tree first.", timeout=3,
            )
            return
        is_dir = bool(node.allow_expand)
        target = f"{node.data}/..." if is_dir else str(node.data)
        try:
            self.query_one("#right_tabs", TabbedContent).active = "tab_history"
        except Exception:  # noqa: BLE001
            pass
        if is_dir:
            self._load_folder_history(target)
        else:
            self._load_file_history(target)

    @work(thread=True, exclusive=True, group="history")
    def _load_folder_history(self, target: str) -> None:
        try:
            rows = self.p4.run("changes", "-L", "-m", "50", target)
        except P4Exception:
            rows = []
        self.call_from_thread(self._render_folder_history, target, rows)

    # The History table swaps its column set depending on whether the
    # current target is a single file (per-rev rows from
    # ``p4 filelog`` — Rev / Action are meaningful per row) or a
    # folder (per-CL rows from ``p4 changes -L`` — neither Rev nor
    # Action have a per-row value, they're CL-level concepts). p4v
    # itself leaves the file-history layout but with empty cells;
    # we'd rather drop the empty columns entirely so the table
    # doesn't waste two columns of width for blank data.
    _HISTORY_FILE_COLS = ("Rev", "Change", "Action", "Date", "User",
                          "Description")
    _HISTORY_FOLDER_COLS = ("Change", "Date", "User", "Description")

    def _reset_history_columns(self, is_folder: bool) -> None:
        table = self.query_one("#history_table", DataTable)
        # Only rebuild if the schema actually changed — DataTable
        # tracks columns by an internal key; recreating them on every
        # render leaks key churn and resets the user's column-width
        # bookkeeping.
        desired = (
            self._HISTORY_FOLDER_COLS if is_folder
            else self._HISTORY_FILE_COLS
        )
        current = tuple(
            str(c.label) for c in getattr(table, "columns", {}).values()
        ) if hasattr(table, "columns") else ()
        if current == desired:
            table.clear()
            return
        table.clear(columns=True)
        table.add_columns(*desired)

    def _render_folder_history(
        self, target: str, rows: list,
    ) -> None:
        # Remember what the History tab is currently showing so the
        # "Refresh Folder History" / "Refresh Revision" menu items
        # can re-fetch the same target on demand.
        self._history_target = target
        self._history_is_folder = True
        self._reset_history_columns(is_folder=True)
        table = self.query_one("#history_table", DataTable)
        for r in rows:
            if not isinstance(r, dict):
                continue
            change = str(r.get("change", ""))
            user = r.get("user", "")
            t = r.get("time", "")
            date = (
                datetime.fromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M")
                if t else ""
            )
            desc_first = truncate_cells(
                first_nonblank_line(r.get("desc", "") or ""), 60,
            )
            # Folder schema: Change, Date, User, Description (Rev /
            # Action columns are absent — they don't exist for a
            # changelist-level row, and leaving them blank just
            # wasted column width).
            table.add_row(change, date, user, desc_first)

    def _render_history(self, depot_file: str, rows: list[dict]) -> None:
        self._history_target = depot_file
        self._history_is_folder = False
        self._reset_history_columns(is_folder=False)
        table = self.query_one("#history_table", DataTable)
        for r in rows:
            t = r.get("time", "")
            date = (
                datetime.fromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M")
                if t else ""
            )
            desc_first = truncate_cells(
                first_nonblank_line(r.get("desc", "") or ""), 60,
            )
            table.add_row(
                f"#{r.get('rev', '')}",
                r.get("change", ""),
                r.get("action", ""),
                date,
                r.get("user", ""),
                desc_first,
            )

    # --- file actions ------------------------------------------------------

    # Actions whose result is destructive — require explicit confirm.
    _CONFIRM_ACTIONS = {"revert", "delete"}

    def on_file_action_requested(self, event: FileActionRequested) -> None:
        action, target = event.action, event.target
        source_node = event.source_node

        # Long-running chunked variants are dispatched to the JobRunner
        # rather than executed inline — they survive interruptions and
        # don't block the rest of the UI.
        if action == "chunked_sync":
            self._enqueue_chunked(
                ChunkedSyncJob(
                    self.p4, target,
                    strategy=self._chunking_for("sync"),
                ),
                target,
            )
            return
        if action == "chunked_force_sync":
            self._enqueue_chunked(
                ChunkedSyncJob(
                    self.p4, target, force=True,
                    strategy=self._chunking_for("force_sync"),
                ),
                target,
            )
            return
        if action == "chunked_reconcile":
            self._enqueue_chunked(
                ChunkedReconcileJob(
                    self.p4, target,
                    strategy=self._chunking_for("reconcile"),
                ),
                target,
            )
            return
        if action == "chunked_revert":
            self._confirm_then_enqueue(
                ChunkedRevertJob(
                    self.p4, target,
                    strategy=self._chunking_for("revert"),
                ),
                title=f"Revert all opened files under {target}?",
                message=(
                    "Discard pending edits to every file opened in this "
                    "subtree. Cannot be undone."
                ),
                ok_label="Revert",
            )
            return
        if action == "chunked_clean":
            self._confirm_then_enqueue(
                ChunkedCleanJob(
                    self.p4, target,
                    strategy=self._chunking_for("clean"),
                ),
                title=f"Clean {target}?",
                message=(
                    "Restore locally-modified files and DELETE files unknown "
                    "to the depot under this subtree. Cannot be undone."
                ),
                ok_label="Clean",
            )
            return
        if action in ("integrate", "copy", "branch"):
            self._open_bci_modal(action, target)
            return
        if action == "resolve":
            self._open_resolve_modal(target)
            return
        if action in ("show_in", "open_cmd"):
            self._run_fs_handoff(action, target)
            return
        if action == "open_with":
            self._open_with_picker(target)
            return
        if action == "annotate":
            self._open_annotate(target)
            return
        if action == "timelapse":
            self._open_timelapse(target)
            return
        if action == "rev_graph":
            self._open_rev_graph(target)
            return
        if action == "file_props":
            self._open_file_properties(target)
            return
        if action == "undo":
            self._confirm_undo_file(target)
            return
        if action == "diff_have":
            self._diff_against_have(target)
            return
        if action == "get_revision":
            self._open_get_revision(target)
            return
        if action == "copy_path":
            self._copy_text(target, "Depot path")
            return
        if action == "copy_permalink":
            self._copy_permalink(target)
            return
        if action == "bookmark_add":
            self._add_bookmark(target)
            return
        if action == "copy_swarm":
            self._copy_swarm_url(target, event.is_directory)
            return
        if action == "rename":
            self._open_rename_modal(target, is_directory=event.is_directory)
            return
        if action == "quick_rename":
            self._open_quick_rename(target, is_directory=event.is_directory)
            return

        if action in self._CONFIRM_ACTIONS:
            self.push_screen(
                ConfirmModal(
                    title=f"{action.capitalize()} {target}?",
                    message=self._confirm_message_for(action, target),
                    ok_label=action.capitalize(),
                    ok_variant="error",
                ),
                callback=lambda yes, a=action, t=target, n=source_node:
                    self._run_file_action(a, t, n) if yes else None,
            )
        else:
            self._run_file_action(action, target, source_node)

    @staticmethod
    def _confirm_message_for(action: str, target: str) -> str:
        body = f"  {target}\n\n"
        if action == "revert":
            return (
                f"This will revert the following target:\n\n{body}"
                "Pending edits to these files will be lost."
            )
        if action == "delete":
            return (
                f"This will mark the following target for delete:\n\n{body}"
                "Files stay on disk until you submit; the local copy will "
                "then be removed and the depot version marked deleted."
            )
        return f"Run `p4 {action} {target}`?"

    # Verbs that *open files for change* — these must never land in
    # the default changelist because another tool (p4v, p4 CLI,
    # automation) sharing this workspace can be using it
    # concurrently and the pool would mix unrelated work into one
    # submit. Per the project policy "디폴트 체인지리스트를 사용
    # 하면 안됩니다" the app creates a single-target numbered CL up
    # front and passes ``-c <CL#>`` to the verb. Verbs that don't
    # open files (sync / revert / lock-on-existing) are unaffected.
    _ISOLATE_TO_NEW_CL = {
        "edit":   "Check out",
        "add":    "Mark for add",
        "delete": "Mark for delete",
    }

    @work(thread=True, group="p4_action")
    def _run_file_action(
        self,
        action: str,
        target: str,
        source_node,
    ) -> None:
        cl_args: tuple[str, ...] = ()
        if action in self._ISOLATE_TO_NEW_CL:
            try:
                desc = f"{self._ISOLATE_TO_NEW_CL[action]}: {target}"
                new_cl = self.p4.create_changelist(desc)
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(
                    self.notify,
                    f"{action} failed (creating CL): {e}",
                    severity="error", timeout=10,
                )
                return
            cl_args = ("-c", str(new_cl))
        try:
            result = self.p4.run(action, *cl_args, target)
            summary = self._summarize_result(result)
            self.call_from_thread(
                self.notify,
                f"{action} {target} — {summary}"
                + (f"  (CL {cl_args[1]})" if cl_args else ""),
                timeout=6,
            )
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"{action} failed: {e}",
                severity="error", timeout=10,
            )
        self.call_from_thread(self._refresh_after_action, source_node)

    @staticmethod
    def _summarize_result(result: list) -> str:
        if not result:
            return "no changes"
        # P4Python returns mixed strings + dicts. Count files acted on.
        n = sum(1 for r in result if isinstance(r, dict))
        return f"{n} file(s)" if n else f"{len(result)} message(s)"

    def _refresh_after_action(self, source_node) -> None:
        ws = self.query_one(WorkspaceTree)
        # Reload the source node's parent — that's the level whose listing
        # contains the affected entries.
        if source_node is not None and source_node.parent is not None:
            ws.reload_node(source_node.parent)
        else:
            ws.refresh_root()
        self._load_pending()
        # Reload may have invalidated the node identity that was holding
        # focus. Bring focus back to the tree so subsequent key bindings
        # (Menu, Check Out, etc.) keep working without an extra click.
        ws.focus()

    # --- bulk file actions over a tree multi-selection (item 4) --------

    def on_bulk_file_action_requested(
        self, event: BulkFileActionRequested,
    ) -> None:
        action, targets = event.action, event.targets
        if not targets:
            return
        if action in self._CONFIRM_ACTIONS:
            shown = "\n".join(f"  {t}" for t in targets[:12])
            if len(targets) > 12:
                shown += f"\n  … and {len(targets) - 12} more"
            self.push_screen(
                ConfirmModal(
                    title=f"{action.capitalize()} {len(targets)} target(s)?",
                    message=(
                        f"This will {action} the following:\n\n{shown}\n\n"
                        "Pending edits to these files will be lost."
                    ),
                    ok_label=action.capitalize(),
                    ok_variant="error",
                ),
                callback=lambda yes, a=action, ts=targets:
                    self._run_bulk_file_action(a, ts) if yes else None,
            )
        else:
            self._run_bulk_file_action(action, targets)

    @work(thread=True, group="p4_action")
    def _run_bulk_file_action(self, action: str, targets: list[str]) -> None:
        cl_args: tuple[str, ...] = ()
        if action in self._ISOLATE_TO_NEW_CL:
            try:
                desc = f"{self._ISOLATE_TO_NEW_CL[action]}: {len(targets)} file(s)"
                new_cl = self.p4.create_changelist(desc)
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(
                    self.notify, f"Bulk {action} failed (creating CL): {e}",
                    severity="error", timeout=10,
                )
                return
            cl_args = ("-c", str(new_cl))
        try:
            result = self.p4.run(action, *cl_args, *targets)
            summary = self._summarize_result(result)
            self.call_from_thread(
                self.notify,
                f"Bulk {action}: {summary} over {len(targets)} target(s)"
                + (f"  (CL {cl_args[1]})" if cl_args else ""),
                timeout=6,
            )
        except P4Exception as e:
            self.call_from_thread(
                self.notify, f"Bulk {action} failed: {e}",
                severity="error", timeout=10,
            )
        self.call_from_thread(self._refresh_after_action, None)

    # --- global actions ----------------------------------------------------

    def action_refresh(self) -> None:
        self._load_pending()
        self._load_submitted()
        self.query_one(WorkspaceTree).refresh_root()
        self.query_one(DepotTree).refresh_root()

    def _panel_widgets(self) -> list:
        """Return the focusable widget representing each major panel.

        Panels are: active left tree, active right-top table, detail files
        table. Whichever tab is currently selected on each side determines
        which widget plays the role of "the panel".
        """
        out = []
        try:
            left_tabs = self.query_one("#left_tabs", TabbedContent)
            out.append(self.query_one(
                self._LEFT_TAB_TO_WIDGET.get(left_tabs.active, "#depot_tree")
            ))
        except Exception:  # noqa: BLE001
            pass
        try:
            right_tabs = self.query_one("#right_tabs", TabbedContent)
            out.append(self.query_one(
                self._RIGHT_TAB_TO_WIDGET.get(right_tabs.active, "#pending_table")
            ))
        except Exception:  # noqa: BLE001
            pass
        try:
            out.append(self.query_one("#detail_files", DataTable))
        except Exception:  # noqa: BLE001
            pass
        return out

    def _cycle_panel(self, step: int) -> None:
        panels = self._panel_widgets()
        if not panels:
            return
        current = self.focused
        idx = -1
        for i, p in enumerate(panels):
            if current is p:
                idx = i
                break
        next_idx = (idx + step) % len(panels) if idx >= 0 else 0
        panels[next_idx].focus()

    def action_focus_next_panel(self) -> None:
        self._cycle_panel(1)

    def action_focus_prev_panel(self) -> None:
        self._cycle_panel(-1)

    # --- Perforce-aware clipboard (Ctrl+C / X / V on a tree) ----------
    #
    # Single-slot in-app clipboard. Ctrl+C stores a source path with
    # mode "copy"; Ctrl+X stores it with mode "move". Ctrl+V on any
    # tree node combines that source with the cursor's path to form
    # a destination, runs the matching p4 verb (copy / move) inside
    # a fresh numbered changelist, then queues a resilient submit.
    _p4_clipboard: dict | None = None

    def on_p4_clipboard_action(self, event: P4ClipboardAction) -> None:
        if event.op == "copy":
            self._p4_clipboard = {
                "mode": "copy",
                "source": event.path,
                "is_directory": event.is_directory,
            }
            self.notify(
                f"Copied (p4): {event.path}    "
                "Move cursor to destination and press Ctrl+V to paste.",
                timeout=6,
            )
            return
        if event.op == "cut":
            self._p4_clipboard = {
                "mode": "move",
                "source": event.path,
                "is_directory": event.is_directory,
            }
            self.notify(
                f"Cut (p4): {event.path}    "
                "Move cursor to destination and press Ctrl+V to move.",
                timeout=6,
            )
            return
        if event.op == "paste":
            self._handle_clipboard_paste(event)

    def _handle_clipboard_paste(self, event: P4ClipboardAction) -> None:
        clip = self._p4_clipboard
        if not clip:
            self.notify(
                "Clipboard is empty. Press Ctrl+C on a source first.",
                severity="warning", timeout=5,
            )
            return
        src_path = clip["source"]
        src_is_dir = clip["is_directory"]
        mode = clip["mode"]

        # Compute the destination: the source's leaf name is placed
        # under the cursor's parent directory.
        dest_parent = (
            event.path if event.is_directory
            else event.path.rsplit("/", 1)[0]
        )
        if not dest_parent or not dest_parent.startswith("//"):
            self.notify(
                "Could not compute paste destination — cursor is "
                "not on a valid depot location.",
                severity="error", timeout=8,
            )
            return
        leaf = src_path.rstrip("/").rsplit("/", 1)[-1]
        dest_path = f"{dest_parent.rstrip('/')}/{leaf}"
        if dest_path == src_path:
            self.notify(
                "Destination is identical to source — nothing to do.",
                severity="warning", timeout=4,
            )
            return

        def on_close(yes: bool) -> None:
            if not yes:
                return
            self._run_clipboard_paste(
                mode, src_path, src_is_dir, dest_path,
            )

        verb = "Copy" if mode == "copy" else "Move"
        message = (
            f"{verb} (Perforce):\n\n"
            f"  source:      {src_path}"
            f"{' (directory)' if src_is_dir else ''}\n"
            f"  destination: {dest_path}\n\n"
            f"A new changelist will be created, the {verb.lower()} "
            "will be performed inside it, and that changelist will "
            "be auto-submitted via ResilientSubmitJob."
        )
        self.push_screen(
            ConfirmModal(
                title=f"{verb} and submit?",
                message=message,
                ok_label=f"{verb} & Submit",
                ok_variant="primary",
            ),
            callback=on_close,
        )

    @work(thread=True, group="p4_clipboard_paste")
    def _run_clipboard_paste(
        self,
        mode: str,
        src_path: str,
        src_is_dir: bool,
        dest_path: str,
    ) -> None:
        # Spec form: directories get /... appended on both sides so
        # the operation recurses; files use the plain depot path.
        if src_is_dir:
            src_spec = f"{src_path.rstrip('/')}/..."
            dst_spec = f"{dest_path.rstrip('/')}/..."
        else:
            src_spec = src_path
            dst_spec = dest_path

        verb = "Copy" if mode == "copy" else "Move"
        desc = (
            f"p4v-tui clipboard {verb.lower()}:\n"
            f"  {src_spec}\n"
            f"  -> {dst_spec}\n"
        )
        try:
            new_cl = self.p4.create_changelist(desc)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"Create CL for clipboard {mode} failed: {e}",
                severity="error", timeout=10,
            )
            return

        try:
            if mode == "copy":
                self.p4.run(
                    "copy", "-c", new_cl, src_spec, dst_spec,
                )
            else:
                # `p4 move` requires the source to be opened for
                # edit; open inside the same CL so a single submit
                # carries the whole rename.
                self.p4.run("edit", "-c", new_cl, src_spec)
                self.p4.run(
                    "move", "-c", new_cl, src_spec, dst_spec,
                )
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"{verb} {src_spec} -> {dst_spec} failed: {e}",
                severity="error", timeout=10,
            )
            return

        # Queue the resilient submit — survives reconnects and
        # idempotently recognizes lost-ack on retry.
        def queue_submit(cl: str = new_cl) -> None:
            job = ResilientSubmitJob(self.p4, cl)
            self.jobs.submit_job(job)
            self.notify(
                f"Queued resilient submit for CL {cl} "
                f"({verb.lower()} {src_path} → {dest_path}).",
                timeout=8,
            )

        self.call_from_thread(queue_submit)

        # Cut is a one-shot operation; clear the clipboard so a
        # follow-up V doesn't accidentally re-paste at a new cursor.
        # Copy stays in the clipboard so the user can paste multiple
        # times.
        if mode == "move":
            self._p4_clipboard = None

    # --- tree filter overlay -------------------------------------------

    _filter_target: Any = None  # which tree the overlay is currently
    # routing into (Workspace or Depot). Reset on close.

    def on_tree_filter_requested(self, event: TreeFilterRequested) -> None:
        """A tree's `/` shortcut was hit — show the overlay and bind
        its events to that specific tree."""
        self._filter_target = event.tree
        try:
            overlay = self.query_one(
                "#tree_filter_overlay", TreeFilterOverlay,
            )
        except Exception:  # noqa: BLE001
            return
        overlay.show_for()

    def on_tree_filter_overlay_filter_changed(
        self, event: "TreeFilterOverlay.FilterChanged",
    ) -> None:
        target = self._filter_target
        if target is None:
            return
        try:
            target.apply_filter(event.query)
        except Exception:  # noqa: BLE001
            pass

    def on_tree_filter_overlay_filter_closed(
        self, event: "TreeFilterOverlay.FilterClosed",
    ) -> None:
        target = self._filter_target
        if target is None:
            return
        if event.restored:
            try:
                target.apply_filter("")
            except Exception:  # noqa: BLE001
                pass
        # Return focus to the tree so subsequent keys hit it again.
        try:
            target.focus()
        except Exception:  # noqa: BLE001
            pass
        self._filter_target = None

    def action_show_preferences(self) -> None:
        """Open the in-app TOML editor."""
        def on_close(new_cfg) -> None:
            if new_cfg is None:
                return
            # Hot-swap the live config so chunking changes take effect
            # for the next bulk job. Connection-table edits show up
            # only on next launch — surface that explicitly.
            self.config = new_cfg
            self.notify(
                f"Preferences saved → {new_cfg.source}. "
                "Connection changes apply on next launch.",
                timeout=6,
            )

        self.push_screen(PreferencesModal(self.config), on_close)

    # Tracks whether a quit is already in flight so a second Q during
    # the teardown delay doesn't push another modal or queue another
    # exit timer.
    _quitting: bool = False

    def action_quit(self) -> None:
        """Show an "Exiting…" modal immediately, then run the full
        teardown synchronously *before* leaving the alt-screen.

        Earlier versions called ``self.exit()`` after a 200 ms
        cosmetic delay and let on_unmount handle JobRunner / P4
        shutdown afterwards. That meant the alt-screen tore down
        while a slow ``p4`` RPC was still in flight, the user
        landed at their CLI prompt with a several-second period
        of unresponsiveness while threads / connections wound
        down in the background.

        The current flow:
          1. push QuittingModal (visual confirmation Q registered).
          2. on a daemon worker, run jobs.stop + p4.disconnect to
             completion. The modal is visible the whole time.
          3. once cleanup returns, call_from_thread schedules
             ``self.exit()`` — Textual restores the terminal and
             returns to CLI, by which point there is nothing left
             to clean up.

        Net effect: the moment the CLI prompt appears, typing
        works immediately.
        """
        if self._quitting:
            return
        self._quitting = True
        try:
            self.push_screen(QuittingModal())
        except Exception:  # noqa: BLE001
            # Pushing a modal during teardown can race with screen
            # cleanup; if it fails just exit immediately rather than
            # blocking the user.
            self.exit()
            return
        # Run the heavy cleanup in a background thread so the
        # QuittingModal animation / paint actually shows. Then
        # marshal exit() back onto the UI thread.
        import threading as _threading

        def _shutdown() -> None:
            try:
                self.jobs.stop(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            # Submit the dedicated shared-state CL BEFORE disconnecting —
            # the submit obviously needs a live connection. on_unmount
            # also calls this as a fallback for abnormal exits where we
            # never get here.
            self._submit_shared_state_cl_on_exit()
            try:
                self.p4.disconnect()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.call_from_thread(self.exit)
            except Exception:  # noqa: BLE001
                # App is already tearing down — abandon ship.
                pass

        # Daemon so an interpreter-level KeyboardInterrupt while
        # waiting on jobs.stop doesn't hang the process.
        _threading.Thread(
            target=_shutdown, daemon=True, name="p4v-tui-quit",
        ).start()

    def _chunking_for(self, job_kind: str):
        """Resolve the chunking strategy for ``job_kind``.

        Falls through to the global default if no per-job override
        exists. Returning the strategy object — not a copy — is fine;
        :class:`ChunkingStrategy` is frozen.
        """
        return self.config.chunking.for_job(job_kind)

    def _enqueue_chunked(self, job: Job, target: str) -> None:
        self.jobs.submit_job(job)
        # Surface the active chunking shape so the user can confirm it
        # matched their config without digging into the command monitor.
        strategy = getattr(job, "strategy", None)
        suffix = f" ({strategy.describe()})" if strategy is not None else ""
        self.notify(f"Queued: {job.name}{suffix}", timeout=4)

    def _confirm_then_enqueue(
        self,
        job: Job,
        *,
        title: str,
        message: str,
        ok_label: str,
    ) -> None:
        def on_close(yes: bool, j: Job = job) -> None:
            if yes:
                self.jobs.submit_job(j)
                strategy = getattr(j, "strategy", None)
                suffix = (
                    f" ({strategy.describe()})"
                    if strategy is not None else ""
                )
                self.notify(f"Queued: {j.name}{suffix}", timeout=4)

        self.push_screen(
            ConfirmModal(
                title=title, message=message,
                ok_label=ok_label, ok_variant="error",
            ),
            callback=on_close,
        )

    # --- "View Submitted/Pending Changelist" -- switch tab + select row -

    def _view_submitted_cl(self, change: str) -> None:
        """Tab over to Submitted Changelists and put the cursor on the
        row for ``change``. The detail pane below the table reacts to
        cursor moves and shows description + file list — which is the
        TUI analog of p4v's "View Submitted Changelist" window."""
        self._focus_change_row("submitted", change)

    def _view_pending_cl(self, change: str) -> None:
        """Same as :meth:`_view_submitted_cl` but for the Pending tab."""
        self._focus_change_row("pending", change)

    def _focus_change_row(self, tab: str, change: str) -> None:
        tab_id = f"tab_{tab}"
        table_id = f"#{tab}_table"
        try:
            tabs = self.query_one("#right_tabs", TabbedContent)
            tabs.active = tab_id
            table = self.query_one(table_id, DataTable)
        except Exception:  # noqa: BLE001
            self.notify(f"Couldn't open {tab} tab.",
                        severity="warning", timeout=4)
            return
        target = str(change)
        for r in range(table.row_count):
            try:
                row = table.get_row_at(r)
            except Exception:  # noqa: BLE001
                continue
            if row and str(row[0]) == target:
                table.move_cursor(row=r)
                table.focus()
                return
        self.notify(
            f"CL {change} not in the {tab} list right now — refresh "
            f"({tab} list) and try again.",
            severity="warning", timeout=4,
        )

    # --- Submitted-CL Merge / Copy via BCI modal ------------------------

    def _open_bci_for_cl(self, operation: str, change: str) -> None:
        """Pop the BCI modal for a Merge/Integrate or Copy whose
        revision range scopes to a single submitted changelist.

        We pre-fill the Source field with the revision-range form
        ``@={CL},@={CL}`` (just like p4v's right-click flow) — the
        user pastes the depot path in front of it and types the
        destination on the next line."""
        source = f"@={change},@={change}"
        self._open_bci_modal(operation, target="", source=source)

    # --- Re-resolve a pending CL ---------------------------------------

    def _confirm_re_resolve_cl(self, change: str) -> None:
        """``p4 resolve -f -c <CL>`` re-runs resolve on previously-
        resolved files. Destructive (loses earlier resolution work)
        so we confirm before launching the Resolve picker."""
        def on_close(yes: bool, c: str = change) -> None:
            if yes:
                self._open_resolve_modal(["-f", "-c", str(c)])

        self.push_screen(
            ConfirmModal(
                title=f"Re-resolve files in CL {change}?",
                message=(
                    f"Open the Resolve picker on every file in CL "
                    f"{change} that has already been resolved "
                    f"(``p4 resolve -f -c {change}``).\n\n"
                    "The previous resolution decisions on those files "
                    "will be discarded — you'll have to pick Auto / "
                    "Yours / Theirs again."
                ),
                ok_label="Re-resolve",
                ok_variant="error",
            ),
            callback=on_close,
        )

    # --- Pending-CL diff against #have ---------------------------------

    def _run_diff_pending_against_have(self, change: str) -> None:
        """Show the unified diff of every opened file in pending
        ``change`` against its #have revision in a FileViewerModal."""
        self._fetch_diff_have(change)

    @work(thread=True, group="diff_have")
    def _fetch_diff_have(self, change: str) -> None:
        text = self.p4.diff_against_have(change)
        if not text.strip():
            text = (
                f"[CL {change} — no diff against have "
                "(empty CL, binary files only, or nothing opened "
                "for edit)]"
            )
        self.call_from_thread(
            self.push_screen,
            FileViewerModal(
                f"Diff against #have · pending CL {change}", text,
            ),
        )

    # --- Print / Print Preview a CL ------------------------------------

    def _print_cl(
        self,
        change: str,
        *,
        submitted: bool,
        preview: bool,
    ) -> None:
        """Render a changelist as a formatted text document.

        TUI doesn't have a printer surface, so "Print Preview" opens
        the formatted output in a FileViewerModal and "Print" writes
        the same text to a temp file the user can hand off to a real
        print pipeline (or just keep as an archive). Both flows go
        through the same worker that gathers description + file list
        (+ unified diff, for submitted CLs)."""
        self._fetch_then_print(change, submitted, preview)

    @work(thread=True, group="cl_print")
    def _fetch_then_print(
        self, change: str, submitted: bool, preview: bool,
    ) -> None:
        try:
            info = self.p4.describe(change)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"Fetch CL {change} for print failed: {e}",
                severity="error", timeout=8,
            )
            return
        text = self._format_cl_for_print(change, info, submitted)
        if preview:
            title = (
                f"Print Preview · {'Submitted' if submitted else 'Pending'} "
                f"CL {change}"
            )
            self.call_from_thread(
                self.push_screen, FileViewerModal(title, text),
            )
            return
        # "Print" — write to a temp file and tell the user where it
        # landed. They can pipe / `cat` / open it from there.
        import tempfile, os
        try:
            fd, path = tempfile.mkstemp(
                prefix=f"p4v-tui-cl-{change}-", suffix=".txt",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            self.call_from_thread(
                self.notify,
                f"Write print file for CL {change} failed: {e}",
                severity="error", timeout=8,
            )
            return
        self.call_from_thread(
            self.notify,
            f"CL {change} written to:\n{path}\n\n"
            "TUI has no printer surface — hand the file to your OS "
            "print pipeline if you need a hard copy.",
            timeout=12,
        )

    @staticmethod
    def _format_cl_for_print(
        change: str, info: dict, submitted: bool,
    ) -> str:
        """Turn a ``p4 describe`` dict into a print-friendly text
        block: header + description + per-file action/rev list. Used
        by both print-preview and print-to-file paths."""
        lines: list[str] = []
        lines.append(
            f"Changelist {change} "
            f"({'submitted' if submitted else 'pending'})"
        )
        user = info.get("user", "") or ""
        client = info.get("client", "") or ""
        t = info.get("time") or ""
        try:
            from datetime import datetime as _dt
            date = _dt.fromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            date = str(t)
        lines.append(f"User:   {user}")
        lines.append(f"Client: {client}")
        if date:
            lines.append(f"Date:   {date}")
        lines.append("")
        lines.append("Description")
        lines.append("-----------")
        desc = (info.get("desc") or "").rstrip()
        lines.append(desc if desc else "(no description)")
        lines.append("")
        depot_files = info.get("depotFile") or []
        revs = info.get("rev") or []
        actions = info.get("action") or []
        types = info.get("type") or []
        if depot_files:
            lines.append(f"Files ({len(depot_files)})")
            lines.append("-----")
            for i, df in enumerate(depot_files):
                rev = revs[i] if i < len(revs) else ""
                act = actions[i] if i < len(actions) else ""
                typ = types[i] if i < len(types) else ""
                rev_str = f"#{rev}" if rev else ""
                lines.append(f"  {act:>12}  {df}{rev_str}  ({typ})")
        return "\n".join(lines) + "\n"

    # --- Refresh one pending CL ---------------------------------------

    def _refresh_one_pending_cl(self, change: str) -> None:
        """Refresh the description + file list shown for ``change`` in
        the detail pane. The pending-table row itself doesn't have any
        derived columns that change without a full reload, so we use
        the existing detail-fetch path."""
        self._load_change_detail(change, submitted=False)

    def _confirm_get_revs_files(self, change: str) -> None:
        def on_close(yes: bool, c: str = change) -> None:
            if yes:
                self._run_get_revs_files(c)

        self.push_screen(
            ConfirmModal(
                title=f"Sync files in CL {change} to that revision?",
                message=(
                    f"Run `p4 sync <file>@{change}` for every file in "
                    f"changelist {change}.\n\nLocal copies of those files "
                    "will be replaced with the version recorded in this "
                    "changelist."
                ),
                ok_label="Sync",
                ok_variant="primary",
            ),
            callback=on_close,
        )

    def action_diff_prev_revs(self) -> None:
        """Ctrl+D — open the unified diff for the highlighted Submitted CL."""
        try:
            tabs = self.query_one("#right_tabs", TabbedContent)
        except Exception:  # noqa: BLE001
            return
        if tabs.active != "tab_submitted":
            self.notify(
                "Switch to Submitted Changelists tab to diff against "
                "previous revisions.",
                severity="warning", timeout=4,
            )
            return
        table = self.query_one("#submitted_table", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            return
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:  # noqa: BLE001
            return
        change = str(row[0]) if row else ""
        if not change:
            return
        self._run_diff_prev_revs(change)

    def _open_sxs_diff(self, change: str) -> None:
        self._fetch_then_show_sxs_diff(change)

    # --- command monitor -------------------------------------------------

    def action_show_cmd_monitor(self) -> None:
        self.push_screen(CmdMonitorModal(self.cmd_log))

    # --- find file -------------------------------------------------------

    # --- Fast Search (Ctrl+F) -----------------------------------------

    def _init_search_index(self, info: P4Info) -> None:
        """Open the per-(port, client) SQLite index and kick off the
        right kind of refresh — incremental if the DB is already
        built, full build if it isn't."""
        path = index_path_for(info.port or self.p4.port,
                              info.client or self.p4.client)
        idx = SearchIndex(path)
        try:
            idx.open()
        except Exception as e:  # noqa: BLE001
            self.notify(
                f"Search index unavailable: {e}",
                severity="warning", timeout=8,
            )
            return
        self._search_index = idx
        # Build state — has the first full enumeration completed?
        build_done = idx.get_meta("build_complete") == "1"
        if build_done:
            self._search_index_status = (
                f"updating · {idx.file_count():,} file(s)"
            )
            self.jobs.submit_job(IndexUpdateJob(self.p4, idx))
        else:
            self._search_index_status = (
                "building first index · "
                f"{idx.file_count():,} file(s) so far"
            )
            self.jobs.submit_job(IndexBuildJob(self.p4, idx))

    def action_fast_search(self) -> None:
        """Ctrl+F — open the Fast Search modal."""
        self.action_open_search()

    def _install_macro_bindings(self) -> None:
        """Register a Binding for every `[[macro]] key = "..."` entry.

        Macros without ``key`` stay reachable via Ctrl+Shift+M
        picker. Bad keys (Textual rejects the syntax, e.g. trailing
        spaces or unknown modifiers) are surfaced as toasts and
        skipped so one bad entry doesn't kill the rest.

        We bind each key to the dedicated
        ``run_macro_by_index(N)`` action so the keymap stays
        per-instance and survives config reload (call
        :meth:`_install_macro_bindings` again to refresh).
        """
        macros = (self.config.macros or []) if self.config else []
        for i, m in enumerate(macros):
            if not m.key:
                continue
            try:
                # Textual's App.bind() takes (keys, action,
                # description, show). Action string maps to the
                # ``action_run_macro_by_index`` method below.
                self.bind(
                    m.key,
                    f"run_macro_by_index({i})",
                    description=f"Macro: {m.name}",
                )
            except Exception as e:  # noqa: BLE001
                try:
                    self.notify(
                        f"Macro {m.name!r} key {m.key!r} invalid: {e}",
                        severity="warning", timeout=6,
                    )
                except Exception:  # noqa: BLE001
                    pass

    def action_run_macro_by_index(self, index: int) -> None:
        """Run the i-th macro by its position in ``Config.macros``.

        Index is the same one used when :meth:`_install_macro_bindings`
        registered the binding. If macros have been reloaded since
        and the index is stale, surface a warning instead of crashing.
        """
        macros = (self.config.macros or []) if self.config else []
        if not (0 <= index < len(macros)):
            self.notify(
                f"Macro slot {index} no longer exists.",
                severity="warning", timeout=4,
            )
            return
        self._run_macro_worker(macros[index])

    def action_run_macro(self) -> None:
        """Ctrl+Shift+M — show a picker of TOML-defined macros.

        Each macro is a list of steps loaded from ``[[macro]]``
        sections of the config file. Picking one runs the steps in
        order via :meth:`_run_macro_worker`. No macros defined →
        the picker shows an empty-state row that points users to
        the docs.
        """
        macros = (self.config.macros or []) if self.config else []
        items = []
        if not macros:
            items.append(ContextMenuItem(
                "(no macros defined — add [[macro]] blocks to "
                "p4v-tui.toml)",
                "_noop", "",
            ))
        else:
            for i, m in enumerate(macros):
                label = m.name
                if m.description:
                    label = f"{m.name} — {m.description}"
                items.append(ContextMenuItem(
                    label, f"macro:{i}", "",
                ))

        def on_close(action_id: str | None, ms=macros) -> None:
            if not action_id or not action_id.startswith("macro:"):
                return
            try:
                idx = int(action_id.split(":", 1)[1])
            except ValueError:
                return
            if not (0 <= idx < len(ms)):
                return
            self._run_macro_worker(ms[idx])

        self.push_screen(
            ContextMenuModal(items, title="Run Macro"),
            on_close,
        )

    @work(thread=True, group="macro_runner", exclusive=False)
    def _run_macro_worker(self, macro) -> None:
        """Execute every step of ``macro`` in order on a worker.

        Steps fail-fast: any P4Exception or unexpected error halts
        the chain and surfaces a toast so the user can investigate.
        ``kind="notify"`` is the explicit "checkpoint" step that
        lets a macro author insert progress messages between
        operations.
        """
        for i, step in enumerate(macro.steps):
            label = f"[{macro.name} step {i + 1}/{len(macro.steps)}]"
            try:
                if step.kind == "notify":
                    self.call_from_thread(
                        self.notify, f"{label} {step.message or ''}",
                        timeout=4,
                    )
                    continue
                if step.kind == "sync":
                    target = step.target or "//..."
                    self.call_from_thread(
                        self.notify, f"{label} chunked sync {target}",
                        timeout=4,
                    )
                    self.jobs.submit_job(
                        ChunkedSyncJob(self.p4, target),
                    )
                    continue
                if step.kind == "p4":
                    if not step.args:
                        continue
                    result = self.p4.run(*step.args)
                    self.call_from_thread(
                        self.notify,
                        f"{label} p4 {' '.join(step.args)} → "
                        f"{self._summarize_result(result)}",
                        timeout=4,
                    )
                    continue
                self.call_from_thread(
                    self.notify,
                    f"{label} unknown kind {step.kind!r}",
                    severity="warning", timeout=4,
                )
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(
                    self.notify,
                    f"{label} failed: {e}",
                    severity="error", timeout=8,
                )
                return
        self.call_from_thread(
            self.notify,
            f"Macro {macro.name!r} finished.",
            timeout=4,
        )

    def action_open_search(self, *, initial_query: str = "") -> None:
        """Open Fast Search optionally pre-filled with ``initial_query``.

        Splitting this off from ``action_fast_search`` lets the tree's
        "Search In This Folder…" context menu pass the cursor path
        through as a seed query without duplicating the modal-launch
        plumbing. An empty seed behaves exactly like the unparameterised
        Ctrl+F path.
        """
        if self._search_index is None:
            self.notify(
                "Search index not initialized yet. "
                "Wait for the connection, or use Ctrl+Shift+F for a "
                "server-side filename search.",
                severity="warning", timeout=6,
            )
            return
        status = self._refresh_search_index_status()

        def on_close(result) -> None:
            if result is None:
                return
            # The modal can return either a depot path (Enter), or a
            # dict signaling a special action.
            if isinstance(result, dict):
                if result.get("rebuild"):
                    self._rebuild_search_index()
                    return
                viewer_path = result.get("viewer")
                if viewer_path:
                    self._open_file_viewer(viewer_path)
                    return
                diff_path = result.get("diff")
                if diff_path:
                    self._diff_against_have(diff_path)
                    return
                get_path = result.get("get")
                if get_path:
                    self._enqueue_chunked(
                        ChunkedSyncJob(
                            self.p4, get_path,
                            strategy=self._chunking_for("sync"),
                        ),
                        get_path,
                    )
                    return
                return
            if isinstance(result, str):
                self._navigate_tree_to(result)

        self.push_screen(
            SearchModal(
                self._search_index, self.p4,
                index_status=status,
                initial_query=initial_query,
            ),
            on_close,
        )

    def _refresh_search_index_status(self) -> str:
        idx = self._search_index
        if idx is None:
            return "(not yet initialized)"
        try:
            count = idx.file_count()
            indexed_at = idx.get_meta("indexed_at")
            build_done = idx.get_meta("build_complete") == "1"
            disk_mb = idx.disk_size_bytes() / (1024 * 1024)
        except Exception:  # noqa: BLE001
            return "(error reading meta)"
        when = ""
        if indexed_at:
            try:
                ts = int(indexed_at)
                when = " · " + datetime.fromtimestamp(ts).strftime(
                    "%Y-%m-%d %H:%M",
                )
            except (TypeError, ValueError):
                pass
        state = "fresh" if build_done else "building"
        status = (
            f"{state} · {count:,} file(s) · {disk_mb:.1f} MB"
            f"{when}"
        )
        self._search_index_status = status
        return status

    def _rebuild_search_index(self) -> None:
        """Wipe the index file and re-enqueue a full build. Triggered
        from the SearchModal's Ctrl+R."""
        idx = self._search_index
        if idx is None:
            return
        path = idx.path
        try:
            idx.close()
            for suffix in ("", "-wal", "-shm"):
                p = path.with_name(path.name + suffix)
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            pass
        new_idx = SearchIndex(path)
        try:
            new_idx.open()
        except Exception as e:  # noqa: BLE001
            self.notify(
                f"Could not re-open index after wipe: {e}",
                severity="error", timeout=8,
            )
            return
        self._search_index = new_idx
        self.jobs.submit_job(IndexBuildJob(self.p4, new_idx))
        self.notify(
            "Search index rebuild queued. "
            "Existing search still works while it runs.",
            timeout=6,
        )

    def action_find_file(self) -> None:
        def on_close(picked: str | None) -> None:
            if not picked:
                return
            self.notify(f"Found: {picked}", timeout=4)
            try:
                self.query_one("#right_tabs", TabbedContent).active = "tab_history"
            except Exception:  # noqa: BLE001
                pass
            self._load_file_history(picked)
            # Auto-navigate the tree to the picked file. Workspace
            # first (if mapped), depot otherwise — same fallback the
            # Submitted CL "Show Files in Tree" path uses.
            self._navigate_tree_to(picked)

        self.push_screen(
            FindFileModal(self.p4, search_index=self._search_index),
            on_close,
        )

    # --- go to path (item 9) -------------------------------------------

    def action_goto_path(self) -> None:
        """Paste a depot or local path and reveal it in the tree."""
        self.push_screen(GotoPathModal(), self._goto_path)

    def _goto_path(self, raw: str | None) -> None:
        if not raw:
            return
        from .path_nav import classify_path

        kind, norm = classify_path(raw)
        if kind == "permalink":
            self._goto_permalink(norm)
            return
        if kind == "depot":
            # Patterns / directory paths navigate the tree directly: there
            # is no single file to existence-check, and directories aren't
            # in the file index so the fuzzy ladder can't help. A concrete
            # file path that doesn't resolve drops into the fallback below.
            if norm.endswith(("/...", "/")) or "*" in norm or "..." in norm:
                self._navigate_tree_to(norm)
            else:
                self._goto_depot_or_fallback(norm)
            return
        if kind == "local":
            # Translate the local path to a depot path so the shared
            # _navigate_tree_to walk (which keys off `p4 where`) can run.
            depot = None
            try:
                info = self.p4.where(norm)
                depot = (info or {}).get("depotFile")
            except Exception:  # noqa: BLE001
                depot = None
            if depot:
                self._navigate_tree_to(depot)
            else:
                self.notify(
                    f"'{norm}' isn't under any depot mapping for this client.",
                    severity="warning", timeout=6,
                )
            return
        if kind == "unknown" and norm:
            # A bare fragment (no //, no leading slash) — there's no exact
            # path to try, so go straight to the same fuzzy ladder Fast
            # Search / Find File use. This is the "직접 path 입력에도 동일
            # fallback" roadmap step.
            self._goto_fuzzy_fallback(norm)
            return
        self.notify(
            "Paste a depot path (//…) or an absolute local path.",
            severity="warning", timeout=5,
        )

    # --- Go-to-path fuzzy fallback (roadmap: "직접 path 입력") ----------
    #
    # When an exact Go-to-path lookup misses (typo, moved file, dropped
    # slash, or a bare filename fragment) we reuse the Fast Search fuzzy
    # ladder — token-AND loose match, then Levenshtein "did you mean…" —
    # instead of dead-ending. The pure branch logic lives in
    # path_nav.plan_goto_fallback; these methods do the index query +
    # screen wiring off the UI thread.

    @work(thread=True, group="goto_fallback", exclusive=True)
    def _goto_depot_or_fallback(self, norm: str) -> None:
        """Navigate to ``norm`` if it's a real file, else fuzzy-fallback."""
        try:
            rows = self.p4.run("files", "-e", norm)
            exists = any(
                isinstance(r, dict) and r.get("depotFile") for r in rows
            )
        except Exception:  # noqa: BLE001
            exists = False
        if exists:
            self.call_from_thread(self._navigate_tree_to, norm)
            return
        self._run_goto_fuzzy(norm, norm.rsplit("/", 1)[-1])

    @work(thread=True, group="goto_fallback", exclusive=True)
    def _goto_fuzzy_fallback(self, frag: str) -> None:
        """Fuzzy-resolve a bare fragment typed into Go-to-path."""
        self._run_goto_fuzzy(frag, frag.rsplit("/", 1)[-1])

    def _run_goto_fuzzy(self, original: str, leaf: str) -> None:
        """Index-backed fuzzy resolution; runs on a worker thread.

        ``original`` feeds the token-AND loose match (it tokenizes on
        whitespace + ``/`` so a full depot path or a spaced fragment both
        work); ``leaf`` is the last path segment, used for the tighter
        Levenshtein typo suggestions when loose came back empty.
        """
        from .path_nav import plan_goto_fallback

        loose: list[str] = []
        suggestions: list[str] = []
        idx = self._search_index
        if idx is not None:
            try:
                hits = idx.query_files_loose(original, limit=50)
                loose = [h.depot_path for h in hits]
                if not loose:
                    suggestions = idx.suggest_corrections(leaf)
            except Exception:  # noqa: BLE001
                pass
        plan = plan_goto_fallback(loose, suggestions)
        self.call_from_thread(self._apply_goto_plan, original, plan)

    def _apply_goto_plan(
        self, original: str, plan: tuple[str, list[str]],
    ) -> None:
        action, payload = plan
        if action == "navigate":
            target = payload[0]
            self.notify(
                f"No exact match for {original} — jumped to closest:\n"
                f"{target}",
                timeout=6,
            )
            self._navigate_tree_to(target)
            return
        if action == "pick":
            def on_pick(picked: str | None) -> None:
                if picked:
                    self._navigate_tree_to(picked)

            self.push_screen(
                FileInCLPickerModal(
                    "", payload,
                    title=(
                        f" Go to · {len(payload)} close matches "
                        f"for {original} "
                    ),
                ),
                on_pick,
            )
            return
        if action == "suggest":
            self.notify(
                f"No match for {original}. Did you mean: "
                + ", ".join(payload[:5]),
                severity="warning", timeout=8,
            )
            return
        # action == "none"
        if self._search_index is None:
            self.notify(
                f"'{original}' not found "
                "(search index not ready yet — try again shortly).",
                severity="warning", timeout=6,
            )
        else:
            self.notify(
                f"'{original}' not found and no close matches.",
                severity="warning", timeout=6,
            )

    # --- permalinks (item 11) -----------------------------

    @staticmethod
    def _shared_state_dir():
        """Versioned, in-workspace dir for cross-machine state (item: share
        permalinks + bookmarks via Perforce). Lives under the project root
        so it is inside the client view (submittable) and gitignored."""
        from pathlib import Path
        return Path(__file__).resolve().parents[1] / "shared-state"

    @work(thread=True, group="state_track", exclusive=False)
    def _track_state_file(self, path) -> None:
        """Open a just-written shared-state file in the dedicated CL.

        Delegates to :attr:`_shared_state_cl` which lazily creates one
        numbered changelist per session, runs ``reconcile -c <CL>`` into
        it, and records the (path, action) for the exit-time submit.
        Routing into a *numbered* CL is mandatory — see the class doc on
        :class:`SharedStateChangelist`.

        Off the UI thread + best-effort: any failure (file outside the
        client view, server down, …) just means this write is left for
        the user to reconcile/submit manually.
        """
        self._shared_state_cl.track(self.p4, path)

    @property
    def _permalink_registry(self):
        reg = getattr(self, "_permalink_reg", None)
        if reg is None:
            from .permalink import PermalinkRegistry
            reg = PermalinkRegistry(
                self._shared_state_dir() / "permalinks.json",
                after_write=self._track_state_file,
            )
            self._permalink_reg = reg
        return reg

    def _copy_permalink(self, target: str) -> None:
        """Mint (or reuse) a stable permalink for ``target`` + copy it.

        Also remembered as ``_last_permalink`` so Ctrl+Shift+V can jump back
        to it without a clipboard read (the TUI clipboard is write-only).
        """
        from .permalink import make_permalink
        vid = self._permalink_registry.register(target)
        self._last_permalink = vid
        addr = make_permalink(vid)
        self._copy_text(addr, "Permalink")

    def action_paste_permalink(self) -> None:
        """Ctrl+Shift+V — navigate to the permalink last copied."""
        vid = getattr(self, "_last_permalink", None)
        if vid is None:
            self.notify(
                "No permalink copied this session. Copy one with "
                "Alt+C, or paste an external //@p/N via Ctrl+G.",
                severity="warning", timeout=6,
            )
            return
        self._goto_permalink(str(vid))

    def _goto_permalink(self, vid: str) -> None:
        origin = self._permalink_registry.lookup(vid)
        if not origin:
            self.notify(
                f"Unknown permalink //@p/{vid} "
                "(was it copied on this machine?).",
                severity="warning", timeout=6,
            )
            return
        self._resolve_and_navigate_permalink(origin)

    @work(thread=True, group="permalink_resolve")
    def _resolve_and_navigate_permalink(self, origin: str) -> None:
        current = self._resolve_moved_path(origin)
        self.call_from_thread(self._navigate_tree_to, current)
        if current != origin:
            self.call_from_thread(
                self.notify,
                f"Followed move:\n{origin}\n→ {current}", timeout=6,
            )

    def _resolve_moved_path(self, origin: str) -> str:
        """Follow ``p4`` move/rename history to ``origin``'s current path.

        Best-effort: returns ``origin`` unchanged when it wasn't moved or
        when the integration chain can't be parsed.

        Verified live (CL 56812 probe + an existing depot rename) against
        both backends. Two non-obvious facts the implementation depends on:

        * The ``moved into`` integration is recorded on the revision
          *below* the ``move/delete`` head (the content that was moved),
          not on the head itself — so we must fetch at least two
          revisions (``-m 1`` silently dropped the target on every move).
        * P4Python and the CLI ``-G`` backend return integration data in
          different shapes; :meth:`_find_moved_into` /
          :meth:`_head_action` normalise both.
        """
        current = origin
        seen: set[str] = set()
        for _ in range(20):  # cap the chain to avoid loops
            if current in seen:
                break
            seen.add(current)
            try:
                # -m 2: head (the move/delete) + the revision below it,
                # which carries the "moved into" integration record.
                log = self.p4.run("filelog", "-m", "2", current)
            except Exception:  # noqa: BLE001
                break
            rec = log[0] if log and isinstance(log[0], dict) else {}
            action = self._head_action(rec)
            if "move/delete" not in action and action != "delete":
                break
            moved_to = self._find_moved_into(rec)
            if not moved_to or moved_to == current:
                break
            current = moved_to
        return current

    @staticmethod
    def _head_action(rec: dict) -> str:
        """Head-revision action from a filelog record, backend-agnostic.

        P4Python returns ``action`` as a per-revision list (head first);
        the CLI ``-G`` backend emits flat ``action0`` / ``action1`` keys.
        """
        a = rec.get("action")
        if isinstance(a, list):
            return str(a[0]) if a else ""
        if isinstance(a, str):
            return a
        a0 = rec.get("action0")
        return str(a0) if a0 is not None else ""

    @staticmethod
    def _find_moved_into(rec: dict) -> str | None:
        """Pull the 'moved into' destination path from a filelog record.

        Returns the most recent move target (or None). Handles both
        backend shapes — verified live against real renames:

        * **P4Python**: ``how`` is a list parallel to the revisions, each
          element either ``None`` or a list of integration verbs (e.g.
          ``[None, ['moved into']]``); ``file`` is the parallel list of
          target paths (list-of-lists). Index 0 is the newest revision,
          so the first match is the most recent move.
        * **CLI ``-G``**: integration data is flat-keyed ``how<rev>,<n>``
          (e.g. ``how1,0``) with the paired path in ``file<rev>,<n>``.
          We scan by ascending ``(rev, n)`` so the newest move wins
          regardless of dict iteration order.
        """
        how = rec.get("how")
        if isinstance(how, list):
            files = rec.get("file")
            for i, hrow in enumerate(how):
                if not isinstance(hrow, list):
                    continue
                frow = (files[i] if isinstance(files, list)
                        and i < len(files) else None)
                for j, h in enumerate(hrow):
                    if isinstance(h, str) and "moved into" in h:
                        if isinstance(frow, list) and j < len(frow):
                            tgt = frow[j]
                            if isinstance(tgt, str) and tgt:
                                return tgt
            return None
        # CLI -G flat shape.
        best: tuple[int, int, str] | None = None
        for key, val in rec.items():
            if not (isinstance(key, str) and key.startswith("how")
                    and isinstance(val, str) and "moved into" in val):
                continue
            target = rec.get("file" + key[3:])
            if not (isinstance(target, str) and target):
                continue
            try:
                ri_s, ni_s = key[3:].split(",")
                ri, ni = int(ri_s), int(ni_s)
            except ValueError:
                ri, ni = 0, 0
            if best is None or (ri, ni) < (best[0], best[1]):
                best = (ri, ni, target)
        return best[2] if best else None

    # --- bookmarks (permalink-backed) --------------------------------------

    @property
    def _bookmark_store(self):
        store = getattr(self, "_bookmarks", None)
        if store is None:
            from .bookmarks import BookmarkStore
            store = BookmarkStore(
                self._shared_state_dir() / "bookmarks.json",
                after_write=self._track_state_file,
            )
            self._bookmarks = store
        return store

    def _add_bookmark(self, target: str) -> None:
        """Bookmark ``target`` via a permalink (survives moves)."""
        from .permalink import make_permalink
        vid = self._permalink_registry.register(target)
        is_new = self._bookmark_store.add(vid, target)
        verb = "Bookmarked" if is_new else "Bookmark updated"
        self.notify(
            f"{verb}: {target}\n({make_permalink(vid)})", timeout=5,
        )

    def action_bookmarks(self) -> None:
        """Open the bookmark picker; jump to the chosen path."""
        if len(self._bookmark_store) == 0:
            self.notify(
                "No bookmarks yet — press Ctrl+B on a tree node to add one.",
                timeout=5,
            )
            return

        def on_close(vid: str | None) -> None:
            if vid:
                # Reuse the permalink resolver: id → origin →
                # follow moves → navigate the tree.
                self._goto_permalink(vid)

        self.push_screen(BookmarkPickerModal(self._bookmark_store), on_close)

    # --- pending chunked jobs from previous run -------------------------

    def _maybe_show_pending_jobs(self) -> None:
        items = _pending_jobs_mod.discover()
        if not items:
            return

        def on_close(result: dict | None) -> None:
            if not result:
                return
            action = result.get("action")
            targets = result.get("targets") or []
            if action == "skip" or not targets:
                return
            if action == "remove":
                removed = 0
                for it in targets:
                    if _pending_jobs_mod.delete_state(it):
                        removed += 1
                self.notify(
                    f"Removed {removed} pending job state(s).",
                    timeout=5,
                )
                return
            if action == "resume":
                resumed = 0
                for it in targets:
                    job = _pending_jobs_mod.build_job(self.p4, it)
                    if job is None:
                        continue
                    self.jobs.submit_job(job)
                    resumed += 1
                self.notify(
                    f"Re-queued {resumed} chunked job(s) — see "
                    "Log panel below or F2 for the full command tree.",
                    timeout=6,
                )

        self.push_screen(PendingJobsModal(items), on_close)

    # --- rename / move (file or directory) ------------------------------

    def _open_rename_modal(
        self, source: str, *, is_directory: bool = False,
    ) -> None:
        def on_close(target: str | None, src: str = source,
                     is_dir: bool = is_directory) -> None:
            if not target:
                return
            self._run_rename(src, target, is_dir)
        self.push_screen(
            RenameMoveModal(
                source,
                is_directory=is_directory,
                p4_service=self.p4,
            ),
            on_close,
        )

    def _open_quick_rename(
        self, source: str, *, is_directory: bool = False,
    ) -> None:
        """F2 entry point — pop the lightweight rename popup, then on
        confirmation hand the assembled target to the auto-submit
        worker. The user gives just the new leaf name; we recompute
        the full target by swapping the leaf on the source path.
        """
        if "/" in source:
            base = source.rsplit("/", 1)[0]
            old_leaf = source.rsplit("/", 1)[-1]
        else:
            base = ""
            old_leaf = source

        def on_close(
            new_leaf: str | None,
            src: str = source,
            is_dir: bool = is_directory,
            base_: str = base,
            old: str = old_leaf,
        ) -> None:
            if not new_leaf or new_leaf == old:
                return
            target = f"{base_}/{new_leaf}" if base_ else new_leaf
            self._run_quick_rename_and_submit(src, target, is_dir)

        self.push_screen(
            QuickRenameModal(source, is_directory=is_directory),
            on_close,
        )

    @work(thread=True, group="rename_submit")
    def _run_quick_rename_and_submit(
        self, source: str, target: str, is_directory: bool,
    ) -> None:
        """Do the whole F2 pipeline in one worker, end to end:

          1. Create a pending CL with a descriptive Korean message
             explaining what got renamed.
          2. ``p4 edit -c <CL> <src>`` opens the source (or every
             file under it for a directory rename) into that CL,
             pulling the file out of the default CL if it was
             already opened there.
          3. ``p4 move -c <CL> <src> <dst>`` performs the rename.
          4. Submit the CL **inline**, on this worker thread, by
             driving :class:`ResilientSubmitJob`'s chunks directly.
             We don't go through the JobRunner queue — submit-right-
             away is what the user asked for, and chaining via the
             queue introduces enough indirection that a failure in
             the submit step would only surface as a still-pending
             CL with no clear signal. Running it inline lets us
             notify success / failure deterministically and refresh
             the pending + workspace views before returning.
        """
        src_p4 = f"{source}/..." if is_directory else source
        tgt_p4 = f"{target}/..." if is_directory else target
        scope = "디렉토리(재귀)" if is_directory else "파일"
        src_leaf = (
            source.rsplit("/", 1)[-1] if "/" in source else source
        )
        tgt_leaf = (
            target.rsplit("/", 1)[-1] if "/" in target else target
        )
        desc = (
            f"이름 변경: {src_leaf} → {tgt_leaf}\n"
            f"\n"
            f"대상: {source}\n"
            f"변경 후: {target}\n"
            f"범위: {scope}\n"
            f"\n"
            f"p4v-tui F2 빠른 리네임으로 생성한 변경 — `p4 edit` + "
            f"`p4 move` 후 동일 CL 을 즉시 서브밋."
        )
        try:
            new_cl = self.p4.create_changelist(desc)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"Create CL for rename failed: {e}",
                severity="error", timeout=10,
            )
            return
        try:
            # `p4 edit -c <CL>` is idempotent on already-open files
            # (it just re-homes them into the target CL) so we can
            # always run it before the move.
            self.p4.run("edit", "-c", new_cl, src_p4)
            self.p4.run("move", "-c", new_cl, src_p4, tgt_p4)
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Rename {source} → {target} failed: {e}",
                severity="error", timeout=10,
            )
            # Best-effort cleanup: delete the empty CL so it doesn't
            # litter the pending list. Ignore secondary failures.
            try:
                self.p4.run("change", "-d", new_cl)
            except Exception:  # noqa: BLE001
                pass
            return

        # Submit synchronously here. ResilientSubmitJob's _do_submit
        # already wraps the call in a resilient retry + lost-ack
        # idempotency check, so driving its single chunk directly
        # gives us the same robustness without the JobRunner queue.
        job = ResilientSubmitJob(self.p4, new_cl)
        try:
            for chunk in job.chunks():
                chunk()
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Submit CL {new_cl} for rename failed: {e}\n"
                f"리네임 항목은 CL {new_cl} 에 보존됩니다 — 펜딩 "
                f"CL 에서 직접 서브밋하거나 revert 해 주세요.",
                severity="error", timeout=12,
            )
            return

        submitted = job.result_change or new_cl
        self.call_from_thread(
            self.notify,
            f"Rename submitted @{submitted}: {src_leaf} → {tgt_leaf} "
            f"({scope})",
            timeout=6,
        )

        # Refresh both list views and the workspace tree now that the
        # depot reflects the new path. The depot tree isn't refreshed
        # here — its state belongs to the user; if they were browsing
        # the renamed area they can hit F5 themselves (and the new
        # refresh_root preserves their expansion).
        self.call_from_thread(self._load_pending)
        self.call_from_thread(self._load_submitted)
        try:
            self.call_from_thread(
                self.query_one(WorkspaceTree).refresh_root,
            )
        except Exception:  # noqa: BLE001
            pass

    @work(thread=True, group="rename")
    def _run_rename(
        self, source: str, target: str, is_directory: bool,
    ) -> None:
        # For a directory, append /... so `p4 edit` and `p4 move` walk
        # every file. For a single file, the path itself is the target.
        src_p4 = f"{source}/..." if is_directory else source
        tgt_p4 = f"{target}/..." if is_directory else target
        try:
            # `p4 edit` is idempotent on already-open files (warning,
            # not error), so we don't need to pre-check; just always
            # open then move.
            self.p4.run("edit", src_p4)
            self.p4.run("move", src_p4, tgt_p4)
        except P4Exception as e:
            self.call_from_thread(
                self.notify, f"Move {source} → {target} failed: {e}",
                severity="error", timeout=10,
            )
            return
        scope = "files in directory" if is_directory else "file"
        self.call_from_thread(
            self.notify,
            f"Moved {scope}: {source} → {target}",
            timeout=5,
        )
        self.call_from_thread(self._load_pending)
        try:
            self.call_from_thread(
                self.query_one(WorkspaceTree).refresh_root,
            )
        except Exception:  # noqa: BLE001
            pass

    # --- clipboard helpers ----------------------------------------------

    def _copy_text(self, text: str, label: str) -> None:
        """Copy text to both the terminal (OSC52) and OS clipboards.

        We fire both paths because they cover different setups:
        OSC52 (via Textual ``copy_to_clipboard``) is the only route
        that crosses an SSH boundary back to the user's local
        clipboard, but Windows Terminal disables OSC52 by default and
        some tmux/screen setups strip it. The OS-native path
        (``clip.exe`` / ``pbcopy`` / ``wl-copy`` / ``xclip`` /
        ``xsel``) reliably populates the system clipboard on a local
        terminal so the copied text can be pasted into other apps.
        Either succeeding is enough to report success."""
        osc52_ok = False
        try:
            self.copy_to_clipboard(text)
            osc52_ok = True
        except Exception:  # noqa: BLE001 -- old Textual / unsupported terminal
            osc52_ok = False
        os_ok = self._write_os_clipboard(text)
        if osc52_ok or os_ok:
            self.notify(f"Copied — {label}: {text}", timeout=8)
        else:
            self.notify(
                f"{label}: {text}\n(clipboard unavailable, select manually)",
                timeout=15,
            )

    def _write_os_clipboard(self, text: str) -> bool:
        """Write ``text`` to the OS-native clipboard via platform tools.

        Returns True on success. Tries clip.exe on Windows, pbcopy on
        macOS, then wl-copy / xclip / xsel on Linux — falling through
        to the next candidate if one is missing or errors out.
        clip.exe takes UTF-16 LE (no BOM) so non-ASCII (e.g. Korean
        descriptions copied elsewhere in the app) survives the round
        trip without a leading FEFF leaking into pastes; the Unix
        tools take UTF-8 directly."""
        import shutil
        import subprocess
        import sys

        if sys.platform == "win32":
            candidates: list[tuple[list[str], str]] = [(["clip"], "utf-16-le")]
        elif sys.platform == "darwin":
            candidates = [(["pbcopy"], "utf-8")]
        else:
            candidates = []
            if shutil.which("wl-copy"):
                candidates.append((["wl-copy"], "utf-8"))
            if shutil.which("xclip"):
                candidates.append(
                    (["xclip", "-selection", "clipboard"], "utf-8"),
                )
            if shutil.which("xsel"):
                candidates.append(
                    (["xsel", "--clipboard", "--input"], "utf-8"),
                )

        for cmd, enc in candidates:
            try:
                subprocess.run(
                    cmd,
                    input=text.encode(enc),
                    check=True,
                    capture_output=True,
                    timeout=2,
                )
                return True
            except (OSError, subprocess.SubprocessError):
                continue
        return False

    def _swarm_base_or_warn(self) -> str | None:
        """Return the configured Swarm base URL, or ``None`` after
        emitting a guidance toast. Centralised so the two CL helpers
        below stay short."""
        base = (self.config.swarm.base_url
                if self.config.swarm and self.config.swarm.base_url
                else None)
        if not base:
            self.notify(
                "No Swarm base_url configured. Add a [swarm] section "
                "to ~/.p4v-tui/config.toml — e.g. base_url = "
                '"http://swarm.example".',
                severity="warning", timeout=8,
            )
            return None
        return base

    def _copy_swarm_cl_url(self, change: str) -> None:
        """Build the Swarm CL URL and copy it to the clipboard.

        Pattern: ``{base}/changes/{N}`` — Swarm 302-redirects to the
        attached review if one exists, otherwise renders the
        change-details page. So this single form works for both
        "ready for review" and "post-submit history" use cases.
        """
        if not change or change == "default":
            self.notify(
                "Swarm URL needs a numbered changelist — the default "
                "CL has no permanent number.", timeout=4,
            )
            return
        base = self._swarm_base_or_warn()
        if not base:
            return
        from .config import build_swarm_review_url
        url = build_swarm_review_url(base, change)
        self._copy_text(url, f"Swarm CL {change}")

    def _open_swarm_cl_in_browser(self, change: str) -> None:
        """Open the Swarm CL URL in the system browser.

        We use ``webbrowser.open_new_tab`` so a logged-in browser
        session picks the URL up directly. Headless environments (no
        DISPLAY / no default browser) fall through to a toast with
        the URL so the user can still grab it.
        """
        if not change or change == "default":
            self.notify(
                "Swarm URL needs a numbered changelist.", timeout=4,
            )
            return
        base = self._swarm_base_or_warn()
        if not base:
            return
        from .config import build_swarm_review_url
        url = build_swarm_review_url(base, change)
        import webbrowser
        try:
            ok = webbrowser.open_new_tab(url)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            self.notify(
                f"Opened in Swarm: {url}", timeout=6,
            )
        else:
            # No browser handler available — show URL so the user
            # can copy it manually.
            self._copy_text(url, f"Swarm CL {change}")

    @work(thread=True, group="copy_swarm")
    def _copy_swarm_url(
        self, depot_path: str, is_directory: bool,
    ) -> None:
        base = (self.config.swarm.base_url
                if self.config.swarm and self.config.swarm.base_url else None)
        if not base:
            self.call_from_thread(
                self.notify,
                "No Swarm base_url configured. Add a [swarm] section "
                "to your p4v-tui.toml (see p4v-tui.toml.example).",
                severity="warning", timeout=8,
            )
            return
        rev = None
        if not is_directory:
            try:
                rows = self.p4.fstat(depot_path)
                if rows and isinstance(rows[0], dict):
                    rev = rows[0].get("headRev") or rows[0].get("haveRev")
            except Exception:  # noqa: BLE001
                rev = None
        url = build_swarm_url(base, depot_path, rev)
        self.call_from_thread(self._copy_text, url, "Swarm URL")

    # --- filesystem hand-offs (Show In / Open Command Window) -----------

    @work(thread=True, group="fs_handoff")
    def _run_fs_handoff(self, action: str, depot_path: str) -> None:
        info = self.p4.where(depot_path)
        local = info.get("path") if info else None
        if not local:
            self.call_from_thread(
                self.notify,
                f"Cannot resolve {depot_path} to a local path.",
                severity="warning", timeout=5,
            )
            return
        if action == "show_in":
            ok = show_in_filesystem(local)
            verb = "Show in filesystem"
        else:
            ok = open_command_window(local)
            verb = "Open terminal"
        if ok:
            self.call_from_thread(
                self.notify, f"{verb}: {local}", timeout=4,
            )
        else:
            self.call_from_thread(
                self.notify,
                f"{verb} failed for {local} "
                "(missing helper or path doesn't exist locally).",
                severity="error", timeout=8,
            )

    # --- Open With… (configurable external editors) --------------------

    def _open_with_picker(self, depot_path: str) -> None:
        editors = list(self.config.external_editors)
        if not editors:
            self.notify(
                "No external editors configured. Add one in "
                "Preferences (Ctrl+,) or under [[external_editor]] "
                "in p4v-tui.toml.",
                severity="warning", timeout=8,
            )
            return

        def on_pick(editor_name: str | None,
                    path: str = depot_path) -> None:
            if not editor_name:
                return
            ed = next(
                (e for e in self.config.external_editors
                 if e.name == editor_name),
                None,
            )
            if ed is None:
                return
            self._launch_external_editor(ed, path)

        self.push_screen(OpenWithModal(editors, depot_path), on_pick)

    @work(thread=True, group="open_with")
    def _launch_external_editor(self, editor, depot_path: str) -> None:
        """Resolve depot_path to a local file then launch ``editor``.

        Workspace paths resolve straight through ``p4 where``. Pure
        depot paths (not in the user's view) get printed to a temp
        file so the editor still has something to open — that copy
        is read-only by intent.
        """
        info = self.p4.where(depot_path)
        local = info.get("path") if info else None
        if not local:
            local = self._print_depot_to_temp(depot_path)
            note = " (depot copy → temp file, read-only)"
        else:
            note = ""
        if not local:
            self.call_from_thread(
                self.notify,
                f"Could not get a local copy of {depot_path}.",
                severity="error", timeout=8,
            )
            return
        ok = open_with_external(editor.command, editor.args, local)
        if ok:
            self.call_from_thread(
                self.notify,
                f"Opened {editor.name}: {local}{note}",
                timeout=4,
            )
        else:
            self.call_from_thread(
                self.notify,
                f"Failed to launch {editor.name} ({editor.command}). "
                "Check command + args in Preferences.",
                severity="error", timeout=8,
            )

    def _print_depot_to_temp(self, depot_path: str) -> str | None:
        """``p4 print`` the depot file into a temp file and return its
        path. Used by Open With when the file isn't mapped locally."""
        import tempfile
        from pathlib import Path
        try:
            result = self.p4.run("print", "-q", depot_path)
        except Exception:  # noqa: BLE001
            return None
        parts: list[bytes] = []
        for item in result:
            if isinstance(item, str):
                parts.append(item.encode("utf-8", errors="replace"))
            elif isinstance(item, (bytes, bytearray)):
                parts.append(bytes(item))
        if not parts:
            return None
        suffix = Path(depot_path).suffix or ".txt"
        try:
            tmp = tempfile.NamedTemporaryFile(
                prefix="p4v-tui-", suffix=suffix,
                delete=False,
            )
            tmp.write(b"".join(parts))
            tmp.close()
            return tmp.name
        except OSError:
            return None

    # --- Annotate / Blame -----------------------------------------------

    def _open_annotate(self, depot_path: str) -> None:
        """Annotate is a file-only operation. Caller filters out
        directory targets via the menu visibility, but guard here too
        so a stray /... target doesn't 500 the modal."""
        if depot_path.endswith("/...") or depot_path.endswith("/"):
            self.notify(
                "Annotate applies to a single file, not a directory.",
                severity="warning", timeout=4,
            )
            return
        self.push_screen(AnnotateModal(depot_path, self.p4))

    def _open_timelapse(self, depot_path: str) -> None:
        """Time-lapse is also file-only. Same guard as Annotate."""
        if depot_path.endswith("/...") or depot_path.endswith("/"):
            self.notify(
                "Time-lapse applies to a single file, not a directory.",
                severity="warning", timeout=4,
            )
            return
        self.push_screen(TimelapseModal(depot_path, self.p4))

    def _open_rev_graph(self, depot_path: str) -> None:
        """Revision Graph is file-only — directory graphs are too
        noisy to be useful in a textual rendering."""
        if depot_path.endswith("/...") or depot_path.endswith("/"):
            self.notify(
                "Revision Graph applies to a single file, not a "
                "directory.",
                severity="warning", timeout=4,
            )
            return
        self.push_screen(RevisionGraphModal(depot_path, self.p4))

    def _open_file_properties(self, depot_path: str) -> None:
        if depot_path.endswith("/...") or depot_path.endswith("/"):
            self.notify(
                "File Properties applies to a single file, not a "
                "directory.",
                severity="warning", timeout=4,
            )
            return
        self.push_screen(FilePropertiesModal(depot_path, self.p4))

    # --- Show Files in Tree (Submitted CL) -----------------------------

    def _show_cl_in_tree(self, change: str) -> None:
        self._fetch_cl_files_then_show(change)

    @work(thread=True, group="cl_show_tree", exclusive=True)
    def _fetch_cl_files_then_show(self, change: str) -> None:
        try:
            info = self.p4.describe(change)
        except Exception:  # noqa: BLE001
            info = {}
        files = list(info.get("depotFile") or [])
        if not files:
            self.call_from_thread(
                self.notify,
                f"CL {change}: no files to show.",
                severity="warning", timeout=4,
            )
            return
        if len(files) == 1:
            self.call_from_thread(
                self._navigate_tree_to, files[0],
            )
            return

        def push_picker(items=files, c=change) -> None:
            def on_pick(picked: str | None,
                        cc: str = c) -> None:
                if not picked:
                    return
                self._navigate_tree_to(picked)

            self.push_screen(
                FileInCLPickerModal(c, items),
                on_pick,
            )

        self.call_from_thread(push_picker)

    def _navigate_tree_to(self, depot_path: str) -> None:
        """Switch to the Workspace tab and walk to ``depot_path``.

        Translates the depot path through ``p4 where`` so the
        workspace tree (rooted at //<client>) can find it. Falls back
        to the depot tree if the path isn't mapped into the workspace.
        """
        try:
            tabs = self.query_one("#left_tabs", TabbedContent)
        except Exception:  # noqa: BLE001
            return
        # Try Workspace first.
        try:
            info = self.p4.where(depot_path)
        except Exception:  # noqa: BLE001
            info = None
        client_path = (info or {}).get("clientFile") if info else None
        if client_path:
            tabs.active = "tab_workspace"
            try:
                tree = self.query_one("#workspace_tree")
                navigate = getattr(tree, "navigate_to_path", None)
                if navigate is not None:
                    navigate(client_path)
                    return
            except Exception:  # noqa: BLE001
                pass
        # Fall back to depot tree.
        tabs.active = "tab_depot"
        try:
            tree = self.query_one("#depot_tree")
            navigate = getattr(tree, "navigate_to_path", None)
            if navigate is not None:
                navigate(depot_path)
        except Exception:  # noqa: BLE001
            pass

    # --- Tag with Label (Submitted CL) ---------------------------------

    def _tag_cl_with_label(self, change: str) -> None:
        self._fetch_labels_then_pick(change)

    @work(thread=True, group="labels_fetch", exclusive=True)
    def _fetch_labels_then_pick(self, change: str) -> None:
        try:
            labels = self.p4.run("labels", "-m", "500")
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"Listing labels failed: {e}",
                severity="error", timeout=8,
            )
            return
        labels = [r for r in labels if isinstance(r, dict)]
        if not labels:
            self.call_from_thread(
                self.notify,
                "No labels exist on this server. Create one with "
                "`p4 label <name>` first.",
                severity="warning", timeout=8,
            )
            return

        def push_picker(items=labels, c=change) -> None:
            def on_pick(label_name: str | None,
                        cc: str = c) -> None:
                if not label_name:
                    return
                self._run_tag_label(label_name, cc)

            self.push_screen(
                LabelPickerModal(items, purpose=f"tag CL {c}"),
                on_pick,
            )

        self.call_from_thread(push_picker)

    @work(thread=True, group="tag_label")
    def _run_tag_label(self, label: str, change: str) -> None:
        try:
            res = self.p4.run("tag", "-l", label, f"@{change}")
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Tag CL {change} with {label!r} failed: {e}",
                severity="error", timeout=10,
            )
            return
        n = sum(1 for r in res if isinstance(r, dict))
        self.call_from_thread(
            self.notify,
            f"Tagged {n} file(s) in CL {change} with label "
            f"{label!r}.",
            timeout=6,
        )

    # --- Undo Changes (p4 undo, Helix 19.1+) ---------------------------

    def _confirm_undo_file(self, depot_path: str) -> None:
        """``p4 undo <file>`` opens the file in default with the
        content it had one revision before head — i.e. undoes the
        most recent submitted change. Confirm before opening since
        it puts the file in an edit state."""
        if depot_path.endswith("/...") or depot_path.endswith("/"):
            target = depot_path
            scope_label = "every file under this path"
        else:
            target = depot_path
            scope_label = depot_path

        def on_close(yes: bool, t: str = target) -> None:
            if yes:
                self._run_undo(t)

        self.push_screen(
            ConfirmModal(
                title="Undo most recent change?",
                message=(
                    f"Run `p4 undo {target}` — this opens "
                    f"{scope_label} with the content from one "
                    "revision before head, ready to be submitted as "
                    "a reverse-CL.\n\n"
                    "Requires Helix 19.1+ on the server. The result "
                    "lands in your default changelist; you'll need to "
                    "submit it for the undo to stick."
                ),
                ok_label="Undo",
                ok_variant="error",
            ),
            callback=on_close,
        )

    def _confirm_undo_cl(self, change: str) -> None:
        """Undo a whole submitted changelist via ``p4 undo @<CL>``.
        Opens every file from the CL with the content from one rev
        before this CL touched it; submitting that lands the reverse."""
        def on_close(yes: bool, c: str = change) -> None:
            if yes:
                self._run_undo(f"@{c}")

        self.push_screen(
            ConfirmModal(
                title=f"Undo changelist {change}?",
                message=(
                    f"Run `p4 undo @{change}` — every file touched by "
                    f"CL {change} is opened with the content it had "
                    "one revision before this CL.\n\n"
                    "Requires Helix 19.1+ on the server. The result "
                    "lands in your default changelist; submit that "
                    "for the undo to stick on the depot."
                ),
                ok_label="Undo CL",
                ok_variant="error",
            ),
            callback=on_close,
        )

    @work(thread=True, group="undo")
    def _run_undo(self, target: str) -> None:
        try:
            res = self.p4.run("undo", target)
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Undo {target} failed: {e}",
                severity="error", timeout=10,
            )
            return
        n = sum(1 for r in res if isinstance(r, dict))
        self.call_from_thread(
            self.notify,
            f"Opened {n} file(s) for undo. Submit your default CL "
            "to finalize the reverse change.",
            timeout=8,
        )
        self.call_from_thread(
            self.query_one(WorkspaceTree).refresh_root,
        )
        self.call_from_thread(self._load_pending)

    # --- Resolve --------------------------------------------------------

    def _open_resolve_modal(self, target: str) -> None:
        """Push the Resolve picker for ``target``. Caller has already
        run integrate / copy / unshelve and wants to handle the
        resulting unresolved files."""
        def on_close(ran) -> None:
            # Ctrl+E in the modal hands one file to the 3-way merge editor.
            if isinstance(ran, dict) and ran.get("merge"):
                self._run_3way_merge(ran["merge"])
                return
            if ran:
                # Resolve actions touch open files; refresh the
                # workspace tree + pending list so status overlays /
                # CL contents reflect what changed.
                self.query_one(WorkspaceTree).refresh_root()
                self._load_pending()

        self.push_screen(ResolveModal(target, self.p4), on_close)

    @work(thread=True, group="merge3")
    def _run_3way_merge(self, target: str) -> None:
        """Materialise conflict markers, open the merge editor, accept (item 1).

        The resolve-flag choreography here is non-obvious and was *wrong*
        until verified live (CL 56826 probe conflict, both backends):

        * ``resolve -am`` accepts a clean auto-merge but **skips**
          conflicting files *without writing markers* — so it can only
          tell us "clean vs conflict", never produce a file to hand-edit.
        * ``resolve -af`` is what actually writes Perforce's 3-way
          ``>>>> ORIGINAL`` / ``==== THEIRS`` / ``==== YOURS`` / ``<<<<``
          markers (it regenerates the merge, ignoring the workspace file),
          and it also marks the file resolved.
        * ``resolve -af`` does **not** accept hand edits — it regenerates.
          To commit the user's chosen merge we instead write the workspace
          file and rely on the fact that a resolved open file submits its
          *current workspace content* (see :meth:`_write_and_accept_merge`).

        Flow: ``-am`` (clean → done); on conflict, snapshot "yours", run
        ``-af`` to emit markers, parse + hand-merge in the editor, then
        write the result back (accept) or restore "yours" (cancel).
        """
        import os
        from .merge3 import has_conflicts, parse_conflict_markers
        from .widgets.merge_editor_modal import MergeEditorModal

        # 1. Try a clean auto-merge. Resolves cleanly-mergeable files;
        #    raises / skips when there's a real conflict (no markers).
        try:
            self.p4.run("resolve", "-am", target)
        except P4Exception:
            pass

        local = None
        try:
            info = self.p4.where(target) or {}
            # `path` is the local OS path; `clientFile` is client *syntax*
            # (//client/…) and won't exist on disk, so prefer `path`.
            local = info.get("path") or info.get("clientFile")
        except Exception:  # noqa: BLE001
            local = None
        if not local or not os.path.exists(local):
            self.call_from_thread(
                self.notify, f"Can't locate the local file for {target}.",
                severity="error", timeout=8,
            )
            return

        # 2. Did -am leave the file unresolved? Then it conflicted and we
        #    must hand-merge. (`resolve -n` lists files still needing one.)
        conflicted = False
        try:
            pending = self.p4.run("resolve", "-n", target)
            conflicted = any(isinstance(r, dict) for r in pending)
        except Exception:  # noqa: BLE001
            conflicted = False
        if not conflicted:
            self.call_from_thread(
                self.notify,
                f"{target}: auto-merged cleanly — nothing to hand-merge.",
                timeout=5,
            )
            self.call_from_thread(self._refresh_after_action, None)
            return

        # 3. Snapshot the marker-free "yours" content, then force Perforce
        #    to write its 3-way conflict markers with -af (this also marks
        #    the file resolved — that's fine, we set the final content
        #    ourselves below and a resolved open file submits its
        #    workspace content).
        try:
            with open(local, encoding="utf-8", errors="replace") as fh:
                yours_snapshot = fh.read()
        except OSError as e:
            self.call_from_thread(
                self.notify, f"Read failed: {e}", severity="error", timeout=8,
            )
            return
        try:
            self.p4.run("resolve", "-af", target)
        except P4Exception as e:
            self.call_from_thread(
                self.notify, f"Preparing merge markers failed: {e}",
                severity="error", timeout=10,
            )
            return
        try:
            with open(local, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError as e:
            self.call_from_thread(
                self.notify, f"Read failed: {e}", severity="error", timeout=8,
            )
            return

        segs = parse_conflict_markers(content)
        if not has_conflicts(segs):
            # Defensive: -n said conflict but -af produced no markers.
            # Put "yours" back so we don't leave markers in the file.
            self._restore_merge_file(local, yours_snapshot)
            self.call_from_thread(
                self.notify,
                f"{target}: no conflict hunks to hand-merge.",
                timeout=5,
            )
            return

        def on_close(merged, p=target, lp=local, ys=yours_snapshot) -> None:
            if merged is not None:
                self._write_and_accept_merge(p, lp, merged)
            else:
                self._cancel_merge(p, lp, ys)

        self.call_from_thread(
            self.push_screen, MergeEditorModal(target, segs), on_close,
        )

    @staticmethod
    def _restore_merge_file(local_path: str, text: str) -> bool:
        try:
            with open(local_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return True
        except OSError:
            return False

    @work(thread=True, group="merge3_accept")
    def _write_and_accept_merge(
        self, target: str, local_path: str, merged_text: str,
    ) -> None:
        # The file was already marked resolved by the `-af` that emitted
        # the markers; overwriting the workspace file with the user's
        # chosen merge means the submit will carry exactly this content.
        # (We must NOT re-run `resolve -af` here — it would regenerate the
        # merge and discard the user's choices.)
        if not self._restore_merge_file(local_path, merged_text):
            self.call_from_thread(
                self.notify, "Writing merged file failed.",
                severity="error", timeout=8,
            )
            return
        self.call_from_thread(
            self.notify, f"Merged and resolved {target}.", timeout=5,
        )
        self.call_from_thread(self._refresh_after_action, None)

    @work(thread=True, group="merge3_accept")
    def _cancel_merge(
        self, target: str, local_path: str, yours_text: str,
    ) -> None:
        # Editor cancelled. The `-af` that produced the markers already
        # resolved the file, and re-opening it for resolve is interactive
        # (can't be scripted safely), so we keep it resolved but strip the
        # markers back to your version — i.e. effectively "accept yours".
        # The user can redo via the Pending-CL "Re-resolve" action.
        self._restore_merge_file(local_path, yours_text)
        self.call_from_thread(
            self.notify,
            f"Merge cancelled — kept your version of {target} "
            "(resolved as yours; use Re-resolve to redo).",
            timeout=7,
        )
        self.call_from_thread(self._refresh_after_action, None)

    @work(thread=True, group="resolve_check")
    def _check_resolve_after_bci(self, target: str, op: str) -> None:
        """After integrate / copy, check whether anything needs resolve
        and prompt the user to open the modal."""
        try:
            rows = self.p4.run("resolve", "-n", target)
        except Exception:  # noqa: BLE001
            return
        unresolved = [r for r in rows if isinstance(r, dict)]
        if not unresolved:
            return
        n = len(unresolved)

        def prompt(count: int = n, t: str = target) -> None:
            def on_close(yes: bool, tt: str = t) -> None:
                if yes:
                    self._open_resolve_modal(tt)

            self.push_screen(
                ConfirmModal(
                    title=f"{count} file(s) need resolve",
                    message=(
                        f"{op.capitalize()} {t} left {count} file(s) "
                        "open with merges to resolve. Open the Resolve "
                        "picker now?"
                    ),
                    ok_label="Open Resolve",
                    ok_variant="primary",
                ),
                callback=on_close,
            )

        self.call_from_thread(prompt)

    # --- Delete empty Pending CL ---------------------------------------

    def _confirm_delete_cl(self, change: str) -> None:
        """``p4 change -d`` deletes a pending CL — but only when it has
        no opened files and no shelf. Show a confirm and let p4's
        error message guide the user when those preconditions aren't
        met."""
        def on_close(yes: bool, c: str = change) -> None:
            if yes:
                self._run_delete_cl(c)

        self.push_screen(
            ConfirmModal(
                title=f"Delete pending changelist {change}?",
                message=(
                    f"Run `p4 change -d {change}`. The CL must be "
                    "empty (no opened files, no shelf) for this to "
                    "succeed.\n\n"
                    "If it isn't, p4 returns an error and the CL "
                    "stays put — revert / move / delete-shelf first, "
                    "then retry."
                ),
                ok_label="Delete CL",
                ok_variant="error",
            ),
            callback=on_close,
        )

    @work(thread=True, group="cl_delete")
    def _run_delete_cl(self, change: str) -> None:
        try:
            self.p4.run("change", "-d", str(change))
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Delete CL {change} failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.call_from_thread(
            self.notify, f"Deleted pending CL {change}.", timeout=5,
        )
        self.call_from_thread(self._load_pending)

    # --- Shelving -------------------------------------------------------

    def _run_shelve_interactive(self, change: str) -> None:
        """Pick a subset of the CL's open files, then shelve them (item 2)."""
        self._fetch_then_pick_shelve(change)

    @work(thread=True, group="shelve_pick")
    def _fetch_then_pick_shelve(self, change: str) -> None:
        try:
            opened = self.p4.run("opened", "-c", change)
        except P4Exception as e:
            self.call_from_thread(
                self.notify, f"Listing files in CL {change} failed: {e}",
                severity="error", timeout=8,
            )
            return
        files = [o.get("depotFile", "") for o in opened if o.get("depotFile")]
        if not files:
            self.call_from_thread(
                self.notify, f"CL {change} has no open files to shelve.",
                severity="warning", timeout=5,
            )
            return

        def on_close(selected: list[str] | None, c: str = change) -> None:
            if selected:
                # All selected → omit the file list so p4 shelves the whole
                # CL exactly as before; a subset passes explicit paths.
                files_arg = None if len(selected) == len(files) else selected
                self._run_shelve(c, force=False, files=files_arg)

        from .widgets.shelve_picker_modal import ShelvePickerModal
        self.call_from_thread(
            self.push_screen, ShelvePickerModal(change, files), on_close,
        )

    @work(thread=True, group="shelve")
    def _run_shelve(
        self, change: str, *, force: bool, files: list[str] | None = None,
    ) -> None:
        args: list = ["shelve", "-c", str(change)]
        if force:
            args.append("-f")
        # `p4 shelve` with no file list shelves everything currently
        # opened in the CL — same as p4v's "Shelve Files". A non-empty
        # `files` list shelves only that subset (item 2 partial shelve).
        if files:
            args.extend(files)
        try:
            res = self.p4.run(*args)
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Shelve CL {change} failed: {e}",
                severity="error", timeout=10,
            )
            return
        n = sum(1 for r in res if isinstance(r, dict))
        verb = "Re-shelved" if force else "Shelved"
        self.call_from_thread(
            self.notify,
            f"{verb} {n} file(s) in CL {change}.",
            timeout=5,
        )
        self.call_from_thread(self._load_pending)

    def _confirm_shelve_delete(self, change: str) -> None:
        def on_close(yes: bool, c: str = change) -> None:
            if yes:
                self._run_shelve_delete(c)

        self.push_screen(
            ConfirmModal(
                title=f"Delete shelf for CL {change}?",
                message=(
                    f"Discard the shelved copy attached to CL {change}. "
                    "Open files in the CL stay open — only the shelf is "
                    "removed. Cannot be undone."
                ),
                ok_label="Delete shelf",
                ok_variant="error",
            ),
            callback=on_close,
        )

    @work(thread=True, group="shelve_delete")
    def _run_shelve_delete(self, change: str) -> None:
        try:
            self.p4.run("shelve", "-d", "-c", str(change))
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Delete shelf for CL {change} failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.call_from_thread(
            self.notify, f"Deleted shelf for CL {change}.", timeout=4,
        )
        self.call_from_thread(self._load_pending)

    def _open_unshelve_target_picker(self, source_change: str) -> None:
        """Pick a destination CL (default / existing / new), then
        unshelve ``source_change``'s shelf into it."""
        choices: list[tuple[str, str]] = [("default", "default")]
        try:
            pending = self.p4.pending_changes(client=self.p4.client)
        except P4Exception:
            pending = []
        for r in pending:
            num = str(r.get("change", ""))
            if not num or num == source_change:
                continue
            desc = first_nonblank_line(r.get("desc", "") or "")
            label = f"{num} — {desc}" if desc else num
            choices.append((num, truncate_cells(label, 80)))
        choices.append((NEW_CL_SENTINEL, "New changelist…"))

        def on_close(target_id: str | None,
                     src: str = source_change) -> None:
            if target_id is None:
                return
            if target_id == NEW_CL_SENTINEL:
                self._open_new_cl_then_unshelve(src)
                return
            self._run_unshelve(src, target_id)

        self.push_screen(
            MoveToChangelistModal(source_change, choices),
            on_close,
        )

    def _open_new_cl_then_unshelve(self, source_change: str) -> None:
        """User chose 'New CL' as the unshelve target — collect a
        description, create the CL, then unshelve into it."""
        def on_desc(desc: str | None, src: str = source_change) -> None:
            if not desc:
                return
            self._create_then_unshelve(src, desc)

        self.push_screen(NewChangelistModal(), on_desc)

    @work(thread=True, group="unshelve")
    def _create_then_unshelve(
        self, source_change: str, description: str,
    ) -> None:
        try:
            new_cl = self.p4.create_changelist(description)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"Create destination CL failed: {e}",
                severity="error", timeout=10,
            )
            return
        self._unshelve_into(source_change, new_cl)

    @work(thread=True, group="unshelve")
    def _run_unshelve(
        self, source_change: str, target_change: str,
    ) -> None:
        self._unshelve_into(source_change, target_change)

    def _unshelve_into(
        self, source_change: str, target_change: str,
    ) -> None:
        # `target_change` is "default" or a numeric string. p4 takes
        # either form for `-c`.
        try:
            res = self.p4.run(
                "unshelve",
                "-s", str(source_change),
                "-c", str(target_change),
            )
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Unshelve {source_change} → {target_change} failed: {e}",
                severity="error", timeout=10,
            )
            return
        n = sum(1 for r in res if isinstance(r, dict))
        self.call_from_thread(
            self.notify,
            f"Unshelved {n} file(s) from CL {source_change} into "
            f"CL {target_change}.",
            timeout=6,
        )
        self.call_from_thread(self._load_pending)
        # Unshelve can leave files needing resolve too — same flow as
        # integrate / copy.
        self._check_resolve_after_unshelve(target_change)

    @work(thread=True, group="resolve_check")
    def _check_resolve_after_unshelve(self, target_change: str) -> None:
        try:
            rows = self.p4.run("resolve", "-n", "-c", target_change)
        except Exception:  # noqa: BLE001
            return
        unresolved = [r for r in rows if isinstance(r, dict)]
        if not unresolved:
            return
        n = len(unresolved)

        def prompt(count: int = n, t: str = target_change) -> None:
            def on_close(yes: bool, tt: str = t) -> None:
                if yes:
                    # Resolve scope = files in the destination CL.
                    self._open_resolve_modal(["-c", tt])

            self.push_screen(
                ConfirmModal(
                    title=f"{count} file(s) need resolve",
                    message=(
                        f"Unshelve into CL {t} left {count} file(s) "
                        "open with merges to resolve. Open the Resolve "
                        "picker now?"
                    ),
                    ok_label="Open Resolve",
                    ok_variant="primary",
                ),
                callback=on_close,
            )

        self.call_from_thread(prompt)

    # --- Submit & Resolve ----------------------------------------------

    def _submit_and_resolve(self, change: str) -> None:
        """Open the Resolve modal first; on close, kick off the
        normal Submit flow. If nothing needed resolving the picker
        closes immediately and we go straight to Submit."""
        def on_close(ran: bool | None, c: str = change) -> None:
            if ran:
                self.query_one(WorkspaceTree).refresh_root()
                self._load_pending()
            # Whether anything ran or not, drop into Submit after
            # the user closes the picker — that's what "Submit & Resolve"
            # means.
            self._apply_edits_then_submit(c, None, [])

        self.push_screen(
            ResolveModal(["-c", change], self.p4), on_close,
        )

    # --- branch / copy / integrate --------------------------------------

    def _open_bci_modal(
        self,
        operation: str,
        target: str,
        *,
        source: str = "",
    ) -> None:
        """Open the BCI modal. ``source`` is pre-filled when the
        operation is scoped to a specific submitted changelist
        (e.g. ``@={CL},@={CL}`` from the right-pane menus)."""
        def on_close(result: dict | None, op: str = operation) -> None:
            if not result:
                return
            self._run_bci(
                op, result["source"], result["target"],
                result.get("description", ""),
            )

        self.push_screen(
            BranchCopyIntegrateModal(operation, target=target, source=source),
            on_close,
        )

    @work(thread=True, group="bci")
    def _run_bci(
        self,
        operation: str,
        source: str,
        target: str,
        description: str,
    ) -> None:
        try:
            if operation == "integrate":
                result = self.p4.run("integrate", source, target)
                action_done = "integrated"
            elif operation == "copy":
                result = self.p4.run("copy", source, target)
                action_done = "copied"
            elif operation == "branch":
                # populate auto-submits the resulting CL with the
                # description we pass through -d.
                result = self.p4.run(
                    "populate", "-d", description, source, target,
                )
                action_done = "branched"
            else:
                self.call_from_thread(
                    self.notify,
                    f"Unknown BCI operation: {operation}",
                    severity="error", timeout=5,
                )
                return
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"{OPERATION_LABEL.get(operation, operation)} "
                f"{source} → {target} failed: {e}",
                severity="error", timeout=10,
            )
            return
        n = sum(1 for r in result if isinstance(r, dict))
        msg = f"{action_done} {n} file(s): {source} → {target}"
        self.call_from_thread(self.notify, msg, timeout=6)
        # Refresh: integrate/copy open files in default CL → pending changes;
        # branch (populate) creates a new submitted CL → submitted list.
        if operation == "branch":
            self.call_from_thread(self._load_submitted)
        else:
            self.call_from_thread(self._load_pending)
        self.call_from_thread(
            self.query_one(WorkspaceTree).refresh_root,
        )
        # Integrate / copy may leave files needing resolve. Branch
        # (populate) auto-submits so it can't.
        if operation in ("integrate", "copy"):
            self._check_resolve_after_bci(target, operation)

    # --- edit changelist description ------------------------------------

    def _open_edit_desc_modal(self, change: str, *, force: bool) -> None:
        """Two-step: fetch current desc on a worker, then push the modal
        on the UI thread with that desc pre-filled."""
        self._fetch_then_edit_desc(change, force)

    @work(thread=True, group="cl_edit_desc_fetch")
    def _fetch_then_edit_desc(self, change: str, force: bool) -> None:
        try:
            form = self.p4.get_changelist_form(change)
            current_desc = form.get("Description", "") or ""
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify, f"Read CL {change} failed: {e}",
                severity="error", timeout=10,
            )
            return

        def push_modal() -> None:
            def on_close(new_desc: str | None,
                         c: str = change, f: bool = force) -> None:
                if not new_desc:
                    return
                if new_desc.strip() == current_desc.strip():
                    self.notify(
                        "Description unchanged.", timeout=3,
                    )
                    return
                self._save_cl_description(c, new_desc, force=f)

            self.push_screen(
                EditChangelistDescModal(
                    change, current_desc, force=force,
                ),
                on_close,
            )

        self.call_from_thread(push_modal)

    @work(thread=True, group="cl_edit_desc_save")
    def _save_cl_description(
        self, change: str, new_desc: str, *, force: bool,
    ) -> None:
        try:
            self.p4.update_changelist_description(
                change, new_desc, force=force,
            )
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify,
                f"Save CL {change} description failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.call_from_thread(
            self.notify,
            f"Saved description for CL {change}",
            timeout=4,
        )
        # Refresh whichever list this CL lives in.
        if force:
            self.call_from_thread(self._load_submitted)
            # Also refresh the detail pane if this CL is highlighted.
            self.call_from_thread(
                self._load_change_detail, change, True,
            )
        else:
            self.call_from_thread(self._load_pending)

    # --- new pending changelist -----------------------------------------

    def action_new_pending_cl(self) -> None:
        def on_close(desc: str | None) -> None:
            if desc:
                self._create_pending_cl(desc)

        self.push_screen(NewChangelistModal(), on_close)

    @work(thread=True, group="cl_create")
    def _create_pending_cl(self, description: str) -> None:
        try:
            change = self.p4.create_changelist(description)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify, f"Create CL failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.call_from_thread(
            self.notify, f"Created pending changelist {change}", timeout=4,
        )
        self.call_from_thread(self._load_pending)

    @work(thread=True, group="cl_revert_unchanged")
    def _run_revert_unchanged(self, change: str) -> None:
        try:
            # `p4 revert -a -c <change>`: revert files that are unchanged
            # vs head (i.e. opened-but-no-edits-actually-made).
            res = self.p4.run("revert", "-a", "-c", change)
        except P4Exception as e:
            self.call_from_thread(
                self.notify,
                f"Revert unchanged in CL {change} failed: {e}",
                severity="error", timeout=10,
            )
            return
        n = sum(1 for r in res if isinstance(r, dict))
        self.call_from_thread(
            self.notify,
            f"CL {change}: reverted {n} unchanged file(s)",
            timeout=4,
        )
        self.call_from_thread(self._load_pending)
        self.call_from_thread(
            self.query_one(WorkspaceTree).refresh_root,
        )

    # --- submit current pending CL ---------------------------------------

    def action_submit_pending(self) -> None:
        # Only meaningful when the user is on the Pending tab.
        try:
            tabs = self.query_one("#right_tabs", TabbedContent)
        except Exception:  # noqa: BLE001
            return
        if tabs.active != "tab_pending":
            self.notify(
                "Switch to the Pending Changelists tab first.",
                severity="warning", timeout=4,
            )
            return
        table = self.query_one("#pending_table", DataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            return
        try:
            row = table.get_row_at(table.cursor_row)
        except IndexError:
            return
        change = str(row[0]) if row else ""
        if not change:
            return
        if change == "default":
            self.notify(
                "The default changelist must be promoted to a numbered "
                "changelist before submit.",
                severity="warning", timeout=6,
            )
            return
        # Submit binds opened files in the current client, so a CL
        # whose files live in another workspace cannot be submitted
        # from here. Refuse loudly rather than queueing a job that
        # would fail with a cryptic p4 error several seconds later.
        if self._is_remote_pending(change):
            self.notify(
                self._remote_workspace_note(change),
                severity="warning", timeout=8,
            )
            return

        def on_close(yes: bool, c: str = change) -> None:
            if yes:
                job = ResilientSubmitJob(self.p4, c)
                self.jobs.submit_job(job)
                self.notify(f"Queued resilient submit: CL {c}", timeout=4)

        # Pre-submit guards (item 5): surface unresolved files / oversized
        # blobs / empty CL so the user can back out before queuing.
        warnings = evaluate_submit_guards(self._gather_submit_files(change))
        warn_block = format_guard_warnings(warnings)
        message = (
            f"Submit CL {change} to depot.\n\n"
            "Will retry transparently on connection drops; if the "
            "server commits but the ack is lost, the next attempt "
            "is recognized as already-submitted."
        )
        jira_note = self._jira_submit_note(change)  # item 8
        if jira_note:
            message = f"{jira_note}\n\n{message}"
        if warn_block:
            message = f"{warn_block}\n\n{message}"

        self.push_screen(
            ConfirmModal(
                title=f"Submit changelist {change}?",
                message=message,
                ok_label="Submit anyway" if warnings else "Submit",
                ok_variant="warning" if has_blocking(warnings) else "primary",
            ),
            callback=on_close,
        )

    def _gather_submit_files(self, change: str) -> list[SubmitFile]:
        """Best-effort gather of a CL's opened files for guard evaluation.

        Never raises: any probe failure just yields fewer/no warnings so a
        guard error can never block an otherwise-valid submit. Sizes use the
        local working copy (os.stat of the client path) since that is what is
        actually being submitted; falls back to the depot fileSize.
        """
        import os
        try:
            opened = self.p4.run("opened", "-c", change)
        except Exception:  # noqa: BLE001
            return []
        depots = [o.get("depotFile", "") for o in opened if o.get("depotFile")]

        sizes: dict[str, int] = {}
        if depots:
            try:
                for r in self.p4.run("fstat", "-Ol", *depots):
                    depot = r.get("depotFile", "")
                    client = r.get("clientFile")
                    if client and os.path.exists(client):
                        try:
                            sizes[depot] = os.path.getsize(client)
                            continue
                        except OSError:
                            pass
                    if r.get("fileSize"):
                        try:
                            sizes[depot] = int(r["fileSize"])
                        except (TypeError, ValueError):
                            pass
            except Exception:  # noqa: BLE001
                pass

        unresolved: set[str] = set()
        if depots:
            try:
                for r in self.p4.run("fstat", "-Ru", *depots):
                    if r.get("depotFile"):
                        unresolved.add(r["depotFile"])
            except Exception:  # noqa: BLE001 -- none-to-resolve / filter empty
                pass

        return [
            SubmitFile(
                depot_path=o.get("depotFile", ""),
                action=o.get("action", ""),
                size_bytes=sizes.get(o.get("depotFile", "")),
                unresolved=o.get("depotFile", "") in unresolved,
            )
            for o in opened
        ]

    def _jira_submit_note(self, change: str) -> str:
        """Submit-time Jira linkage note for the confirm dialog (item 8).

        Returns "" when no ``[jira] base_url`` is configured (feature off).
        Otherwise reads the CL description and either confirms the matched
        issue key(s) + browse URL, or warns that none is referenced — the
        Jira ↔ Perforce link is made via the key in the description.
        """
        from .jira import build_jira_url, extract_jira_keys, projects_for_paths

        jira_cfg = getattr(self.config, "jira", None)
        base = getattr(jira_cfg, "base_url", None)
        if not base:
            return ""
        desc, paths = "", []
        try:
            # Use the normalised describe() façade — it returns a flat
            # `depotFile` list on both backends. (The old raw
            # `run("describe")` + `startswith("depotFile")` collected one
            # nested list on P4Python and per-numbered-key strings on the
            # CLI backend, so the per-path Jira project map was dead on the
            # default backend.)
            info = self.p4.describe(change)
            desc = (info.get("desc", "") or "") if info else ""
            paths = [p for p in (info.get("depotFile") or []) if p]
        except Exception:  # noqa: BLE001 -- best-effort; absence just warns
            desc, paths = "", []
        # Expected project(s): from the CL's depot paths first (per-path
        # mapping), falling back to the flat projects list, else any key.
        expected = (
            projects_for_paths(paths, jira_cfg.path_projects)
            or jira_cfg.projects
            or None
        )
        keys = extract_jira_keys(desc, known_projects=expected)
        if keys:
            shown = ", ".join(
                f"{k} → {build_jira_url(base, k)}" for k in keys[:2]
            )
            extra = "" if len(keys) <= 2 else f" (+{len(keys) - 2} more)"
            return f"🔗 Jira: {shown}{extra}"
        if expected:
            hint = " / ".join(p.upper() for p in expected[:3])
            return (
                f"⚠ No Jira issue referenced — expected {hint} "
                f"(e.g. {expected[0].upper()}-123) via Edit Description."
            )
        return (
            "⚠ No Jira issue referenced in the description "
            "(add e.g. ABC-123 via Edit Description before submit)."
        )

    def action_shrink_left(self) -> None:
        self.left_pane_width = max(MIN_LEFT_WIDTH,
                                   self.left_pane_width - LEFT_WIDTH_STEP)

    def action_grow_left(self) -> None:
        self.left_pane_width = min(MAX_LEFT_WIDTH,
                                   self.left_pane_width + LEFT_WIDTH_STEP)

    def watch_left_pane_width(self, value: int) -> None:
        try:
            self.query_one("#left_pane").styles.width = value
        except Exception:  # noqa: BLE001 -- pane not mounted yet
            pass
        self._save_pane_sizes()

    def watch_detail_pane_height(self, value: int) -> None:
        try:
            self.query_one("#detail_pane").styles.height = value
        except Exception:  # noqa: BLE001
            pass
        self._save_pane_sizes()

    def watch_log_panel_height(self, value: int) -> None:
        try:
            self.query_one("#log_panel").styles.height = value
        except Exception:  # noqa: BLE001
            pass
        self._save_pane_sizes()

    def _save_pane_sizes(self) -> None:
        """Persist current splitter positions. Skipped while we're
        applying restored state so reading the file in ``__init__``
        doesn't immediately bounce it back through the save path."""
        if self._restoring_state:
            return
        self._ui_state["left_pane_width"] = int(self.left_pane_width)
        self._ui_state["detail_pane_height"] = int(self.detail_pane_height)
        self._ui_state["log_panel_height"] = int(self.log_panel_height)
        save_state(self._ui_state)

    # --- mouse-drag splitter handling ---------------------------------

    def on_splitter_dragged(self, event: SplitterDragged) -> None:
        """Routes drag deltas from the three splitters to the right
        reactive. The splitter widget itself knows nothing about
        which pane it sits next to — that's mapped here by ``id``."""
        sid = event.splitter.id
        if sid == "main_splitter":
            new = self.left_pane_width + event.delta
            self.left_pane_width = max(
                MIN_LEFT_WIDTH, min(MAX_LEFT_WIDTH, new),
            )
        elif sid == "detail_splitter":
            # Splitter is above the detail pane: dragging UP (negative
            # delta_y) grows the detail pane, dragging DOWN shrinks it.
            new = self.detail_pane_height - event.delta
            self.detail_pane_height = max(
                MIN_DETAIL_HEIGHT,
                min(MAX_DETAIL_HEIGHT, new),
            )
        elif sid == "log_splitter":
            # Splitter is above the log panel: same rule as detail.
            new = self.log_panel_height - event.delta
            self.log_panel_height = max(
                MIN_LOG_HEIGHT, min(MAX_LOG_HEIGHT, new),
            )

    def watch_narrow_mode(self, value: bool) -> None:
        # When leaving narrow mode, reset the navigator back to the tree
        # page — otherwise a stale "log"/"submitted" page silently
        # confuses the next narrow-mode entry (and would leave the log
        # panel forced to 1fr / a pane hidden in the wide layout).
        if not value:
            self.narrow_page = "tree"
        self._apply_pane_visibility()

    def watch_narrow_page(self, value: str) -> None:
        # Remember the last non-tree page for the F3 / Ctrl+W toggle.
        if value != "tree":
            self._narrow_last_panel = narrow_nav.normalize_page(value)
        self._apply_pane_visibility()

    def _apply_pane_visibility(self) -> None:
        try:
            main = self.query_one("#main")
            left = self.query_one("#left_pane")
            right = self.query_one("#right_pane")
        except Exception:  # noqa: BLE001
            return
        # Log panel + its drag splitter — optional lookups since both
        # may legitimately be missing during teardown.
        try:
            log_panel = self.query_one("#log_panel")
        except Exception:  # noqa: BLE001
            log_panel = None
        try:
            log_splitter = self.query_one("#log_splitter")
        except Exception:  # noqa: BLE001
            log_splitter = None
        # Detail pane (+ its splitter) inside the right-pane stack.
        # We don't hide it in narrow-panels mode, but we do shrink
        # it so the actual list (pending / submitted / history) has
        # vertical room to breathe — without this the table can
        # collapse to 0 visible rows on a phone-sized viewport.
        try:
            detail_pane = self.query_one("#detail_pane")
        except Exception:  # noqa: BLE001
            detail_pane = None

        def _show(widget, displayed: bool) -> None:
            if widget is None:
                return
            # Textual's canonical hide API is the ``display`` bool
            # property (which maps to styles.display under the hood).
            # Using it directly invalidates the layout the same way
            # an explicit refresh would — important here because we
            # want the right-pane's ``1fr`` height region to expand
            # the moment the log panel disappears.
            try:
                widget.display = bool(displayed)
            except Exception:  # noqa: BLE001
                try:
                    widget.styles.display = (
                        "block" if displayed else "none"
                    )
                except Exception:  # noqa: BLE001
                    pass

        try:
            detail_splitter = self.query_one("#detail_splitter")
        except Exception:  # noqa: BLE001
            detail_splitter = None

        if not self.narrow_mode:
            main.remove_class("narrow")
            _show(main, True)
            left.styles.display = "block"
            right.styles.display = "block"
            left.styles.width = self.left_pane_width
            # Restore the persisted heights — leaving narrow mode
            # mustn't strand the log panel hidden or stuck at the
            # full-screen 1fr the narrow "log" page forces on it.
            _show(log_panel, True)
            _show(log_splitter, True)
            _show(detail_pane, True)
            _show(detail_splitter, True)
            if log_panel is not None:
                try:
                    log_panel.styles.height = self.log_panel_height
                except Exception:  # noqa: BLE001
                    pass
            if detail_pane is not None:
                try:
                    detail_pane.styles.height = self.detail_pane_height
                except Exception:  # noqa: BLE001
                    pass
            return

        # Narrow mode — a single full-screen "page" at a time. The detail
        # pane is always hidden here (its inline CL preview isn't worth the
        # vertical room on a phone; Enter still opens the FileViewerModal),
        # and the Log panel is shown ONLY on its own dedicated page so the
        # tree / tables get the whole viewport instead of being squeezed
        # into the ~6 rows left over after a fixed 10-row Log strip.
        main.add_class("narrow")
        page = narrow_nav.normalize_page(self.narrow_page)
        _show(detail_pane, False)
        _show(detail_splitter, False)

        if page == "log":
            # Full-screen Log: collapse #main entirely and let the Log
            # panel (a sibling of #main) take the 1fr it leaves behind.
            _show(main, False)
            _show(log_panel, True)
            _show(log_splitter, False)
            if log_panel is not None:
                try:
                    log_panel.styles.height = "1fr"
                except Exception:  # noqa: BLE001
                    pass
                try:
                    log_panel.focus()
                except Exception:  # noqa: BLE001
                    pass
            return

        # tree + panel pages share: #main visible, Log hidden.
        _show(main, True)
        _show(log_panel, False)
        _show(log_splitter, False)

        if narrow_nav.is_panel_page(page):
            left.styles.display = "none"
            right.styles.display = "block"
            right.styles.width = "1fr"
            tab_id = narrow_nav.right_tab_for_page(page)
            if tab_id:
                try:
                    self.query_one(
                        "#right_tabs", TabbedContent,
                    ).active = tab_id
                except Exception:  # noqa: BLE001
                    pass
            self._focus_active_right_pane()
        else:  # "tree"
            left.styles.display = "block"
            right.styles.display = "none"
            left.styles.width = "1fr"
            self._focus_active_left_pane()

    def _focus_active_left_pane(self) -> None:
        try:
            tabs = self.query_one("#left_tabs", TabbedContent)
            wid = self._LEFT_TAB_TO_WIDGET.get(tabs.active, "#depot_tree")
            self.query_one(wid).focus()
        except Exception:  # noqa: BLE001
            pass

    def _focus_active_right_pane(self) -> None:
        try:
            tabs = self.query_one("#right_tabs", TabbedContent)
            wid = self._RIGHT_TAB_TO_WIDGET.get(tabs.active, "#pending_table")
            self.query_one(wid).focus()
        except Exception:  # noqa: BLE001
            pass

    def action_smart_tab(self, delta: int) -> None:
        """Tab / Shift+Tab — page cycle (narrow) / focus cycle (wide).

        In **narrow mode** every screen is its own full-screen page, so
        Tab simply advances the page navigator
        (tree → pending → history → submitted → log → tree) and
        Shift+Tab walks it back. Tab is the *reliable* phone driver for
        this: iPhone Blink and similar terminals expose a Tab key in
        their accessory bar but do **not** emit Ctrl+Arrow escape
        sequences, so the Ctrl+→ / Ctrl+← desktop page cycle never
        reaches the app on those keyboards. ``narrow_page``'s watcher
        moves focus onto the page's primary widget, so no explicit
        ``.focus()`` is needed here.

        In **wide mode** all panes are visible at once, so Tab keeps a
        curated focus chain (active tree → active CL/History table →
        Log panel) — friendlier than Textual's default "every focusable
        in DOM order", which stops on header / underline widgets that
        aren't useful targets.

        Input widgets (filter overlay, modal text fields) are NOT
        affected: Modal screens own their own Tab bindings with
        higher precedence, and the tree-filter overlay traps
        focus until dismissed.
        """
        if self.narrow_mode:
            self.narrow_page = narrow_nav.cycle_page(self.narrow_page, delta)
            return

        chain = self._smart_focus_chain()
        if not chain:
            return
        current = self.focused
        # Find the index of the currently focused widget on the
        # chain. If focus isn't on any of them (e.g. user is on a
        # tab header), start at -1 so Tab lands on chain[0] and
        # Shift+Tab on chain[-1].
        try:
            idx = chain.index(current) if current is not None else -1
        except ValueError:
            idx = -1
        target = chain[(idx + delta) % len(chain)]
        try:
            target.focus()
        except Exception:  # noqa: BLE001
            pass

    def _smart_focus_chain(self):
        """Curated focus cycle used by :meth:`action_smart_tab`.

        Order matches the user's mental model on a phone-sized
        viewport: tree on the left, then the matching CL/history
        table on the right, then the global Log panel. Missing
        widgets (e.g. log panel not mounted yet) are silently
        skipped so the chain stays usable during teardown / re-init.
        """
        chain: list = []
        # 1) active left tree
        try:
            left_tabs = self.query_one("#left_tabs", TabbedContent)
            wid = self._LEFT_TAB_TO_WIDGET.get(
                left_tabs.active, "#depot_tree",
            )
            chain.append(self.query_one(wid))
        except Exception:  # noqa: BLE001
            pass
        # 2) active right pane (table) — the whole point of the
        #    custom traversal.
        try:
            right_tabs = self.query_one("#right_tabs", TabbedContent)
            wid = self._RIGHT_TAB_TO_WIDGET.get(
                right_tabs.active, "#pending_table",
            )
            chain.append(self.query_one(wid))
        except Exception:  # noqa: BLE001
            pass
        # 3) Log panel — always visible (unless narrow + overlay,
        #    in which case our action_smart_tab will flip back to
        #    tree first; the next Tab from log to tree handles that
        #    direction because the chain still includes log).
        try:
            chain.append(self.query_one("#log_panel"))
        except Exception:  # noqa: BLE001
            pass
        return chain

    def on_resize(self, event) -> None:
        # Auto-enter / leave narrow mode whenever the terminal resizes
        # across the threshold.
        try:
            self.narrow_mode = event.size.width < NARROW_TERMINAL_WIDTH
        except Exception:  # noqa: BLE001
            pass

    # --- narrow-mode panel toggle ---------------------------------------

    def action_toggle_narrow_panels(self) -> None:
        """F3 / Ctrl+W — quick-toggle between the tree and the last
        panel page (narrow mode), or just focus the right pane (wide
        mode)."""
        if self.narrow_mode:
            self.narrow_page = narrow_nav.toggle_target(
                self.narrow_page, self._narrow_last_panel,
            )
        else:
            self._focus_active_right_pane()

    def action_narrow_back(self) -> None:
        """Backspace — in narrow mode, return to the tree page from any
        other page.

        No-op if the focused widget consumes Backspace (Input fields,
        File Viewer's own handler, etc.), since widget bindings beat
        app bindings.
        """
        if self.narrow_mode and self.narrow_page != "tree":
            self.narrow_page = "tree"

    def action_right_tab_next(self) -> None:
        """Ctrl+→ — step forward through the layout.

        In **narrow** mode this cycles the whole full-screen page set
        (tree → pending → history → submitted → log → tree), so every
        screen is reachable from one key. In **wide** mode it advances
        the right-pane TabbedContent by one tab (Pending → History →
        Submitted), since all panes are already visible.
        """
        if self.narrow_mode:
            self.narrow_page = narrow_nav.cycle_page(self.narrow_page, +1)
            return
        self._cycle_right_tab(+1)

    def action_right_tab_prev(self) -> None:
        """Ctrl+← — reverse of :meth:`action_right_tab_next`."""
        if self.narrow_mode:
            self.narrow_page = narrow_nav.cycle_page(self.narrow_page, -1)
            return
        self._cycle_right_tab(-1)

    def _cycle_right_tab(self, delta: int) -> None:
        # Wide-mode only: step the right-pane TabbedContent. (Narrow
        # mode handles Ctrl+←/→ via the page navigator above.)
        try:
            tabs = self.query_one("#right_tabs", TabbedContent)
        except Exception:  # noqa: BLE001
            return
        pane_ids = [p.id for p in tabs.query("TabPane")
                    if p.id is not None]
        if not pane_ids:
            return
        try:
            i = pane_ids.index(tabs.active)
        except ValueError:
            i = 0
        i = (i + delta) % len(pane_ids)
        tabs.active = pane_ids[i]
        self._focus_active_right_pane()
