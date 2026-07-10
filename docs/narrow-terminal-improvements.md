# Improving p4v-tui for narrow / remote terminals

A forward-looking improvement plan for the **mobile / thin-pane remote**
use case: driving p4v-tui from iPhone Blink, an SSH session off a phone
keyboard, or a thin tmux split. The north star for this scenario:

> Show **only the one panel that matters right now**, fill the whole
> viewport with it, and let the user move between panels **freely and
> predictably** with keys that actually reach the app on a mobile
> terminal.

This doc is the *plan*. The current behaviour and its rationale live in
[`narrow-terminal-scenario.md`](narrow-terminal-scenario.md); the pure
sequencing core is `p4v_tui/narrow_nav.py`
(tested in `tests/test_narrow_nav.py`); the widget show/hide wiring is
`P4VApp._apply_pane_visibility` + the focus-follow invariant in
`on_descendant_focus`. Read those first — everything below builds on them
and points at the module to touch.

---

## Shipped so far (CLs 58761–58766)

The P0 cluster plus the two cheap P2 wins are **done** — see the
status column in the priority table at the bottom and the
[scenario doc](narrow-terminal-scenario.md) for the as-built behaviour.
One thing the work *surfaced* that wasn't in the original plan:

> **Bug found + fixed: the `Tab` page cycle never actually fired.**
> Textual's `Screen` binds `tab`/`shift+tab` to `app.focus_next`, and
> the screen sits below the app in binding resolution — so the app's
> `tab → smart_tab` binding was shadowed. The "primary, phone-reliable"
> driver silently did nothing. `P4VApp.on_key` now intercepts `Tab` (and
> the new digit jump) in narrow mode. Caught only because the P0 work
> added the first headless pilot e2e tests for the navigator
> (`tests/test_e2e_narrow.py`).

Also: **P0.4 shipped as a number-key jump, not a `g`-chord** — `g` is
already bound in the workspace tree (chunked sync) and the search modal,
so a `g`-leader would collide. Bare digits are unbound everywhere and
reachable from a phone keyboard, and they pair with the now-numbered
breadcrumb (the digit to press is the chip's number).

---

## Where we are today (the foundation is good)

The single-page navigator already nails the core idea:

- Auto-enters narrow mode below `NARROW_TERMINAL_WIDTH = 100` cells; a
  separate `SHORT_TERMINAL_HEIGHT = 45` rule collapses the Log strip in
  the wide layout. Both re-evaluated on every `on_resize`.
- One full-screen page at a time, cycle order
  `tree → pending → history → submitted → log → (wrap)`.
- `Tab` / `Shift+Tab` is the primary, **phone-reliable** page cycle
  (Blink emits `Tab`; it does *not* emit `Ctrl+Arrow` escape sequences).
  `F3` / `Ctrl+W` quick-toggle tree ⇄ last panel; `Backspace` jumps home
  to the tree.
- Focus-follow invariant keeps `narrow_page` in sync with whatever gets
  focused, so a stray Tab or click can't focus a widget on a hidden page.

What follows are the gaps a real phone session still hits — grouped by
priority for this scenario, not by effort.

---

## P0 — orientation & reach (the daily friction)

### P0.1 Persistent page indicator / breadcrumb

**Problem.** With exactly one full-screen page visible and no docked
chrome, the user has *no on-screen cue* of which page they're on or how
many exist. They navigate from memory. On a phone, where you can't see
the wide layout for reference, this is the single biggest disorientation.

**Proposal.** A one-row breadcrumb (only rendered in narrow mode) showing
the cycle with the current page highlighted:

```
  tree · ‹pending› · history · submitted · log
```

Put it in the existing `Footer` region or a thin `Static` above `#main`.
Drive it from `watch_narrow_page`; the labels come straight from
`narrow_nav.NARROW_PAGES`. Hide empty pages' labels (see P0.3) so the
breadcrumb mirrors the *real* cycle. Cheap, pure-presentation, and it
makes every other navigation improvement legible.

### P0.2 Curated, page-aware footer hints

**Problem.** `Footer()` lists the full class-level `BINDINGS` set
(~20 entries). At 80 cells it truncates to a useless prefix, and most
bindings (`Ctrl+Shift+V` paste-permalink, `[`/`]` pane resize) are
irrelevant on the current page.

**Proposal.** In narrow mode, swap the default footer for a curated set
keyed off `narrow_page` — e.g. on a panel page show only
`Tab pages · m menu · Enter open · / filter · q quit`. Textual supports
per-screen/dynamic bindings; simplest path is a small custom footer
widget fed by a `_narrow_footer_hints(page)` pure helper (testable,
sits next to `narrow_nav`).

### P0.3 Skip empty / disabled pages in the cycle

**Problem.** The cycle always walks all five pages. A mobile user who
never opens Submitted/History still tabs through empty tables to reach
Log. `cycle_page` is fixed-arity over `NARROW_PAGES`.

**Proposal.** Make the cycle operate over an *effective* page list:
1. drop pages the user disabled in config (`[narrow] pages = [...]`), and
2. optionally skip pages whose table is empty this session
   (`skip_empty = true`).

Refactor `narrow_nav.cycle_page` / `toggle_target` to take the effective
list as an argument (keep `NARROW_PAGES` as the default) so the pure
tests just parametrize the list. `tree` and `log` are never droppable.

### P0.4 Direct page jump (no O(n) tabbing)

**Problem.** Reaching `log` from `tree` is four `Tab`s. There's no direct
jump, and single number keys can't be stolen (they're typed into
filters / inputs).

**Proposal.** A `g`-prefixed chord (vim-style, mobile-friendly, no escape
sequences): `g` then `t/p/h/s/l` jumps to tree/pending/history/submitted/
log. Implement as a tiny key-sequence state on the app (a pending-`g`
flag cleared on the next key or a short timeout) routed through the
existing action layer; the target resolution is a pure
`narrow_nav.jump_target(key)` map. Falls back to no-op if the page is
disabled (P0.3).

---

## P1 — fitting content into the viewport

### P1.1 Responsive table columns

**Problem.** `pending_table` has 5 columns
(`Change, Workspace, User, Date, Description`), `history_table` 6. They're
`HScrollDataTable`s, so wide content scrolls horizontally — but on 80
cells the *Description* (the column you actually read) is pushed off the
right edge and the user must horizontal-scroll every row.

**Proposal.** A narrow column profile per table: hide/merge low-value
columns when `narrow_mode` (e.g. Pending → `Change · Description`, drop
Workspace/User/Date or fold them into a dim suffix). Define the profiles
as data (a `{table_id: [cols]}` map, wide vs narrow) and rebuild columns
on mode change. The full detail stays one `Enter` away in the modal.
Keep horizontal scroll as the escape hatch.

### P1.2 Reclaim header / connection-bar rows on short viewports

**Problem.** On a ~25-row phone, `Header(show_clock=True)` + `ConnectionBar`
+ `Footer` consume ~3–4 rows of chrome before any content. The connection
bar (`Server / User / Workspace / Root`) is reference info you rarely need
mid-task.

**Proposal.** In narrow (or `short_mode`) collapse the connection bar to a
single compact token in the header (or hide it, surfaced on demand via an
info key / the `i`nfo on the tree). Consider `show_clock=False` in narrow
mode. Each row reclaimed is a row of tree/table.

### P1.3 Audit every modal for an 80×25 viewport

**Problem.** Modals (`FileViewerModal`, menus, Preferences, pickers) were
sized for the desktop layout. On a phone a fixed-width/centered modal can
overflow or clip its action buttons below the fold. Note the standing
Textual trap (see `CLAUDE.md` / `docs/MEMORY.md`): a fresh `ModalScreen`
wrapping a `RichLog` hangs — subclass `FileViewerModal`.

**Proposal.** A pass that makes modals full-bleed (`width: 100%; height:
100%`) and internally scrollable below a width threshold, with the
primary action reachable without scrolling. Add one smoke check per modal
to `handoff-manual-tests.md`.

### P1.4 On-demand detail page

**Problem.** The inline detail pane (CL files + description) is correctly
hidden in narrow mode, but its content is only reachable as a transient
modal via `Enter`. There's no way to *navigate* into a CL's file list as
a first-class page and back.

**Proposal.** A contextual `detail` page that joins the cycle only when a
CL row is selected (insert after the originating panel page), showing the
description + `detail_files` table full-screen. `Backspace` returns to the
panel page, not all the way to the tree. Keeps the cycle minimal when
there's nothing to detail.

---

## P2 — environment fit & polish

### P2.1 Manual narrow-mode pin

**Problem.** Mode is a pure function of width. A user on a wide-but-thin
tmux pane (≥100 cells but they *want* single-page), or who prefers the
navigator on a desktop, can't opt in; and someone on a borderline width
gets flapping on resize.

**Proposal.** A tri-state preference `[ui] layout = auto | narrow | wide`
(persisted, togglable with a binding). `auto` keeps today's threshold
behaviour; `narrow`/`wide` pin it. Decouples the *layout* decision from
the raw terminal width.

### P2.2 Preserve narrow_page across mode flips (rotation)

**Problem.** `watch_narrow_mode` resets `narrow_page` to `tree` whenever
narrow mode is left (correct, to avoid stranding the Log at `1fr` in the
wide layout). But on a **phone rotation** (portrait→landscape→portrait)
this silently loses the user's place every time they rotate.

**Proposal.** Remember the last narrow page in a separate field when
leaving narrow mode and restore it (instead of `tree`) when re-entering —
distinct from the wide-layout reset, which still resets the *visible*
widgets. Small change in the two watchers; covered by extending the
focus-follow tests.

### P2.3 Terminal capability awareness

**Problem.** Remote terminals vary: Blink does mouse + truecolor; a bare
`ssh` from some clients does neither; tmux can mangle key passthrough.
Today the experience assumes a capable terminal.

**Proposal.** Document the verified-good matrix (Blink, tmux, mosh,
plain ssh) in the scenario doc, and degrade gracefully where detectable
(don't rely on mouse for any narrow-mode action — already mostly true;
ensure no action is mouse-only). Confirm the `Ctrl+W` / `F3` / `Tab` /
`Backspace` set works under tmux's default prefix (`Ctrl+W` is fine;
`Ctrl+B`-prefixed tmux won't intercept it).

### P2.4 Latency-visible "working" affordance

**Problem.** Resilience (chunking, resume, non-blocking commands) is the
project's headline strength, but on a laggy mobile link the user needs to
*see* that a keystroke registered and work is in flight, or they'll
double-press.

**Proposal.** A lightweight in-flight indicator in the header/breadcrumb
row (spinner + short label) driven off the existing `JobRunner` /
`on_job_progress` signal, prominent in narrow mode where the Log page
isn't visible.

---

## Cross-cutting

**Config.** The shipped work added a small `[narrow]` config surface:
`disabled_pages`, `skip_empty`, `layout` (all parsed in
`config._parse_narrow`, validated against `narrow_nav`). Route any new
knobs through there with sane defaults so the zero-config phone
experience is unchanged.

**Keep the core pure.** Every navigation/visibility decision above should
land in `narrow_nav.py` (or a sibling pure helper) as a function the app
calls — never inline branching in `app.py`. That's the pattern that made
the current navigator unit-testable; preserve it. New pure helpers →
new cases in `tests/test_narrow_nav.py`.

**Manual smoke checks.** Each shipped item adds a checkbox to the
"Manual smoke checks" list in `narrow-terminal-scenario.md` and, where it
touches modals/wiring, to `docs/handoff-manual-tests.md` — driven on a
real `<100`-col terminal (the pure logic is already covered by tests).

---

## Priority summary

| # | Improvement | Status | Why it matters for phone/remote | Touches |
|---|---|---|---|---|
| P0.1 | Page-indicator breadcrumb | ✅ CL 58762 | Orientation — *where am I* | `_update_breadcrumb`, `render_breadcrumb` |
| P0.2 | Page-aware footer hints | ✅ CL 58765 | Usable key hints at 80 cells | `_update_footer`, `footer_hints` |
| P0.3 | Skip empty/disabled pages | ✅ CL 58761 | Don't tab through dead pages | `effective_pages`, `[narrow]` config |
| P0.4 | Number-key direct jump | ✅ CL 58764 | O(1) reach, no escape seqs | `jump_target_by_index`, `on_key` |
| P1.1 | Responsive table columns | ✅ CL 58769 | Read the Description, not scroll | `TABLE_FIELDS`, `_set_table_columns`, `select_cells` |
| P1.2 | Reclaim chrome rows | ⏳ planned | More content on 25-row screens | `compose` / `ConnectionBar` |
| P1.3 | Modal viewport audit | ⏳ planned | Modals usable on 80×25 | widgets/ modals |
| P1.4 | On-demand detail page | ⏳ planned | Navigate into a CL, not just modal | navigator + `_apply_pane_visibility` |
| P2.1 | Manual narrow-mode pin | ✅ CL 58766 | Thin-but-wide panes, no flapping | `resolve_narrow_mode`, `Ctrl+Shift+N` |
| P2.2 | Preserve page across rotation | ✅ CL 58763 | Don't lose place on rotate | `watch_narrow_mode` |
| P2.3 | Terminal capability matrix | ⏳ planned | Works across remote clients | docs + degrade |
| P2.4 | In-flight indicator | ⏳ planned | See that laggy keystrokes landed | header + JobRunner signal |

P0 is the highest-leverage cluster: it makes the existing navigator
*legible* (you can see where you are, what the keys do, and jump
directly) without changing the underlying model — **all of P0 is now
shipped**, along with the two cheap P2 wins (pin + rotation). P1 makes
each page actually fit a phone; P2's remaining items broaden the range
of remote environments and add polish.

### P1.1 (responsive columns) — shipped CL 58769

Landed as a pure column-profile system: `narrow_nav.TABLE_FIELDS`
defines wide vs narrow field sets per table; `column_headers` /
`select_cells` are unit-tested; the renders build a `{field: cell}` map
and select the active profile. `_set_table_columns` rebuilds columns
lazily (only on a real schema change) and `_rerender_tables_for_mode`
re-renders from cached rows on a narrow⇄wide flip. The cell-0 identity
invariant is preserved (the remote `↗` marker moves to the Description
cell in narrow). The headless e2e asserts column counts, cell-0
integrity, and live re-render — but **column legibility / auto-sizing at
80 cells is verified on a real terminal** (the one thing the pilot
can't judge); that's the open manual check in the scenario doc.
