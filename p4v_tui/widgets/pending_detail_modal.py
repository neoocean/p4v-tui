"""Editable info popup for a Pending Changelist.

Triggered by Enter on a Pending Changelists row. Shows the description
in an editable TextArea (so the user can fix typos before submit) and
the file list as a SelectionList of checkboxes (so the user can drop
unwanted files out of the upcoming submit). Plus four actions:

  Submit       — apply edits and resilient-submit the CL
  Save         — apply edits without submitting (default CL is promoted)
  Revert Files — revert every opened file in this CL (with confirm)
  Cancel       — close (prompts to save if there are unsaved edits)

Returns a dict with the user's intent, or ``None`` on cancel:

```
{
    "action":           "submit" | "save" | "revert",
    "new_description":  str | None,           # None = unchanged
    "unchecked_files":  list[str],            # depot paths to move out
}
```
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import (
    Button, SelectionList, Static, TextArea,
)
from textual.widgets.selection_list import Selection

from .unsaved_changes_modal import UnsavedChangesModal


class PendingDetailModal(ModalScreen[Optional[dict]]):
    DEFAULT_CSS = """
    PendingDetailModal { align: center middle; }
    PendingDetailModal > #dialog {
        width: 95%;
        height: 90%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    /* When the triggering row sits in the upper half of the screen,
       hug the bottom — and shrink — so the row that launched the
       popup stays visible behind it. The same idea inverted for a
       row in the lower half. The shrunken height keeps the dialog
       from re-covering the cursor it was just told to avoid. */
    PendingDetailModal.place-bottom { align: center bottom; }
    PendingDetailModal.place-top    { align: center top;    }
    PendingDetailModal.place-bottom > #dialog,
    PendingDetailModal.place-top    > #dialog {
        height: 55%;
    }
    PendingDetailModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    PendingDetailModal Static.section_title {
        text-style: bold;
        margin-top: 1;
        color: $text;
    }
    PendingDetailModal #desc_area {
        height: 12;
        margin-bottom: 1;
    }
    PendingDetailModal #file_list {
        height: 1fr;
        background: transparent;
    }
    PendingDetailModal #buttons {
        height: 3;
        align: right middle;
        padding: 0 1;
    }
    PendingDetailModal Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        change: str,
        description: str,
        files: list[dict],
        is_default: bool,
    ) -> None:
        super().__init__()
        self._change = change
        self._original_desc = (description or "").rstrip()
        self._files = files or []
        self._is_default = is_default

    def compose(self) -> ComposeResult:
        scope = "default" if self._is_default else f"CL {self._change}"
        with Container(id="dialog"):
            yield Static(f" Pending Changelist — {scope} ", id="title")

            yield Static("Description (editable):",
                         classes="section_title")
            yield TextArea(
                self._original_desc,
                id="desc_area",
                soft_wrap=True,
            )

            yield Static(
                f"Files ({len(self._files)}) — Space toggles · "
                "unchecked items will be moved to default before submit",
                classes="section_title",
            )
            if self._files:
                selections = [
                    Selection(
                        self._format_file(f),
                        str(i),
                        True,  # default: every file checked / will submit
                    )
                    for i, f in enumerate(self._files)
                ]
                yield SelectionList[str](*selections, id="file_list")
            else:
                # SelectionList with no entries fails to mount; fall
                # back to a simple "no files" notice.
                yield Static("  (no opened files)", id="file_list")

            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Revert Files", id="revert",
                             variant="error")
                yield Button("Save", id="save")
                yield Button("Submit", id="submit",
                             variant="primary")

    def on_mount(self) -> None:
        # Focus the description so the user can immediately edit. They
        # can Tab into the file list to toggle checkboxes.
        try:
            self.query_one("#desc_area", TextArea).focus()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _format_file(f: dict) -> str:
        action = f.get("action", "") or ""
        path = f.get("depotFile", "") or ""
        rev = (f.get("rev") or f.get("haveRev") or "") or ""
        ftype = f.get("type", "") or ""
        bits = [f"[{action}]" if action else "", path]
        if rev:
            bits.append(f"#{rev}")
        if ftype:
            bits.append(f"({ftype})")
        return "  ".join(b for b in bits if b)

    # --- buttons ---------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "submit":
            self._dismiss_with("submit")
        elif bid == "save":
            self._dismiss_with("save")
        elif bid == "revert":
            # Revert blows away the files; surfacing an unsaved-edits
            # prompt on top would be confusing. The downstream confirm
            # modal already gates the destructive action.
            self._dismiss_with("revert")
        else:
            self._cancel_with_guard()

    def _has_unsaved_changes(self) -> bool:
        """True if description text or file selection differs from the
        state the modal was opened with. Baseline: original description
        and every file checked.
        """
        try:
            ta = self.query_one("#desc_area", TextArea)
            if ta.text.rstrip() != self._original_desc:
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            lst = self.query_one("#file_list")
            if isinstance(lst, SelectionList):
                if len(lst.selected) != len(self._files):
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _cancel_with_guard(self) -> None:
        """Cancel path. If the user typed something or unchecked a
        file, route through UnsavedChangesModal first."""
        if not self._has_unsaved_changes():
            self.dismiss(None)
            return

        def on_choice(choice: str | None) -> None:
            if choice == "save":
                self._dismiss_with("save")
            elif choice == "discard":
                self.dismiss(None)
            # None → user picked Continue editing; modal stays open

        self.app.push_screen(UnsavedChangesModal(), on_choice)

    def _dismiss_with(self, action: str) -> None:
        # Description: emit only if the user actually changed it.
        try:
            ta = self.query_one("#desc_area", TextArea)
            new_desc = ta.text.rstrip()
        except Exception:  # noqa: BLE001
            new_desc = self._original_desc
        desc_changed = new_desc != self._original_desc

        # Files: SelectionList may not exist if there were no files.
        unchecked: list[str] = []
        try:
            lst = self.query_one("#file_list")
            if isinstance(lst, SelectionList):
                selected = set(lst.selected)
                for i, f in enumerate(self._files):
                    if str(i) not in selected:
                        df = f.get("depotFile") or ""
                        if df:
                            unchecked.append(df)
        except Exception:  # noqa: BLE001
            pass

        self.dismiss({
            "action": action,
            "new_description": new_desc if desc_changed else None,
            "unchecked_files": unchecked,
        })

    def action_cancel(self) -> None:
        self._cancel_with_guard()

    def on_key(self, event: events.Key) -> None:
        # TextArea may capture Esc on some Textual builds, so handle it
        # explicitly to guarantee close.
        if event.key == "escape":
            event.stop()
            self._cancel_with_guard()
