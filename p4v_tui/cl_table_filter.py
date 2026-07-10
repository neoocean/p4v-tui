"""Pure filter + sort logic for the Pending / Submitted CL tables.

p4v lets you filter and sort the changelist lists by user / workspace /
date / description. The TUI's tables previously rendered whatever order
``p4 changes`` returned (with a local-then-remote grouping for Pending).
This module adds a small, fully-testable view model that the app applies
to the raw ``p4 changes`` row dicts *before* rendering — so no extra
server round trips, and the decision logic stays out of the Textual
wiring.

A row dict carries the keys ``p4 changes`` yields: ``change`` (str/int),
``user``, ``time`` (unix epoch str), ``desc``, and ``client`` (the owning
workspace; present on Pending rows). Filtering and sorting only ever read
those keys, never the server.

Path filtering is intentionally omitted: it would require a ``describe``
per CL (the table only holds the description, not the file list), which
defeats the "no extra round trips" goal. The filters offered here all
work off data already in hand.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime

# Sort keys. "default" preserves the caller's incoming order (server order
# for Submitted, local-first grouping for Pending) — the historical
# behaviour, kept as the no-op default so an unconfigured table looks
# exactly as it always did.
SORT_KEYS = ("default", "change", "user", "date", "desc", "workspace")


@dataclass
class CLTableView:
    """User-configurable filter + sort state for one CL table.

    All fields default to the inert state (no filtering, default order),
    so ``CLTableView()`` is the "show everything as before" view.
    """

    sort_key: str = "default"
    descending: bool = True
    user: str = ""        # case-insensitive substring on the CL owner
    workspace: str = ""   # case-insensitive substring on owning client
    text: str = ""        # case-insensitive substring on description
    regex: str = ""       # regex search on description (invalid → ignored)
    date_from: str = ""   # inclusive lower bound, "YYYY-MM-DD"
    date_to: str = ""     # inclusive upper bound, "YYYY-MM-DD"

    def is_active(self) -> bool:
        """True iff this view changes anything vs. the raw default."""
        return (
            self.sort_key != "default"
            or bool(self.user or self.workspace or self.text or self.regex)
            or bool(self.date_from or self.date_to)
        )

    def has_filter(self) -> bool:
        return bool(
            self.user or self.workspace or self.text
            or self.regex or self.date_from or self.date_to
        )

    def summary(self) -> str:
        """Short human-readable description for a status line / toast."""
        bits: list[str] = []
        if self.sort_key != "default":
            arrow = "↓" if self.descending else "↑"
            bits.append(f"sort:{self.sort_key}{arrow}")
        if self.user:
            bits.append(f"user~{self.user}")
        if self.workspace:
            bits.append(f"ws~{self.workspace}")
        if self.text:
            bits.append(f"text~{self.text}")
        if self.regex:
            bits.append(f"/{self.regex}/")
        if self.date_from:
            bits.append(f"≥{self.date_from}")
        if self.date_to:
            bits.append(f"≤{self.date_to}")
        return ", ".join(bits) if bits else "(none)"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> "CLTableView":
        """Reconstruct from persisted state, ignoring unknown / bad keys."""
        if not isinstance(data, dict):
            return cls()
        kwargs = {}
        for f in cls.__dataclass_fields__:  # type: ignore[attr-defined]
            if f in data:
                val = data[f]
                if f == "descending":
                    kwargs[f] = bool(val)
                else:
                    kwargs[f] = str(val)
        view = cls(**kwargs)
        if view.sort_key not in SORT_KEYS:
            view.sort_key = "default"
        return view


def _epoch_to_date(epoch: object) -> str:
    try:
        return datetime.fromtimestamp(int(str(epoch))).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""


def _matches(row: dict, view: CLTableView, _regex) -> bool:
    desc = str(row.get("desc", "") or "")
    if view.user and view.user.lower() not in str(row.get("user", "")).lower():
        return False
    if view.workspace:
        client = str(row.get("client", "") or "")
        if view.workspace.lower() not in client.lower():
            return False
    if view.text and view.text.lower() not in desc.lower():
        return False
    if _regex is not None and not _regex.search(desc):
        return False
    if view.date_from or view.date_to:
        d = _epoch_to_date(row.get("time", ""))
        # A row with no parseable time is dropped only when a bound is set
        # and it can't be compared — conservative: keep it out of a dated
        # filter rather than show an undateable row.
        if not d:
            return False
        if view.date_from and d < view.date_from:
            return False
        if view.date_to and d > view.date_to:
            return False
    return True


def _sort_value(row: dict, key: str):
    if key == "change":
        try:
            return int(str(row.get("change", "0")))
        except ValueError:
            return 0
    if key == "user":
        return str(row.get("user", "")).lower()
    if key == "date":
        try:
            return int(str(row.get("time", "0")))
        except ValueError:
            return 0
    if key == "desc":
        return str(row.get("desc", "")).lower()
    if key == "workspace":
        return str(row.get("client", "")).lower()
    return 0


def apply_view(rows: list[dict], view: CLTableView) -> list[dict]:
    """Return a filtered + sorted copy of ``rows`` per ``view``.

    Filtering always applies; sorting only when ``sort_key != "default"``
    (otherwise the incoming order — server order / local-first grouping —
    is preserved). An invalid regex is treated as "no regex filter" so a
    half-typed pattern never empties the table.
    """
    compiled = None
    if view.regex:
        try:
            compiled = re.compile(view.regex, re.IGNORECASE)
        except re.error:
            compiled = None

    out = [r for r in rows if _matches(r, view, compiled)]

    if view.sort_key != "default":
        out.sort(
            key=lambda r: _sort_value(r, view.sort_key),
            reverse=view.descending,
        )
    return out
