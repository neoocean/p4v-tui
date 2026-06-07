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


def normalize_page(page: str | None) -> str:
    """Coerce an arbitrary value to a valid page id (``tree`` if unknown)."""
    return page if page in NARROW_PAGES else "tree"


def cycle_page(current: str | None, delta: int) -> str:
    """Return the page ``delta`` steps from ``current`` (wraps both ways).

    ``delta`` is normally +1 (Ctrl+→) or -1 (Ctrl+←) but any integer
    works — the index wraps modulo the page count.
    """
    cur = normalize_page(current)
    idx = NARROW_PAGES.index(cur)
    return NARROW_PAGES[(idx + delta) % len(NARROW_PAGES)]


def is_panel_page(page: str | None) -> bool:
    """True when ``page`` corresponds to a right-pane table."""
    return normalize_page(page) in PANEL_PAGES


def right_tab_for_page(page: str | None) -> str | None:
    """The right_tabs TabPane id for a panel page, else ``None``."""
    return _PAGE_TO_RIGHT_TAB.get(normalize_page(page))


def page_for_right_tab(tab_id: str | None) -> str | None:
    """The page id for a right_tabs TabPane id, else ``None``."""
    return _RIGHT_TAB_TO_PAGE.get(tab_id or "")


def toggle_target(current: str | None, last_panel: str | None) -> str:
    """Where F3 / Ctrl+W should jump.

    The quick-toggle flips between the tree and "the panels". From the
    tree we go to the most recently visited non-tree page (so the user's
    place is preserved); if there isn't one we default to Pending. From
    any non-tree page we return to the tree.
    """
    cur = normalize_page(current)
    if cur == "tree":
        target = normalize_page(last_panel)
        return target if target != "tree" else DEFAULT_PANEL_PAGE
    return "tree"
