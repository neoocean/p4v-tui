# p4v-tui — a connection-resilient Perforce TUI client

> **Language:** English (this page) · [한국어](README.ko.md)

A terminal-UI Perforce (Helix) client built on
[Textual](https://textual.textualize.io/). It covers p4v's daily
workflow (get / edit / submit / revert / reconcile / history) with a
keyboard-first interface, and is designed to **stay reliable on slow or
flaky networks**.

![p4v-tui main screen — tree on the left, changelist tables and detail pane on the right, Log at the bottom](docs/image/01-overview.svg)

Where p4v holds one long call open and makes you start over when the
link drops, p4v-tui splits every long operation into small chunks and
persists progress to disk — quit mid-sync and the next launch resumes
exactly where it stopped.

- Single Python entry point; no daemon, no plugins
- Usable on narrow terminals like iPhone Blink at 80 columns
  (automatic single-page narrow mode); short viewports auto-collapse
  the bottom Log panel (command history stays on `F2`)
- Single-letter shortcuts keep working with the Korean Hangul IME on
  (2-beolsik jamo aliases)

> 📖 **The full manual — per-screen screenshots and the complete
> keyboard reference — is in [`docs/MANUAL.md`](docs/MANUAL.md)
> (Korean).** Architecture and the complete p4v feature-coverage
> matrix are in [`DESIGN.md`](DESIGN.md) (English).

## Why

Built by a daily p4v user to fix the paper cuts p4v wouldn't, and to
own a foundation new features can keep landing on:

- **View a text file instantly** — p4v detours through an external
  viewer; here `Enter` on a tree leaf opens a full-screen popup
  immediately (chunk-rendered up to 5 MB, syntax-highlighted).
- **Huge operations that never wedge the UI** — syncs / reconciles /
  cleans over tens of thousands of files run as chunks with on-disk
  progress; interactive commands interleave between chunks.
- **Clean shutdown** — quitting mid-job finishes only the current
  chunk; the next launch offers a resume/discard picker.
- **CJK-friendly** — Hangul-IME shortcut aliases, display-cell-aware
  truncation, Korean descriptions render correctly.

## Tour

| | |
|---|---|
| ![Pending — cross-workspace CLs with the ↗ marker](docs/image/02-pending.svg) | ![Fast Search — instant results with preview](docs/image/09-fast-search.svg) |
| **Cross-workspace pending CLs** — every unsubmitted CL of yours, across all your workspaces, on one screen; remote ones carry a `↗` marker. | **Fast Search** (`Ctrl+F`) — local SQLite index, results as you type, live preview with match highlighting. |
| ![Workspace tree — status markers](docs/image/06-workspace-tree.svg) | ![In-app file viewer](docs/image/07-file-viewer.svg) |
| **Workspace tree** — one-glyph status markers (`e`/`+`/`*`/…) per file. | **File viewer** — `Enter` on a leaf; opens on the right half with syntax highlighting. |

More screens (Depot tree · Submitted/History · Command Monitor ·
Go-to-path · narrow mode · context menus) are walked through in
[`docs/MANUAL.md`](docs/MANUAL.md).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # textual>=8, (optional) p4python
python p4v.py
```

- One Perforce backend is enough: **P4Python or the `p4` CLI**
  (auto-selected; force with `P4V_BACKEND=python|cli`).
- Configuration is **optional** — with no `p4v-tui.toml` the app rides
  on your P4 environment / `P4CONFIG`. Multiple `[[profile]]` entries
  get a picker at startup. ⚠️ `[[macro]]` / `[[editor]]` blocks run
  arbitrary local commands — **never run an untrusted `p4v-tui.toml`**
  (see [`docs/security-audit.md`](docs/security-audit.md)).
- Put the bundled `p4v` wrapper on PATH
  (`ln -sf "$PWD/p4v" ~/.local/bin/p4v`) to launch from anywhere; it
  bootstraps `.venv` automatically when missing.

## Feature highlights

Focused on what stock p4v does *not* give you. The complete 1:1
coverage matrix lives in [`DESIGN.md`](DESIGN.md) § "p4v Feature
Coverage"; usage details in [`docs/MANUAL.md`](docs/MANUAL.md).

- **① Connection resilience** — auto-reconnect with backoff, chunked +
  resumable sync / revert / reconcile / clean, lost-ack idempotent
  submit, a resume picker for interrupted jobs, configurable chunking
  strategy.
- **② Operational visibility** — Command Monitor (`F2`, parent/child
  command tree with ETA) and a scrollable, timestamped Log panel.
- **③ Pending workflow, hardened** — cross-workspace pending CLs with
  local/remote action gating, 30 s auto-refresh (latency-adaptive),
  default-changelist isolation (every file-opening action lands in a
  fresh numbered CL).
- **④ Search / navigation** — Fast Search (`Ctrl+F`: filename, `?`
  content grep, `cl:` descriptions, `nl:` natural language), Go-to-path
  (`Ctrl+G`), Find File, tree clipboard (`Ctrl+C/X/V` = p4 copy/move +
  auto-submit), move-tracking permalinks (`Alt+C`) and bookmarks
  (`Ctrl+B`).
- **⑤ Small screens / CJK / keyboard** — single-page narrow-mode
  navigator (phone / thin tmux pane), Hangul jamo shortcut aliases,
  CJK-width-aware tables.
- **⑥ A workspace that remembers itself** — pane sizes, active tabs,
  focus, and sort orders persist across launches (`state.json`).
- **⑦ p4v parity plus guardrails** — file viewer, Get Revision dialog,
  Swarm URLs, full Resolve/Shelve cycle (in-app 3-way merge editor +
  external merge tool), Annotate / Time-lapse / Revision Graph / Undo /
  File Properties / Tag with Label, side-by-side & arbitrary diff,
  pre-submit guards (unresolved / oversized / empty CL) and Jira issue
  linkage at submit.

## Deployment — running it on another machine

This is an **interactive terminal app**, not a daemon: "deploying" it
means users SSH into that machine and run it.

**Runtime prerequisites**

- Python **3.12+** (uses `tomllib`).
- ONE Perforce backend: P4Python (`pip install p4python`) **or** the
  `p4` CLI on PATH. Auto-selected; locked-down or exotic architectures
  where the wheel won't build simply fall back to the CLI backend (no
  compiler needed).
- A real **TTY** and a **UTF-8 locale** (`LANG`/`LC_ALL=*.UTF-8`).
  Below 100 columns the narrow mode engages automatically.
- A **writable HOME** — runtime state goes to `~/.p4v-tui/`
  (state.json, sync-state/, index/*.sqlite, last-error.log).
- Network reachability to p4d (default 1666), plus the Swarm host if
  you use the Swarm URL features.

**Four steps**

```bash
git clone <repo-url> && cd p4v-tui
cp p4v-tui.toml.example p4v-tui.toml   # optional — P4 env/P4CONFIG works too
p4 login                                # authenticate OUTSIDE the app (below)
./p4v                                   # auto-bootstrapping venv wrapper
```

**Authentication is deliberately outside the app.** There is no in-app
login / SSO / MFA / ticket UI — the security boundary stays in one
well-understood place, the `p4` CLI. Have a valid `p4 login` ticket (or
`~/.p4tickets` / P4 env) before launching; the CLI backend runs with
`stdin=DEVNULL` so an expired ticket fails fast instead of hanging. For
unattended machines, a long-lived ticket / service account becomes that
machine's real secret — manage it outside the repository.

## Limits and non-goals

The big picture (full matrix in [`DESIGN.md`](DESIGN.md), inverse view
in [`docs/p4v-feature-gaps.md`](docs/p4v-feature-gaps.md)):

- **Everything operational + inspectional is covered in-app** — the
  daily loop, resolve/shelve cycles, annotate / time-lapse / revision
  graph / undo, diffs, Get Revision, Find File / Fast Search,
  Preferences, Open With….
- **Admin / metadata editors are deliberately out of scope** —
  workspaces, branch mappings, streams, jobs, users/groups, triggers,
  the label editor: spec-editing surfaces belong to the canonical
  `p4` CLI.
- **No auth UI by design** (see Deployment above).
- **Terminal-intrinsic limits** — ANSI-art image preview instead of
  pixels, text-mode revision graph / time-lapse, no path filter on the
  CL tables (description-based filtering only).

## License

MIT — see [`LICENSE`](LICENSE).
