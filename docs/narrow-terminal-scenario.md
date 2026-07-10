# Narrow-terminal navigation scenario

How p4v-tui behaves on a phone-sized portrait viewport (iPhone Blink,
SSH from a mobile keyboard, a thin split pane) — the design, the control
scheme, and the manual smoke checks.

Pure sequencing logic lives in `p4v_tui/narrow_nav.py` (unit-tested in
`tests/test_narrow_nav.py`); the widget show/hide wiring is in
`P4VApp._apply_pane_visibility` and the narrow-mode actions in `app.py`.

---

## The problem

The wide layout shows four things at once: the tree (left), the
CL/History tables (right-top), the detail pane (right-bottom), and the
Log panel docked across the bottom. On a phone there is neither the
width nor the height for that.

The old narrow mode collapsed to **two** states — *tree* and a
*panels overlay* — but kept the Log panel docked under the tree at its
fixed ~10-row height. On a ~25-row phone screen that left the tree with
2-3 visible rows (see the regression screenshot that motivated this
work), and the Log strip plus the always-present right-pane tabs made
the CL/History tables feel unreachable.

## The model: one full-screen page at a time

Narrow mode (auto-entered when the terminal is narrower than
`NARROW_TERMINAL_WIDTH = 100` cells) is a **single-page navigator**.
Exactly one "page" fills the whole viewport; there is no docked Log
strip and no inline detail pane stealing rows.

Pages, in cycle order:

```
 tree  →  pending  →  history  →  submitted  →  log  →  (wrap to tree)
```

| Page | Fills the screen with | Notes |
|---|---|---|
| `tree` | Left pane (Depot / Workspace inner tabs) | Depot ↔ Workspace still swap via the inner `TabbedContent` tabs. Full height — no Log strip. |
| `pending` | Pending Changelists table | |
| `history` | History table | |
| `submitted` | Submitted Changelists table | |
| `log` | Log panel | The only place the Log is shown in narrow mode; it gets the full height (`#main` collapses, the sibling Log panel takes the `1fr`). |

On every non-`log` page the **detail pane is hidden** (its inline CL
preview isn't worth the rows on a phone — `Enter` still opens the
read-only `FileViewerModal` for a row), and the **Log panel is hidden**
(any background p4 / job feedback is one keystroke away on the `log`
page). On the three panel pages the right-pane `TabbedContent` is
switched to the matching tab so the correct table is on screen.

### Page indicator (breadcrumb)

With one full-screen page visible and no docked chrome, the user has no
cue of *where they are* in the cycle. A one-row breadcrumb
(`#narrow_breadcrumb`, shown only in narrow mode, just under the
connection bar) renders the effective cycle with each chip **numbered**
and the current page reverse-highlighted:

```
  1 tree · 2 pending ·  3 history  · 4 submitted · 5 log
```

The numbers double as the direct-jump keys (press `3` → History), so the
breadcrumb is self-documenting. The markup is built by the pure
`narrow_nav.render_breadcrumb(..., numbered=True, width=…)` (so it follows
`effective_pages` and is unit-tested without a terminal);
`P4VApp._update_breadcrumb` shows/hides + refreshes it on every
visibility pass. It stays visible even on the full-screen `log` page so
orientation is never lost.

**Width-adaptive (real-device fix).** On a phone in portrait (~46 cols)
the full strip overflows and the last chip (`5 log`) was clipped off the
edge — losing the knowledge that Log exists and its jump number. So
`render_breadcrumb` takes the available width and, when the full labels
won't fit, falls back to a **compact** form: only the current page keeps
its label, the rest collapse to bare jump numbers —
`1 · 2 · 3 · 4 submitted · 5`. "You are here" plus every jump target stay
visible at any width; full labels return automatically when there's room
(landscape, a tablet, a wide split).

### Responsive table columns

The right-pane tables carry more columns than fit an 80-cell line
(Pending: Change · Workspace · User · Date · Description), so the
Description — the column you actually read — used to be scrolled off the
right edge on a phone. In narrow mode each table trims to a **subset**
profile that keeps Description plus an identity column:

| Table | Wide | Narrow |
|---|---|---|
| Pending | Change · Workspace · User · Date · Description | **Change · Description** |
| Submitted | Change · User · Date · Description | **Change · Description** |
| History (file) | Rev · Change · Action · Date · User · Description | **Rev · Action · Description** |
| History (folder) | Change · Date · User · Description | **Change · Description** |

The profiles are pure data in `narrow_nav.TABLE_FIELDS` (+
`column_headers` / `select_cells`, unit-tested); the render methods build
a `{field: cell}` map per row and select the active profile's cells.
Columns are rebuilt lazily by `P4VApp._set_table_columns` (only when the
schema actually changes), and a narrow⇄wide flip re-renders from cached
rows via `_rerender_tables_for_mode` — no server round-trip.

Two invariants: **column 0 stays the plain identity cell** (`str(row[0])`
must equal the change/rev for cursor-restore + menu lookups), so on a
remote Pending CL the `↗` "lives elsewhere" marker — which rides the
Workspace cell in the wide layout — **moves onto the Description cell** in
narrow (Workspace is dropped) rather than polluting column 0.

### Page-aware footer hints

The default Textual `Footer` lists *every* app binding (~20), which
truncates to a useless prefix at 80 cells. In narrow mode it's hidden
and replaced by `#narrow_footer` — a one-row, page-relevant hint strip
built by the pure `narrow_nav.render_footer_hints(page, n_pages, width=…)`
(universal navigator keys + a couple specific to the page: open/search
on the tree, the row `m`enu + `^S` submit on Pending, …). The jump hint
shows the real range (`1-3` when pages are trimmed). `P4VApp._update_footer`
swaps the two on every visibility pass.

**Width-adaptive (real-device fix, same as the breadcrumb).** At ~46 cols
the full hint set overflowed and clipped mid-word (`q quit` → `q`). Each
hint now carries a priority; when the strip won't fit, the
least-important hints are dropped — **as a strict by-importance prefix**,
so a low-value hint (`1-N jump`, `⌫ tree`) can never survive past a
higher-value one (`^S submit`) that was dropped. `Tab` (the navigator)
and `q` (the exit) are the last to go, and the bar never clips a word.

### Pinning the layout (config + runtime)

By default narrow vs wide is decided purely from terminal width
(`< NARROW_TERMINAL_WIDTH`). That's wrong for a **thin-but-wide** tmux
pane (≥100 cells but the user wants the single-page navigator anyway),
or a borderline width that flaps on every resize. The `[narrow] layout`
pin overrides the width rule: `auto` (default), `narrow`, or `wide`.
It's seeded from config and **runtime-togglable with `Ctrl+Shift+N`**
(cycles `auto → narrow → wide`, with a toast). The pure
`narrow_nav.resolve_narrow_mode(mode, width, threshold)` makes the
decision; `P4VApp._recompute_narrow_mode` applies it from `on_mount`,
`on_resize`, and the toggle, so a pin can't be undone by a stray resize.

### Trimming the cycle (config)

The *visible* cycle isn't always all five pages. The navigator walks an
**effective** page list computed by `narrow_nav.effective_pages()` from
two inputs (app side: `P4VApp._effective_narrow_pages`):

- `[narrow] disabled_pages` in the TOML config — panel pages
  (`pending` / `history` / `submitted`) the user never wants on a phone.
- `[narrow] skip_empty = true` — additionally skips a panel page whose
  table has no rows this session (re-included the moment it gains rows;
  the live row count is read by `P4VApp._empty_panel_pages`).

`tree` and `log` (`narrow_nav.ALWAYS_ON_PAGES`) are never droppable, so
the cycle always has at least those two and the user can't be stranded.
A `narrow_page` that falls off the effective list (e.g. its table just
went empty) resolves to the first page on the next `Tab` rather than
raising.

## Controls

| Key | Narrow mode | Wide mode |
|---|---|---|
| `Tab` | **Next page** (tree → pending → history → submitted → log → tree) | Focus next pane (curated chain) |
| `Shift+Tab` | **Previous page** (wraps the other way) | Focus previous pane |
| `F3` / `Ctrl+W` | Quick-toggle: tree ⇄ the **last-visited** panel page (defaults to Pending the first time) | Focus the right pane |
| `Backspace` | Return to the `tree` page from anywhere | (consumed by focused widget) |
| `1`…`9` | **Jump** straight to that position in the cycle (the breadcrumb numbers each chip, so the digit to press is visible) | (typed into focused widget) |
| `Ctrl+→` / `Ctrl+←` | Next / previous page — **desktop alias** for `Tab` / `Shift+Tab` | Next / previous right-pane tab |

### Why `Tab` is the primary driver (not `Ctrl+Arrow`)

`Tab` / `Shift+Tab` is the page cycle because it's the one key that
**reliably reaches the app on a phone**: iPhone Blink (and most mobile
terminals) expose a `Tab` key in the on-screen accessory bar but do
**not** emit the `Ctrl+Arrow` escape sequences (`\x1b[1;5C` etc.) that
`Ctrl+→` / `Ctrl+←` need. So `Ctrl+Arrow` silently does nothing there —
it's kept only as a desktop-terminal alias. `Ctrl+W` (vim / tmux
"switch window") and `F3` both work on Blink and stay as the fast
tree ⇄ panel toggle; `Backspace` jumps straight back to the tree.

Design rationale for the two-tier scheme: `Tab` / `Shift+Tab` is the
**complete** walk (reach any screen, including Log, with one repeated
key); `F3` / `Ctrl+W` is the **fast** path for the common "pop over to my
CLs and come straight back to the tree" round trip without stepping
through History / Submitted / Log.

> **Implementation note — why `Tab` is intercepted in `on_key`, not just
> bound.** Textual's `Screen` binds `tab` / `shift+tab` to
> `app.focus_next` / `app.focus_previous`, and the screen sits *below*
> the app in the binding-resolution chain — so a plain app-level
> `tab → smart_tab` binding is **shadowed and never fires** (the whole
> point of the narrow navigator silently did nothing before this was
> found via the e2e pilot). `P4VApp.on_key` therefore intercepts the raw
> `Tab` before the default focus binding runs, but only in narrow mode,
> only on the base screen (`len(screen_stack) == 1`, so modals keep their
> own Tab handling), and never when a text `Input` is focused (so the
> tree-filter overlay / search boxes still tab between fields). Wide mode
> deliberately falls through to Textual's default focus traversal.

## Short terminals (wide layout, few rows)

Width and height are handled independently. The page navigator above is
the **width** story (`< NARROW_TERMINAL_WIDTH = 100` cells). There is a
separate **height** rule for the wide layout: when the terminal is
shorter than `SHORT_TERMINAL_HEIGHT = 30` rows (`short_mode`), the
bottom Log panel (+ its drag splitter) is collapsed out so the ~10-row
Log strip doesn't crowd the tree / tables. The command history stays
reachable via **F2** (Command Monitor); grow the terminal back over the
threshold and the Log panel returns at its persisted height. `short_mode`
is a no-op in narrow mode — there the navigator already owns the Log via
its dedicated `log` page. Both thresholds are re-evaluated on every
`on_resize` (and once in `on_mount` for the startup size).

## Layout rules (what `_apply_pane_visibility` enforces)

1. **Wide mode** (`narrow_mode == False`): both panes, detail pane, and
   the Log panel are all visible and restored to their persisted
   heights/width. Leaving narrow mode resets `narrow_page` to `tree` so
   a stale `log`/`submitted` page can't strand the Log panel at `1fr` or
   leave a pane hidden in the wide layout — but it first saves the page
   into `_narrow_resume_page`, and **re-entering** narrow restores that
   page instead of `tree`. So a phone rotation
   (portrait → landscape → portrait) keeps the user where they were
   rather than dumping them back on the tree each time.
2. **Narrow, `tree` / panel page**: `#main` visible, Log panel + its
   splitter hidden, detail pane + its splitter hidden. Exactly one of
   left/right pane is shown at `1fr`.
3. **Narrow, `log` page**: `#main` hidden entirely; the Log panel (a
   sibling of `#main`, not a child) expands to `1fr` and takes focus.
4. `_apply_persisted_pane_sizes` does **not** write the persisted
   wide-mode Log height while narrow — the navigator owns the Log
   panel's height there.

## Focus-follow invariant

`on_descendant_focus` and `action_smart_tab` both keep `narrow_page` in
sync with whatever just got focus:

- focus lands in the left pane → page `tree`;
- focus lands on a right-pane table → the matching panel page
  (`pending_table` → `pending`, etc.);
- focus lands on the Log panel → page `log`.

So a stray Tab or a click can never focus a widget that lives on a
hidden page — the page flips first.

---

## Manual smoke checks (phone or `< 100`-col terminal)

Drive these on a real narrow terminal; the pure sequencing is already
covered by `tests/test_narrow_nav.py`, so anything that fails here is in
the app wiring, not the core.

- [ ] **Resize across the threshold** — shrink the terminal under 100
  cols: the layout collapses to the `tree` page, the tree fills the
  height (no Log strip beneath it), the detail pane is gone. Grow back
  over 100 cols: the full four-region wide layout returns with the
  persisted pane sizes.
- [ ] **`Tab` walks every page (phone-critical)** — from `tree`,
  repeated `Tab` visits Pending → History → Submitted → Log → back to
  tree, each full-screen. `Shift+Tab` walks the reverse and wraps. This
  is *the* check on a real phone (iPhone Blink etc.) — confirm `Tab`
  from the accessory bar drives it.
- [ ] **`Ctrl+→` / `Ctrl+←` (desktop terminals only)** — on a terminal
  that emits Ctrl+Arrow, these mirror `Tab` / `Shift+Tab`. Expected to
  be inert on mobile keyboards — that's why `Tab` is primary.
- [ ] **`F3` / `Ctrl+W` quick-toggle** — from the tree it jumps to the
  last panel page you were on (Pending on a fresh start); pressing it
  again returns to the tree. Confirm it remembers (e.g. visit Submitted,
  go to tree, `Ctrl+W` lands back on Submitted).
- [ ] **`Backspace` from any page** returns to the tree (when focus is
  on a table/log, not inside a text Input).
- [ ] **Log page is full-screen and live** — on the `log` page the Log
  panel fills the viewport and shows new p4/job entries; `↑`/`↓` walk
  entries, `Enter` opens the entry detail (as in wide mode).
- [ ] **Tables are usable** — on each panel page the table shows its
  column headers + multiple rows (not collapsed to 0 rows), row cursor
  moves, `m` opens the row menu, `Enter` opens the read-only detail
  viewer.
- [ ] **Responsive columns** — in narrow mode Pending/Submitted show
  just **Change · Description** (Description fully visible, not scrolled
  off); History (file) shows **Rev · Action · Description**. A remote
  Pending CL shows the `↗` marker on the Description cell (not column 0).
  Grow back to wide and the full column set returns. Resizing across the
  threshold re-renders the columns without a refresh. *(This is the
  layout pass that needs eyeballing on a real ~80-col terminal — the
  e2e test asserts column counts + cell-0 integrity, not legibility.)*
