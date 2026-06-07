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

## Controls

| Key | Narrow mode | Wide mode |
|---|---|---|
| `Tab` | **Next page** (tree → pending → history → submitted → log → tree) | Focus next pane (curated chain) |
| `Shift+Tab` | **Previous page** (wraps the other way) | Focus previous pane |
| `F3` / `Ctrl+W` | Quick-toggle: tree ⇄ the **last-visited** panel page (defaults to Pending the first time) | Focus the right pane |
| `Backspace` | Return to the `tree` page from anywhere | (consumed by focused widget) |
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

## Layout rules (what `_apply_pane_visibility` enforces)

1. **Wide mode** (`narrow_mode == False`): both panes, detail pane, and
   the Log panel are all visible and restored to their persisted
   heights/width. Leaving narrow mode resets `narrow_page` to `tree` so
   a stale `log`/`submitted` page can't strand the Log panel at `1fr` or
   leave a pane hidden in the wide layout.
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
