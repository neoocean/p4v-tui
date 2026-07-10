"""Pure navigation logic for the narrow-terminal page navigator.

On a phone-sized portrait viewport (iPhone Blink etc.) p4v-tui can't
show the tree, the changelist/history tables, the detail pane and the
Log panel side by side — there isn't the width *or* the height. Instead
of cramming everything in (which left the tree at 2-3 visible rows and
the Log panel eating ~40% of the screen), narrow mode shows exactly one
full-screen "page" at a time and lets the user flip between them.

This module is the pure decision core — no Textual, no I/O — so the
sequencing rules are unit-testable in isolation. ``app.py`` owns the
actual widget show/hide wiring and calls these helpers to decide *which*
page to land on for a given gesture.

Pages, in cycle order::

    tree → pending → history → submitted → log → (wrap to tree)

* ``tree`` shows the left pane (Depot / Workspace inner tabs).
* ``pending`` / ``history`` / ``submitted`` each show the matching
  right-pane table full-screen (the detail pane + Log are hidden so the
  table fills the viewport).
* ``log`` shows the Log panel full-screen.
"""

from __future__ import annotations

# Ordered page identifiers. The order IS the Ctrl+→ / Ctrl+← cycle.
NARROW_PAGES: tuple[str, ...] = (
    "tree",
    "pending",
    "history",
    "submitted",
    "log",
)

# The three pages that map onto a right-pane TabbedContent tab.
PANEL_PAGES: frozenset[str] = frozenset({"pending", "history", "submitted"})

# Pages that can never be dropped from the cycle, no matter what the user
# disables or which tables are empty. ``tree`` is the home page and ``log``
# is the only place background p4 / job output is visible in narrow mode —
# hiding either would strand the user, so they're always reachable.
ALWAYS_ON_PAGES: frozenset[str] = frozenset({"tree", "log"})

# page id  <->  right_tabs TabPane id
_PAGE_TO_RIGHT_TAB = {
    "pending": "tab_pending",
    "history": "tab_history",
    "submitted": "tab_submitted",
}
_RIGHT_TAB_TO_PAGE = {v: k for k, v in _PAGE_TO_RIGHT_TAB.items()}

# Fallback used whenever we need "some panel page" but don't have a
# better one (e.g. first flip away from the tree with no history).
DEFAULT_PANEL_PAGE = "pending"

# Human-facing label for each page, shown in the narrow-mode breadcrumb.
# Kept short so the whole strip fits an ~80-cell phone line.
PAGE_LABELS: dict[str, str] = {
    "tree": "tree",
    "pending": "pending",
    "history": "history",
    "submitted": "submitted",
    "log": "log",
}


# Layout-mode pin values. ``auto`` decides narrow vs wide from the
# terminal width; ``narrow`` / ``wide`` force the choice regardless of
# width (e.g. a thin-but-wide tmux pane where the user wants the
# single-page navigator anyway, or a desktop user who never does).
LAYOUT_MODES: tuple[str, ...] = ("auto", "narrow", "wide")


def normalize_page(page: str | None) -> str:
    """Coerce an arbitrary value to a valid page id (``tree`` if unknown)."""
    return page if page in NARROW_PAGES else "tree"


def normalize_layout_mode(mode: str | None) -> str:
    """Coerce an arbitrary value to a valid layout mode (``auto`` default)."""
    return mode if mode in LAYOUT_MODES else "auto"


def resolve_narrow_mode(mode: str | None, width: int, threshold: int) -> bool:
    """Decide whether narrow mode is on, honouring the layout pin.

    ``auto`` falls back to the width rule (``width < threshold``);
    ``narrow`` / ``wide`` pin the answer regardless of width. Keeping
    this pure means the pin logic is unit-testable without a terminal.
    """
    m = normalize_layout_mode(mode)
    if m == "narrow":
        return True
    if m == "wide":
        return False
    return width < threshold


def cycle_layout_mode(mode: str | None) -> str:
    """Next layout mode in the ``auto → narrow → wide → auto`` cycle."""
    m = normalize_layout_mode(mode)
    return LAYOUT_MODES[(LAYOUT_MODES.index(m) + 1) % len(LAYOUT_MODES)]


def effective_pages(
    disabled: "frozenset[str] | set[str] | tuple[str, ...] | None" = None,
    empty: "frozenset[str] | set[str] | tuple[str, ...] | None" = None,
) -> tuple[str, ...]:
    """The page cycle with user-disabled and empty panel pages removed.

    The navigator's *visible* cycle isn't always the full
    :data:`NARROW_PAGES`. A mobile user who never opens Submitted /
    History shouldn't have to ``Tab`` through them to reach the Log
    (``disabled``), and a panel page whose table has no rows this
    session is just dead space to step over (``empty``).

    ``tree`` and ``log`` (:data:`ALWAYS_ON_PAGES`) are never dropped, so
    the result always has at least those two and the user can never be
    stranded. Order is preserved from :data:`NARROW_PAGES`.
    """
    drop = set(disabled or ()) | set(empty or ())
    drop -= ALWAYS_ON_PAGES
    return tuple(p for p in NARROW_PAGES if p not in drop)


def _resolve(current: str | None, pages: "tuple[str, ...]") -> str:
    """Return ``current`` if it's in ``pages``, else the first page."""
    if current in pages:
        return current  # type: ignore[return-value]
    return pages[0] if pages else "tree"


def cycle_page(
    current: str | None,
    delta: int,
    pages: "tuple[str, ...] | None" = None,
) -> str:
    """Return the page ``delta`` steps from ``current`` (wraps both ways).

    ``delta`` is normally +1 (Ctrl+→ / Tab) or -1 (Ctrl+← / Shift+Tab)
    but any integer works — the index wraps modulo the page count.

    ``pages`` is the *effective* cycle (see :func:`effective_pages`);
    it defaults to the full :data:`NARROW_PAGES`. A ``current`` that has
    fallen off the effective list (e.g. its table just went empty)
    resolves to the first page so the next step is still well-defined.
    """
    pages = pages or NARROW_PAGES
    cur = _resolve(current, pages)
    idx = pages.index(cur)
    return pages[(idx + delta) % len(pages)]


def is_panel_page(page: str | None) -> bool:
    """True when ``page`` corresponds to a right-pane table."""
    return normalize_page(page) in PANEL_PAGES


def right_tab_for_page(page: str | None) -> str | None:
    """The right_tabs TabPane id for a panel page, else ``None``."""
    return _PAGE_TO_RIGHT_TAB.get(normalize_page(page))


def page_for_right_tab(tab_id: str | None) -> str | None:
    """The page id for a right_tabs TabPane id, else ``None``."""
    return _RIGHT_TAB_TO_PAGE.get(tab_id or "")


def toggle_target(
    current: str | None,
    last_panel: str | None,
    pages: "tuple[str, ...] | None" = None,
) -> str:
    """Where F3 / Ctrl+W should jump.

    The quick-toggle flips between the tree and "the panels". From the
    tree we go to the most recently visited non-tree page (so the user's
    place is preserved); if that page isn't in the effective cycle
    anymore we fall back to Pending, or — if Pending itself is disabled
    — the first non-tree page that survives. From any non-tree page we
    return to the tree.
    """
    pages = pages or NARROW_PAGES
    cur = _resolve(current, pages)
    if cur != "tree":
        return "tree"
    if last_panel and last_panel != "tree" and last_panel in pages:
        return last_panel
    if DEFAULT_PANEL_PAGE in pages:
        return DEFAULT_PANEL_PAGE
    for p in pages:
        if p != "tree":
            return p
    return "tree"


def jump_target_by_index(
    n: int,
    pages: "tuple[str, ...] | None" = None,
) -> str | None:
    """Resolve a 1-based position in the effective cycle to a page id.

    Backs the number-key direct jump: pressing ``3`` on a phone goes
    straight to the third page shown in the breadcrumb, instead of
    walking there with repeated ``Tab``. Position-based (not a fixed
    key→page map) so it always matches the *visible* order even when
    pages are trimmed — ``3`` is whatever the breadcrumb's third chip
    is. Out-of-range / non-int returns ``None`` (the keystroke is a
    no-op rather than an error).
    """
    pages = pages or NARROW_PAGES
    if not isinstance(n, int) or n < 1 or n > len(pages):
        return None
    return pages[n - 1]


def breadcrumb_segments(
    current: str | None,
    pages: "tuple[str, ...] | None" = None,
) -> list[tuple[str, bool]]:
    """``(label, is_current)`` pairs for the narrow-mode page indicator.

    With one full-screen page visible and no docked chrome, the user has
    no cue of *where they are* in the cycle. This renders the effective
    cycle (see :func:`effective_pages`) as an ordered list so the app can
    draw a breadcrumb with the current page highlighted. ``current`` is
    resolved into ``pages`` first, so a stale page still highlights
    something sensible (the first page) rather than nothing.
    """
    pages = pages or NARROW_PAGES
    cur = _resolve(current, pages)
    return [(PAGE_LABELS.get(p, p), p == cur) for p in pages]


def _breadcrumb_chip_text(idx: int, label: str, is_cur: bool,
                          numbered: bool, compact: bool) -> str:
    """The plain (markup-free) text of one breadcrumb chip."""
    if compact and not is_cur:
        # Compact: non-current chips collapse to just their jump number.
        return str(idx)
    return f"{idx} {label}" if numbered else label


def _breadcrumb_plain_width(segs, sep: str, numbered: bool,
                            compact: bool) -> int:
    """Rendered cell width of the breadcrumb (markup excluded, current
    chip's reverse-highlight padding included). Labels are ASCII so a
    character count is an accurate cell count here."""
    parts = []
    for idx, (label, is_cur) in enumerate(segs, start=1):
        text = _breadcrumb_chip_text(idx, label, is_cur, numbered, compact)
        if is_cur:
            text = f" {text} "  # the " X " reverse-highlight padding
        parts.append(text)
    return len(sep.join(parts))


def render_breadcrumb(
    current: str | None,
    pages: "tuple[str, ...] | None" = None,
    sep: str = " · ",
    numbered: bool = False,
    width: int | None = None,
) -> str:
    """Rich-markup breadcrumb string, current page reverse-highlighted.

    Pure (returns a string) so it's unit-testable without a terminal.
    Non-current labels are dimmed; the current one is bold + reversed so
    it reads as "you are here" even on a monochrome mobile terminal.

    When ``numbered`` is set, each label is prefixed with its 1-based
    position (``1 tree · 2 pending · …``) so the number-key direct jump
    (:func:`jump_target_by_index`) is self-documenting — the digit to
    press is right there in the chip.

    ``width`` makes it fit a phone in portrait: if the full strip would
    overflow the given cell width, it falls back to a **compact** form
    where only the current page keeps its label and the others collapse
    to bare jump numbers (``1 · 2 · 3 · 4 submitted · 5``). This keeps
    "you are here" + every jump target visible without the right-hand
    chips (notably ``5 log``) being clipped off the edge. ``None`` (the
    default) always renders the full form.
    """
    segs = breadcrumb_segments(current, pages)
    compact = (
        width is not None
        and _breadcrumb_plain_width(segs, sep, numbered, False) > width
    )
    out: list[str] = []
    for idx, (label, is_cur) in enumerate(segs, start=1):
        text = _breadcrumb_chip_text(idx, label, is_cur, numbered, compact)
        if is_cur:
            out.append(f"[b reverse] {text} [/]")
        else:
            out.append(f"[dim]{text}[/]")
    return sep.join(out)


# --- responsive table columns ---------------------------------------------
#
# Each right-pane table shows a *full* column set in the wide layout and a
# trimmed one in narrow mode, so the Description (the column you actually
# read) fits an ~80-cell phone line instead of being scrolled off the right
# edge. Columns are addressed by stable field keys; the render code builds a
# ``{field: cell}`` map per row and ``select_cells`` picks the active
# profile's cells in order. The first field is always ``change`` /
# ``rev``-style identity so ``str(row[0])`` cursor-restore keeps working.

# Display headers for every field key any table can show.
FIELD_HEADERS: dict[str, str] = {
    "change": "Change",
    "workspace": "Workspace",
    "user": "User",
    "date": "Date",
    "desc": "Description",
    "rev": "Rev",
    "action": "Action",
}

# Per-table field order: wide (full) vs narrow (trimmed to fit a phone).
# History has two schemas — per-file (filelog: Rev/Action are per-row) and
# per-folder (changes -L: those columns don't exist).
TABLE_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "pending": {
        "wide": ("change", "workspace", "user", "date", "desc"),
        "narrow": ("change", "desc"),
    },
    "submitted": {
        "wide": ("change", "user", "date", "desc"),
        "narrow": ("change", "desc"),
    },
    "history_file": {
        "wide": ("rev", "change", "action", "date", "user", "desc"),
        "narrow": ("rev", "action", "desc"),
    },
    "history_folder": {
        "wide": ("change", "date", "user", "desc"),
        "narrow": ("change", "desc"),
    },
}


def column_fields(table: str, narrow: bool) -> tuple[str, ...]:
    """Ordered field keys for ``table`` in the given layout.

    Unknown table names fall back to an empty tuple rather than raising,
    so a typo can't crash a render.
    """
    prof = TABLE_FIELDS.get(table)
    if not prof:
        return ()
    return prof["narrow"] if narrow else prof["wide"]


def column_headers(table: str, narrow: bool) -> tuple[str, ...]:
    """Display headers (``add_columns`` args) for ``table`` + layout."""
    return tuple(
        FIELD_HEADERS.get(f, f) for f in column_fields(table, narrow)
    )


def select_cells(
    table: str,
    narrow: bool,
    cells_by_field: dict,
) -> list:
    """Pick a row's cells for the active profile, in column order.

    ``cells_by_field`` maps every field the table *could* show to its
    cell value (str or a styled ``rich.text.Text``); the caller builds
    it once per row and this drops/reorders to the active profile. A
    field missing from the map yields ``""`` so a partial row can't
    throw a ``KeyError`` mid-render.
    """
    return [
        cells_by_field.get(f, "") for f in column_fields(table, narrow)
    ]


def _footer_hint_specs(
    page: str | None,
    n_pages: int = len(NARROW_PAGES),
) -> list[tuple[str, str, int]]:
    """``(key, label, priority)`` hints for ``page``; lower priority =
    more important (kept first when width is tight).

    Priorities: the page navigator (``Tab``) and the exit (``q``) are
    most important; the page's primary action next; then search; the
    ``1-N jump`` and ``⌫ tree`` hints are most droppable because the
    numbered breadcrumb and ``Tab`` already cover that ground.
    """
    page = normalize_page(page)
    specs: list[tuple[str, str, int]] = [("Tab", "pages", 0)]
    if n_pages >= 2:
        specs.append((f"1-{n_pages}", "jump", 4))
    if page == "tree":
        specs += [("↵", "open", 2), ("^F", "search", 3)]
    elif page in PANEL_PAGES:
        specs += [("↵", "detail", 2), ("m", "menu", 2)]
        if page == "pending":
            specs.append(("^S", "submit", 2))
    elif page == "log":
        specs.append(("↵", "detail", 2))
    if page != "tree":
        specs.append(("⌫", "tree", 4))
    specs.append(("q", "quit", 1))
    return specs


def footer_hints(
    page: str | None,
    n_pages: int = len(NARROW_PAGES),
) -> list[tuple[str, str]]:
    """Curated ``(key, label)`` key hints for the current narrow page.

    The default Textual ``Footer`` lists *every* app binding (~20), which
    truncates to a useless prefix at 80 cells and mostly shows keys
    irrelevant to the page you're on. This returns the short, relevant
    set instead: the universal navigator keys plus a couple specific to
    the page (open / search on the tree, the row menu + submit on panel
    pages, …). ``n_pages`` is the effective cycle length so the jump
    hint shows the real range (``1-3`` when pages are trimmed).
    """
    return [(k, lbl) for (k, lbl, _p) in _footer_hint_specs(page, n_pages)]


def _fit_footer_hints(
    specs: list[tuple[str, str, int]],
    sep: str,
    width: int,
) -> list[tuple[str, str, int]]:
    """Drop the least-important hints until the strip fits ``width``.

    Adds hints in importance order (priority asc, then display order) and
    **stops at the first one that doesn't fit**, so the survivors are
    always a strict by-importance prefix — a low-priority hint can never
    sneak in past a higher-priority one that was dropped (e.g. keeping
    ``⌫ tree`` while dropping ``^S submit``). Survivors are returned in
    *display* order so the bar's layout stays stable. Labels/keys are
    ASCII so a character count is an accurate cell count.
    """
    def total(idxs) -> int:
        return len(sep.join(
            f"{specs[i][0]} {specs[i][1]}" for i in sorted(idxs)))

    by_importance = sorted(range(len(specs)), key=lambda i: (specs[i][2], i))
    kept: set[int] = set()
    for i in by_importance:
        if total(kept | {i}) <= width:
            kept.add(i)
        else:
            break
    return [specs[i] for i in range(len(specs)) if i in kept]


def render_footer_hints(
    page: str | None,
    n_pages: int = len(NARROW_PAGES),
    sep: str = "  ",
    width: int | None = None,
) -> str:
    """Rich-markup key-hint strip for the narrow-mode footer.

    Pure (returns a string) so it's unit-testable. Each hint renders as
    a bold key followed by a dim label, e.g. ``[b]Tab[/] [dim]pages[/]``.

    ``width`` makes it fit a phone: when the full set would overflow, the
    least-important hints (``1-N jump``, ``⌫ tree`` first) are dropped so
    the bar never clips mid-word (``q quit`` → ``q``). ``None`` renders
    the full set.
    """
    specs = _footer_hint_specs(page, n_pages)
    if width is not None:
        specs = _fit_footer_hints(specs, sep, width)
    parts = [f"[b]{key}[/] [dim]{label}[/]" for (key, label, _p) in specs]
    return sep.join(parts)
