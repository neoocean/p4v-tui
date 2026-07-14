# Handoff — manual test checklist (2026-05-24 feature wave)

The features below were built with their **decision logic unit-tested**
(235 passing tests at the time; the suite has since grown to 586), but
the agent could not drive the live TUI or exercise some Perforce flows
in its environment. This is the list of checks **you** should run on a
real terminal + server to confirm UI rendering, keybindings, and the
live `p4` round trips.

> **Status 2026-07-10:** every automatable item below is now a
> regression test (see the per-item pointers). What still needs a human
> is only the *visual* pair at the bottom of Priority B — merge-editor
> legibility and the real-phone narrow layout.

## Setup

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt   # if not already
python -m pytest tests/ -q            # expect: all green, 6 skipped (write-gated)
python p4v.py                         # drive the app for the checks below
```

Run the live write-path tests too (creates + deletes one probe CL per
backend): `PYTEST_ALLOW_WRITES=1 python -m pytest -q`.

---

## Priority A — live Perforce round trips (highest risk)

> The two ⚠ items here (3-way merge, permalink move-following) have now been
> **verified against real Perforce and fixed** — both were broken. The
> end-to-end TUI gestures that previously "remained to click through" are now
> **driven headlessly** by `tests/test_e2e_gestures.py` (Textual's
> `run_test()` pilot scripts the exact `Alt+C` / `Ctrl+G` / `Ctrl+E` /
> `y`/`t`/`Enter` keystrokes against the synthetic `DemoBackend`), so they run
> in CI on every commit. Details + one navigation caveat inline below.

- [x] **3-way merge editor** — create a real conflict (edit a file in two
  workspaces / branches and integrate so `p4 resolve` reports a conflict),
  open the Resolve modal, press **`Ctrl+E`** on the conflicting row.
  - Editor lists the conflict hunks; `↑↓` moves, `y/t/b/o` set
    Yours/Theirs/Base/Both, the detail pane reflects the choice.
  - `Enter` writes the merged file; confirm the workspace file content
    matches your choices and the file is *resolved* (no longer in
    `p4 resolve -n`).
  - Try a clean auto-merge too: it should report "auto-merged cleanly".
  - ✅ **VERIFIED + FIXED (probe conflicts CL 56826/56836/56840, both
    backends).** The resolve semantics were *wrong* and are now corrected:
    - `resolve -am` **skips** a real conflict *without writing markers* —
      the old code used it to "materialise markers", so the editor could
      never open on an actual conflict. Now `-am` is used only to detect
      the clean-vs-conflict split (via a follow-up `resolve -n`).
    - `resolve -af` is what actually writes the `>>>> ORIGINAL` / `THEIRS`
      / `YOURS` / `<<<<` markers (verified format — see
      `tests/test_merge3.py::TestRealPerforceMarkers`), so it's now used to
      emit them on the conflict path.
    - Accept no longer re-runs `resolve -af` (which *regenerates* the merge
      and would discard the user's choices). The hand-merged workspace file
      is written directly; a resolved open file submits its workspace
      content. Cancel restores "yours".
    - Also fixed: the local file was looked up via `where().clientFile`
      (client *syntax*, never on disk) instead of `.path` — the editor
      couldn't even open the file. Now uses `.path`.
  - ✅ **End-to-end TUI gesture now automated** —
    `tests/test_e2e_gestures.py::test_resolve_modal_ctrl_e_opens_merge_editor_and_applies`
    pushes the Resolve modal, presses `Ctrl+E` on the conflicting row, asserts
    the merge editor opens with the hunk parsed from real `resolve -af`
    markers, presses `t` (Theirs) then `Enter`, and asserts the workspace file
    now holds the Theirs resolution **and** that `-af` ran exactly once (the
    accept path must not re-run it and clobber the choice).

- [x] **Permalink move-tracking** — `Alt+C` on a file to copy
  its `//@p/N` address. Then **move/rename** that file (`p4 move` +
  submit). Paste the same `//@p/N` into **`Ctrl+G`**.
  - Expect: the tree navigates to the file's **new** location, with a
    "Followed move:" toast.
  - Unmoved files: pasting `//@p/N` should still land on the original.
  - ✅ **VERIFIED (CL 56812 probe move + an existing depot rename,
    both backends).** The `_find_moved_into` parsing was *broken* and is
    now fixed: (1) the `moved into` integration lives on the revision
    *below* the `move/delete` head, so the old `filelog -m 1` fetched too
    little and never found the target; (2) P4Python and the CLI `-G`
    backend return integration data in different shapes (list-of-lists vs
    flat `how1,0` keys) and only the CLI shape was handled — so following
    silently no-op'd on the *default* P4Python backend. Both fixed and
    covered by `tests/test_move_following.py`.
  - ✅ **End-to-end TUI gesture now automated** —
    `tests/test_e2e_gestures.py::test_permalink_alt_c_then_ctrl_g_follows_move`
    positions the cursor on a file, presses `Alt+C` (asserts a `//@p/N` is
    minted for the origin), then with the backend reporting that file as
    `move/delete`d, presses `Ctrl+G`, pastes the permalink, and asserts the
    "Followed move: ORIGIN → RENAMED" toast fires and the app switches to the
    workspace tab.
  - ✅ **Navigation caveat — FIXED (was pre-existing, not move-specific).**
    The workspace tree keys **file leaves by depot path** but **directory nodes
    by client namespace** (`WorkspaceTree._format_file` returns the `depotFile`,
    while dir nodes come from client-syntax `p4 dirs`). So
    `_navigate_tree_to` → `navigate_to_path(clientFile)` walked every directory
    correctly but the final (client-syntax) file segment never exact-matched
    the depot-keyed leaf, settling the cursor on the file's **containing
    directory** rather than the leaf. Fixed *without* the feared
    namespace-unification ripple: `P4Tree._match_child` adds a **final-segment
    basename fallback** — when the last walk segment (`next_path == target`)
    doesn't exact-match any child, it matches a *leaf* child by basename. Dir
    nodes still match exactly (same namespace) so nothing mid-walk mis-routes,
    and the depot tree (uniform namespace) hits the exact path first and never
    reaches the fallback. `node.data` is unchanged, so copy-path / permalink /
    bookmark / open-viewer / p4-action wiring are untouched. Covered
    end-to-end by `tests/test_tree_navigation.py` (cursor lands on the
    depot-keyed leaf for a client-syntax target; directory navigation still
    lands on the dir).

- [x] **Shared-state cross-machine sync** (permalinks + bookmarks) —
  - On machine **A**: `Alt+C` (copy permalink) and `Ctrl+B`
    (bookmark) a path. Confirm `shared-state/permalinks.json` /
    `shared-state/bookmarks.json` gained the entry **and** the file shows
    up in `p4 opened` (the app auto-`p4 reconcile`s it after the write).
    Submit it.
  - ✅ **VERIFIED (probe round trip, both backends).** The `track()` →
    `reconcile -c <CL> <path>` → `submit_if_dirty` path works as designed
    (no bug found): a new shared-state file reconciles into a *numbered*
    CL as an `add`, the action is captured on both backends, and the
    submit lands it in the depot with the Korean per-file description.
    Locked in by the gated `tests/test_shared_state_live.py` (reconcile +
    revert, no depot tombstone). The cross-*machine* leg is just `p4 sync`.
  - On machine **B**: `p4 sync`, launch the app, open the bookmark picker
    (`Ctrl+Shift+B`) → the bookmark from A is there and jumps correctly.
  - Edit on **B** (the synced copy is read-only): add another bookmark →
    the save must still land (atomic temp+replace bypasses read-only) and
    the file must re-open via reconcile so it is submittable.
  - The `after_write` → `p4 reconcile` round trip is now verified for the
    open-for-add (new file) case, **and** (2026-07-10) for the
    open-for-**edit** on a synced read-only copy — the machine-B leg is
    covered by the gated
    `test_shared_state_live.py::test_shared_state_readonly_edit_reconciles_as_edit`
    (chmod 444 → atomic temp+`os.replace` write → reconcile lands as
    `edit`, both backends, reverted + restored on teardown). If reconcile
    ever misbehaves, the file still saved — just
    `p4 reconcile shared-state/...` by hand before submitting.

- [x] **Submit guards** — now **automated** in
  `tests/test_e2e_submit_guards.py` (2026-07-10): unresolved + ≥ 25 MB
  CL lists the ⛔/⚠ warnings (with file names) and demotes the button to
  "Submit anyway"/warning; empty CL shows the ⛔ block; a clean CL shows
  no markers and a plain "Submit"; a remote workspace's CL refuses with
  a toast instead of the modal.

- [x] **Partial shelve** — now **automated** in
  `tests/test_e2e_shelve_bulk.py`: unchecking files shelves only the
  checked subset (explicit paths on the `p4 shelve` argv); leaving all
  checked omits the file list (whole-CL shelve).

- [x] **Tree multi-select bulk** — now **automated** in
  `tests/test_e2e_shelve_bulk.py`: two `Space`-marked workspace files →
  `e` runs ONE `edit -c <fresh numbered CL> f1 f2`; `r` prompts exactly
  once with the full list before one `revert f1 f2`. (The depot-tree
  menu variants and the mark-glyph/Esc visuals stay covered by
  `test_e2e_gestures_more.py`.)

- [x] **Jira at submit** — set `[jira] base_url` (and optionally
  `projects`) in `p4v-tui.toml`. With a key like `ABC-123` in the CL
  description, `Ctrl+S` shows "🔗 Jira: ABC-123 → <url>"; with none, it
  warns "No Jira issue referenced". Unset `[jira]` → no Jira line at all.
  - ✅ **Bug found + fixed (both backends).** The `[jira] path_projects`
    per-path expected-project map was dead on the default P4Python backend:
    the submit note collected depot paths via raw
    `run("describe") + startswith("depotFile")`, which yields one nested
    list on P4Python (vs per-key strings on CLI). Now routed through the
    normalised `describe()` façade — which was *also* fixed to flatten the
    CLI backend's numbered `depotFile0/1/…` keys into a list (it returned
    `None` there, silently emptying every describe-driven file list, not
    just Jira's). Covered by
    `test_p4client_live.py::test_describe_file_fields_are_parallel_lists`.
  - ✅ The confirm-dialog surface itself is now **automated** in
    `tests/test_e2e_submit_guards.py` (2026-07-10): key present → "🔗
    Jira: KEY → browse-url"; key absent → "⚠ No Jira issue referenced";
    `[jira]` unset → no Jira line at all.

## Priority B — UI rendering + keybindings

> Most of these are now **automated** headlessly in
> `tests/test_e2e_gestures_more.py` (same DemoBackend + `run_test()` pilot
> pattern). The boxes below are checked where a regression test now drives
> the gesture; the few left open need a *visual* eyeball a headless pilot
> can't give (true terminal rendering / phone layout).

- [x] **Command palette gone** — `Ctrl+P` no longer opens the Textual
  palette; inside Fast Search it walks query history.
  (`test_command_palette_disabled` pins `ENABLE_COMMAND_PALETTE is False`.)
- [x] **Backend in title bar** — the Header shows `P4Python` or `p4 CLI`
  (force the other with `P4V_BACKEND=cli|python` and re-check).
  (`test_backend_name_in_subtitle` asserts `sub_title` after connect.)
- [x] **Go-to-path** — `Ctrl+G`, paste a depot path (`//…`) and a local
  absolute path; both expand + highlight the right node.
  (`test_ctrl_g_goto_path_navigates` drives the modal + navigation.)
- [x] **Bookmarks** — `Ctrl+B` on a node ("Bookmarked…" toast),
  `Ctrl+Shift+B` opens the picker; `Enter` jumps + highlights, `d`/`Del`
  removes, `Esc` closes. Persists across restarts
  (`~/.p4v-tui/bookmarks.json`).
  (`test_bookmark_add_and_picker` drives Ctrl+B add + Ctrl+Shift+B picker.)
- [x] **Fast Search row actions** — now **automated** in
  `tests/test_e2e_search_actions.py` (2026-07-10): a seeded SQLite
  SearchIndex fixture drives the real query path; `d` (diff vs have) and
  `g` (chunked get-latest) fire from the results list, and the same
  letters typed into the focused query Input stay query text (the
  by-design gating, pinned).
- [x] **Mark glyph rendering** — the `●` marker shows on marked nodes and
  survives expanding/collapsing the subtree; `Esc` clears it.
  (`test_space_marks_node_glyph_and_esc_clears`.)
- [x] **Image / binary preview** — Enter on an image leaf renders half-block
  ANSI art (not raw bytes); non-image binary shows a hex window.
  (`test_enter_on_image_leaf_opens_ansi_preview`; pure renderer in
  `tests/test_image_preview.py`.)
- [x] **CL table filter / sort** — Pending/Submitted `Shift+M` →
  Filter/Sort; filtering by a non-matching user empties the table.
  (`test_pending_filter_applies_and_reduces_rows`; pure logic in
  `tests/test_cl_table_filter.py`.) NOTE: the filter view persists to
  `state.json`; `_isolated_home` now also redirects `state.STATE_PATH`
  so tests can't pollute the real `~/.p4v-tui/state.json`.
- [ ] **Merge editor layout** — hunk list + detail render legibly; no
  Textual render hang (it uses OptionList + Static, not a fresh RichLog).
  (Logic + Ctrl+E gesture covered by `test_e2e_gestures.py`; the *visual*
  legibility is the only manual bit.)
- [ ] **Narrow-terminal page navigator** — on a phone / `< 100`-col
  terminal, the layout collapses to one full-screen page at a time
  (tree / Pending / History / Submitted / Log). Full checklist in
  **`docs/narrow-terminal-scenario.md`** — verify **`Tab`/`Shift+Tab`**
  cycle all five pages (the phone-reliable driver; `Ctrl+→`/`Ctrl+←` are
  a desktop-only alias — mobile terminals don't send Ctrl+Arrow),
  `F3`/`Ctrl+W` quick-toggle tree ⇄ last panel, `Backspace` returns to
  tree, the tree/tables get the full height (no squashing Log strip),
  and the Log page is full-screen + live.

## Website / GitHub Pages (2026-07-12, CL 64212 + 64218)

The intro/guide site lives at **`docs/landing/`** and is **live at
https://p4v-tui.woojinkim.org** (GitHub Pages, Actions source, HTTPS
enforced). It's a self-contained static site (no build step) with 26
screenshots generated from the real app.

- **Regenerate screenshots** (real `P4VApp` driven headless with the
  synthetic `scripts/demo_backend.py` — no live server touched):
  `python3 scripts/gen_screenshots.py [name-filter]` → `docs/image/*.svg`,
  then resync the landing copy:
  `cd docs/landing && for f in image/*.svg; do cp "../image/$(basename "$f")" "$f"; done`.
  SVGs are post-processed by `scripts/svg_postprocess.py` into
  font-independent vectors (needs `fonttools` + `scripts/fonts/` Fira Code).
- **Redeploy**: submit the change, then mirror + push —
  `SYNC_GITHUB_REMOTE=https://github.com/neoocean/p4v-tui.git ./scripts/sync-to-github.sh dry-run`
  (inspect scrub) → `… sync`. The `pages.yml` workflow auto-deploys on any
  `docs/landing/**` change. (Env foot-gun + Pages-enable + CNAME-scrub
  traps are in `docs/MEMORY.md`; full procedure in
  `docs/github-migration-and-deployment.md`.)

Manual visual checks (the site can't be auto-verified for legibility):

- [ ] **Live site on desktop** — browse https://p4v-tui.woojinkim.org:
  hero + all feature rows render, every screenshot is crisp (box-drawing
  unbroken, no font-fallback tofu), the lightbox zooms, and the top-nav
  anchors + `/guide` links work (root-absolute links only resolve on the
  custom domain, **not** on `neoocean.github.io/p4v-tui/`).
- [ ] **Guide chapters** — open a few `/guide/<topic>` pages: the injected
  nav / TOC (current chapter highlighted) / prev-next pager / footer all
  appear, and the per-chapter screenshots + keybinding tables render.
- [ ] **Phone / narrow** — on a real phone, the grids collapse to one
  column, the TOC hides, and screenshots stay legible.

## Notes

- Anything that fails here is most likely in the **app wiring / live p4
  flags**, not the pure cores (those are covered by `tests/`). Start by
  reproducing with a single `p4` command on the CLI to isolate.
- Both Priority-A ⚠ items (3-way **merge** and permalink **move-following**)
  are now **verified against real Perforce and fixed** — see their entries
  above. The pure cores plus the real-output contract tests
  (`test_merge3.py::TestRealPerforceMarkers`, `test_move_following.py`)
  guard the decision logic, and the end-to-end TUI gestures are now driven
  headlessly by `tests/test_e2e_gestures.py` — so there is no remaining
  manual smoke check for either. The one open item from that automation is
  the cosmetic workspace-tree file-vs-dir namespace caveat noted above.
