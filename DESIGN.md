# p4v-tui — Design Notes

## Scope

A Textual-based TUI for Perforce (Helix) targeting users on slow /
unstable networks where p4v's monolithic operations stall the UI.

**Primary value:** *resilience* — auto-reconnect, chunked + resumable
long ops, non-blocking interactive commands.
**Secondary:** feature breadth covering p4v's daily-use surface, and
**remote usability** — the same slow/unstable link is usually a *small*
remote screen too (iPhone Blink, a thin tmux split), so a single-page
narrow navigator and a perceived-performance ("feel") layer make that
viewport usable and the lag legible. See
`docs/narrow-terminal-scenario.md` and
`docs/perceived-performance-scenario.md`.

Operates against an existing `p4` install. The Perforce binding is
**pluggable**: P4Python (`pip install p4python`) is the preferred
backend, the `p4` CLI is a drop-in fallback that engages
automatically when the P4Python wheel can't be installed (older
Linux, non-x86 sidecars, SSH-only servers without a compiler). Force
either with `P4V_BACKEND={python,cli}`. See *Backends* below and
`docs/p4-cli-fallback-scenario.md` for the contract. No background
service, no plugins; runs in any reasonably modern terminal including
iPhone Blink: below `NARROW_TERMINAL_WIDTH = 100` cells the layout
collapses to the single-page navigator (one full-screen page at a
time, cycled with `Tab`), and below `SHORT_TERMINAL_HEIGHT = 45` rows
the bottom Log panel auto-collapses so a short viewport keeps its
tree / tables. Both width and the layout choice are overridable
(`[narrow] layout` / `Ctrl+Shift+N`).

---

## Architecture

```
                ┌─────────────────────────────────┐
                │         P4VApp (Textual)        │
                │  ┌────────┐  ┌────────────────┐ │
                │  │ Screen │  │ Modals / popups│ │
                │  └────┬───┘  └────────────────┘ │
                │       │                          │
                │       ▼                          │
                │  ┌──────────┐  ┌────────────┐   │
                │  │JobRunner │──│   CmdLog   │◀──┼── F2 monitor
                │  │ (1 worker│  │(ring + tree│   │
                │  │  thread) │  │ parents)   │   │
                │  └─────┬────┘  └──────┬─────┘   │
                │        │              │         │
                │        ▼              ▼         │
                │  ┌──────────────────────────┐   │
                │  │     P4Service (lock)     │   │
                │  │  _run_resilient: retry   │   │
                │  │  + reconnect + lock      │   │
                │  │  release between attempts│   │
                │  └────────────┬─────────────┘   │
                └─────────────┬─┴──────────────────┘
                              ▼
                     P4Python  ↔  p4d
```

* `P4Service` — every p4 invocation goes through `_run_resilient`:
  reconnect-on-drop, exponential backoff (1s→30s cap), lock released
  during sleep so other queued commands interleave. Optional `cmd_log`
  hook records each call. `P4Service` is a thin façade over a
  pluggable `_Backend` (see *Backends*); the resilient runner, lock,
  and `cmd_log` wiring live at the façade so both backends inherit
  the same behaviour without re-implementing the retry loop.
* `JobRunner` — single worker, heap-priority queue. `PRIORITY_INTERACTIVE`
  jumps in front of `PRIORITY_CHUNKED` between chunks. Sets a
  thread-local "current job" id around each chunk so the commands a
  chunk fires are recorded as children of the job in `CmdLog`.
* `CmdLog` — in-memory ring of `CmdEntry` records (id, parent_id, name,
  state, timing, optional done/total/start for ETA). Listeners notified
  on every begin/end/update.
* `narrow_nav` (pure) — the decision core for the **single-page narrow
  navigator** (phone / thin-tmux). No Textual, no I/O, so the sequencing
  is unit-tested in isolation; `P4VApp` does the widget show/hide wiring.
  Owns: the page cycle + `effective_pages` (trim disabled/empty pages),
  number-key `jump_target_by_index`, the layout pin
  (`resolve_narrow_mode` auto/narrow/wide), and the **width-adaptive**
  `render_breadcrumb` / `render_footer_hints` (compact when a phone in
  portrait can't fit the full strip) + the responsive table-column
  profiles (`TABLE_FIELDS` / `select_cells`).
* `perf_feel` (pure) — the decision core for the **perceived-performance
  ("체감 성능") feel layer**: `should_show_activity` (≥150 ms threshold so
  fast ops never flicker) + escalating `activity_label`, and
  `next_refresh_interval` (back the pending auto-refresh off on a slow
  link). `P4VApp` owns the timers, the ConnectionBar activity suffix,
  and the per-`@work`-load activity registry. See
  `docs/perceived-performance-scenario.md`.
* **Tab interception** — Textual's `Screen` binds `tab`/`shift+tab` to
  `app.focus_next`, which shadows any app-level `tab` binding; the narrow
  page cycle therefore lives in `P4VApp.on_key` (guarded to narrow mode,
  base screen, non-`Input` focus) rather than a Binding that never fires.

### Backends

Two interchangeable implementations of the `_Backend` interface live
inside `p4v_tui/p4client.py`:

* **`_PythonBackend`** — wraps `P4.P4()` from the P4Python C
  extension. One persistent connection re-used across calls
  (~2–10 ms per local-server call), `run()` returns the same
  dict-list shape it always did. Streaming grep uses
  `P4.OutputHandler`. Translates `P4.P4Exception` into the local
  `P4Exception` so callers never see a P4Python-typed exception.
* **`_CLIBackend`** — spawns a short-lived `p4` subprocess per call.
  Tagged output goes through `p4 -G ...` (Python marshal-2),
  parsed back into the same dict shape the Python backend
  produces. Form writes (`change -i` / `client -i`) drop `-G` and
  pipe a text form on stdin; the response is parsed as lines so
  `["Change 12345 created."]` still matches the existing
  `create_changelist()` parser. Streaming grep reads marshalled
  rows off `Popen.stdout` for the same "first match appears in
  ms" UX P4Python gives. Connection params are snapshotted from
  `p4 set -q` at startup so a later env mutation can't change
  target mid-session. Subprocess spawn is portable to Windows via
  a `CREATE_NO_WINDOW` flag that suppresses the brief console-
  flash the GUI-subsystem `p4.exe` would otherwise produce. Every
  `_invoke()` call carries a per-call timeout — default 1800 s, env-
  overridable via `P4V_CLI_TIMEOUT`, per-call-overridable via the
  `timeout` kwarg — so a network partition or hung p4d surfaces as
  a `P4Exception` instead of blocking the worker forever. (The
  resilient runner only retries on raised exceptions, not on hangs,
  so the timeout is the actual liveness guarantee.) `grep_stream`
  has no `_invoke`-mediated timeout because it's already
  cancellable via the `cancelled()` callback.

  **Concurrency + read cache** (closest practical approximation of
  "connection reuse" given that the `p4` binary has no REPL mode —
  see `docs/p4-cli-fallback-scenario.md` §16):
  - `max_concurrent_calls` = `P4V_PY_CONCURRENCY` / `P4V_CLI_CONCURRENCY`
    (both default 4). P4Service splits its old single-`Lock` into a
    short `_connect_lock` (mutex around connect/disconnect state) plus a
    `_call_sem = BoundedSemaphore(max_concurrent_calls)`. Both backends
    achieve concurrency with *independent* connections rather than a
    shared one (a single `P4.P4()` socket / `p4` invocation isn't
    thread-safe): the Python backend leases from a pool of N `P4.P4()`
    connections (`_PyConn` / `_acquire` / `_release`), the CLI backend
    forks N subprocesses. So UI fan-outs (tree expand → dirs+files+fstat)
    run concurrently, and — critically — one slow command (large `print`,
    deep `filelog`, laggy sync chunk) occupies a single permit +
    connection instead of serialising every other p4 call behind it,
    keeping the UI responsive. (P4Python releases the GIL during socket
    I/O, so the event-loop thread was never the bottleneck — the old
    single-permit serialisation was.)
  - Idempotent reads (`info`, `client -o <name>`) are cached in
    `_CLIBackend._read_cache` for `P4V_CLI_READ_CACHE_TTL` seconds
    (default 30). Hits skip the subprocess entirely; `save_form`
    flushes the whole cache so a spec write is visible on the next
    read. `P4V_CLI_READ_CACHE_TTL=0` disables the cache.

Backend selection (highest precedence first):

1. `P4V_BACKEND` env var — `python` | `cli` | unset.
2. `import P4` succeeds → `_PythonBackend`.
3. `shutil.which("p4")` finds the binary → `_CLIBackend`.
4. Neither → `P4SetupError` → `p4v.py` prints a Korean install hint
   covering both options and exits 1.

The active backend's identity is recorded in the LogPanel at startup
("Backend: P4Python (api 99)" / "Backend: p4 CLI 2024.1 (LINUX26AARCH64)")
so bug reports always carry which path was taken. Behaviour parity is
covered by the live-server tests under `tests/test_p4client_live.py`
(parametrised over both backends) and the form-CRUD probe under
`tests/test_p4client_live_crud.py` (gated by `PYTEST_ALLOW_WRITES=1`).

---

## p4v Feature Coverage

Coverage is assessed against p4v's full menu surface (file menu, the
context menus on Workspace / Depot / Pending / Submitted, plus the
top-level views and tools). Each entry is one of:

- ✅ **Implemented** — fully wired, comparable to p4v's behavior
- 🟡 **Partial** — present but reduced scope (e.g. unified diff but no
  side-by-side editor; integrate but no resolve UI)
- ❌ **Not implemented** — no equivalent in the TUI today
- ⏭ **Out of scope** — won't ship in TUI form (e.g. visual print
  preview, native installer dialogs)

Where the TUI does something p4v can't, it's marked **➕ TUI-only**.

### File operations (Workspace tree)

| p4v action | TUI | Where |
|---|---|---|
| Get Latest Revision | ✅ | `Ctrl+Shift+G` · `s` · `ㄴ` · context menu |
| Get Latest, chunked + resumable | ➕ TUI-only | `g` · `ㅎ` · context menu |
| Force Get Latest (chunked) | ✅ | context menu |
| Get Revisions for Files in CL | ✅ | Submitted CL context menu |
| Get **Previous** Revisions for Files in CL | ✅ | Submitted CL context menu |
| Check Out (open for edit) | ✅ | `Ctrl+E` · `e` · `ㄷ` · context menu |
| Mark for Add | ✅ | context menu |
| Mark for Delete | ✅ | context menu (with confirm) |
| Revert Files | ✅ | `Ctrl+R` · `r` · `ㄱ` · context menu (confirm) |
| Revert Files (chunked) | ➕ TUI-only | context menu |
| Lock / Unlock | ✅ | `Ctrl+L` / `Ctrl+U` · context menu |
| Reconcile Offline Work | ✅ interactive per-file picker (dry-run preview) + chunked | context menu — `reconcile -n` preview → check/uncheck files → chunked; all-checked == old all-or-nothing |
| Clean | ✅ interactive per-file picker + chunked | context menu — `clean -n` preview → check/uncheck → confirm (lists delete count) |
| Move / Rename | ✅ | context menu (Browse picker for new base) · `F2` quick in-place rename + auto-submit |
| Diff Against Have | ✅ | context menu (`#have` vs working copy) |
| Merge / Integrate Files | ✅ | context menu — auto-prompts Resolve picker after |
| Copy Files | ✅ | context menu — auto-prompts Resolve picker after |
| Branch Files (`p4 populate`) | ✅ branch-mapping picker + dry-run preview | context menu — `p4 branches` picker (or manual src/tgt) → `populate -n` preview modal → confirm → submit |
| Resolve Files | ✅ | context menu — Auto / Yours / Theirs / Skip per file |
| Shelve / Unshelve | ✅ | Pending CL menu (full shelf cycle) |
| Update / Delete Shelved Files | ✅ | Pending CL menu |
| Diff Two Files | ✅ | `Ctrl+Shift+D` Arbitrary Diff — any two paths (single resolved row earlier in matrix; duplicates removed) |
| Diff Folders | ✅ | `Ctrl+Shift+D` with `<a>/...` vs `<b>/...` — picker over differing pairs |
| Annotate / Blame | ✅ | context menu (`p4 annotate -i -c`) |
| Time-lapse View | ✅ | context menu — `,`/`.` walk revisions (older/newer); ←/→ left for body horizontal scroll |
| Revision Graph | ✅ | context menu — text-mode integration tree (`p4 filelog -i -l`; design + walk-through in `docs/revision-graph-scenario.md`) |
| File Properties (filetype, attributes) | ✅ | context menu — view + edit |
| Undo Changes (`p4 undo`) | ✅ | context menu — file or `@CL` (Submitted) |
| Open With / external editor | ✅ | context menu picker driven by `[[external_editor]]` |

### File operations (Depot tree)

| p4v action | TUI | Where |
|---|---|---|
| Browse depot namespace from `//` | ✅ | lazy load via `p4 depots → dirs → files` |
| Per-node context menu | ✅ | `m` · `Shift+F10` · `ㅡ` |
| Get Latest / Get Latest (chunked) | ✅ | context menu |
| File History / Folder History | ✅ | `Ctrl+T` and on cursor highlight |
| View File | ✅ | `Enter` on a text-file leaf — pygments-based syntax highlight when filename extension is recognised, plain text for unknown / oversized files |
| View Image / Binary file | ✅ | `Enter` on an image leaf renders half-block ANSI art (`image_preview.py`, Pillow); non-image binary shows a bounded hex window instead of "cannot display" |
| Find File | ✅ | `Ctrl+Shift+F` |
| Dim non-mapped paths in Depot tree | ➕ TUI-only | client View parsed once; paths the workspace doesn't include rendered with Rich `dim` style |
| Loading spinner on tree expand | ➕ TUI-only | brail-spinner glyph appended to the parent label while ``_fetch_node_data`` runs — shared 120 ms timer |
| Show In… (file manager) | ✅ | context menu |
| Open Command Window Here | ✅ | context menu |
| Copy Depot Path | ✅ | context menu |
| Copy Swarm URL (file) | ➕ TUI-only | context menu (uses `[swarm] base_url`) |
| Copy Swarm Review URL (CL) | ➕ TUI-only | Pending / Submitted CL context menu — `{base}/changes/{N}` |
| Open Swarm in browser (CL) | ➕ TUI-only | Pending / Submitted CL context menu — `webbrowser.open_new_tab` |
| Rename / Move… | ✅ | context menu (Browse picker) · `F2` quick in-place rename + auto-submit |
| Mark for Delete | ✅ | context menu (with confirm) — same `p4 delete` path as Workspace tree; folder nodes recurse via `/...` |
| Refresh node / root | ✅ | `F5` · context menu — every previously-expanded subtree + the cursor are restored after the reload, so refresh never collapses the view |

### Pending Changelists

| p4v action | TUI | Where |
|---|---|---|
| Submit | ✅ resilient | `Ctrl+S` · context menu · pending-detail Submit button |
| View Pending CL details | ✅ | row highlight populates description + file list |
| Edit Pending CL description | ✅ | pending-detail TextArea |
| Save edits without submit | ✅ | pending-detail Save button |
| Promote default → numbered on Save/Submit | ✅ | automatic if user typed a description |
| Toggle which files go in this submit | ✅ | pending-detail SelectionList checkboxes |
| Move Files to Another CL | ✅ | context menu (default / existing / new) |
| New Pending CL | ✅ | `Ctrl+N` · context menu |
| Revert Files in CL | ✅ | pending-detail · context menu (confirm) |
| Revert Unchanged Files | ✅ | context menu |
| Refresh | ✅ | `F5` · context menu |
| Submit & Resolve | ✅ | context menu — opens Resolve picker, then Submit |
| Shelve / Unshelve / Update / Delete Shelf | ✅ | context menu — full shelf cycle |
| Re-resolve Previously Resolved Files | ✅ | reopens Resolve picker scoped via `-f -c <CL>`; action commands also carry `-f` so the re-run actually retriggers resolve |
| Delete (empty) Pending CL | ✅ | context menu (`p4 change -d` with confirm) |
| Job association (`p4 fix`) | ⏭ out of scope | 2026-07 server survey: 7 jobs total, all closed, none touched since 2025-02 (a brief task-integration experiment, since abandoned) — no live demand; `p4 fix -c <CL> <job>` on the CLI |
| Unsaved-edits guard on Cancel | ➕ TUI-only | three-button Save / Discard / Continue modal |
| List **other workspaces'** pending CLs of the same user | ➕ TUI-only | `p4 changes -s pending -u <me>` — Pending table groups local first, then remote workspaces; remote rows rendered dim/italic with `↗ workspace-name` yellow marker in the new Workspace column |
| Local vs remote action gating | ➕ TUI-only | remote rows: context menu drops Submit / Revert / Shelve / Move / Re-resolve / Diff-against-have; Ctrl+S refuses with a toast; Enter opens read-only FileViewerModal (`p4 describe`) instead of the editable PendingDetailModal; row-highlight detail pane falls back to `p4 describe` since `p4 opened -c <N>` is current-client scoped |
| Workspace column truncate | ➕ TUI-only | long workspace names clipped to `XXXXXX..` (6 + `..`) so the column doesn't drag the table out; full name kept on `_pending_client_by_change` for menu titles / toasts |
| Popup placement avoids trigger row | ➕ TUI-only | PendingDetailModal / FileViewerModal hug `place-top` or `place-bottom` (height 55 %) based on the highlighted row's screen position — the row that opened the popup stays visible |

### Submitted Changelists

| p4v action | TUI | Where |
|---|---|---|
| View Submitted CL | ✅ | row highlight populates description + files |
| Edit Submitted CL Description (`p4 change -f`) | ✅ | context menu (admin) |
| Get Revisions for Files in CL | ✅ | context menu (confirm) |
| Get Previous Revisions for Files in CL | ✅ | context menu |
| Diff Files Against Previous Revisions | ✅ unified + side-by-side | `Ctrl+D` (unified) · context menu (side-by-side) |
| Refresh All / Refresh One | ✅ | `F5` · context menu |
| Tag with Label | ✅ | context menu — picker over `p4 labels` |
| Show Files in Tree | ✅ | context menu — auto-navigates Workspace or Depot tree |
| Diff Submitted CL Against Another CL | ✅ | via Arbitrary Diff (`Ctrl+Shift+D` with `//...@CL_A` vs `//...@CL_B`) |
| Undo whole Changelist (`p4 undo @CL`) | ✅ | context menu — opens reverse change in default CL |
| Enter / double-click → read-only detail viewer | ➕ TUI-only | `RowSelected` on `submitted_table` opens FileViewerModal with `p4 describe -s` output (header, description, affected files); Esc / Backspace / q closes |

### History panel

| p4v action | TUI | Where |
|---|---|---|
| File history (`p4 filelog`) | ✅ | auto-loads on file-leaf hover; `Ctrl+T` |
| Folder history (`p4 changes -L`) | ✅ | auto-loads on directory hover; `Ctrl+T` |
| Per-target column schema swap | ➕ TUI-only | file mode uses `Rev / Change / Action / Date / User / Description`; folder mode drops `Rev` + `Action` (per-CL data has no per-file values) — `DataTable.clear(columns=True)` rebuilds only when schema actually changes |
| Time-lapse View | ✅ | context menu — keyboard-driven revision walker |
| Revision Graph | ✅ | context menu — text-mode integration tree (see `docs/revision-graph-scenario.md`) |

### Search / navigation

| p4v action | TUI | Where |
|---|---|---|
| Find File | ✅ depot-wide | `Ctrl+Shift+F` (`p4 files -m 100`) |
| Auto-navigate tree to a Find result | ✅ | picker close → tree walks to file (Workspace if mapped, else Depot) |
| Path/text filter on tree | ✅ | `/` opens floating filter input — live hide non-matches, auto-expand parents |
| Job search | ⏭ out of scope | no Jobs view (declined 2026-07 — see Pending table's `p4 fix` row); `p4 jobs -e <expr>` on the CLI |
| Mirror cursor between Depot ↔ Workspace on tab switch | ➕ TUI-only | uses `p4 where` to translate, falls back to closest ancestor |
| Cycle focus through panes | ✅ | `F6` / `Shift+F6` |
| Narrow-terminal layout (auto < 100 cells) | ➕ TUI-only | **Single full-screen page navigator** (`narrow_nav.py`): one of `tree` / `pending` / `history` / `submitted` / `log` fills the viewport. `Tab` / `Shift+Tab` cycle the whole page set (every screen reachable from one key, Log included) — `Tab` is the reliable phone driver since iPhone Blink & most mobile terminals send Tab but **not** the `Ctrl+Arrow` escape sequences (`Ctrl+→`/`Ctrl+←` are kept only as a desktop alias). `F3` / `Ctrl+W` quick-toggle tree ⇄ last panel page; `Backspace` returns to tree. On every non-`log` page the Log panel **and** the detail pane (+ both splitters) are hidden so the tree / CL table gets the full height — the old mode docked a fixed ~10-row Log strip under the tree and squeezed it to 2-3 rows on a phone. The `log` page collapses `#main` and gives the Log panel `1fr`. Focus tracking: `on_descendant_focus` + `action_smart_tab` keep `narrow_page` in sync with whatever gains focus, so a Tab/click never lands on an off-screen widget. Full design + smoke checks in `docs/narrow-terminal-scenario.md`. |
| Fast Search — token-AND loose fallback | ➕ TUI-only | `foo bar` matches both `//x/foo_bar` and `//x/foo/bar/baz`; ranks by leaf hits then recency |
| Fast Search — Levenshtein "did you mean…" | ➕ TUI-only | when strict + loose both return 0; Enter on a suggestion rewrites the Input |
| Fast Search — `?<query>` content grep | ➕ TUI-only | `p4 grep` mode; first matching line + line number rendered as a second row under the path (inline diff style) |
| Fast Search — `cl:<query>` description search | ➕ TUI-only | local `changes` table; cold-cache seed via `p4 changes -m 500 -l` on first hit |
| Fast Search — `@user:` / `type:` / `/regex/` filters | ➕ TUI-only | parsed out of the query string, AND-applied at SQL stage |
| Fast Search — `nl:` natural-language | ➕ TUI-only | rule-based intent parser (time / user / CL keywords in 한/영) |
| Fast Search — result cap toggle | ➕ TUI-only | `Ctrl+Shift+L` cycles 200 / 2 K / unlimited |
| Fast Search — match minimap | ➕ TUI-only | 40-cell horizontal dot bar in preview status line — `•` = chunk with matches, `·` = clean |
| Fast Search — query history | ➕ TUI-only | `Ctrl+P` / `Ctrl+N` walks the most recent 20 queries; App-shared across modal opens |
| Fast Search — Search In This Folder… | ➕ TUI-only | tree context menu pre-seeds the Input with the cursor path |

### Connection / profiles

| p4v action | TUI | Where |
|---|---|---|
| Open Connection | ✅ | startup picker (multi-`[[profile]]` TOML) |
| Recent connections | 🟡 implicit | `[[profile]]` list serves the same purpose |
| Edit / Add / Remove Connection (GUI) | ✅ | Preferences (`Ctrl+,`) → Profiles tab — add/edit/delete `[[profile]]` entries via dialog; persisted to TOML |
| Login / Logout / Set Password | ⏭ out of scope | use `p4 login` / `p4 logout` / `p4 passwd` outside the TUI; intentionally not shipping |
| Tickets management | ⏭ out of scope | same — handled by `p4` CLI |
| SSO / Helix Authentication Service | 🟡 inherited from `p4` env | no in-app prompt; user authenticates outside |
| Multi-server profile picker | ✅ | `widgets/profile_picker.py` |

### Admin / metadata views (p4v top-level)

| p4v view | TUI |
|---|---|
| Workspaces (manage) | ⏭ out of scope |
| Branch Mappings (manage / editor) | ⏭ out of scope |
| Labels (list / editor) | 🟡 list+pick only | LabelPickerModal for "Tag with Label"; full editor ⏭ out of scope |
| Streams (list / Stream Graph) | ⏭ out of scope |
| Jobs (list / spec / link to CL) | ⏭ out of scope |
| Users / Groups / Permissions | ⏭ out of scope |
| Triggers / server admin | ⏭ out of scope |
| Custom Tools menu | 🟡 | `[[external_editor]]` covers Open With…; no general "run X on selection" |
| Preferences GUI | ✅ | `Ctrl+,` — in-app TOML editor for connection / swarm / chunking |

The admin / metadata editors above are intentionally not shipping.
This TUI stays focused on the working developer's daily loop;
defining workspaces, branch mappings, streams, labels, jobs, users,
groups, and triggers is fundamentally an admin / spec-editing
surface and is better served by the existing `p4` CLI (`p4 client`,
`p4 branch`, `p4 stream`, `p4 label`, `p4 user`, `p4 group`,
`p4 triggers`) where the spec format is already canonical.

### Diff / merge / resolve

| p4v action | TUI |
|---|---|
| Diff Against Have | ✅ | workspace tree context menu — `#have` vs working copy |
| Diff Two Files | ✅ | `Ctrl+Shift+D` Arbitrary Diff — any two paths |
| Diff Two Revisions | ✅ | `Ctrl+Shift+D` with `<file>#A` vs `<file>#B` |
| Diff Two Folders | ✅ | `Ctrl+Shift+D` with `<a>/...` vs `<b>/...` — picker over differing pairs |
| Diff in Submitted CL (unified per CL) | ✅ `Ctrl+D` |
| Side-by-side diff viewer | ✅ | Submitted CL menu + reused for every Arbitrary Diff result |
| Diff Two CLs | ✅ | `Ctrl+Shift+D` with `//...@A` vs `//...@B` |
| Resolve (auto / interactive merge tool) | ✅ Auto / Yours / Theirs / Skip **+ in-app 3-way merge** | context menu; `Ctrl+E` opens the hunk-by-hunk merge editor (`merge3` + `MergeEditorModal`) |
| Merge tool integration (P4Merge) | ✅ external 3-way launch | Resolve modal `Ctrl+T` launches `[merge_tool]` (e.g. P4Merge) with base/theirs/yours/merge temp files, blocks, reads the merged result back; complements the in-app `Ctrl+E` editor |

### Resilience features (no direct p4v counterpart)

| Feature | TUI | Notes |
|---|---|---|
| Auto-reconnect with backoff (1s → 30s) | ➕ | every `P4Service.run()` |
| Lock release between retry sleeps | ➕ | other commands interleave during reconnect |
| Chunked + resumable sync | ➕ | per-file completion in `~/.p4v-tui/sync-state/{hash}.json` |
| Pending-jobs picker on next launch | ➕ | resume / discard interrupted jobs individually |
| Chunked revert / reconcile / clean / force-sync | ➕ | one chunk at a time, interactive priority interleaves |
| Resilient submit with lost-ack idempotency | ➕ | on "no such pending CL" verifies via `p4 changes` |
| Configurable chunking strategy | ➕ | `[chunking]` TOML: count / size / single / subdir + per-job overrides |
| Strategy displayed in queue toast | ➕ | "Queued: Sync (≤ 50 MB per chunk)" |
| Strategy persisted in resume state (v3) | ➕ | resumed job uses the same chunking |
| Command Monitor with parent/child tree | ➕ | `F2`; jobs show their child commands + ETA |
| Log panel (scrollable tail of p4 + jobs, timestamped) | ➕ | bottom-anchored, scrollback up to CmdLog capacity, 1s tick + listener, follow-tail auto-engages at the bottom; replaces the old single-line status bar |
| Log panel — click + Enter detail viewer | ➕ | clicked / ↑↓-navigated entries highlight in reverse; Enter opens LogEntryViewerModal (FileViewerModal subclass) with ±8 surrounding entries + full traceback / error details on the focused row. The popup hugs the top of the screen (`place-top`: 55% height, center top) so the LogPanel at the bottom of the layout stays visible behind it — matches the "popup must not cover its trigger" rule the Pending / Submitted row pop-ups already obey. Inside the popup ↑/↓ (and j/k, ㅏ/ㅓ) walk to the previous/next entry — PgUp/PgDn scroll the body for long tracebacks; Esc closes |
| Exception routing to LogPanel | ➕ | `App._handle_exception` overridden to record summary + full traceback into CmdLog (rendered as `✗`) and persist the traceback to `~/.p4v-tui/last-error.log` instead of dumping Textual's fatal-exit traceback to the terminal |
| Macros (`[[macro]]` TOML) | ➕ | Ctrl+Shift+M picker; step kinds `p4` / `sync` / `notify`; thread worker fail-fast with toast on first error |
| Pending Changelists auto-refresh | ➕ | 30s default (`auto_refresh_pending_seconds` in state.json); cursor preserved across reloads. **Adaptive cadence** (`perf_feel.next_refresh_interval`): a self-rescheduling `set_timer` backs the interval off on a slow link (scaled by recent pending-load latency, capped 4× base, never *faster* than configured) so the background refresh doesn't contend with foreground calls |
| In-flight activity indicator | ➕ | spinner + label appended inline to the ConnectionBar while an interactive `@work` load runs (pending / submitted / history / file-action); latency-adaptive — hidden < 150 ms (no flicker), escalates past 1 s / 8 s. No extra layout row: activity text is a suffix of the existing Server/User line so the screen never shifts. Answers "is it working or hung?" on a laggy link, esp. in narrow mode where the Log page isn't visible |
| Reconnect state surfaced in ConnectionBar | ➕ | service-level `_on_retry`/`_on_recover` hooks on `P4Service` (default None, parity-safe); during a mid-command reconnect the bar shows `⟳ Reconnecting… (attempt N/max)`, restored on recovery — a stall the resilient runner is working through *reads* as "working on it" |
| Cancellation on quit (no-corrupt teardown) | ➕ | already-running chunk finishes; queued chunks cancel |

### TUI conveniences (no direct p4v counterpart)

| Feature | TUI |
|---|---|
| File viewer (5 MB cap, chunked render) | ➕ Enter on text leaf — opens the **right ~75%** of the screen so the tree behind it stays visible. Diff / Print Preview / Get Revision reuse the same modal in its wide 95% form. Every rendered line carries a dim `<n>` prefix (auto-widthed to the largest line number, min 3 chars) so the user can reference specific positions; press `n` / `ㅜ` to toggle the prefix off (e.g., when copy-pasting the body). LogEntryViewerModal opts out by default (log entries already have their own row index) but the same `n` key still works for ad-hoc toggling. The footer hint reflects the current ON / OFF state so the toggle is discoverable. |
| Quitting modal (instant feedback on Q / Ctrl+Q) | ➕ |
| Hangul IME aliases for every single-letter shortcut | ➕ |
| CJK display-cell-aware truncation in tables | ➕ |
| Horizontal scroll on Pending / Submitted / History tables | ➕ Shift+←/→ + mouse wheel |
| Pane resize via `[` / `]` (left pane) | ➕ keyboard |
| Pane resize via mouse drag on splitter handles | ➕ all three boundaries: left/right · tables/detail · main/log |
| Persisted pane sizes across launches | ➕ `~/.p4v-tui/state.json` keys: `left_pane_width`, `detail_pane_height`, `log_panel_height` — re-applied to live widgets in `on_mount` so they actually land |
| Persisted active-tab state across launches | ➕ `~/.p4v-tui/state.json` |
| Persisted focused panel across launches | ➕ `focused_widget` key in `state.json`; 1Hz poll captures focus changes on the main-layout whitelist (trees, right-pane tables, log panel) and `_restore_ui_state` refocuses after tabs settle |
| Persisted detail-pane file sort across launches | ➕ `detail_files_sort` key in `state.json`; chosen via `Shift+M` → Sort Files By on the Pending tab |
| Fast Search (`Ctrl+F`) — typing-as-you-go filename | ➕ local SQLite index + live preview + match highlight; off-UI-thread query, IME-friendly debounce |
| Tree clipboard (`Ctrl+C` / `Ctrl+X` / `Ctrl+V`) | ➕ p4 copy / move into a fresh CL, auto-submit via ResilientSubmitJob |
| Tree multi-select + bulk ops (`Space` / `Esc`) | ➕ marked set drives one multi-file `p4` call: edit/revert/add into a single numbered CL (WorkspaceTree), Get-Latest / Mark-for-Delete (DepotTree) |
| Pre-submit guards | ➕ `submit_guards.py` — unresolved / oversized-file / empty-CL warnings injected into the submit confirm |
| Jira issue linkage at submit | ➕ `jira.py` + `[jira]` config — surfaces / warns the description's issue key + browse URL; description is the link (no live API) |
| Active backend in the title bar | ➕ Header `sub_title` = P4Python / p4 CLI |
| Go-to-path (`Ctrl+G`) | ➕ `path_nav.py` — paste a depot / local / virtual path → tree navigates |
| Immutable permalink (`//@p/<id>`, `Alt+C`) | ➕ `permalink.py` — stable handle that follows move/rename history to the current path when pasted into Go-to-path |
| Bookmarks (`Ctrl+B` / `Ctrl+Shift+B`) | ➕ `bookmarks.py` — permalink-backed, so a bookmark survives the path being moved |
| In-app 3-way merge editor (`Ctrl+E`) | ➕ `merge3.py` + `MergeEditorModal` — per-hunk Yours/Theirs/Base/Both over `p4 resolve -am` markers |
| Partial shelve | ➕ file-selection picker before `p4 shelve -c` (all-selected == old shelve-everything) |
| Fast Search row actions | ➕ `d` diff-vs-have / `g` get-latest on the highlighted hit |
| Command palette disabled | ➕ `ENABLE_COMMAND_PALETTE = False` — frees Ctrl+P for Fast Search history |
| Get Revision dialog (multi-target, by CL / Label / Date / Rev) | ➕ p4v "Get Revision…" port — Force / Safe Update / files-in-CL / remove-not-in-label options |
| Cross-workspace Pending Changelists panel | ➕ `_pending_client_by_change` tracks owner workspace; `_render_pending` rich.text dim-italic + `↗` marker for remote rows; `_is_remote_pending` / `_remote_workspace_note` helpers; `_show_remote_pending_view` opens read-only `p4 describe` view |
| Friendly missing-dependency message at startup | ➕ `p4v.py` lazy-imports `P4VApp` inside `main()`, catches `ModuleNotFoundError`, prints Korean install hint (`pip install p4python` / `textual`) + extra P4Python wheel/compiler note; exits 1 instead of dumping a traceback |
| Single-page narrow navigator (phone / thin tmux) | ➕ below `NARROW_TERMINAL_WIDTH = 100` cells one full-screen "page" at a time, cycle `tree → pending → history → submitted → log`. `Tab`/`Shift+Tab` cycle (phone-reliable; intercepted in `on_key` — the app `tab` Binding is shadowed by the Screen's `focus_next`), bare `1`-`9` jump to a page, `F3`/`Ctrl+W` quick-toggle tree⇄last-panel, `Backspace` home. `narrow_nav` pure core + `tests/test_e2e_narrow.py` |
| Narrow page breadcrumb + page-aware footer (width-adaptive) | ➕ numbered breadcrumb (`1 tree · 2 pending · …`, the digit IS the jump key) + a curated per-page key-hint footer replacing Textual's full one. Both **compact on a phone in portrait** rather than clipping at the edge — breadcrumb collapses non-current chips to bare numbers, footer drops least-important hints by priority (a real iPhone-Blink finding) |
| Responsive table columns in narrow mode | ➕ `TABLE_FIELDS` profiles trim Pending/Submitted to `Change · Description` (History → `Rev · Action · Description`) so the Description fits 80 cells; rebuilt lazily + re-rendered from cached rows on a layout flip. Column 0 stays the plain CL/rev (cursor-restore invariant); a remote CL's `↗` marker moves to the Description cell |
| Trim / pin the narrow layout (`[narrow]` config) | ➕ `disabled_pages` / `skip_empty` drop pages from the cycle; `layout = auto\|narrow\|wide` pins narrow vs wide regardless of width (thin-but-wide tmux pane), runtime-togglable with `Ctrl+Shift+N` |
| Rotation-safe narrow page | ➕ the page is restored on re-entering narrow mode (phone portrait→landscape→portrait) instead of always resetting to the tree |
| Optimistic per-row action marker | ➕ a `⟳` glyph on the affected file leaf the instant a status-changing action dispatches; reconciled (and rolled back on failure) by the post-action `reload_node`. Neutral "in flight" glyph, never a predicted end-state, so it can't show a state the server didn't confirm |

### Coverage summary

| p4v surface | TUI |
|---|---|
| Daily edit / sync / submit loop | ✅ covered + hardened |
| Pending CL workflow (edit desc / toggle files / Save / Submit) | ✅ |
| Submitted CL inspection (unified + side-by-side diff) | ✅ |
| File + folder history | ✅ + auto-load on cursor hover |
| File viewing (text + image ANSI-art + binary hex) | ✅ |
| Locking | ✅ |
| Reconcile / Clean | ✅ (interactive per-file picker + chunked) |
| Branch / Copy / Integrate | ✅ (Branch: mapping picker + preview; Copy/Integrate auto-prompt Resolve) |
| Resolve | ✅ Auto / Yours / Theirs / Skip + in-app 3-way + external merge tool |
| Submit & Resolve | ✅ |
| Shelve / Unshelve / Update / Delete shelf | ✅ |
| Annotate / Time-lapse / Revision Graph | ✅ |
| File Properties (filetype + attributes) | ✅ |
| Undo Changes (`p4 undo`, file or `@CL`) | ✅ |
| Tag with Label · Show Files in Tree · Delete empty Pending CL | ✅ |
| Open With… (configurable external editors) | ✅ |
| Preferences GUI (in-app TOML editor) | ✅ |
| Tree path filter (`/`) · Find File auto-navigate | ✅ |
| Rename / Move | ✅ |
| Multiple connection profiles (picker + in-app add/edit/delete) | ✅ |
| Filesystem hand-offs (Show In, Open Cmd) | ✅ |
| Arbitrary diff (file vs file / two folders / two CLs / vs Have) | ✅ `Ctrl+Shift+D` + workspace context menu |
| Fast Search (`Ctrl+F`) — filename + live preview + highlight | ➕ TUI-only (local SQLite index, IME-friendly debounce) |
| Tree clipboard (`Ctrl+C` / `Ctrl+X` / `Ctrl+V`) | ➕ TUI-only (p4 copy / move + auto-submit) |
| Get Revision dialog (multi-target, multi-criterion) | ✅ p4v port |
| Drag-resizable + persisted panel sizes | ➕ TUI-only |
| Log panel (scrollable, timestamped tail) | ➕ TUI-only |
| Pending Changelists auto-refresh (30s default) | ➕ TUI-only |
| Cross-workspace Pending Changelists (all of user's workspaces, with local/remote distinction) | ➕ TUI-only |
| Friendly missing-dependency message at startup | ➕ TUI-only |
| Workspace / Branch mappings / User / Group admin | ⏭ out of scope |
| Streams / Stream Graph | ⏭ out of scope |
| Jobs (list / spec / fix) | ⏭ out of scope |
| Login / Logout / Set Password / Tickets UI | ⏭ out of scope |
| Resilience (retry, chunking, resume) | ➕ TUI-only |
| Single-page narrow navigator (phone / thin tmux: breadcrumb, number-jump, page-aware footer, responsive columns, layout pin — all width-adaptive) | ➕ TUI-only |
| Perceived-performance feel layer (in-flight indicator, latency-adaptive feedback, adaptive auto-refresh, reconnect-state bar, optimistic action marker) | ➕ TUI-only |
| IME / CJK / Quitting feedback | ➕ TUI-only |

The full p4v daily-developer surface — get / edit / submit / revert /
reconcile / branch-copy-integrate / resolve / shelve / diff
(submitted CL + arbitrary pairs) / annotate / time-lapse / revision
graph / undo / find / filter — is now covered, plus the resilience
layer (auto-reconnect, chunked + resumable bulk ops, lost-ack
recovery, command monitor) that the GUI lacks.

The remaining items on the matrix are intentionally not shipping
(⏭): admin / spec-editing surfaces (workspaces, branch mappings,
streams, jobs, users, groups, triggers, full label editor) stay
with the canonical `p4` CLI, and Login / Logout / Set Password /
Tickets stay there too so the security boundary lives in one
well-understood place.

---

## Keyboard Reference

### Global
| Key | Action |
|---|---|
| `F2` | Command Monitor popup (or **Quick Rename + auto-submit** when a tree is focused) |
| `F3` / `Ctrl+W` | (narrow) Quick-toggle tree ⇄ last-visited panel page · (wide) Focus right pane |
| `F5` | Refresh all panels |
| `F6` / `Shift+F6` | Cycle focus through panes |
| `Ctrl+Shift+N` | Cycle the layout pin: auto → narrow → wide (force the single-page navigator on a thin-but-wide pane, or the full layout on a narrow window) |
| `Ctrl+F` | **Fast Search** — typing-as-you-go filename + live preview |
| `Ctrl+Shift+F` | Find File (server-side fallback) — picked file auto-navigates the tree |
| `Ctrl+D` | Submitted CL diff vs previous (unified) |
| `Ctrl+Shift+D` | Arbitrary Diff — any two paths / revs / CLs |
| `Ctrl+S` | Submit highlighted Pending CL (resilient) |
| `Ctrl+N` | New Pending Changelist |
| `Ctrl+T` | Folder/File History for tree cursor |
| `Ctrl+,` | Preferences (in-app TOML editor) |
| `[` / `]` | Shrink / grow left pane |
| (mouse drag) | Resize panes on any of the 3 splitter handles (triangles ▸ ▾) |
| `Backspace` | (narrow) Return to the tree page from any page |
| `q` / `ㅂ` · `Ctrl+Q` | Quit |

### Narrow mode (single-page navigator, < 100 cells)
| Key | Action |
|---|---|
| `Tab` / `Shift+Tab` | Next / previous page (`tree → pending → history → submitted → log`, wraps). The phone-reliable driver — Blink emits `Tab` but not `Ctrl+Arrow` |
| `1`–`9` | Jump straight to that position in the cycle (the breadcrumb numbers the chips) |
| `Ctrl+→` / `Ctrl+←` | Next / previous page — desktop-terminal alias for `Tab` / `Shift+Tab` |
| `F3` / `Ctrl+W` | Quick-toggle tree ⇄ last-visited panel page |
| `Backspace` | Return to the tree page |

### Workspace / Depot tree (when focused)
| Key | Action |
|---|---|
| `Right` / `Left` | Expand / collapse (or step into / out of node) |
| `s` / `ㄴ` / `Ctrl+Shift+G` | Get Latest |
| `g` / `ㅎ` | Get Latest (chunked, resumable) |
| `e` / `ㄷ` / `Ctrl+E` | Check Out |
| `r` / `ㄱ` / `Ctrl+R` | Revert (confirm) |
| `Ctrl+L` / `Ctrl+U` | Lock / Unlock |
| `Ctrl+C` | **Clipboard copy** — capture this path for a later `Ctrl+V` (p4 copy + auto-submit) |
| `Ctrl+X` | **Clipboard cut** — same, but the paste runs `p4 move` |
| `Ctrl+V` | **Paste** — fires the captured op into a fresh CL at the cursor's destination |
| `/` | Tree path filter — live hide non-matches |
| `m` / `ㅡ` / `Shift+F10` | Context menu |
| `F2` | **Quick Rename** the cursor leaf — enter new name + auto-submits in its own CL |
| `Enter` | Open file viewer (text leaf) / expand (dir) |

### DataTable (Pending / Submitted / History / Detail)
| Key | Action |
|---|---|
| `↑` / `↓` | Move row cursor |
| `Shift+Left` / `Shift+Right` | Horizontal scroll |
| `m` / `ㅡ` | Row context menu (Pending / Submitted / History tables — mirrors p4v's right-click menu for each) |
| `Shift+M` | Panel-level menu (Pending tab): New Pending Changelist · Sort Files By ▸ · Refresh All — mirrors p4v's right-click-on-empty-space menu |

### Modals
| Key | Action |
|---|---|
| `Esc` | Close (most modals) |
| `Backspace` | Close (File Viewer; alias for narrow flow) |
| `Enter` | Confirm / pick option |

---

## Changelog — CL history index

One line per batch. The long-form narratives that used to live here
(design rationale, bug post-mortems, per-CL indexes) moved verbatim to
[`docs/changelog-archive.md`](docs/changelog-archive.md); the
authoritative record is the Perforce CL descriptions (`p4 changes -l`).

The R-series that built the resilience core (details in the archive):

| CL | Phase | Outcome |
|---|---|---|
| 50204 | R1 | All p4 calls under `_run_resilient` (reconnect + retry + lock release) |
| 50207 | R2 | JobRunner priority queue; interactive preempts chunked |
| 50213 | R3 | ChunkedSyncJob with per-target on-disk resume state |
| 50216 | R4 | ResilientSubmitJob with lost-ack idempotency |
| 50220 | R5 | Chunked Revert / Reconcile / Force-Sync / Clean |

Everything since, one row per batch (⏏ = long-form narrative in the
archive; otherwise the CL descriptions and the cited doc are the detail):

| CLs | Date | Theme | Detail |
|---|---|---|---|
| 50265–50301 | 2026-05 | Command Monitor + ETA, first narrow mode, chunked file viewer | ⏏ |
| 51456–51464 | 2026-05 | Friendly missing-dep startup hint; cross-workspace Pending CLs | ⏏ |
| 52535–52557 | 2026-05-17 | "Next batch" wave 1 — depot dim, viewer syntax highlight, tree spinner, popup placement, Log detail viewer | ⏏ |
| 52558–52573 | 2026-05-17 | Fast Search v2/v3 (`?` grep, `cl:`, `nl:`, filters, minimap, history), macros, Swarm URLs | ⏏ |
| 52574–52591 | 2026-05-17 | Follow-ups; LogDetailModal render-hang saga → rerouted onto FileViewerModal | ⏏ |
| — | 2026-05 | **Backend split**: `P4Service` façade + `_PythonBackend`/`_CLIBackend`, pytest scaffolding | ⏏ |
| 52627–52675 | 2026-05-17 | Post-split hardening ×16 — CLI timeout, concurrency + read cache, typing, test fixtures | ⏏ |
| 54181–54212 | 2026-05-24 | God-class → mixins; tests 59→235; submit guards, Jira, go-to-path, permalinks, bookmarks, 3-way merge editor, partial shelve, multi-select | ⏏ |
| 56812–56856 | 2026-06-06 | Live verification: permalink move-following AND 3-way resolve both found broken, fixed | `docs/handoff-manual-tests.md` |
| 57162–57247 | 2026-06-07 | Security audit + scrub-on-export mirror tooling; `docs/MANUAL.md`; first public GitHub push | `docs/mirror-workflow.md`, `docs/security-audit.md` |
| 57328 | 2026-06-07 | Last two manual TUI gestures automated headlessly | `tests/test_e2e_gestures.py` |
| 57593–57602 | 2026-06-08 | Short-terminal Log auto-collapse; p4 call de-serialization | CL descriptions |
| 57849–57869 | 2026-06-09 | p4v gap-closing ×8 — image/binary preview, CL filter/sort, interactive reconcile/clean, Branch Files preview, profiles GUI, external P4Merge, tree-nav fix, e2e wave | ⏏ |
| 58760–58792 | 2026-06-13 | Narrow / remote-terminal push — page cycle, breadcrumb, number-jump, width-adaptive (real-device fixes) | ⏏ · `docs/narrow-terminal-scenario.md` |
| 58773–58786 | 2026-06-13 | Perceived-performance feel layer | ⏏ · `docs/perceived-performance-scenario.md` |
| 59305–59388 | 2026-06-17 | p4 action-string audit (move/delete bug class ×4 sites), search-index purge, revision-graph tests + arrow fix, de-flakes | CL descriptions |
| 60264–60268 | 2026-06-22 | UI freeze / layout-shift fixes (ConnectionBar inline activity, history debounce, detail Enter) | ⏏ |
| 63995–64060 | 2026-07-10 | Roadmap review + execution: Jobs ⏭ decline, manual-check automation (guards/shelve/bulk/search/shared-state), grep-watcher test, EN README + deploy, mirror re-sync, this diet | `docs/roadmap-2026-07.md` |
