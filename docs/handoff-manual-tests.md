# Handoff — manual test checklist (2026-05-24 feature wave)

The features below were built with their **decision logic unit-tested**
(235 passing tests), but the agent could not drive the live TUI or
exercise some Perforce flows in its environment. This is the list of
checks **you** should run on a real terminal + server to confirm UI
rendering, keybindings, and the live `p4` round trips.

## Setup

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt   # if not already
python -m pytest tests/ -q            # expect: all green, 2 skipped (write-gated)
python p4v.py                         # drive the app for the checks below
```

Run the live write-path tests too (creates + deletes one probe CL per
backend): `PYTEST_ALLOW_WRITES=1 python -m pytest -q`.

---

## Priority A — live Perforce round trips (highest risk)

> The two ⚠ items here (3-way merge, permalink move-following) have now been
> **verified against real Perforce and fixed** — both were broken. Only the
> end-to-end TUI gestures remain to click through; details inline below.

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
  - Remaining manual step: just the end-to-end TUI gesture (Ctrl+E in the
    Resolve modal → pick sides → Enter).

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
    covered by `tests/test_move_following.py`. The remaining manual step
    is only the end-to-end TUI gesture (Alt+C → move → Ctrl+G toast).

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
    open-for-add (new file) case; the open-for-**edit** on a synced
    read-only copy is the only sub-case still relying on the manual
    machine-B gesture above. If reconcile ever misbehaves, the file still
    saved — just `p4 reconcile shared-state/...` by hand before submitting.

- [ ] **Submit guards** — on a CL with (a) an unresolved file, (b) a file
  ≥ 25 MB, (c) no files: press `Ctrl+S`. The confirm dialog should list
  the matching ⛔/⚠ warnings. Confirm a clean CL shows none.

- [ ] **Partial shelve** — Pending CL menu → Shelve. Picker lists open
  files (all checked). Uncheck some, Enter → only the checked files are
  shelved (`p4 describe -S <CL>`). Leaving all checked == shelve-all.

- [ ] **Tree multi-select bulk** — `Space` to mark several files (glyph
  appears), then `e` / `r` / `a` (workspace) → all marked files are
  checked out / reverted / added **in one numbered CL**. On the depot
  tree, mark + Get Latest / Mark for Delete via the menu. `Esc` clears
  marks. Bulk revert prompts **once** with the full list.

- [ ] **Jira at submit** — set `[jira] base_url` (and optionally
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

## Priority B — UI rendering + keybindings

- [ ] **Command palette gone** — `Ctrl+P` no longer opens the Textual
  palette; inside Fast Search it walks query history.
- [ ] **Backend in title bar** — the Header shows `P4Python` or `p4 CLI`
  (force the other with `P4V_BACKEND=cli|python` and re-check).
- [ ] **Go-to-path** — `Ctrl+G`, paste a depot path (`//…`) and a local
  absolute path; both expand + highlight the right node.
- [ ] **Bookmarks** — `Ctrl+B` on a node ("Bookmarked…" toast),
  `Ctrl+Shift+B` opens the picker; `Enter` jumps + highlights, `d`/`Del`
  removes, `Esc` closes. Confirm it persists across restarts
  (`~/.p4v-tui/bookmarks.json`).
- [ ] **Fast Search row actions** — in `Ctrl+F`, move focus to the results
  list, `d` diffs the hit vs have, `g` get-latests it, `Ctrl+Enter`
  opens the viewer. (Single letters only fire when the query Input is not
  focused — by design.)
- [ ] **Mark glyph rendering** — the `●` marker shows on marked nodes and
  survives expanding/collapsing the subtree; `Esc` clears it.
- [ ] **Merge editor layout** — hunk list + detail render legibly; no
  Textual render hang (it uses OptionList + Static, not a fresh RichLog).
- [ ] **Narrow-terminal page navigator** — on a phone / `< 100`-col
  terminal, the layout collapses to one full-screen page at a time
  (tree / Pending / History / Submitted / Log). Full checklist in
  **`docs/narrow-terminal-scenario.md`** — verify **`Tab`/`Shift+Tab`**
  cycle all five pages (the phone-reliable driver; `Ctrl+→`/`Ctrl+←` are
  a desktop-only alias — mobile terminals don't send Ctrl+Arrow),
  `F3`/`Ctrl+W` quick-toggle tree ⇄ last panel, `Backspace` returns to
  tree, the tree/tables get the full height (no squashing Log strip),
  and the Log page is full-screen + live.

## Notes

- Anything that fails here is most likely in the **app wiring / live p4
  flags**, not the pure cores (those are covered by `tests/`). Start by
  reproducing with a single `p4` command on the CLI to isolate.
- Both Priority-A ⚠ items (3-way **merge** and permalink **move-following**)
  are now **verified against real Perforce and fixed** — see their entries
  above. The pure cores plus the real-output contract tests
  (`test_merge3.py::TestRealPerforceMarkers`, `test_move_following.py`)
  guard against regressions; only the end-to-end TUI gestures remain as a
  manual smoke check.
