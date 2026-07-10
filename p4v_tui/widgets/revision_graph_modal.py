"""Textual revision graph for a single file.

p4v's revision graph is a node-and-edge picture; in a TUI we render
the same data as an indented per-revision listing with integration
edges shown as arrows. ``p4 filelog -i`` returns each revision with
the depot paths and revision ranges it integrated from / to, so the
view can answer "where did this file branch from?" and "what merges
landed in rev N?" without leaving the terminal.

Layout per revision::

    rev #N   CL=12345   user   2026-05-08
      desc first line
      ↙ branch  from  //depot/main/foo.cpp#5
      ↙ merge   from  //depot/branch-B/foo.cpp#7..10
      ↗ copy    to    //depot/release/foo.cpp#1

The body is a plain RichLog — Esc / Backspace / q closes.
"""
from __future__ import annotations

from datetime import datetime

from rich.text import Text

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static


class RevisionGraphModal(ModalScreen[None]):
    DEFAULT_CSS = """
    RevisionGraphModal { align: center middle; }
    RevisionGraphModal > #dialog {
        width: 95%;
        height: 90%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    RevisionGraphModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    RevisionGraphModal #body {
        height: 1fr;
        background: $surface;
    }
    RevisionGraphModal #status {
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", priority=True),
        Binding("backspace", "cancel", "Close", priority=True),
        Binding("q", "cancel", "Close", priority=True),
        Binding("ㅂ", "cancel", "Close", priority=True),
    ]

    def __init__(self, depot_path: str, p4_service) -> None:
        super().__init__()
        self._depot_path = depot_path
        self._p4 = p4_service

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(
                f" Revision Graph · {self._depot_path} ", id="title",
            )
            yield RichLog(highlight=False, markup=False,
                          wrap=False, id="body")
            yield Static("  Loading…", id="status")

    def on_mount(self) -> None:
        self._fetch_and_render()

    @work(thread=True, group="revision_graph", exclusive=True)
    def _fetch_and_render(self) -> None:
        # `p4 filelog -i -l` follows integrations and returns the full
        # description per rev. -m caps history depth so a deep file
        # doesn't take forever to render.
        try:
            rows = self._p4.run(
                "filelog", "-i", "-l", "-m", "200",
                self._depot_path,
            )
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(
                self._render_error, f"filelog failed: {e}",
            )
            return
        if not rows or not isinstance(rows[0], dict):
            self.app.call_from_thread(
                self._render_empty,
            )
            return
        # Each row is one depot file with parallel arrays per revision.
        head = rows[0]
        revs = self._extract_revs(head)
        self.app.call_from_thread(self._render_revs, revs)

    @staticmethod
    def _extract_revs(head: dict) -> list[dict]:
        """Collapse filelog's parallel-array layout into per-revision
        dicts so rendering stays per-row instead of per-column."""
        revs_arr = head.get("rev") or []
        out: list[dict] = []
        for i, rev in enumerate(revs_arr):
            entry = {
                "rev":    str(rev),
                "change": _idx(head, "change", i),
                "user":   _idx(head, "user", i),
                "action": _idx(head, "action", i),
                "type":   _idx(head, "type", i),
                "time":   _idx(head, "time", i),
                "desc":   _idx(head, "desc", i),
            }
            # Integration edges arrive as nested arrays (one row per
            # rev, each row a list of strings).
            entry["how"]  = _idx_list(head, "how",  i)
            entry["file"] = _idx_list(head, "file", i)
            entry["srev"] = _idx_list(head, "srev", i)
            entry["erev"] = _idx_list(head, "erev", i)
            out.append(entry)
        return out

    def _render_revs(self, revs: list[dict]) -> None:
        try:
            body = self.query_one("#body", RichLog)
            status = self.query_one("#status", Static)
        except Exception:  # noqa: BLE001
            return
        body.clear()
        if not revs:
            body.write(Text("  (no revisions)", style="dim"))
            status.update("  no history")
            return
        n_edges = 0
        for r in revs:
            self._write_rev(body, r)
            n_edges += len(r.get("how") or [])
        status.update(
            f"  {len(revs)} revision(s), {n_edges} integration edge(s)"
        )

    def _write_rev(self, body: RichLog, r: dict) -> None:
        rev = r.get("rev", "?")
        cl = r.get("change", "?")
        user = r.get("user", "")
        action = r.get("action", "")
        time_s = r.get("time", "")
        try:
            date = datetime.fromtimestamp(int(time_s)).strftime(
                "%Y-%m-%d %H:%M",
            ) if time_s else ""
        except (TypeError, ValueError):
            date = ""
        first = (r.get("desc") or "").splitlines()
        first_line = first[0] if first else ""
        body.write(Text(
            f"rev #{rev}   CL={cl}   {user}   {date}   [{action}]",
            style="cyan bold",
        ))
        if first_line:
            body.write(Text(f"  {first_line}"))
        # Integration edges. ``how[k]`` describes the relationship and
        # ``file[k]/srev[k]/erev[k]`` is the other side.
        hows = r.get("how") or []
        files = r.get("file") or []
        srevs = r.get("srev") or []
        erevs = r.get("erev") or []
        for k, how in enumerate(hows):
            other = files[k] if k < len(files) else ""
            sr = srevs[k] if k < len(srevs) else ""
            er = erevs[k] if k < len(erevs) else ""
            rev_span = self._format_rev_span(sr, er)
            arrow = self._edge_arrow(how)
            body.write(Text(
                f"  {arrow} {how:<14}  {other}{rev_span}",
                style="yellow",
            ))
        body.write(Text(""))

    @staticmethod
    def _edge_arrow(how) -> str:
        """Arrowhead for an integration edge, from filelog's ``how``
        string. ``↗`` = outgoing (this rev was integrated *into*
        another file — ``branch into`` / ``copy into`` / ``merge into``
        / ``moved into`` …); ``↙`` = incoming (created / fed *from*
        another, plus the ``ignored`` / ``undid`` oddballs).

        Match the ``into`` *token*, not a `` into `` substring: the real
        strings are two words with no trailing space (``branch into``),
        so a space-padded test never fired and every outgoing edge drew
        the wrong (incoming) arrow."""
        return "↗" if "into" in str(how).split() else "↙"

    @staticmethod
    def _format_rev_span(sr, er) -> str:
        sr_s = str(sr or "").lstrip("#")
        er_s = str(er or "").lstrip("#")
        if not sr_s and not er_s:
            return ""
        if sr_s == er_s or not er_s:
            return f"#{sr_s}"
        if not sr_s:
            return f"#{er_s}"
        return f"#{sr_s}..{er_s}"

    def _render_empty(self) -> None:
        try:
            body = self.query_one("#body", RichLog)
            status = self.query_one("#status", Static)
        except Exception:  # noqa: BLE001
            return
        body.clear()
        body.write(Text("  (no history for this file)", style="dim"))
        status.update("  no history")

    def _render_error(self, message: str) -> None:
        try:
            body = self.query_one("#body", RichLog)
            status = self.query_one("#status", Static)
        except Exception:  # noqa: BLE001
            return
        body.clear()
        body.write(Text(message, style="red"))
        status.update("  error")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "backspace", "q", "ㅂ"):
            event.stop()
            self.dismiss(None)


def _idx(d: dict, key: str, i: int):
    arr = d.get(key) or []
    return arr[i] if i < len(arr) else ""


def _idx_list(d: dict, key: str, i: int) -> list[str]:
    arr = d.get(key) or []
    if i >= len(arr):
        return []
    val = arr[i]
    if isinstance(val, list):
        return [str(v) for v in val]
    if val is None:
        return []
    return [str(val)]
