"""Get Revision dialog — p4v's multi-target sync picker, ported.

The user can stage a list of files / folders, pick whether to fetch
``@head`` or a specific revision spec (Changelist / Label / Date /
Revision), tweak the standard sync options, and either Preview
(dry-run via ``p4 sync -n``) or fire the real sync.

The real sync is queued through the JobRunner as
:class:`ChunkedSyncJob` per target so the operation stays resumable
and interruption-tolerant — same machinery as the keyboard-driven
"Get Latest" shortcut.

Returns ``None`` (the modal handles its own work).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button, Checkbox, Input, Label, ListView, ListItem,
    RadioButton, RadioSet, Select, Static,
)


# --- public API ----------------------------------------------------------

@dataclass
class GetRevisionRequest:
    """What the modal asks the App to do once the user clicks Get
    Revision. Each entry produces one chunked sync job."""
    targets: list[str]              # depot paths (with /... if directory)
    rev_mode: str                   # "head" / "changelist" / "label" / "date" / "rev"
    rev_value: str                  # blank for "head"; else the value
    force: bool                     # p4 sync -f
    safe_update: bool               # p4 sync -s
    only_files_in_cl: bool          # for "changelist" mode
    remove_not_in_label: bool       # for "label" mode
    preview: bool                   # True → p4 sync -n (dry run)


_REV_MODE_LABEL = {
    "changelist": "Changelist",
    "label":      "Label",
    "date":       "Date",
    "rev":        "Revision",
}


class GetRevisionModal(ModalScreen[Optional[GetRevisionRequest]]):
    DEFAULT_CSS = """
    GetRevisionModal { align: center middle; }
    GetRevisionModal > #dialog {
        width: 95%;
        height: 90%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    GetRevisionModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    GetRevisionModal Label.section { text-style: bold; margin-top: 1; }
    GetRevisionModal #targets_row {
        height: 12;
        margin-top: 1;
    }
    GetRevisionModal #targets_list {
        height: 1fr;
        border: solid $primary;
        background: $surface;
    }
    GetRevisionModal #target_btns {
        width: 14;
        margin-left: 1;
    }
    GetRevisionModal #target_btns Button { width: 100%; margin-top: 0; margin-bottom: 1; }
    GetRevisionModal #add_input_row {
        height: 3;
        margin-top: 1;
        align: left middle;
    }
    GetRevisionModal #add_input { width: 1fr; }
    GetRevisionModal #add_apply { margin-left: 2; }
    GetRevisionModal #rev_mode_row {
        height: 5;
        margin-top: 1;
    }
    GetRevisionModal #rev_value_row {
        height: 3;
        align: left middle;
        margin-top: 1;
    }
    GetRevisionModal #rev_kind   { width: 18; }
    GetRevisionModal #rev_value  { width: 1fr; margin-left: 1; }
    GetRevisionModal #rev_browse { margin-left: 1; }
    GetRevisionModal #options { margin-top: 1; }
    GetRevisionModal Checkbox  { margin-top: 0; }
    GetRevisionModal #buttons {
        height: 3;
        align: right middle;
        margin-top: 1;
        padding: 0 1;
    }
    GetRevisionModal Button { margin-left: 2; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        initial_targets: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._targets: list[str] = list(initial_targets or [])

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Get Revision ", id="title")
            yield Label("Get or replace the following files / folders:",
                        classes="section")
            with Horizontal(id="targets_row"):
                with VerticalScroll(id="targets_list"):
                    yield ListView(
                        *(ListItem(Static(t)) for t in self._targets),
                        id="targets_lv",
                    )
                with VerticalScroll(id="target_btns"):
                    yield Button("Add…", id="add")
                    yield Button("Remove", id="remove",
                                 variant="error")
            with Horizontal(id="add_input_row"):
                yield Input(
                    value="", id="add_input",
                    placeholder="//depot/path  or  //depot/path/...",
                )
                yield Button("Add path", id="add_apply",
                             variant="primary")

            yield Label("Revision", classes="section")
            with RadioSet(id="rev_mode_row"):
                yield RadioButton("Get latest revision",
                                  value=True, id="rev_head")
                yield RadioButton("Specify revision using…",
                                  id="rev_specify")
            with Horizontal(id="rev_value_row"):
                yield Select(
                    [(v, k) for k, v in _REV_MODE_LABEL.items()],
                    id="rev_kind",
                    value="changelist",
                    allow_blank=False,
                )
                yield Input(value="", id="rev_value",
                            placeholder="value (CL #, label name, "
                                        "date, or rev #)")

            yield Label("Options:", classes="section")
            with VerticalScroll(id="options"):
                yield Checkbox(
                    "Force Operation (replace file even if you already "
                    "have the revision specified)",
                    id="opt_force",
                )
                yield Checkbox(
                    "Safe Update: Don't overwrite files that were "
                    "changed without being checked out",
                    id="opt_safe", value=True,
                )
                yield Checkbox(
                    "Only get revisions for files listed in changelist "
                    "(applies when revision = Changelist)",
                    id="opt_only_in_cl",
                )
                yield Checkbox(
                    "Remove files from workspace if they are not in "
                    "label (applies when revision = Label)",
                    id="opt_remove_not_in_label",
                )

            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Preview", id="preview")
                yield Button("Get Revision", id="ok",
                             variant="primary")

    # --- buttons ---------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "cancel":
            self.dismiss(None)
        elif bid == "add":
            try:
                self.query_one("#add_input", Input).focus()
            except Exception:  # noqa: BLE001
                pass
        elif bid == "add_apply":
            self._add_from_input()
        elif bid == "remove":
            self._remove_selected()
        elif bid == "preview":
            self._dispatch(preview=True)
        elif bid == "ok":
            self._dispatch(preview=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "add_input":
            self._add_from_input()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

    # --- list editing ---------------------------------------------------

    def _add_from_input(self) -> None:
        try:
            inp = self.query_one("#add_input", Input)
        except Exception:  # noqa: BLE001
            return
        path = inp.value.strip()
        if not path:
            return
        if not path.startswith("//"):
            self.app.notify(
                "Paths must start with // (depot syntax).",
                severity="warning", timeout=4,
            )
            return
        if path in self._targets:
            self.app.notify(f"{path} already in the list.",
                            timeout=3)
            inp.value = ""
            return
        self._targets.append(path)
        self._rebuild_list()
        inp.value = ""

    def _remove_selected(self) -> None:
        try:
            lv = self.query_one("#targets_lv", ListView)
        except Exception:  # noqa: BLE001
            return
        idx = lv.index
        if idx is None or idx < 0 or idx >= len(self._targets):
            return
        del self._targets[idx]
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        try:
            lv = self.query_one("#targets_lv", ListView)
        except Exception:  # noqa: BLE001
            return
        lv.clear()
        for t in self._targets:
            lv.append(ListItem(Static(t)))

    # --- dispatch -------------------------------------------------------

    def _dispatch(self, *, preview: bool) -> None:
        if not self._targets:
            self.app.notify(
                "Add at least one path before Get Revision / Preview.",
                severity="warning", timeout=4,
            )
            return
        # Read radio set state.
        try:
            specify_btn = self.query_one("#rev_specify", RadioButton)
        except Exception:  # noqa: BLE001
            return
        if specify_btn.value:
            try:
                kind_sel = self.query_one("#rev_kind", Select)
                rev_value = (
                    self.query_one("#rev_value", Input).value.strip()
                )
            except Exception:  # noqa: BLE001
                return
            kind = str(kind_sel.value)
            if not rev_value:
                self.app.notify(
                    f"Enter a {_REV_MODE_LABEL.get(kind, 'value')} or "
                    "switch back to 'Get latest revision'.",
                    severity="warning", timeout=5,
                )
                return
            rev_mode = kind
        else:
            rev_mode = "head"
            rev_value = ""

        force = self.query_one("#opt_force", Checkbox).value
        safe_update = self.query_one("#opt_safe", Checkbox).value
        only_in_cl = self.query_one("#opt_only_in_cl", Checkbox).value
        remove_not_in_label = self.query_one(
            "#opt_remove_not_in_label", Checkbox,
        ).value

        self.dismiss(GetRevisionRequest(
            targets=list(self._targets),
            rev_mode=rev_mode,
            rev_value=rev_value,
            force=force,
            safe_update=safe_update,
            only_files_in_cl=only_in_cl,
            remove_not_in_label=remove_not_in_label,
            preview=preview,
        ))


def build_sync_spec(
    target: str,
    rev_mode: str,
    rev_value: str,
) -> str:
    """Render one of the user's targets into the actual ``p4 sync``
    filespec. Pure function — kept module-level for unit tests.

    Date values are accepted in either ``YYYY-MM-DD`` or
    ``YYYY/MM/DD`` form and emitted as Perforce's preferred
    ``YYYY/MM/DD``. Other modes pass the value through verbatim.
    """
    if rev_mode == "head" or not rev_value:
        return target
    if rev_mode == "rev":
        v = rev_value.lstrip("#")
        return f"{target}#{v}"
    if rev_mode == "date":
        v = rev_value.replace("-", "/")
        return f"{target}@{v}"
    # changelist or label — both use the @<value> form.
    v = rev_value.lstrip("@")
    return f"{target}@{v}"
