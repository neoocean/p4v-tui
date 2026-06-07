"""File Properties — view & edit filetype, attributes, basic stat.

Two halves:

Top:    read-only file metadata (depot path, head/have rev, current
        filetype, open action, locker, file size). Plus an editable
        Input for changing filetype, applied via ``p4 reopen -t``.
        Reopen needs the file to be opened first; the modal warns
        otherwise.

Bottom: attribute table (key / value) plus Add / Delete buttons.
        Add prompts for key + value via two Inputs; Delete removes the
        currently-selected row. Both fire ``p4 attribute`` straight
        through.

Esc / Backspace / q closes (no unsaved-changes guard — every action
button persists immediately, and the filetype Input is only applied
when the user clicks "Apply filetype").
"""
from __future__ import annotations


from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button, DataTable, Input, Label, Static,
)


class FilePropertiesModal(ModalScreen[None]):
    DEFAULT_CSS = """
    FilePropertiesModal { align: center middle; }
    FilePropertiesModal > #dialog {
        width: 90%;
        height: 90%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    FilePropertiesModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    FilePropertiesModal Label.field { margin-top: 1; text-style: bold; }
    FilePropertiesModal Static.value {
        background: $surface;
        padding: 0 1;
    }
    FilePropertiesModal #filetype_row {
        height: 3;
        align: left middle;
    }
    FilePropertiesModal #filetype_input { width: 30; }
    FilePropertiesModal #apply_filetype  { margin-left: 2; }
    FilePropertiesModal #attrs_table {
        height: 1fr;
        margin-top: 1;
    }
    FilePropertiesModal #attr_add_row {
        height: 3;
        align: left middle;
    }
    FilePropertiesModal #attr_key   { width: 20; }
    FilePropertiesModal #attr_value { width: 1fr; }
    FilePropertiesModal #attr_buttons { margin-left: 2; }
    FilePropertiesModal #buttons {
        height: 3;
        align: right middle;
    }
    FilePropertiesModal Button { margin-left: 2; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", priority=True),
    ]

    def __init__(self, depot_path: str, p4_service) -> None:
        super().__init__()
        self._depot_path = depot_path
        self._p4 = p4_service
        self._stat: dict = {}
        self._attrs: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(f" File Properties · {self._depot_path} ",
                         id="title")
            with VerticalScroll():
                yield Label("Path")
                yield Static(self._depot_path,
                             classes="value", id="val_path")
                yield Label("Have / Head rev")
                yield Static("(loading)", classes="value",
                             id="val_revs")
                yield Label("Current filetype")
                yield Static("(loading)", classes="value",
                             id="val_type")
                yield Label("Open action / locker")
                yield Static("(loading)", classes="value",
                             id="val_open")
                yield Label("File size")
                yield Static("(loading)", classes="value",
                             id="val_size")
                yield Label("Change filetype (e.g. text+x, binary+l)")
                with Horizontal(id="filetype_row"):
                    yield Input(value="", id="filetype_input",
                                placeholder="text, binary, +x, +l, …")
                    yield Button("Apply filetype", id="apply_filetype",
                                 variant="primary")
                yield Label("Attributes (p4 attribute)")
                yield DataTable(id="attrs_table",
                                cursor_type="row",
                                zebra_stripes=True)
                with Horizontal(id="attr_add_row"):
                    yield Input(value="", id="attr_key",
                                placeholder="attribute key")
                    yield Input(value="", id="attr_value",
                                placeholder="value")
                    with Horizontal(id="attr_buttons"):
                        yield Button("Add / set", id="attr_add")
                        yield Button("Delete selected",
                                     id="attr_delete", variant="error")
            with Horizontal(id="buttons"):
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        table = self.query_one("#attrs_table", DataTable)
        table.add_columns("Key", "Value")
        self._reload()

    @work(thread=True, group="file_props_load", exclusive=True)
    def _reload(self) -> None:
        try:
            rows = self._p4.run(
                "fstat", "-Oa",
                "-T",
                "depotFile,headRev,haveRev,headType,headAction,"
                "action,otherLock,otherOpen,fileSize,attr-,"
                "attrDigest-",
                self._depot_path,
            )
        except Exception:  # noqa: BLE001
            rows = []
        info: dict = {}
        attrs: dict[str, str] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            info.update(r)
            for k, v in r.items():
                if k.startswith("attr-"):
                    attrs[k[5:]] = str(v)
        self.app.call_from_thread(self._apply_info, info, attrs)

    def _apply_info(self, info: dict, attrs: dict[str, str]) -> None:
        # Renamed from ``_render`` to avoid colliding with Textual's
        # internal ``Widget._render()`` no-arg renderer. The earlier
        # name shadowed it and every paint pass crashed with
        # ``TypeError: _render() missing 2 required positional
        # arguments: 'info' and 'attrs'``.
        self._stat = info
        self._attrs = attrs
        try:
            self.query_one("#val_revs", Static).update(
                f"have={info.get('haveRev', '?')}  "
                f"head={info.get('headRev', '?')}  "
                f"headAction={info.get('headAction', '?')}",
            )
            current_type = str(info.get("headType", "?"))
            self.query_one("#val_type", Static).update(current_type)
            opener = info.get("action", "")
            other = info.get("otherOpen") or info.get("otherLock") or ""
            self.query_one("#val_open", Static).update(
                f"this client: {opener or 'not open'}    "
                f"other: {other or 'none'}",
            )
            size = info.get("fileSize", "?")
            self.query_one("#val_size", Static).update(str(size))
            ft_input = self.query_one("#filetype_input", Input)
            if not ft_input.value:
                ft_input.value = current_type if current_type != "?" else ""
            table = self.query_one("#attrs_table", DataTable)
            table.clear()
            for k in sorted(attrs):
                table.add_row(k, attrs[k], key=k)
            if not attrs:
                table.add_row("(no attributes)", "", key="__empty__")
        except Exception:  # noqa: BLE001
            pass

    # --- buttons --------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "close":
            self.dismiss(None)
        elif bid == "apply_filetype":
            new_type = self.query_one("#filetype_input", Input).value.strip()
            if not new_type:
                self.app.notify("Filetype cannot be empty.",
                                severity="warning", timeout=4)
                return
            self._apply_filetype(new_type)
        elif bid == "attr_add":
            key = self.query_one("#attr_key", Input).value.strip()
            value = self.query_one("#attr_value", Input).value
            if not key:
                self.app.notify("Attribute key cannot be empty.",
                                severity="warning", timeout=4)
                return
            self._set_attribute(key, value)
        elif bid == "attr_delete":
            self._delete_selected_attribute()

    def _delete_selected_attribute(self) -> None:
        table = self.query_one("#attrs_table", DataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            return
        try:
            keys = list(table.rows.keys())
            if table.cursor_row >= len(keys):
                return
            key = str(keys[table.cursor_row].value)
        except Exception:  # noqa: BLE001
            return
        if key in ("", "__empty__"):
            return
        self._delete_attribute(key)

    @work(thread=True, group="file_props_apply")
    def _apply_filetype(self, new_type: str) -> None:
        # `p4 reopen -t` requires the file to be opened in some CL.
        # The most common case is the file is open in default; fall
        # back to opening with `p4 edit` if it isn't.
        opener = str(self._stat.get("action", "") or "")
        if not opener:
            self.app.call_from_thread(
                self.app.notify,
                "File not open in this client. Run Check Out first, "
                "then reopen with the new filetype.",
                severity="warning", timeout=8,
            )
            return
        try:
            self._p4.run("reopen", "-t", new_type, self._depot_path)
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(
                self.app.notify,
                f"Reopen with type {new_type!r} failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.app.call_from_thread(
            self.app.notify,
            f"Reopened {self._depot_path} as {new_type}.",
            timeout=5,
        )
        self._reload()

    @work(thread=True, group="file_props_attr")
    def _set_attribute(self, key: str, value: str) -> None:
        try:
            self._p4.run(
                "attribute", "-n", key, "-v", value, self._depot_path,
            )
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(
                self.app.notify,
                f"Set attribute {key!r} failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.app.call_from_thread(
            self.app.notify, f"Set attribute {key} = {value!r}.",
            timeout=4,
        )
        self._reload()

    @work(thread=True, group="file_props_attr")
    def _delete_attribute(self, key: str) -> None:
        try:
            self._p4.run("attribute", "-n", key, self._depot_path)
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(
                self.app.notify,
                f"Delete attribute {key!r} failed: {e}",
                severity="error", timeout=10,
            )
            return
        self.app.call_from_thread(
            self.app.notify, f"Deleted attribute {key}.", timeout=4,
        )
        self._reload()

    # --- close ----------------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
