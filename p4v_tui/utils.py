"""Small shared helpers."""
from __future__ import annotations

from rich.cells import cell_len, set_cell_size


def first_nonblank_line(text: str) -> str:
    """Return the first non-blank line of ``text`` (stripped), or ``""``.

    `p4 changes -L` returns descriptions that often start with a leading
    newline, so a naive ``.splitlines()[0]`` yields an empty string and the
    Description column ends up blank for those rows.
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def format_eta(seconds: float | int | None) -> str:
    """Render an ETA in seconds as a compact human string.

    None → ``""`` (no estimate). Negative or absurd values are also empty.
    Otherwise we use ``Ns`` under a minute, ``Nm Ns`` under an hour, and
    ``Nh Nm`` beyond — chosen to keep the JobStatusBar readable at a glance
    without dropping precision when an op is genuinely short.
    """
    if seconds is None:
        return ""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return ""
    if s < 0 or s > 86400:  # >1 day → almost certainly unreliable estimate
        return ""
    s_int = int(round(s))
    if s_int < 60:
        return f"{s_int}s"
    if s_int < 3600:
        return f"{s_int // 60}m {s_int % 60}s"
    hours = s_int // 3600
    minutes = (s_int % 3600) // 60
    return f"{hours}h {minutes}m"


def truncate_cells(text: str, max_cells: int, ellipsis: str = "…") -> str:
    """Truncate ``text`` so it fits within ``max_cells`` display columns.

    Uses ``rich.cells`` so CJK / emoji widths are counted correctly. Without
    this, slicing by character count silently overflows panel boundaries:
    a 10-char Korean string occupies 20 cells, and slicing to ``[:80]``
    produces a 160-cell string that no narrow column can contain.
    """
    if max_cells <= 0:
        return ""
    width = cell_len(text)
    if width <= max_cells:
        return text
    ell_width = cell_len(ellipsis)
    if max_cells <= ell_width:
        return ellipsis[:max_cells]
    return set_cell_size(text, max_cells - ell_width).rstrip() + ellipsis
