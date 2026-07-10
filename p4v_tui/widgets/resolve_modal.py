"""Per-file Resolve picker for files left open after integrate / copy /
shelf-unshelve.

p4v's Resolve dialog walks one file at a time and offers Accept Yours /
Accept Theirs / Auto-merge / Skip plus a manual 3-way merge tool. The
TUI version covers the three non-manual options inline; for a true
3-way text edit the user is expected to launch their configured Open
With… editor on the file (or run ``p4 resolve`` from a shell).

Returns nothing — runs the resolve commands itself and refreshes the
caller via the ``done`` callback the App passes in.

State machine
-------------
On open, ``p4 resolve -n <target>`` is called to enumerate
unresolved files. For each file the user picks one of:

  * ``Auto``      → ``p4 resolve -am <file>``
  * ``Yours``     → ``p4 resolve -ay <file>``
  * ``Theirs``    → ``p4 resolve -at <file>``
  * ``Skip``      → leave it for later

The chosen actions are queued and run when the user presses "Run".
After execution the modal re-enumerates and shows what's left
(typically empty — but a non-trivial conflict can leave Auto unable
to merge, in which case the user can pick Yours / Theirs or skip
out and use an external tool).
"""
from __future__ import annotations

from typing import Optional

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import (
    Button, DataTable, Label, Select, Static,
)


_ACTION_LABELS = [
    ("Skip (leave for later)", "skip"),
    ("Accept Auto-merged",     "auto"),
    ("Accept Yours",           "yours"),
    ("Accept Theirs",          "theirs"),
]


class ResolveModal(ModalScreen[Optional[bool]]):
    """Returns True if at least one resolve ran, None on cancel."""

    DEFAULT_CSS = """
    ResolveModal { align: center middle; }
    ResolveModal > #dialog {
        width: 95%;
        height: 90%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    ResolveModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    ResolveModal #hint {
        color: $text-muted;
        padding: 0 1;
    }
    ResolveModal #files_table {
        height: 1fr;
        margin-top: 1;
    }
    ResolveModal #per_file_row {
        height: 3;
        align: left middle;
        padding: 0 1;
    }
    ResolveModal #buttons {
        height: 3;
        align: right middle;
        padding: 0 1;
    }
    ResolveModal Button { margin-left: 2; }
    ResolveModal #per_file_select { width: 35; }
    ResolveModal #bulk_select { width: 35; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", priority=True),
        Binding("ctrl+e", "merge_current", "3-way merge", priority=True),
        Binding("ctrl+t", "merge_external", "Merge tool", priority=True),
    ]

    def __init__(self, scope, p4_service) -> None:
        """``scope`` is either a depot/file path string (used as one
        ``p4 resolve -n <scope>`` arg) or a list of strings spliced
        directly into the resolve invocation (e.g.
        ``["-c", "12345"]`` to scope by changelist)."""
        super().__init__()
        if isinstance(scope, str):
            self._scope_args: list[str] = [scope]
            self._scope_label = scope
        else:
            self._scope_args = list(scope)
            self._scope_label = " ".join(self._scope_args) or "(all)"
        self._p4 = p4_service
        # depot_path → action_key (one of: "skip" / "auto" / "yours" / "theirs")
        self._choices: dict[str, str] = {}
        self._files: list[dict] = []  # rows from `p4 resolve -n`
        self._anything_ran = False

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(
                f" Resolve · {self._scope_label} ", id="title",
            )
            yield Static(
                "  Pick an action per file, then Run. Auto picks the "
                "non-conflicting merge; Yours/Theirs replace the file; "
                "Skip leaves it for later. Ctrl+E in-app 3-way merge · "
                "Ctrl+T external merge tool.",
                id="hint",
            )
            with Horizontal(id="per_file_row"):
                yield Label(" Selected file action:")
                yield Select(_ACTION_LABELS,
                             id="per_file_select",
                             allow_blank=False, value="skip")
                yield Label("  Apply to ALL:")
                yield Select(_ACTION_LABELS,
                             id="bulk_select",
                             allow_blank=False, value="skip")
            yield DataTable(id="files_table", cursor_type="row",
                            zebra_stripes=True)
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Refresh list", id="refresh")
                yield Button("Preview diff", id="preview_diff")
                yield Button("Run resolves", id="run",
                             variant="primary")

    def on_mount(self) -> None:
        table = self.query_one("#files_table", DataTable)
        table.add_columns("File", "Action")
        self._reload_unresolved()

    # --- enumeration ----------------------------------------------------

    @work(thread=True, group="resolve_enumerate", exclusive=True)
    def _reload_unresolved(self) -> None:
        try:
            rows = self._p4.run("resolve", "-n", *self._scope_args)
        except Exception:  # noqa: BLE001
            rows = []
        # `p4 resolve -n` returns dict rows for files needing resolve,
        # or strings like "no file(s) to resolve." when nothing pending.
        files = [r for r in rows if isinstance(r, dict)]
        self.app.call_from_thread(self._populate, files)

    def _populate(self, files: list[dict]) -> None:
        self._files = files
        # Default everyone to skip; users opt into actions.
        self._choices = {
            (f.get("clientFile") or f.get("fromFile") or
             f.get("toFile") or "?"): "skip"
            for f in files
        }
        try:
            table = self.query_one("#files_table", DataTable)
        except Exception:  # noqa: BLE001
            return
        table.clear()
        for f in files:
            client = (f.get("clientFile") or f.get("fromFile") or
                      f.get("toFile") or "?")
            table.add_row(client, "skip", key=client)
        if not files:
            self.app.notify(
                f"No unresolved files under {self._scope_label}.",
                timeout=4,
            )

    # --- per-file action selection -------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "per_file_select":
            self._set_selected_action(str(event.value))
        elif event.select.id == "bulk_select":
            value = str(event.value)
            for k in list(self._choices.keys()):
                self._choices[k] = value
            self._refresh_action_column()

    def _set_selected_action(self, action: str) -> None:
        try:
            table = self.query_one("#files_table", DataTable)
        except Exception:  # noqa: BLE001
            return
        if table.cursor_row is None or table.cursor_row < 0:
            return
        try:
            row_keys = list(table.rows.keys())
            if table.cursor_row >= len(row_keys):
                return
            key = str(row_keys[table.cursor_row].value)
        except Exception:  # noqa: BLE001
            return
        if key not in self._choices:
            return
        self._choices[key] = action
        self._refresh_action_column()

    def _refresh_action_column(self) -> None:
        try:
            table = self.query_one("#files_table", DataTable)
        except Exception:  # noqa: BLE001
            return
        for row_key in table.rows:
            key = str(row_key.value)
            try:
                table.update_cell(row_key, "Action", self._choices.get(key, "skip"))
            except Exception:  # noqa: BLE001
                pass

    # --- buttons --------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "cancel":
            self.dismiss(self._anything_ran or None)
        elif bid == "refresh":
            self._reload_unresolved()
        elif bid == "run":
            self._run_choices()
        elif bid == "preview_diff":
            self._preview_diff_for_current()

    def _preview_diff_for_current(self) -> None:
        """Open ``p4 resolve -o -t`` style preview for the row under
        the cursor.

        The preview is a yours-vs-theirs unified diff so the user can
        eyeball the conflict before committing to Auto / Yours /
        Theirs. We reuse :class:`FileViewerModal` (read-only, RichLog
        scroll, Esc closes) rather than spawning an external merge
        tool — same in-app contract as every other diff in the app.
        """
        if not self._files:
            self.app.notify("No files to preview.", timeout=3)
            return
        try:
            table = self.query_one("#files_table", DataTable)
            row_idx = table.cursor_row
        except Exception:  # noqa: BLE001
            row_idx = 0
        if row_idx is None or row_idx < 0 or row_idx >= len(self._files):
            self.app.notify("Move the cursor onto a file first.",
                            timeout=3)
            return
        f = self._files[row_idx]
        path = (f.get("clientFile") or f.get("fromFile")
                or f.get("toFile") or "?")
        self._spawn_diff_worker(path)

    def action_merge_current(self) -> None:
        """Hand the cursor row's file to the app's 3-way merge editor."""
        if not self._files:
            self.app.notify("No files to merge.", timeout=3)
            return
        try:
            table = self.query_one("#files_table", DataTable)
            row_idx = table.cursor_row
        except Exception:  # noqa: BLE001
            row_idx = 0
        if row_idx is None or row_idx < 0 or row_idx >= len(self._files):
            self.app.notify("Move the cursor onto a file first.", timeout=3)
            return
        f = self._files[row_idx]
        path = (f.get("clientFile") or f.get("fromFile")
                or f.get("toFile") or "?")
        # Defer to the app (like the Fast Search modal's tagged dict): the
        # merge round trip needs p4 calls + a nested modal the app owns.
        self.dismiss({"merge": path})

    def action_merge_external(self) -> None:
        """Hand the cursor row's file to the app's external merge tool.

        Mirrors :meth:`action_merge_current` but routes to the configured
        ``[merge_tool]`` (e.g. P4Merge) instead of the in-app editor. The
        app surfaces a hint if no merge tool is configured."""
        if not self._files:
            self.app.notify("No files to merge.", timeout=3)
            return
        try:
            table = self.query_one("#files_table", DataTable)
            row_idx = table.cursor_row
        except Exception:  # noqa: BLE001
            row_idx = 0
        if row_idx is None or row_idx < 0 or row_idx >= len(self._files):
            self.app.notify("Move the cursor onto a file first.", timeout=3)
            return
        f = self._files[row_idx]
        path = (f.get("clientFile") or f.get("fromFile")
                or f.get("toFile") or "?")
        self.dismiss({"merge_tool": path})

    @work(thread=True, group="resolve_diff_preview", exclusive=True)
    def _spawn_diff_worker(self, path: str) -> None:
        try:
            text = self._p4.run("diff", "-du", path)
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(
                self.app.notify,
                f"diff preview failed: {e}",
                severity="warning", timeout=4,
            )
            return
        body_parts: list[str] = []
        for chunk in text or []:
            if isinstance(chunk, str):
                body_parts.append(chunk)
            elif isinstance(chunk, dict):
                # p4 -ztag mode wraps the data; just stringify any
                # value-like field so we have something to show.
                body_parts.append(
                    str(chunk.get("data") or chunk),
                )
        body = "\n".join(body_parts) or "(no diff content)"
        from .file_viewer import FileViewerModal
        self.app.call_from_thread(
            self.app.push_screen,
            FileViewerModal(
                f"Resolve preview · {path}", body, filename=path,
            ),
        )

    def action_cancel(self) -> None:
        self.dismiss(self._anything_ran or None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(self._anything_ran or None)

    @work(thread=True, group="resolve_run")
    def _run_choices(self) -> None:
        # Group choices by action so we can issue batched p4 calls.
        buckets: dict[str, list[str]] = {
            "auto": [], "yours": [], "theirs": [],
        }
        for path, action in self._choices.items():
            if action in buckets:
                buckets[action].append(path)
        if not any(buckets.values()):
            self.app.call_from_thread(
                self.app.notify,
                "No actions queued — pick auto/yours/theirs on at "
                "least one file or Cancel.",
                severity="warning", timeout=5,
            )
            return

        flag = {"auto": "-am", "yours": "-ay", "theirs": "-at"}
        # Forward any scope-defining flags from the enumerate stage to
        # the action stage. ``-f`` is the load-bearing one — when the
        # modal was opened for "Re-resolve Previously Resolved Files"
        # the action commands MUST repeat ``-f`` or the server will
        # refuse with "no file(s) to resolve" because the files are
        # already at "resolved" state. ``-c <CL>`` also propagates so
        # the actions stay scoped to the original CL even when a path
        # arg would otherwise match a wider opened set.
        scope_flags: list[str] = []
        skip_next = False
        for tok in self._scope_args:
            if skip_next:
                scope_flags.append(tok)
                skip_next = False
                continue
            if tok in ("-f", "-as", "-ay", "-at", "-am", "-an"):
                scope_flags.append(tok)
            elif tok in ("-c",):
                scope_flags.append(tok)
                skip_next = True
        # ``-an`` / ``-as`` shouldn't ride along on action commands
        # (they're enumeration-only). Filter them out.
        scope_flags = [t for t in scope_flags if t not in ("-an", "-as")]

        summary: list[str] = []
        any_failed = False
        for action, files in buckets.items():
            if not files:
                continue
            try:
                self._p4.run(
                    "resolve", flag[action], *scope_flags, *files,
                )
                summary.append(f"{action}: {len(files)}")
                self._anything_ran = True
            except Exception as e:  # noqa: BLE001
                summary.append(f"{action} FAILED ({e})")
                any_failed = True

        msg = f"Resolve done — {' · '.join(summary)}"
        sev = "error" if any_failed else "information"
        self.app.call_from_thread(
            self.app.notify, msg, severity=sev, timeout=6,
        )
        # Re-enumerate so the user sees what (if anything) remains.
        self._reload_unresolved()
