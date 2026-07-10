"""Parse ``p4 reconcile -n`` / ``p4 clean -n`` dry-run output (pure logic).

p4v opens an interactive Reconcile / Clean dialog that previews the
add/edit/delete set and lets the user check individual files. The TUI's
chunked Reconcile/Clean were all-or-nothing. This module turns a dry-run
tagged result into a flat, pickable list of entries — no Perforce or
Textual here, so the parsing is unit-testable against the row shapes both
backends produce.

A tagged dry-run row is a dict. The interesting ones carry an ``action``
(``add`` / ``edit`` / ``delete`` / ``move/*`` …) plus a ``clientFile``
and/or ``depotFile``. Informational rows (``{"code": "info", "data":
"... - no file(s) to reconcile"}``) and anything without an action are
dropped — they aren't operable files.

The *spec* used to run the real op is the ``clientFile`` when present,
falling back to ``depotFile``: a freshly-added local file has no depot
path yet, so the client path is the only handle that reconciles an add.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreviewEntry:
    action: str
    depot_file: str
    client_file: str

    @property
    def spec(self) -> str:
        """The path to hand the real reconcile/clean command.

        Client path first (handles adds with no depot path yet); depot
        path as the fallback for the rare row that only carries one.
        """
        return self.client_file or self.depot_file

    @property
    def display(self) -> str:
        """``edit    //depot/path`` style row for the picker label."""
        shown = self.depot_file or self.client_file
        return f"{self.action:<11} {shown}"


def parse_preview(rows: list) -> list[PreviewEntry]:
    """Flatten a dry-run tagged result into operable :class:`PreviewEntry`.

    Rows with no ``action`` or no usable path are skipped (info banners,
    "no file(s)" notices). Order is preserved so the picker mirrors what
    ``p4`` reported.
    """
    out: list[PreviewEntry] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        action = str(r.get("action", "") or "").strip()
        if not action:
            continue
        depot = str(r.get("depotFile", "") or "")
        client = str(r.get("clientFile", "") or "")
        if not (depot or client):
            continue
        out.append(PreviewEntry(action=action, depot_file=depot,
                                client_file=client))
    return out
