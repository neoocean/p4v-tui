"""Read-only file viewer modal.

Wide popup (~95% × 95%) for inspecting a depot file's contents. Uses
:class:`textual.widgets.RichLog` rather than a single Static so very
large files don't block the UI on render — content is streamed in
~1000-line batches via ``call_after_refresh`` so each frame can paint.

Driven by RichLog's standard scroll bindings:

  * ↑ / ↓        — line scroll
  * PageUp/Down  — page scroll
  * Home / End   — top / bottom
  * n / ㅜ        — toggle line numbers (on by default)
  * Esc / q / ㅂ / Backspace — close

A hard cap on bytes loaded keeps memory bounded; oversized files get a
truncation banner appended at the end.

Pass ``filename`` to opt into syntax highlighting based on the
extension (or the embedded shebang). Highlighting is skipped for
files above ``MAX_HIGHLIGHT_BYTES`` so opening a multi-megabyte log
stays as snappy as before.

Line numbers are prefixed onto every rendered line; the column is sized
to fit the largest line number in the file. Use the `n` key to flip
the column off (e.g., when copy-pasting selections out of the viewer).
The width budget for the prefix is bounded so toggling is cheap.
"""
from __future__ import annotations

from rich.syntax import Syntax
from rich.text import Text

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static


# 5 MiB — generous for source code but keeps a runaway binary from
# locking the renderer. ChunkedSyncJob can still dump multi-GB files
# locally; viewing them is a luxury we explicitly cap.
MAX_VIEW_BYTES = 5 * 1024 * 1024

# Lines per per-frame batch. Big enough to fill a typical screen in one
# pass, small enough that even a 10MB log paints in <1s without blocking
# user input between batches.
BATCH_LINES = 1000

# Pygments runs synchronously and is roughly O(content size). Above
# this cap we skip highlighting and render the file plain — a 5 MB
# source file is rare, and a freeze on the UI thread while pygments
# lexes is more annoying than monochrome syntax.
MAX_HIGHLIGHT_BYTES = 256 * 1024

# Theme passed to ``rich.syntax.Syntax`` — terminal-safe ANSI colours
# so highlighting works on any background scheme the user has.
_SYNTAX_THEME = "ansi_dark"


class FileViewerModal(ModalScreen[None]):
    DEFAULT_CSS = """
    FileViewerModal { align: center middle; }
    FileViewerModal > #dialog {
        width: 95%;
        height: 95%;
        border: thick $primary;
        background: $panel;
    }
    /* Narrow variant — the modal hugs the right edge and takes ~three
       quarters of the screen so the left-pane tree behind it stays
       visible. Used for opening a single depot file via the tree,
       where keeping the tree on-screen helps the user keep their
       bearings. */
    FileViewerModal.narrow { align: right middle; }
    FileViewerModal.narrow > #dialog {
        width: 75%;
        height: 95%;
    }
    /* place-top / place-bottom — when this viewer is used from a
       DataTable row selection (Submitted CL detail, remote pending
       CL view), hug the screen edge opposite the cursor so the row
       that opened the popup stays visible. The shorter 55% height
       guarantees a non-overlapping strip even when the cursor sits
       near the middle. */
    FileViewerModal.place-top    { align: center top;    }
    FileViewerModal.place-bottom { align: center bottom; }
    FileViewerModal.place-top    > #dialog,
    FileViewerModal.place-bottom > #dialog {
        height: 55%;
    }
    FileViewerModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    FileViewerModal #content {
        height: 1fr;
        padding: 0 1;
        background: $background;
    }
    FileViewerModal #footer_hint {
        height: 1;
        padding: 0 1;
        background: $boost;
        color: $text-muted;
    }
    """

    BINDINGS = [
        # priority=True so the focused RichLog can't accidentally swallow
        # the close keys via its own bindings.
        Binding("escape",    "close", "Close", priority=True),
        Binding("q",         "close", show=False, priority=True),
        Binding("ㅂ",         "close", show=False, priority=True),
        Binding("backspace", "close", show=False, priority=True),
        # Line-number toggle. priority=True for the same reason: the
        # focused RichLog has no "n" binding today but a future Textual
        # version might (e.g., "next" for some scroll variant), and
        # we want the toggle to remain reliable. ㅜ is the 2-beolsik
        # Hangul jamo for the same keypress, so IME-on users get the
        # toggle without switching layouts.
        Binding("n",  "toggle_line_numbers", "Toggle line #", priority=True),
        Binding("ㅜ", "toggle_line_numbers", show=False, priority=True),
    ]

    def __init__(
        self,
        title: str,
        content: str,
        *,
        narrow: bool = False,
        filename: str | None = None,
        line_numbers: bool = True,
        rendered: list | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._content = content
        # Pre-rendered body lines (e.g. half-block image ANSI art). When
        # set, the viewer writes them verbatim and skips the text path
        # (byte cap, syntax highlight, splitlines) entirely — the caller
        # has already produced ``rich`` renderables. ``content`` is kept
        # only so the line-number toggle has a stable no-op fallback.
        self._rendered = rendered
        # Filename is used solely for lexer detection by ``rich.syntax``.
        # Passing ``None`` keeps the viewer in plain-text mode (the
        # right choice for diff dumps, CL describes, etc).
        self._filename = filename
        # Line numbers ON by default — the most common ask is "what
        # line am I looking at?". Subclasses (e.g. LogEntryViewerModal,
        # where each "line" is already an entry index in the body's
        # own format) can pass `line_numbers=False` to start with
        # them off; users still toggle with `n`.
        self._line_numbers = line_numbers
        if narrow:
            self.add_class("narrow")

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(f" {self._title} ", id="title")
            yield RichLog(
                id="content",
                markup=False,    # show raw text, no Rich tag interpretation
                wrap=False,      # horizontal scroll for long lines
                auto_scroll=False,
                highlight=False,
            )
            yield Static(
                self._footer_hint_text(),
                id="footer_hint",
            )

    def _footer_hint_text(self) -> str:
        # Reflect the current line-numbers state in the footer so the
        # toggle is discoverable and the user can see at a glance
        # whether they're on or off.
        on_off = "ON" if self._line_numbers else "OFF"
        return f" ↑↓/PgUp/PgDn scroll · n line# ({on_off}) · Esc close "

    def on_mount(self) -> None:
        log = self.query_one("#content", RichLog)
        log.focus()
        # Defer the actual write so the modal frame paints first; the
        # user sees the dialog immediately and content fills in behind.
        self._pending_lines = self._body_lines()
        self._line_idx = 0
        self.call_after_refresh(self._write_next_batch)

    def _body_lines(self) -> list:
        """The lines to write: pre-rendered renderables when supplied,
        otherwise the text pipeline (cap + highlight + line numbers)."""
        if self._rendered is not None:
            return self._apply_line_numbers(self._rendered)
        return self._prepare_lines(self._content)

    def _prepare_lines(self, content: str) -> list:
        # Apply the byte cap on the raw string; trim mid-line if needed
        # and append a truncation banner so the user knows they're not
        # seeing the full file.
        truncated = False
        encoded_len = len(content.encode("utf-8", errors="replace"))
        if encoded_len > MAX_VIEW_BYTES:
            # Slice characters instead of bytes — slightly conservative
            # but avoids splitting a multi-byte sequence in half.
            ratio = MAX_VIEW_BYTES / encoded_len
            cut = int(len(content) * ratio)
            content = content[:cut]
            truncated = True

        highlighted = self._maybe_highlight(content)
        if highlighted is None:
            lines: list = content.splitlines()
        else:
            lines = highlighted.split("\n")
        if truncated:
            banner = (
                f"--- (truncated — file larger than "
                f"{MAX_VIEW_BYTES // (1024 * 1024)}MB; "
                f"showing first {len(lines):,} lines) ---"
            )
            lines.append("")
            lines.append(banner)
        return self._apply_line_numbers(lines)

    def _apply_line_numbers(self, lines: list) -> list:
        """Prepend `<n>  ` to each line when `_line_numbers` is on.

        Width auto-fits the largest line number in the body (with a
        minimum of 3 so a tiny file's numbers don't dance on every
        toggle). The prefix is styled dim so it doesn't shout over
        the actual content. For lines that already carry Rich `Text`
        styling (syntax-highlighted), the prefix is built as a fresh
        `Text` and concatenated so the original styling survives.
        """
        if not self._line_numbers or not lines:
            return list(lines)
        width = max(3, len(str(len(lines))))
        out: list = []
        for i, line in enumerate(lines, start=1):
            prefix_str = f"{i:>{width}}  "
            if isinstance(line, Text):
                wrapped = Text(prefix_str, style="dim")
                wrapped.append_text(line)
                out.append(wrapped)
            else:
                wrapped = Text(prefix_str, style="dim")
                wrapped.append(str(line))
                out.append(wrapped)
        return out

    def action_toggle_line_numbers(self) -> None:
        """Flip line numbers on / off and re-render the body in place.

        Cheap: `_prepare_lines` does the same work it did on first
        mount (truncation + syntax-highlight + numbering). For a typical
        source file (a few thousand lines) this is well under one
        frame; for a 5 MB log the batched write keeps it interactive.
        """
        self._line_numbers = not self._line_numbers
        try:
            footer = self.query_one("#footer_hint", Static)
            footer.update(self._footer_hint_text())
        except Exception:  # noqa: BLE001
            pass
        try:
            log = self.query_one("#content", RichLog)
            log.clear()
        except Exception:  # noqa: BLE001
            return
        self._pending_lines = self._body_lines()
        self._line_idx = 0
        self.call_after_refresh(self._write_next_batch)

    def _maybe_highlight(self, content: str):
        """Return per-line :class:`rich.text.Text` segments for ``content``,
        or ``None`` to fall back to plain rendering.

        Highlighting is opt-in via the ``filename`` constructor arg —
        a viewer used for diff dumps or CL describes passes ``None``
        and stays monochrome. Above ``MAX_HIGHLIGHT_BYTES`` we also
        give up on highlighting so pygments doesn't stall the UI
        thread.
        """
        if not self._filename:
            return None
        if len(content) > MAX_HIGHLIGHT_BYTES:
            return None
        try:
            lexer = Syntax.guess_lexer(self._filename, code=content)
        except Exception:  # noqa: BLE001
            return None
        # ``guess_lexer`` always returns *some* lexer (falls back to a
        # plain-text default); the plain default leaves the Text
        # unstyled so the early-return cost is just the lex pass.
        try:
            return Syntax(
                content,
                lexer,
                theme=_SYNTAX_THEME,
                line_numbers=False,
                word_wrap=False,
                background_color="default",
            ).highlight(content)
        except Exception:  # noqa: BLE001
            return None

    def _write_next_batch(self) -> None:
        try:
            log = self.query_one("#content", RichLog)
        except Exception:  # noqa: BLE001
            return
        end = min(self._line_idx + BATCH_LINES, len(self._pending_lines))
        for i in range(self._line_idx, end):
            log.write(self._pending_lines[i])
        self._line_idx = end
        if self._line_idx < len(self._pending_lines):
            # Yield back to the event loop so input/scroll stays
            # responsive between batches.
            self.call_after_refresh(self._write_next_batch)

    def on_key(self, event: events.Key) -> None:
        # The focused RichLog's own bindings can shadow our ModalScreen
        # ones depending on Textual version, so handle the close keys
        # explicitly here as a guarantee.
        if (event.key in ("escape", "q", "backspace")
                or event.character == "ㅂ"):
            event.stop()
            self.dismiss()

    def on_click(self, event: events.Click) -> None:
        """Click / tap outside the dialog dismisses the viewer.

        The ModalScreen itself spans the whole viewport but the only
        visible content is the centred ``#dialog`` Container. Clicks
        that hit the backdrop (i.e. land on ``self`` rather than a
        descendant of ``#dialog``) close the popup — the standard
        "tap-away to dismiss" UX users expect on touch keyboards
        where the existing Esc / q / Backspace shortcuts can be
        awkward to reach.
        """
        w = getattr(event, "widget", None)
        # Walk up from the clicked widget; if any ancestor is our
        # ``#dialog`` Container the click was inside the modal body
        # and we leave it alone.
        cur = w
        while cur is not None:
            if getattr(cur, "id", None) == "dialog":
                return
            if cur is self:
                break
            cur = cur.parent
        event.stop()
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()
