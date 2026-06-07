"""Bookmark picker (permalink-backed bookmarks).

Lists saved bookmarks; Enter jumps to the highlighted one (the app
resolves its permalink to the current path and navigates the
tree), Delete removes it in place, Esc closes. Returns the selected
bookmark's permalink id, or None.
"""
from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..bookmarks import BookmarkStore


class BookmarkPickerModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    BookmarkPickerModal { align: center middle; }
    BookmarkPickerModal > #dialog {
        width: 90%;
        height: 70%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    BookmarkPickerModal #title { text-style: bold; background: $boost; padding: 0 1; }
    BookmarkPickerModal #list { height: 1fr; margin-top: 1; }
    BookmarkPickerModal #hint { color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", priority=True),
        Binding("delete", "delete_current", "Delete"),
        Binding("d", "delete_current", "Delete"),
    ]

    def __init__(self, store: BookmarkStore) -> None:
        super().__init__()
        self._store = store

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Bookmarks ", id="title")
            yield OptionList(*self._options(), id="list")
            yield Static(
                "Enter: go · d / Del: remove · Esc: close",
                id="hint",
            )

    def _options(self) -> list[Option]:
        items = self._store.list()
        if not items:
            return [Option("(no bookmarks — Ctrl+B on a tree node to add)",
                           id="__none__", disabled=True)]
        return [
            Option(f"{b.label}    //@p/{b.permalink_id}", id=b.permalink_id)
            for b in items
        ]

    def on_mount(self) -> None:
        try:
            self.query_one("#list", OptionList).focus()
        except Exception:  # noqa: BLE001
            pass

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        oid = event.option.id
        if oid and oid != "__none__":
            self.dismiss(oid)

    def action_delete_current(self) -> None:
        try:
            lst = self.query_one("#list", OptionList)
            idx = lst.highlighted
            if idx is None:
                return
            opt = lst.get_option_at_index(idx)
            vid = getattr(opt, "id", None)
        except Exception:  # noqa: BLE001
            return
        if not vid or vid == "__none__":
            return
        self._store.remove(vid)
        # Rebuild the list in place so the picker stays open.
        lst.clear_options()
        for opt in self._options():
            lst.add_option(opt)
        try:
            self.app.notify("Bookmark removed.", timeout=2)
        except Exception:  # noqa: BLE001
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)
