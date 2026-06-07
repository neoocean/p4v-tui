"""Annotate / blame viewer.

Renders ``p4 annotate -i -c <file>`` output as one row per source line:

    NN  CL=12345  user  YYYY-MM-DD  | <line text>

``-i`` follows file history through integrations / branches so the
attribution points at the original CL that introduced a line, not
the merge commit. ``-c`` adds the changelist number for each line.
The CL → (user, date) lookup uses ``p4 changes`` results we collect
on the side.

Esc / Backspace / q closes.
"""
from __future__ import annotations


from rich.text import Text

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static


class AnnotateModal(ModalScreen[None]):
    DEFAULT_CSS = """
    AnnotateModal { align: center middle; }
    AnnotateModal > #dialog {
        width: 95%;
        height: 90%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    AnnotateModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    AnnotateModal #status {
        color: $text-muted;
        padding: 0 1;
    }
    AnnotateModal #annotate_log {
        height: 1fr;
        background: $surface;
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
                f" Annotate · {self._depot_path} ", id="title",
            )
            yield Static("  Loading…", id="status")
            yield RichLog(highlight=False, markup=False,
                          wrap=False, id="annotate_log")

    def on_mount(self) -> None:
        self._fetch_and_render()

    @work(thread=True, group="annotate", exclusive=True)
    def _fetch_and_render(self) -> None:
        try:
            rows = self._p4.run(
                "annotate", "-i", "-c", self._depot_path,
            )
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(
                self._render_error, f"annotate failed: {e}",
            )
            return

        # First row is metadata about the file (depotFile / type /
        # depotRev) — skip it. Subsequent rows are per-line dicts
        # like {"data": "<text>\n", "lower": "12345", "upper": "12345"}.
        annotated_lines: list[tuple[str, str]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            if "data" not in r:
                continue
            cl = (r.get("upper") or r.get("lower") or "?")
            line = r.get("data", "")
            # `data` arrives with the trailing newline preserved; strip
            # so each row gets one line in the log.
            annotated_lines.append((str(cl), line.rstrip("\n")))

        # Bulk-fetch the user / date for each unique CL.
        unique_cls = sorted({cl for cl, _ in annotated_lines if cl != "?"})
        cl_meta: dict[str, tuple[str, str]] = {}
        for cl in unique_cls:
            try:
                info = self._p4.run("describe", "-s", cl)
                if info and isinstance(info[0], dict):
                    user = str(info[0].get("user", ""))
                    t = info[0].get("time", "")
                    if t:
                        from datetime import datetime
                        try:
                            date = datetime.fromtimestamp(int(t)).strftime(
                                "%Y-%m-%d",
                            )
                        except (TypeError, ValueError):
                            date = ""
                    else:
                        date = ""
                    cl_meta[cl] = (user, date)
            except Exception:  # noqa: BLE001
                continue

        self.app.call_from_thread(
            self._render_annotation, annotated_lines, cl_meta,
        )

    def _render_annotation(
        self,
        lines: list[tuple[str, str]],
        cl_meta: dict[str, tuple[str, str]],
    ) -> None:
        try:
            log = self.query_one("#annotate_log", RichLog)
            status = self.query_one("#status", Static)
        except Exception:  # noqa: BLE001
            return
        log.clear()
        if not lines:
            log.write(
                Text("  (no content — empty file or binary)", style="dim"),
            )
            status.update(f"  {self._depot_path}: 0 lines")
            return
        # Width tuning so the gutter is uniform regardless of largest
        # CL number / username length.
        cl_w = max((len(cl) for cl, _ in lines), default=1)
        max_user = max(
            (len(cl_meta.get(cl, ("", ""))[0]) for cl, _ in lines),
            default=4,
        )
        max_user = min(max_user, 16)
        for n, (cl, text) in enumerate(lines, 1):
            user, date = cl_meta.get(cl, ("", ""))
            user = (user or "?")[:max_user].ljust(max_user)
            date = (date or "----------").ljust(10)
            gutter = (
                f"{n:>5}  CL={cl:>{cl_w}}  {user}  {date}  | "
            )
            log.write(Text(gutter, style="cyan").append(text))
        unique = len({cl for cl, _ in lines})
        status.update(
            f"  {self._depot_path}: {len(lines)} lines, "
            f"{unique} contributing CL(s)",
        )

    def _render_error(self, message: str) -> None:
        try:
            status = self.query_one("#status", Static)
            log = self.query_one("#annotate_log", RichLog)
        except Exception:  # noqa: BLE001
            return
        log.clear()
        log.write(Text(message, style="red"))
        status.update(f"  {self._depot_path}: error")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "backspace", "q", "ㅂ"):
            event.stop()
            self.dismiss(None)
