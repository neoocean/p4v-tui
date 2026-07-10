"""Branch / Copy / Integrate modal.

One small picker for the three p4 commands that schedule cross-path
work. Result is a dict ``{"operation", "source", "target", "description"}``
or ``None`` if cancelled. The App layer turns each operation into the
corresponding p4 invocation:

* ``integrate`` — ``p4 integrate <src> <tgt>`` (opens for resolve)
* ``copy``      — ``p4 copy <src> <tgt>``      (no merge needed)
* ``branch``    — ``p4 populate -d <desc> <src> <tgt>`` (immediate
                  submit; description required so the new CL has one)
"""
from __future__ import annotations

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


OPERATION_LABEL = {
    "integrate": "Merge/Integrate",
    "copy": "Copy",
    "branch": "Branch (populate + submit)",
}


class BranchCopyIntegrateModal(ModalScreen[Optional[dict]]):
    DEFAULT_CSS = """
    BranchCopyIntegrateModal { align: center middle; }
    BranchCopyIntegrateModal > #dialog {
        width: 90%;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1;
    }
    BranchCopyIntegrateModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
        margin-bottom: 1;
    }
    BranchCopyIntegrateModal Static.field_label {
        margin-top: 1;
    }
    BranchCopyIntegrateModal Input {
        margin-top: 0;
    }
    BranchCopyIntegrateModal #buttons {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    BranchCopyIntegrateModal Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        operation: str,
        target: str = "",
        source: str = "",
        branch_spec: str = "",
    ) -> None:
        super().__init__()
        if operation not in OPERATION_LABEL:
            operation = "integrate"
        self._operation = operation
        self._initial_target = target
        self._initial_source = source
        # When set (branch operation only), populate runs in branch-
        # mapping mode (`populate -b <branch>`): the mapping defines the
        # source→target view, so the Source field is hidden and Target
        # becomes an optional restriction.
        self._branch_spec = branch_spec

    def compose(self) -> ComposeResult:
        op_label = OPERATION_LABEL[self._operation]
        mapping_mode = bool(self._branch_spec)
        with Container(id="dialog"):
            yield Static(f" {op_label} files ", id="title")
            with Vertical():
                if mapping_mode:
                    yield Static(
                        f"Branch mapping: {self._branch_spec}",
                        classes="field_label",
                    )
                    yield Static(
                        "Target (optional — restrict to a subpath):",
                        classes="field_label",
                    )
                    yield Input(
                        value=self._initial_target,
                        placeholder="(blank → whole mapping)",
                        id="tgt",
                    )
                else:
                    yield Static(
                        "Source (depot path, e.g. //depot/main/...):",
                        classes="field_label",
                    )
                    yield Input(
                        value=self._initial_source,
                        placeholder="//depot/source/path/...",
                        id="src",
                    )
                    yield Static("Target:", classes="field_label")
                    yield Input(
                        value=self._initial_target,
                        placeholder="//depot/target/path/...",
                        id="tgt",
                    )
                if self._operation == "branch":
                    yield Static("Description (required for branch):",
                                 classes="field_label")
                    yield Input(
                        placeholder="Description for the new branch CL",
                        id="desc",
                    )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(
                    op_label, id="ok",
                    variant="error" if self._operation == "branch"
                                    else "primary",
                )

    def on_mount(self) -> None:
        # Branch-mapping mode has no Source field — focus Target. Else,
        # when the source was pre-filled (e.g. from a Submitted-CL
        # integrate menu) jump straight to the target field.
        if self._branch_spec:
            target_field = "#tgt"
        else:
            target_field = "#tgt" if self._initial_source else "#src"
        self.query_one(target_field, Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        mapping_mode = bool(self._branch_spec)
        tgt = self.query_one("#tgt", Input).value.strip()
        src = ""
        if not mapping_mode:
            src = self.query_one("#src", Input).value.strip()
        desc = ""
        if self._operation == "branch":
            try:
                desc = self.query_one("#desc", Input).value.strip()
            except Exception:  # noqa: BLE001
                desc = ""
        # In mapping mode the branch view supplies source→target, so only
        # the description is required; target is an optional restriction.
        if not mapping_mode and (not src or not tgt):
            self.app.notify("Source and target are required.",
                            severity="warning", timeout=3)
            return
        if self._operation == "branch" and not desc:
            self.app.notify(
                "Description is required for Branch (populate auto-submits).",
                severity="warning", timeout=4,
            )
            return
        self.dismiss({
            "operation": self._operation,
            "source": src,
            "target": tgt,
            "description": desc,
            "branch": self._branch_spec,
        })

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
