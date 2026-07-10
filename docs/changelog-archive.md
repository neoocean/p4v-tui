# p4v-tui — changelog archive (CL narratives)

Verbatim CL-batch narratives moved out of `DESIGN.md` (2026-07-10,
roadmap P3 "history diet") so the design doc stays an orientation
document. Nothing here was rewritten on the way out — this is the
historical record as it accumulated, oldest first.

The authoritative record is always the Perforce changelist descriptions
themselves (`p4 changes -l` over this project's depot path); this file
is the browsable long-form companion. New batches append at the bottom;
`DESIGN.md` § "Changelog — CL history index" keeps the one-line-per-batch
timeline.

---

## Resilience Roadmap (R-series, completed)

| CL | Phase | Outcome |
|---|---|---|
| 50204 | R1 | All p4 calls under `_run_resilient` (reconnect + retry + lock release between attempts) |
| 50207 | R2 | JobRunner priority queue; interactive jumps in front of chunked between chunks |
| 50213 | R3 | ChunkedSyncJob with per-target on-disk resume state |
| 50216 | R4 | ResilientSubmitJob with lost-ack idempotency check |
| 50220 | R5 | Chunked Revert / Reconcile / Force-Sync / Clean |

Subsequent CLs (50265, 50282, 50292, 50297, 50301) layered Command
Monitor, ETA tracking, narrow terminal mode, and chunked file viewing
on top.

CLs 51456 / 51460 / 51462 / 51464 (2026-05) — friendly missing-
dependency message at startup (`p4v.py` lazy import +
`ModuleNotFoundError` → Korean install hint with module-specific
extras, exit 1 instead of traceback); cross-workspace Pending
Changelists (`p4 changes -s pending -u <me>` instead of `-c <client>`,
new Workspace column, rich.text dim/italic + `↗` marker on remote
rows, `_is_remote_pending` gates Submit/Revert/Shelve/Move/Re-resolve/
Diff-against-have in the row menu and Ctrl+S, remote Enter opens a
read-only `p4 describe` viewer, detail pane falls back to
`p4 describe` instead of `p4 opened`); two doc passes that fold both
features into `README.md` (settings/screen/menu/feature sections) and
`DESIGN.md` (Pending Changelists / TUI conveniences / Coverage summary
matrices), plus a dedicated `## p4v 에는 없는 기능 (p4v-tui only)`
section in `README.md` aggregating every `➕ TUI-only` row into seven
categories for fast orientation. The tracking issue closes with the
first of these.

CLs 52535 / 52545–52557 (2026-05-17) — first wave of "next batch"
features split into ten single-purpose CLs (Depot tree dim for
non-mapped paths, Submitted CL Enter → read-only detail, FileViewer
syntax highlighting via pygments, tree expand loading spinner,
FilePropertiesModal `_render` collision fix + exception routing to
LogPanel, Pending Workspace column truncate, popup placement
avoiding the trigger row, History column-schema swap by file/folder
mode, Mark for Delete always landing in a numbered CL, Log panel
click + Enter detail viewer); plus 52535 (TOML BOM-tolerant loader)
that unblocked the chain.

CLs 52558–52573 (2026-05-17) — README roadmap + Fast Search v2 / v3:
Swarm CL URL (copy + browser), perforce path tolerance (loose
token-AND + Levenshtein "did you mean"), rule-based `nl:` natural-
language, ResolveModal preview-diff (R4 1st pass) plus the `-f`
plumbing fix, macro picker (`[[macro]]` TOML + Ctrl+Shift+M), Fast
Search `?` content grep, `cl:` description search, filter prefixes
(`@user:` / `type:` / `/regex/`), "Search In This Folder…" tree
context, result-cap toggle, match minimap, history stack (Ctrl+P /
N), inline content-match preview rows, did-you-mean adoption.
DESIGN/README matrices updated to reflect the new coverage.

CLs 52574–52591 (2026-05-17) — "next batch" follow-ups + recurring
bug-hunt:

  Solid wins —
    * Re-resolve `-f` now propagates to the action commands as
      well, not just enumerate (52573).
    * Find File modal falls back to the SearchIndex loose +
      Levenshtein ladder when the server-side `p4 files` lookup
      returns 0 (52575).
    * Macro keybindings (`[[macro]] key = "f9"` etc.) registered
      on App.on_mount (52576).
    * `p4 grep` is now streaming via P4Python `OutputHandler` —
      first match shows up tens of milliseconds after the user
      finishes typing rather than after the whole-depot walk
      (52577).
    * Default-CL isolation extended from `delete` to `edit` and
      `add`: every file-opening action lands in a fresh numbered
      CL (52579). The "Default CL 격리" policy is documented in
      `README.md` ⑧.
    * "Loaded config: …" + similar informational notifies routed
      through the new `CmdLog.log_info` so they leave a scrollable
      Log-panel entry rather than a few-second toast that
      disappears (52583).
    * Quit sequence (q / Ctrl+Q) now runs JobRunner.stop +
      P4.disconnect on a daemon worker before exiting the alt-
      screen — the CLI prompt is responsive the instant it
      appears (52581).

  Unresolved — LogDetailModal hang regression —
    Repeated `AttributeError: 'NoneType' object has no attribute
    'render_strips'` at `Visual.to_strips` whenever the user
    presses Enter on a Log-panel entry. Six attempts traced in
    succession (52580 / 52583 / 52585 / 52587 / 52589 / 52590)
    chased through Rich-style parse, padding rules, deferred
    rendering, structural simplification, an in-app widget-tree
    dump, and finally an opaque-background workaround. The
    instrumented dump confirmed the failing widget is the
    `LogDetailModal` screen itself — `_render()` returns `None`
    and the `_layout_cache` keeps replaying it. `FileViewerModal`
    follows the same compose / CSS pattern and never reproduces.
    Without a deeper Textual-internals investigation we could
    not isolate the trigger.

    52591 — pragmatic resolution: route LogPanel Enter through
    the production-proven FileViewerModal instead of the
    dedicated modal. `LogPanel._format_entry_detail` rebuilds the
    same content (header + ±8 surrounding entries + full detail
    block for the focused row) as plain text. We lose ↑/↓ inter-
    entry navigation inside the popup; the user closes with Esc,
    moves the LogPanel cursor, and re-opens.
    `log_detail_modal.py` is left in the tree as reference until
    the navigation is rebuilt on top of `FileViewerModal`.

  Follow-up — navigation rebuilt on FileViewerModal —
    `widgets/log_entry_viewer.py` introduces `LogEntryViewerModal`,
    a thin `FileViewerModal` subclass that adds priority-bound
    ↑/↓/j/k entry navigation (also Hangul ㅏ/ㅓ). The render
    pipeline is unchanged — `_step()` updates `self._entry_id`,
    refreshes the title widget, and re-runs the same
    `_prepare_lines → _write_next_batch` path FileViewerModal used
    on first mount, so we stay on the only render path Textual 8.x
    is reliably happy with for our content shape. `priority=True`
    on the nav bindings beats the focused RichLog's built-in line-
    scroll bindings; PageUp/PageDn / Home / End still scroll the
    body for entries with long tracebacks. The footer hint is
    updated post-mount to "↑↓ entry · PgUp/PgDn scroll · Esc
    close" so the dual-purpose key layout is discoverable.
    `LogPanel.action_open_detail` now pushes `LogEntryViewerModal`
    directly and `_format_entry_detail` moves out of LogPanel into
    the new module. `log_detail_modal.py` is removed from the tree
    now that the rebuild is in place.

Backend split — P4Python becomes optional, `p4` CLI is the fallback
(`docs/p4-cli-fallback-scenario.md`):

  `p4client.py` is refactored into a `P4Service` façade plus two
  `_Backend` implementations. `_PythonBackend` preserves the original
  P4Python path (one persistent `P4.P4()` connection, OutputHandler-
  driven grep streaming, P4.P4Exception translated into a backend-
  agnostic `P4Exception` so callers stop importing the C extension
  for an exception class). `_CLIBackend` (new) spawns a per-call `p4`
  subprocess, reads tagged output as Python marshal-2 (`p4 -G ...`)
  and pipes text-form stdin to write commands like `change -i`
  (scenario §5.2 — using `-G` for input would require marshalled
  bytes and trip "Invalid marshalled data supplied as input.").
  Streaming grep reads marshalled rows off `Popen.stdout` so the
  Fast Search `?` preview lights up within milliseconds, same as
  P4Python's OutputHandler.

  Backend selection is auto with `P4V_BACKEND` env-var override
  (`python` | `cli`) — see *Backends* above. Connection params for
  the CLI backend are snapshotted from `p4 set -q` at startup to
  satisfy scenario §7.2 (mid-session env mutation must not affect
  in-flight commands). Subprocess spawn carries `CREATE_NO_WINDOW`
  on Windows so `p4.exe` doesn't briefly flash a console window each
  call (§11.3 scope expansion: Windows is supported this cycle, not
  deferred).

  Caller migration: `bulk_jobs.py`, `submit_job.py`, `search_jobs.py`,
  and `app.py` swap `import P4` + `except P4.P4Exception` for
  `from .p4client import P4Exception` + `except P4Exception` — same
  semantics, no C-extension import dependency at module load time.
  `config.py`'s env probe (`_detect_env_profile`) gains a
  `p4 set -q` fallback path used when P4Python isn't importable.
  `p4v.py` adds a `_print_no_backend` handler that catches
  `P4SetupError` from `_select_backend()` (the only failure mode
  where neither backend can be activated) and emits a Korean install
  hint mentioning both options instead of dumping a traceback. The
  P4 install hint in `_INSTALL_HINTS` is rewritten to call out that
  P4Python is optional, with explicit pointers to the CLI download
  page and the `P4V_BACKEND` env var.

  pytest scaffolding (scenario §11.4 scope expansion: opt for a
  proper suite instead of manual smoke):
    * `pyproject.toml` — minimal pytest config + `tool.pytest.ini_options`
      with `pythonpath = ["."]` so the suite imports the in-tree
      `p4v_tui` package without an install step.
    * `requirements-dev.txt` — pinned `pytest>=7.0` plus the README
      for `PYTEST_ALLOW_WRITES=1` (gates the form-CRUD probe).
    * `tests/conftest.py` — session-scope `P4V_BACKEND` scrubber,
      `has_p4python` / `has_p4_cli` / `p4_live_ok` capability fixtures,
      parametrised `live_backend` fixture that boots a `P4Service`
      against each backend in turn (auto-skip when a backend's
      prerequisite is missing).
    * `tests/test_p4client_unit.py` — 34 unit tests over the marshal
      decode, the spec-form text serializer, the numbered-field
      flatten, and the tagged-row projection. No server / no `p4`
      binary required.
    * `tests/test_p4client_live.py` — 22 parametrised live-server
      tests (connect, info, depots, dirs, fetch_client_view,
      login_status, pending_changes, submitted_changes, where, run
      passthrough, grep_stream delivery, grep_stream cancellation).
    * `tests/test_p4client_live_crud.py` — 2 parametrised
      `create → fetch → update → fetch → delete` probes gated by
      `PYTEST_ALLOW_WRITES=1`. Always cleans up the probe CL on
      teardown.

CLs 52627-52675 (2026-05-17) — post-backend-split review-driven
hardening. Sixteen single-purpose CLs walked through the review
items captured in this session's chat. Each is its own commit with
a detailed description; the index below is for cross-reference.

  52627 — `_invoke()` per-call timeout (default 1800 s, env var
    `P4V_CLI_TIMEOUT`, per-call `timeout=` kwarg). Hung p4d now
    surfaces as `P4Exception` with a "raise P4V_CLI_TIMEOUT"
    hint instead of blocking the worker forever; `grep_stream`
    intentionally exempt (already cancellable).
  52628 — Drop unreachable `or [""]` branch in `_form_dict_to_text`.
    Add two pin tests covering the short branch the dead branch was
    nominally guarding + the live branch's nearest neighbour.
  52629 — Collapse two-branch `P4Service.connect()` into one
    `configure()` + guarded `connect()`. New `_RecordingBackend`
    fixture in unit tests pins the call sequence so a future
    refactor can't reintroduce the duplication.
  52634 — `grep_stream` cancellation watcher (daemon thread + 100 ms
    `threading.Event.wait`). Kills the subprocess the moment
    `cancelled()` flips, so a mid-blob cancel no longer waits for
    the next row before taking effect. New live test confirms
    elapsed time stays small even when the cancel lands during a
    `marshal.load` block.
  52637 — Document the `marshal.load` trust boundary (CLI backend
    only reads marshal bytes off `p4` subprocess stdout, never
    from an external source). Module docstring "Trust boundary"
    section + inline comment at each `marshal.load` site + new
    §7.3a in `docs/p4-cli-fallback-scenario.md`.
  52640 — Add `GrepMatchCallback` / `CancelledFn` type aliases
    and annotate all four `grep_stream` signatures (interface +
    both backends + façade). Pyright / mypy now has a concrete
    contract; the row-shape (`depotFile` / `rev` / `line` /
    `matchedLine`) is documented in the façade docstring.
  52644 — `p4v.py::main` drops the fragile
    `type(exc).__name__ == "P4SetupError"` duck-typing and
    imports `P4SetupError` directly. New `TestImportSurface`
    unit test parses `p4client.py` via AST and asserts the
    top-level imports stay stdlib-only — pins the invariant the
    direct import depends on.
  52647 — Simplify backend `fetch_form` / `save_form` signatures
    from raw argv tuples (`("change", "-o", num)`) to a
    `(kind: str, key: str | None = None)` shape. Caller no longer
    constructs `-i` / `-f` flags; the backend builds them
    internally. Reads "what" not "how" — the historical
    `[a for a in rest if a != "-o"]` stripping dance in
    `_PythonBackend.fetch_form` is gone.
  52650 — Cache the OutputHandler subclass in
    `_PythonBackend.grep_stream` via lazy
    `_get_or_build_grep_handler_cls`. Per-call state moves from
    closure-over-locals to explicit constructor args, so the
    handler/caller contract is visible from the signature alone.
    New live test pins that repeated grep calls don't leak
    `count` across invocations.
  52653 — Narrow internal `Sequence[Any]` → `Sequence[str]` on all
    backend-facing methods. Public `P4Service.run(*args: Any)`
    keeps the permissive surface but `str()`-casts at the
    boundary, so the typed internal pipeline gets a clean
    `Sequence[str]`. Drop the unused `Iterable` import.
  52658 — `tests/conftest.py::live_backend` switches from
    `importlib.reload(pc) + env var` to direct backend
    construction via `P4Service(backend=…)`. Avoids the
    class-identity-divergence trap reload causes (post-reload
    `P4Exception` is a *different class object* than the one
    other test modules captured at import time) and saves the
    reload cost per parametrisation.
  52661 — Rename conftest's `_p4_login_works` → `_p4_reachable`
    (and the matching `p4_live_ok` fixture → `p4_reachable`).
    The helper only runs `p4 info`, which checks reachability
    not auth; the old name set the wrong expectation when
    debugging.
  52664 — CRUD test cleanup failure surfaces as `WARN` on stderr
    with the orphan CL number + a manual-fix recipe, instead of
    silently swallowing. A stale probe CL on the shared depot
    is now visible to the operator running the suite.
  52669 — CLI backend perf — parallel subprocess execution +
    idempotent-read cache (the closest practical approximation
    of "connection reuse" given that the `p4` binary has no REPL
    mode). `_Backend.max_concurrent_calls` declares each
    backend's concurrency; P4Service splits its single Lock into
    `_connect_lock` (state mutex) + `_call_sem`
    (BoundedSemaphore(N)) so the Python backend keeps its
    serialised access while the CLI runs N=4 parallel
    subprocesses (env-tunable). `_CLIBackend._read_cache` adds
    a 30 s TTL cache (env-tunable) for `info` / `client -o`
    reads; `save_form` flushes. README adds the CLI tuning
    table.
  52672 — `LogEntryViewerModal` hugs the top of the screen
    (`place-top` class) so the LogPanel that opened it stays
    visible at the bottom of the layout. Mirrors the "popup must
    not cover its trigger" rule the DataTable row pop-ups
    already follow.
  52675 — `FileViewerModal` prepends an auto-widthed dim line-
    number column to every rendered line; press `n` (or `ㅜ`)
    to toggle off. Default ON for the base viewer (the most
    common ask is "what line am I looking at?"); LogEntryViewer
    opts out (its body already has a per-entry `►` marker so a
    left margin would duplicate). Footer hint reflects the
    current ON / OFF state so the toggle is discoverable.

Net: 16 CLs, all green at 87 passed (started this run at 58). No
behaviour regression in the public `P4Service` surface; backend
identity, perf characteristics, and UX placement all improved.

CLs 54181-54212 (2026-05-24) — maintainability pass + a wave of
feature work, all in numbered CLs (the shared `admin@shared`
client makes the default changelist unsafe — see `docs/MEMORY.md`).

  Maintainability —
    * Pure-logic test safety net first (config / chunking / search /
      utils / menu gating / submit guards / jira / path / merge3 /
      permalink / bookmarks): the suite went 59 → 235 tests.
    * `P4VApp` (a 6244-line god class) split into mixins by the
      existing section seams: `app_shared` (constants/helpers/
      ConnectionBar), `app_menus` (`_MenuMixin` + the pure
      `build_pending_menu`), `app_details` (`_DetailMixin`),
      `app_diffrev` (`_DiffRevMixin`) — app.py down to ~4.6k lines.
      Each extraction verified behaviour-preserving by diffing the
      `dir(P4VApp)` surface against a baseline (recipe in CLAUDE.md).
    * Ruff F set driven to zero; root `CLAUDE.md` added as the agent
      entry point.

  Features —
    * Submit guards (`submit_guards.py`): unresolved / oversized /
      empty-CL warnings folded into the submit confirm.
    * Jira-at-submit (`jira.py` + `[jira]` config): the CL
      description's issue key is surfaced + linked at submit; warns
      when a configured shop has none referenced. No live Jira API
      (description is the link, per Smart Commits).
    * Active backend (P4Python / p4 CLI) shown in the Header title.
    * Go-to-path (`Ctrl+G`, `path_nav.py`): paste a depot / local /
      virtual path → navigate the tree (reuses `_navigate_tree_to`).
    * Partial shelve: a file-selection picker before `p4 shelve -c`.
    * Fast Search row actions: `d` diff-vs-have / `g` get-latest.
    * Tree multi-select (`Space`) + bulk edit/revert/add (one numbered
      CL) on WorkspaceTree, Get-Latest/Mark-for-Delete on DepotTree;
      `Esc` clears marks.
    * In-app 3-way merge editor (`Ctrl+E` in Resolve, `merge3.py` +
      `MergeEditorModal`): parse `p4 resolve -am` markers → pick a
      side per hunk → write back → `p4 resolve -af`.
    * Immutable permalinks (`//@p/<id>`, `permalink.py`):
      `Alt+C` copies a stable handle that follows move/rename
      history to the file's current location when pasted into Go-to-path.
      (Avoids `Ctrl+Shift+C`, which Windows Terminal binds to its own
      "Copy" by default and which is indistinguishable from `Ctrl+C` at
      the VT level without modifyOtherKeys/kitty keyboard protocol.)
    * Bookmarks (`bookmarks.py`, `Ctrl+B` / `Ctrl+Shift+B`): permalink-backed
      so a bookmark survives the path being moved.
    * `ENABLE_COMMAND_PALETTE = False` (frees Ctrl+P for Fast Search).

  NOTE — two flows could not be exercised against live state in the dev
  environment and want manual verification (see
  `docs/handoff-manual-tests.md`): the 3-way merge `p4 resolve -am`/`-af`
  round trip (no conflicting files available) and the permalink
  `p4 filelog` move-following (no renamed file available). Pure cores
  are unit tested; unmoved/clean paths behave correctly.

CLs 57849-57869 (2026-06) — p4v feature-gap closing batch (eight
single-purpose CLs, each with its own pure-logic module + unit tests +
a headless e2e gesture where one applies):

  * 57849 — **Image / binary preview.** `image_preview.py` (pure):
    magic-byte detect + half-block ANSI-art render (Pillow) + hex dump.
    `FileViewerModal` gains a `rendered=` path for pre-built renderables;
    `_open_file_viewer` keeps raw bytes and routes images → ANSI art,
    other binaries → hex. Pillow added to requirements (optional; hex
    fallback on import/decode failure).
  * 57854 — **CL table filter / sort.** `cl_table_filter.py` (pure
    `CLTableView` + `apply_view`) + `CLFilterModal`. Pending/Submitted
    `Shift+M` → Filter/Sort; view persisted to `state.json`; re-render
    from cached rows (no extra `p4 changes`). Path filter intentionally
    omitted (would need a per-CL `describe`).
  * 57857 — **Interactive Reconcile / Clean.** `reconcile_preview.py`
    (pure parse of `reconcile -n` / `clean -n`) + `ReconcilePickerModal`
    + `ReconcileFilesJob` / `CleanFilesJob`. All-checked == old
    all-or-nothing subdir job; subset == explicit-files job.
  * 57862 — **GUI connection profiles.** Preferences → Profiles tab
    (`ProfileEditModal`) adds/edits/deletes `[[profile]]` entries;
    `write_config` already emitted them, so save is a model swap.
  * 57863 — **Branch Files preview + mapping.** `branch_files.py` (pure
    `build_populate_args` / `parse_populate_preview`) + `BranchPickerModal`
    (`p4 branches`) + `BranchPreviewModal` (`populate -n` dry run). BCI
    modal gains `branch_spec` (mapping mode hides Source).
  * 57865 — **External P4Merge.** `[merge_tool]` config + blocking
    `fs_actions.run_merge_tool`; Resolve `Ctrl+T` reconstructs
    base/theirs/yours via `merge3`, launches the tool, reads `{merge}`
    back. Complements the in-app `Ctrl+E` editor.
  * 57867 — **Workspace-tree navigation fix** (was a tracked caveat):
    `P4Tree._match_child` final-segment basename fallback lands the
    cursor on a depot-keyed file leaf when walking a client-syntax path,
    without unifying `node.data` (no ripple). `tests/test_tree_navigation.py`.
  * 57869 — **e2e automation + test-state isolation fix.**
    `tests/test_e2e_gestures_more.py` drives the Priority-B handoff
    checklist headlessly (palette/title/marks/Go-to-path/bookmarks/image/
    filter). Found + fixed a real hazard: `state.STATE_PATH` is bound at
    import from `Path.home()`, so the e2e `_isolated_home` (late `$HOME`)
    didn't redirect `save_state` — a saved-filter test was writing the
    dev's real `~/.p4v-tui/state.json` and that persisted filter then
    emptied every later test's pending list. `_isolated_home` now also
    monkeypatches `state.STATE_PATH`.

  Net: six p4v gaps closed (Branch Files, interactive Reconcile/Clean,
  image/binary preview, CL filter/sort, external merge tool, GUI
  connection editor), one caveat fixed, e2e coverage broadened. Suite
  330 → 396 passing. `DESIGN.md` matrix + `docs/p4v-feature-gaps.md`
  updated in lockstep.

CLs 58760-58769, 58790, 58792 (2026-06) — **narrow / remote-terminal
push** (the small-screen half of the resilience story). Plan in
`docs/narrow-terminal-improvements.md`; behaviour in
`docs/narrow-terminal-scenario.md`. Pure core grows in `narrow_nav.py`,
verified by the first headless-pilot navigator tests
(`tests/test_e2e_narrow.py`).

  * **58761 — effective page cycle + the Tab-shadow fix.** `[narrow]`
    config (`disabled_pages` / `skip_empty`) trims the cycle;
    `cycle_page` / `toggle_target` take the effective list. *Bug found:*
    the `Tab` page cycle never actually fired — Textual's `Screen` binds
    `tab` → `app.focus_next`, shadowing the app binding. Now driven from
    `P4VApp.on_key` (narrow + base screen + non-`Input` guards). Caught
    only because this CL added the first navigator e2e tests.
  * **58762 — page-indicator breadcrumb.** `#narrow_breadcrumb` shows the
    effective cycle with the current page reverse-highlighted.
  * **58763 — rotation-safe page.** Re-entering narrow restores the last
    page instead of resetting to the tree (phone rotate round-trip).
  * **58764 — number-key direct jump.** Bare `1`-`9` jump to a page
    (`jump_target_by_index`); the breadcrumb numbers each chip so the
    digit is self-documenting. (A `g`-chord was the original plan but `g`
    is taken by workspace-tree chunked sync.)
  * **58765 — page-aware footer.** `#narrow_footer` replaces Textual's
    full Footer with a curated per-page key-hint strip.
  * **58766 — layout pin.** `[narrow] layout = auto|narrow|wide` +
    `Ctrl+Shift+N` (`resolve_narrow_mode`) — force the navigator on a
    thin-but-wide tmux pane, applied so a stray resize can't undo it.
  * **58769 — responsive table columns.** `TABLE_FIELDS` profiles trim
    Pending/Submitted → `Change · Description`, History →
    `Rev · Action · Description`; `_set_table_columns` rebuilds lazily,
    `_rerender_tables_for_mode` re-renders cached rows on a layout flip.
    Column-0 identity invariant preserved (remote `↗` marker moves to the
    Description cell).
  * **58790 / 58792 — width-adaptive breadcrumb + footer (real-device
    fix).** iPhone-Blink testing at ~46 cols showed the breadcrumb's
    `5 log` chip and the footer's `q quit` clipping off the edge — which
    headless tests (asserting content, not pixel width) missed. The
    breadcrumb now compacts non-current chips to bare jump numbers, and
    the footer drops least-important hints as a *strict by-importance
    prefix* (no low-priority hint survives past a dropped higher one);
    both fall back to full form when there's room.

CLs 58773-58786 (2026-06) — **perceived-performance ("체감 성능") feel
layer** (the sibling to resilience: on the same bad link, does it *feel*
responsive?). Scenario + status in
`docs/perceived-performance-scenario.md`; pure policy in `perf_feel.py`
(`tests/test_perf_feel.py`), wiring + e2e in `tests/test_e2e_perf.py`.

  * **58776 — in-flight activity indicator (P0.1+P0.3).** Wires the
    previously-orphaned `#job_status` slot to a spinner + label shown
    while an interactive `@work` load runs; latency-adaptive (hidden
    < 150 ms so fast ops don't flicker, escalating label past 1 s / 8 s).
    App activity registry (`_begin/_end_activity`, UI-thread only; workers
    marshal via `call_from_thread`), one lazy timer that stops when idle.
  * **58779 — adaptive auto-refresh (P2.1).** Fixed `set_interval` →
    self-rescheduling `set_timer`; `next_refresh_interval` backs the
    pending refresh off on a slow link (never faster than configured,
    capped 4× base) so it stops contending with foreground calls.
  * **58781 / 58786 — optimistic action acknowledgment (P1.3).** Inline
    file actions raise the indicator with a per-action verb (58781), and
    the affected file leaf gets an optimistic `⟳` marker the instant the
    action dispatches (58786), reconciled / rolled back by the existing
    post-action `reload_node`. Neutral "in flight" glyph, never a faked
    end-state.
  * **58782 — reconnect state in the ConnectionBar (P1.2).** Service-level
    `_on_retry` / `_on_recover` hooks on `P4Service` (default None,
    parity-safe) let the bar show `⟳ Reconnecting… (attempt N/max)` during
    a mid-command stall and restore the normal line on recovery.
  * **58777 / 58783 — honest re-scoping.** Reading the code while
    implementing collapsed two planned items into already-handled:
    refresh render is *atomic* (worker fetches, then a single synchronous
    `clear`+repopulate — old rows never blank, so P0.2 stale-while-
    revalidate isn't needed) and Submitted is eager-loaded at connect (so
    P1.1 prefetch is moot; History has no prefetch target). P2.2
    cancellable-loads was declined as cosmetic (a thread worker can't
    interrupt a blocking p4 socket call), P2.3 gated on a measurement.

  Net: the narrow navigator + feel layer make the slow-link *remote*
  experience usable and legible without touching the data path. Suite
  → 512 passing; `DESIGN.md` Architecture / matrices / keyboard reference
  + both scenario docs updated in lockstep.

CLs 60264–60267 (2026-06-22) — **UI freeze / layout-shift bug-fix batch.**

  * **60264 — activity indicator 레이아웃 이동.** `#job_status` 위젯이
    `display:none ↔ block` 토글 시마다 전체 레이아웃 리플로우를 유발해
    화면이 1줄씩 위아래로 흔들리고 0.1 s 간격으로 키 처리가 밀리는 문제.
    `ConnectionBar`에 `set_activity(text)` / `show_reconnecting(text)` /
    `_render_bar()` 추가; 활동 텍스트를 Server/User 줄 끝에 인라인으로
    표시해 높이 불변. `_update_activity_widget` → `conn_bar.set_activity(text)`
    로 교체; `#job_status` 위젯 + CSS 블록 제거; `test_e2e_perf.py` 대응.
  * **60266 — 히스토리 로딩 중 내비게이션 동결 수정.** `on_tree_node_highlighted`
    이 커서 이동마다 즉시 P4 호출을 발생시켜 `_call_sem` 대기 스레드가
    쌓이고 스레드 풀이 포화, 조작 불가 상태가 되는 문제. 300ms 디바운스
    (`_history_highlight_seq` 카운터) 추가 — 커서가 정착할 때까지 P4 호출
    지연. 추가로 취소된 워커 스레드가 `_render_history`를 호출하지 않도록
    `get_current_worker().is_cancelled` 체크 삽입.
  * **60267 — Pending detail 파일 목록 Enter → 파일 뷰어.** `#detail_files`
    DataTable에서 Enter 시 아무 일도 없었음. `on_data_table_row_selected`에
    `detail_files` 케이스 추가; 첫 번째 컬럼(depot 경로)을 꺼내
    기존 `_open_file_viewer`로 라우팅.

  Net: 세 가지 UX 버그 수정 (레이아웃 이동, 동결, 누락된 Enter 동작).
  Suite 575 passing (변경 없음).
