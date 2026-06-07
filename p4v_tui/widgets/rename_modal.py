"""Rename / move modal — p4v-style split layout.

Three rows:

  Current depot path:    (read-only Static — "From")
  New base path:         (editable Input + Browse… button)
  New name:              (editable Input — leaf component)

A live "Target: <base>/<name>" preview updates as the user types so
they can see the assembled path before clicking Move. Browse opens a
DepotBrowserModal that lets the user pick any depot path; the picked
value lands in "New base path".

Returns the assembled depot path (``f"{base}/{name}"``), or ``None``
if cancelled. Empty fields or "same as source" gets a warning toast
and the modal stays open.
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from .depot_browser import DepotBrowserModal


class RenameMoveModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    RenameMoveModal { align: center middle; }
    RenameMoveModal > #dialog {
        width: 95%;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1;
    }
    RenameMoveModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
        margin-bottom: 1;
    }
    RenameMoveModal Static.field_label {
        margin-top: 1;
        text-style: bold;
    }
    RenameMoveModal Static.readonly_value {
        background: $surface;
        padding: 0 1;
    }
    RenameMoveModal #base_row {
        height: 3;
    }
    RenameMoveModal #base {
        width: 1fr;
    }
    RenameMoveModal #browse {
        width: auto;
        margin-left: 1;
    }
    RenameMoveModal #target_preview {
        margin-top: 1;
        padding: 0 1;
        color: $text-muted;
    }
    RenameMoveModal #buttons {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    RenameMoveModal Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        source_path: str,
        *,
        is_directory: bool = False,
        p4_service=None,
    ) -> None:
        super().__init__()
        self._source = source_path
        self._is_directory = is_directory
        self._p4 = p4_service
        # Split source into base + leaf so the modal pre-fills "where it
        # lives now" and "what it's called now" on separate inputs.
        if "/" in source_path:
            base, leaf = source_path.rsplit("/", 1)
        else:
            base, leaf = "", source_path
        self._initial_base = base
        self._initial_leaf = leaf

    def compose(self) -> ComposeResult:
        scope = ("directory (recursive — every file under this path)"
                 if self._is_directory else "file")
        with Container(id="dialog"):
            yield Static(f" Rename / Move — {scope} ", id="title")

            yield Static("Current depot path:", classes="field_label")
            yield Static(f"  {self._source}",
                         classes="readonly_value", id="src_display")

            yield Static("New base path (directory):",
                         classes="field_label")
            with Horizontal(id="base_row"):
                yield Input(value=self._initial_base, id="base")
                yield Button("Browse…", id="browse")

            yield Static(
                "New name:" if not self._is_directory
                else "New folder name:",
                classes="field_label",
            )
            yield Input(value=self._initial_leaf, id="leaf")

            yield Static("Target: ", id="target_preview")

            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Move", id="ok", variant="primary")

    def on_mount(self) -> None:
        self._update_preview()
        # Most renames just tweak the leaf — focus that input and put
        # the cursor at the end.
        try:
            inp = self.query_one("#leaf", Input)
            inp.focus()
            inp.cursor_position = len(inp.value)
        except Exception:  # noqa: BLE001
            pass

    # --- live preview ----------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_preview()

    def _update_preview(self) -> None:
        try:
            base = self.query_one("#base", Input).value.strip().rstrip("/")
            leaf = self.query_one("#leaf", Input).value.strip()
            preview = self._assemble(base, leaf) or "(empty)"
            self.query_one("#target_preview", Static).update(
                f"Target: {preview}"
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _assemble(base: str, leaf: str) -> str:
        if base and leaf:
            return f"{base}/{leaf}"
        return base or leaf

    # --- buttons ---------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "ok":
            self._submit()
        elif bid == "browse":
            self._open_browser()
        else:
            self.dismiss(None)

    def _open_browser(self) -> None:
        if self._p4 is None:
            self.app.notify(
                "Browser unavailable (no P4 service handle).",
                severity="warning", timeout=4,
            )
            return

        def on_pick(picked: str | None) -> None:
            if not picked:
                return
            try:
                self.query_one("#base", Input).value = picked
            except Exception:  # noqa: BLE001
                return
            self._update_preview()

        self.app.push_screen(DepotBrowserModal(self._p4), on_pick)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter inside either input is the same as clicking Move.
        self._submit()

    def _submit(self) -> None:
        base = self.query_one("#base", Input).value.strip().rstrip("/")
        leaf = self.query_one("#leaf", Input).value.strip()
        if not base or not leaf:
            self.app.notify(
                "Both base path and name are required.",
                severity="warning", timeout=4,
            )
            return
        target = self._assemble(base, leaf)
        if target == self._source:
            self.app.notify(
                "Target is identical to source.",
                severity="warning", timeout=3,
            )
            return
        self.dismiss(target)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
