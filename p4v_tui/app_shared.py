"""Shared constants, pure helpers, and small widgets for the app layer.

Extracted from ``app.py`` so that the ``P4VApp`` mixins (``app_menus``,
etc.) can import these without creating an import cycle back through
``app.py``. Everything here is free of ``P4VApp`` dependencies.
"""
from __future__ import annotations

from textual.widgets import Static

from .p4client import P4Info


# Left-pane width in display cells. 60 cells (1.5x of the original 40) gives
# enough room for typical workspace paths without crowding the right pane.
DEFAULT_LEFT_WIDTH = 60
MIN_LEFT_WIDTH = 24
MAX_LEFT_WIDTH = 120
LEFT_WIDTH_STEP = 4

# Detail pane (CL files / desc) inside the right pane. Default is
# a third of a 45-row terminal — leaves the tabs region with the
# remaining 1fr above. Min keeps the table + at least one header
# row visible; max prevents the tables above from disappearing.
DEFAULT_DETAIL_HEIGHT = 15
MIN_DETAIL_HEIGHT = 5
MAX_DETAIL_HEIGHT = 40

# Log panel at the bottom of the whole screen.
MIN_LOG_HEIGHT = 4
MAX_LOG_HEIGHT = 30

# Default interval (seconds) for the auto-refresh of the Pending
# Changelists tab. Tradeoff: short enough to catch a teammate
# creating a CL on this client within a workable lag, long enough
# to not hammer the server. Persisted in state.json under
# ``auto_refresh_pending_seconds``; 0 disables the refresh entirely.
DEFAULT_AUTO_REFRESH_PENDING_SEC = 30

# When the terminal is narrower than this many cells we collapse the
# right pane and hand the full width to the tree. iPhone Blink in
# portrait mode lands around 80 cells; a threshold of 100 leaves room
# to manually toggle by resizing the window on a desktop.
NARROW_TERMINAL_WIDTH = 100


def _extract_qualifier(spec: str) -> str:
    """Pull the trailing ``#rev`` or ``@CL`` qualifier off a depot spec.

    Used by Arbitrary Diff to re-apply the user's qualifier to the
    per-pair file paths returned by ``p4 diff2``. Returns an empty
    string when there's no qualifier.

    Examples
    --------
    >>> _extract_qualifier("//depot/foo/bar.txt#5")
    '#5'
    >>> _extract_qualifier("//depot/foo/...@1234")
    '@1234'
    >>> _extract_qualifier("//depot/foo/...")
    ''
    """
    # ``#`` and ``@`` are the only two qualifier introducers in p4
    # filespec syntax. Pick the latest one (a spec rarely has both,
    # but if it does the rev wins over the CL).
    hash_at = max(spec.rfind("#"), spec.rfind("@"))
    if hash_at <= 0:
        return ""
    # Make sure the qualifier isn't part of the path itself.
    after = spec[hash_at:]
    if "/" in after:
        return ""
    return after


def _truncate_workspace(name: str, head: int = 6) -> str:
    """Cap a workspace name for compact column display.

    Names longer than ``head + 2`` cells are clipped to the first
    ``head`` characters followed by ``..`` — keeps the Pending table's
    Workspace column from being dragged out by an exceptionally long
    client like ``team-alpha-document-processor``. The full name
    stays in ``_pending_client_by_change`` so context menus and the
    remote-CL viewer can still show the unabbreviated string.
    """
    if not name or len(name) <= head + 2:
        return name
    return f"{name[:head]}.."


class ConnectionBar(Static):
    def update_info(self, info: P4Info) -> None:
        self.update(
            f" [b]Server:[/b] {info.port}    "
            f"[b]User:[/b] {info.user}    "
            f"[b]Workspace:[/b] {info.client}    "
            f"[b]Root:[/b] {info.client_root} "
        )
