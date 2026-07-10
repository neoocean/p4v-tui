"""Find-file modal: search depot by filename pattern, return picked path.

The user types a filename fragment (e.g. ``foo``) or a full p4 wildcard
(``//depot/.../*.py``); pressing Enter runs ``p4 files -m 100`` against
the resolved pattern. Results are listed in an OptionList; picking one
dismisses the modal with the chosen depot path. Esc cancels.

Plain-fragment queries get wrapped as ``//.../*<query>*`` so substring
filename matches across all depots work without remembering p4 syntax.
"""
from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class FindFileModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    FindFileModal { align: center middle; }
    FindFileModal > #dialog {
        width: 90%;
        height: 80%;
        border: thick $primary;
        background: $panel;
    }
    FindFileModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    FindFileModal #query {
        margin: 1 1 0 1;
    }
    FindFileModal #help {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    FindFileModal #results {
        background: transparent;
        height: 1fr;
        margin: 0 1 1 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, p4_service, search_index=None) -> None:
        super().__init__()
        self._p4 = p4_service
        # Optional Fast Search index. When provided, the modal falls
        # back to its loose token-AND search + Levenshtein "did you
        # mean…" suggestions if the server-side ``p4 files`` lookup
        # comes back empty. Keeps Find File usable even when the
        # user mistyped, mis-spaced, or dropped a slash.
        self._index = search_index

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Find File ", id="title")
            yield Input(
                placeholder="filename fragment or p4 pattern (e.g. *.py)",
                id="query",
            )
            yield Static(
                " Enter = search · ↑↓ Enter = pick · Esc = cancel ",
                id="help",
            )
            yield OptionList(id="results")

    def on_mount(self) -> None:
        self.query_one("#query", Input).focus()

    # --- search ----------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        q = event.value.strip()
        if not q:
            return
        # Stash the natural query so a 0-result branch can offer the
        # local-index fallback below (loose token-AND + suggestions).
        self._last_query = q
        # Heuristic: if user typed wildcards or path separators, take the
        # query as-is; otherwise wrap it as a substring search across all
        # depots.
        if any(ch in q for ch in ("/", "*")) or "..." in q:
            pattern = q
        else:
            pattern = f"//.../*{q}*"
        # Show "searching…" state immediately for feedback.
        opt_list = self.query_one("#results", OptionList)
        opt_list.clear_options()
        opt_list.add_option(Option(f" Searching {pattern}…",
                                   disabled=True))
        self._run_search(pattern)

    def _run_search(self, pattern: str) -> None:
        # Push p4 query to a worker thread; _on_results then refreshes
        # the OptionList via call_from_thread.
        def _bg() -> None:
            try:
                # ``-e``: only files that exist at head. The server drops
                # delete / move/delete / purge / archive in one shot —
                # a client-side ``action != "delete"`` would miss
                # ``move/delete`` (the old path of a rename) and surface
                # renamed-away paths as live hits. Same trap documented on
                # ``P4Service.files``.
                rows = self._p4.run("files", "-e", "-m", "100", pattern)
            except Exception:  # noqa: BLE001
                rows = []
            self.app.call_from_thread(self._on_results, pattern, rows)

        self.run_worker(
            _bg, thread=True, exclusive=True, group="find_file",
        )

    def _on_results(self, pattern: str, rows: list) -> None:
        # ``-e`` already excluded gone-at-head files server-side, so a
        # depotFile is all we need here.
        files = [r["depotFile"] for r in rows
                 if isinstance(r, dict)
                 and r.get("depotFile")]
        opt_list = self.query_one("#results", OptionList)
        opt_list.clear_options()
        if not files:
            # Server came back empty — try the local index's loose +
            # suggestion ladder before giving up. ``_last_query`` is
            # the raw user input (before the ``//.../*<q>*`` wrap)
            # so the loose matcher sees the natural string.
            loose_files: list[str] = []
            suggestions: list[str] = []
            raw_q = getattr(self, "_last_query", "") or pattern
            if self._index is not None and raw_q:
                try:
                    loose = self._index.query_files_loose(
                        raw_q, limit=200,
                    )
                    loose_files = [h.depot_path for h in loose]
                    if not loose_files:
                        suggestions = self._index.suggest_corrections(
                            raw_q,
                        )
                except Exception:  # noqa: BLE001
                    pass
            if loose_files:
                opt_list.add_option(Option(
                    f"  (server 0, local loose match — {len(loose_files)}):",
                    disabled=True,
                ))
                for f in loose_files:
                    opt_list.add_option(Option(f, id=f))
                opt_list.focus()
                return
            opt_list.add_option(Option(
                f" No matches for {pattern}", disabled=True,
            ))
            for s in suggestions:
                opt_list.add_option(Option(
                    f"   did you mean: {s}", disabled=True,
                ))
            return
        for f in files:
            # Use the depot path itself as the Option id so we can return
            # it directly on selection without extra bookkeeping.
            opt_list.add_option(Option(f, id=f))
        # Hand focus to the results list so the user can arrow + Enter
        # without an intermediate Tab.
        opt_list.focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        oid = event.option.id
        if not oid:
            return
        self.dismiss(oid)

    def action_cancel(self) -> None:
        self.dismiss(None)
