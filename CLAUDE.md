# CLAUDE.md — agent orientation for p4v-tui

A Textual-based terminal UI for Perforce (Helix). Primary value:
*connection resilience* (auto-reconnect, chunked + resumable bulk ops,
non-blocking interactive commands). Secondary: cover p4v's daily-developer
surface. Single entry point, no daemon/plugins.

Read this first, then reach for the deeper docs as needed:
- `README.md` — user-facing overview + deploy guide (English);
  `README.ko.md` is the Korean original (keep the two in sync).
- `DESIGN.md` — architecture, full p4v feature-coverage matrix, CL history.
- `docs/MEMORY.md` — **non-obvious gotchas; read before touching the CLI
  backend, Textual modals, or TOML/spec-form parsing.**

## Build / run / test

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # runtime
pip install -r requirements-dev.txt      # pytest (NOT installed by default)
python p4v.py                            # run the app
python -m pytest tests/ -q               # unit + live (live auto-skips if no server)
```

Live-write tests are gated: `PYTEST_ALLOW_WRITES=1 pytest`. Lint with
`ruff check p4v_tui/ --select F` (catches unused imports + undefined names).

## Version control — Perforce, NOT git

This tree is tracked in **Perforce** (`//depot/p4v-tui/...`),
not git. The harness `git` probe reports "not a git repo" — that's
expected. Commits are numbered CLs via `p4`. The local `.gitignore`/
`LICENSE` exist only to prepare for a future public git release.

**Critical: the `admin@shared` client is shared by concurrent
sessions.** Files opened into the *default* changelist can be swept into
another session's submit. Always isolate your work in a numbered CL:

```bash
CL=$(printf 'Change: new\n\nDescription:\n\t<what + why>\n' | p4 change -i | grep -oE '[0-9]+')
p4 edit -c "$CL" <existing-file>     # or: p4 add -c "$CL" <new-file>
p4 submit -c "$CL"
```

Never `p4 submit -d` the default changelist. Re-check `p4 opened` before
relying on a file you opened in a previous turn.

## Backends

Perforce access goes through a pluggable backend in `p4v_tui/p4client.py`:
`_PythonBackend` (P4Python, preferred) or `_CLIBackend` (`p4` CLI fallback),
auto-selected, override with `P4V_BACKEND=python|cli`. Both are covered by
parity tests in `tests/test_p4client_live.py`.

## Code layout

```
p4v.py                  # entry point (friendly missing-dep handling)
p4v_tui/
  app.py                # P4VApp (Textual) — core lifecycle/state + actions
  app_shared.py         # layout constants, pure helpers, ConnectionBar
  app_menus.py          # _MenuMixin + build_pending_menu (pure, tested)
  app_details.py        # _DetailMixin (pending detail pane / Enter popup)
  app_diffrev.py        # _DiffRevMixin (Get Revision / diff / cross-CL move)
  p4client.py           # P4Service façade + _PythonBackend / _CLIBackend
  jobs.py chunking.py sync_job.py bulk_jobs.py submit_job.py  # resilience
  submit_guards.py      # pre-submit checks (pure, tested)
  jira.py               # Jira key detect / browse URL (pure, [jira] config)
  search_index.py search_jobs.py                              # Fast Search
  path_nav.py permalink.py bookmarks.py   # Go-to-path / permalink / bookmarks (pure)
  merge3.py             # 3-way conflict-marker parse + reconstruct (pure)
  config.py state.py cmd_log.py utils.py messages.py
  widgets/              # modals, trees, panels
tests/                  # pytest — pure-logic units + live-server parity
```

`P4VApp` was a 6000-line god class, now split into mixins
(`_MenuMixin`, `_DetailMixin`, `_DiffRevMixin`). When extracting another
cluster: move methods verbatim into a new `app_<area>.py` mixin
(shared module-level names go in `app_shared.py` to avoid an import
cycle), add it to P4VApp's bases (`class P4VApp(_XxxMixin, …, App)`),
then verify behaviour-preserving: `ruff --fix --select F401` →
`pyflakes` (zero undefined names) → **diff `dir(P4VApp)` against a
pre-move baseline (must be unchanged)** → `pytest`. The new feature
modules above keep their decision logic pure + unit-tested; the app
does the Perforce/Textual wiring. Full recipe + traps in `docs/MEMORY.md`.

Live-verification debt: largely burned down. Both the permalink `filelog`
move-following AND the 3-way merge `resolve -am`/`-af` flow were verified
against real renames/conflicts on both backends — and both turned out to
be *broken*, now fixed (see `tests/test_move_following.py`,
`tests/test_merge3.py::TestRealPerforceMarkers`, and
`docs/handoff-manual-tests.md`). Those two TUI gestures are ALSO
automated now (`tests/test_e2e_gestures.py`, CL 57328). What remains
manual is the short list in `docs/handoff-manual-tests.md` /
`docs/roadmap-2026-07.md` §3: live submit-guard warnings, partial
shelve, tree bulk ops, the shared-state machine-B read-only-edit
reconcile, and the visual/real-device checks (merge-editor legibility,
phone narrow layout).

## Textual gotcha (from docs/MEMORY.md — load-bearing)

Do **not** write a fresh `ModalScreen` that wraps a `RichLog` — it hits a
Textual 8.x `render_strips`-returns-None hang. Subclass `FileViewerModal`
(the one proven-good combination) instead. See `docs/MEMORY.md` for the
full list of traps.
