"""Fast Search modal — typing-as-you-go filename search with live
preview pane.

v1 scope per ``docs/search-scenario.md``:
  * filename + path substring search via the local SQLite index
  * smart-case (all-lowercase query → case-insensitive)
  * left results list, right preview pane synchronized to cursor
  * inline match highlight on result rows
  * body match highlight + n / N jump in preview
  * LRU(64) preview cache so re-visits are instant
  * offline fallback (index-only when disconnected)
  * Enter → navigate workspace/depot tree, Esc closes

Content / CL-description / regex / user filters arrive in v2.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Optional

from rich.text import Text

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from ..search_index import SearchHit, SearchIndex, find_match_spans


# Result list cap — beyond this we show "N more — narrow your query".
RESULT_CAP = 200


def _parse_nl_query(raw: str, current_user: str = "") -> dict:
    """Lightweight rule-based intent parser for ``nl:`` prefixed queries.

    Handles a small, common vocabulary in Korean and English that
    covers the README roadmap examples ("지난 주 내가 만진 파일",
    "TextureManager 수정한 CL 들"). No LLM / embeddings — just
    keyword tokenisation. Returns a dict with:

        user        — author filter (substring), or None
        time_from   — epoch second, or None
        time_to     — epoch second, or None
        keywords    — list of leftover residual tokens
        wants_cls   — True if the query mentions CL/changelist
                      (caller can route to query_changes instead of
                      query_files)

    Future v3-1 work can layer an embedding-based "did you mean
    this intent?" re-ranker on top; the rule-based base case has
    to come first so the modal is usable without a model file.
    """
    import re as _re
    import time as _t
    out = {"user": None, "time_from": None, "time_to": None,
           "keywords": [], "wants_cls": False}
    if not raw:
        return out
    now = int(_t.time())
    day = 86400
    text = raw.strip()
    # 1) CL / changelist anchors
    if _re.search(r"\b(cl|cls|changelist|체인지리스트|체인지)\b",
                  text, _re.IGNORECASE):
        out["wants_cls"] = True
    # 2) Time windows — match Korean + English aliases
    if _re.search(r"오늘|today", text, _re.IGNORECASE):
        out["time_from"] = now - day
    elif _re.search(r"어제|yesterday", text, _re.IGNORECASE):
        out["time_from"] = now - 2 * day
        out["time_to"] = now - day
    elif _re.search(
        r"이번\s*주|this\s*week|이번주", text, _re.IGNORECASE,
    ):
        out["time_from"] = now - 7 * day
    elif _re.search(
        r"지난\s*주|last\s*week|지난주", text, _re.IGNORECASE,
    ):
        out["time_from"] = now - 14 * day
        out["time_to"] = now - 7 * day
    elif _re.search(
        r"이번\s*달|this\s*month|이번달", text, _re.IGNORECASE,
    ):
        out["time_from"] = now - 30 * day
    elif _re.search(
        r"지난\s*달|last\s*month|지난달", text, _re.IGNORECASE,
    ):
        out["time_from"] = now - 60 * day
        out["time_to"] = now - 30 * day
    # 3) "I / me / 내가 / my" → current user
    if _re.search(r"\b(내가|내|my|me|i)\b", text, _re.IGNORECASE):
        if current_user:
            out["user"] = current_user
    # 4) Explicit @user:X still wins. Strip it from the residual so
    #    the keyword extractor below doesn't double-count.
    m = _re.search(r"@user:([A-Za-z0-9_.\-]+)", text)
    if m:
        out["user"] = m.group(1)
        text = text[:m.start()] + text[m.end():]
    # 5) Residual keywords — drop time/user/structural words.
    stop_words = {
        "오늘", "어제", "이번", "지난", "주", "달", "today",
        "yesterday", "this", "last", "week", "month", "i",
        "me", "my", "내", "내가", "이번주", "지난주",
        "cl", "cls", "changelist", "체인지리스트", "체인지",
        "파일", "files", "수정한", "edited", "modified",
        "만진", "touched", "changed",
    }
    tokens = [
        t for t in _re.split(r"\s+", text)
        if t and t.lower() not in stop_words
        and not t.lower().startswith("@user:")
    ]
    out["keywords"] = tokens
    return out


def _parse_search_query(raw: str) -> dict:
    """Split a Fast Search query into its modal pieces.

    Recognised tokens (anywhere in the string):

      * ``@user:<value>``  — head-user substring filter.
      * ``type:<value>``   — filetype substring filter (matches
                             ``text``, ``text+x``, ``binary+l`` …).
      * ``/pattern/flags`` — Python regex; flags string accepts
                             ``i`` (IGNORECASE), ``m`` (MULTILINE),
                             ``s`` (DOTALL). Bare ``/`` not closed
                             with another ``/`` is treated as a
                             literal substring instead.

    Returns a dict ``{"substr", "regex", "user", "ftype"}``; missing
    keys carry ``None``. The substring component is the residual
    whitespace-joined tokens after the named filters are extracted —
    so ``"texture @user:alice"`` ends up with substr="texture",
    user="alice".
    """
    import re as _re
    out = {"substr": None, "regex": None, "user": None, "ftype": None}
    if not raw or not raw.strip():
        return out
    # Tokenise on whitespace. The regex literal ``/foo bar/`` would be
    # broken by naive split, so handle it as a special-case before
    # splitting.
    rx_match = _re.search(r"/(.+?)/([imsx]*)(?=\s|$)", raw)
    if rx_match:
        pat_src, flags_s = rx_match.group(1), rx_match.group(2)
        flags = 0
        for ch in flags_s:
            flags |= {"i": _re.IGNORECASE,
                      "m": _re.MULTILINE,
                      "s": _re.DOTALL,
                      "x": _re.VERBOSE}.get(ch, 0)
        try:
            out["regex"] = _re.compile(pat_src, flags)
        except _re.error:
            out["regex"] = None
        raw = raw[:rx_match.start()] + raw[rx_match.end():]
    tokens = raw.split()
    residual: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low.startswith("@user:"):
            out["user"] = tok.split(":", 1)[1] or None
        elif low.startswith("type:"):
            out["ftype"] = tok.split(":", 1)[1] or None
        else:
            residual.append(tok)
    if residual:
        out["substr"] = " ".join(residual)
    return out

# Cap tiers cycled by Ctrl+Shift+L. ``None`` means "no cap" — the
# index returns everything that matches, useful for one-off "where
# does this string appear at all?" queries on smaller depots. The
# tier is local to a modal instance; closing the modal resets to
# the default cap so a global "unlimited" doesn't accidentally
# survive across sessions.
_CAP_TIERS = (200, 2000, None)
_CAP_TIER_LABELS = {200: "200", 2000: "2 K", None: "unlimited"}

# Plaintext preview viewport — render up to this many lines on
# first paint. The rest stream in as the user scrolls.
PREVIEW_VIEWPORT_LINES = 10_000

# LRU cache size for preview contents (depot_path → list[str]).
PREVIEW_CACHE_SIZE = 64

# Debounce typing → query dispatch. 120 ms instead of "as fast as
# possible" is a deliberate trade-off for Hangul IME composition:
# composing a single syllable (e.g. "한" via ㅎ → 하 → 한) fires
# three intermediate ``on_input_changed`` events from Windows
# Terminal / xterm, separated by 50 – 100 ms each on slower
# machines. A 30 ms debounce wasn't long enough to collapse them
# into one dispatch — every step triggered a full SQLite scan and
# the UI stuttered. 120 ms is still well under a typist's
# perception threshold for ASCII input but long enough for the
# IME to settle.
TYPING_DEBOUNCE_MS = 120


class SearchModal(ModalScreen[Optional[str]]):
    """Returns the picked depot path on Enter, or ``None`` on Esc."""

    DEFAULT_CSS = """
    SearchModal { align: center middle; }
    SearchModal > #dialog {
        width: 98%;
        height: 95%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    SearchModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    SearchModal #query { margin-top: 0; }
    SearchModal #stats {
        color: $text-muted;
        padding: 0 1;
    }
    SearchModal #cols_row { height: 1fr; margin-top: 1; }
    SearchModal #results {
        width: 50%;
        background: $surface;
    }
    SearchModal #preview_pane { width: 50%; }
    SearchModal #preview_title {
        background: $boost;
        text-style: bold;
        padding: 0 1;
    }
    SearchModal #preview_log {
        height: 1fr;
        background: $surface;
    }
    SearchModal #preview_status {
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", priority=True),
        Binding("ctrl+f", "cancel", "Close", priority=True),
        Binding("enter", "open_in_tree", "Open", priority=True),
        Binding("ctrl+enter", "open_viewer", "Viewer", priority=True),
        Binding("d", "diff_hit", "Diff vs have"),
        Binding("g", "get_hit", "Get latest"),
        Binding("n", "next_match", "Next match"),
        Binding("shift+n", "prev_match", "Prev match"),
        Binding("ctrl+r", "rebuild_index", "Rebuild idx"),
        Binding("ctrl+shift+l", "cycle_cap", "Cap"),
        Binding("ctrl+p", "history_prev", "Prev query"),
        Binding("ctrl+n", "history_next", "Next query"),
    ]

    def __init__(
        self,
        index: SearchIndex,
        p4_service,
        *,
        index_status: str = "",
        initial_query: str = "",
    ) -> None:
        super().__init__()
        self._index = index
        self._p4 = p4_service
        # ``index_status`` is the App's snapshot of indexer health
        # at modal-open time ("fresh", "Indexing 42 %", "offline +
        # last-known", …). Drawn into the stats line as-is.
        self._index_status = index_status
        # When opened from a "Search In This Folder…" context-menu
        # item the caller pre-fills the query with the depot path
        # of the focused tree node. ``on_mount`` pushes this into
        # the Input widget and kicks off the first query so the
        # user sees results immediately rather than an empty modal.
        self._initial_query = (initial_query or "").strip()
        # Current query and results pointed by cursor.
        self._query: str = ""
        self._hits: list[SearchHit] = []
        # Typo-recovery suggestions surfaced when both strict and
        # loose searches return zero hits. Populated by the worker
        # callback, rendered as disabled "did you mean…" rows.
        self._suggestions: list[str] = []
        # Result-cap tier — cycled by Ctrl+Shift+L. ``None`` = no cap.
        # Start on the smallest tier so the UI stays snappy for casual
        # use; users explicitly opt into wider scans.
        self._cap_idx = 0
        # Recent-query stack ("tabs") for Ctrl+P / Ctrl+N. Persists
        # across modal instances via the App so users can re-summon
        # the last few queries without re-typing. Cap at 20 — enough
        # to cover a coding session, small enough that JSON state
        # stays tiny.
        self._history_pos = -1  # -1 = "current input, not history"
        self._cursor_idx: int = 0
        # LRU file content cache: depot_path → list[str] (lines).
        self._preview_cache: "OrderedDict[str, list[str]]" = OrderedDict()
        # Cached match-line index within the active preview, used
        # by n / N jumping.
        self._preview_match_lines: list[int] = []
        self._preview_match_pos: int = -1
        # Suppress on_input_changed dispatch during programmatic
        # value sets (currently unused but kept for v2 expansion).
        self._suppress_dispatch: bool = False

    # --- compose --------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static(" Fast Search ", id="title")
            yield Input(
                placeholder="type a filename fragment — / for regex (v2) "
                            "· ? for content (v2) · cl: (v2) · Esc to close",
                id="query",
            )
            yield Static(
                "  type to search · ↑↓ navigate · "
                "Enter open in tree · Ctrl+Enter viewer · "
                "n/N jump match · Ctrl+R rebuild",
                id="stats",
            )
            with Horizontal(id="cols_row"):
                yield OptionList(id="results")
                with Container(id="preview_pane"):
                    yield Static(" (no file selected) ",
                                 id="preview_title")
                    yield RichLog(highlight=False, markup=True,
                                  wrap=False, id="preview_log")
                    yield Static("", id="preview_status")

    def on_mount(self) -> None:
        try:
            inp = self.query_one("#query", Input)
            if self._initial_query:
                # Pre-fill from a "Search In This Folder…" trigger and
                # fire the query immediately. ``on_input_changed`` will
                # take care of the debounce-and-schedule path; we just
                # nudge the Input's value and selection.
                inp.value = self._initial_query
                self._query = self._initial_query
                try:
                    inp.cursor_position = len(self._initial_query)
                except Exception:  # noqa: BLE001
                    pass
            inp.focus()
        except Exception:  # noqa: BLE001
            pass
        if self._initial_query:
            self._run_query_worker(self._initial_query)
        self._refresh_stats(elapsed_ms=0, total=0)

    # --- typing --------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "query":
            return
        if self._suppress_dispatch:
            return
        # Debounce: set a single-shot timer; if a newer keystroke
        # arrives before it fires, the previous one is overwritten
        # by Textual's timer infrastructure (we hold a handle).
        self._query = event.value
        self._schedule_query_dispatch()

    _query_timer = None

    def _schedule_query_dispatch(self) -> None:
        # Cancel pending timer (if any) so the most recent keystroke
        # is what we serve.
        prev = self._query_timer
        if prev is not None:
            try:
                prev.stop()
            except Exception:  # noqa: BLE001
                pass
        self._query_timer = self.set_timer(
            TYPING_DEBOUNCE_MS / 1000.0,
            self._dispatch_query,
        )

    def _dispatch_query(self) -> None:
        q = (self._query or "").strip()
        if not q:
            # Empty query — synchronous "clear" is cheap and keeps
            # the bottom-pane "type to begin" state predictable.
            self._hits = []
            self._render_results(elapsed_ms=0)
            self._show_no_preview()
            return
        # SQLite query goes through the JobRunner-equivalent worker
        # so the UI thread stays responsive — without this, every
        # Hangul composition step blocks the Input for the duration
        # of the scan (10-50 ms on 100k-row indexes, perceptible
        # when stacked 3-4 deep per syllable).
        self._run_query_worker(q)

    def _push_history(self, query: str) -> None:
        """Record ``query`` in the App-wide recent-query stack.

        Stack is newest-first, deduped, capped at 20. Stored on the
        App so it survives across modal opens (Ctrl+P / Ctrl+N picks
        it up next time the modal is summoned). Empty / prefix-only
        queries are not recorded — `?` alone isn't a useful jump-back
        target.
        """
        q = (query or "").strip()
        if not q or q in ("?", "cl:", "nl:"):
            return
        try:
            hist = list(getattr(self.app, "_search_history", []) or [])
        except Exception:  # noqa: BLE001
            hist = []
        if hist and hist[0] == q:
            return
        hist = [q] + [x for x in hist if x != q]
        hist = hist[:20]
        try:
            self.app._search_history = hist
        except Exception:  # noqa: BLE001
            pass

    @work(thread=True, group="search_index_query", exclusive=True)
    def _run_query_worker(self, query: str) -> None:
        """Run a SQLite query off the UI thread and call back on the
        UI thread with the result. ``exclusive=True`` cancels the
        in-flight worker so a stream of keystrokes only races the
        latest one to completion — the stale-result check in
        :meth:`_on_query_result` discards anything that lost the
        race anyway.

        Path-tolerance ladder:
          1. ``query_files`` — strict substring (case-smart).
          2. If 0 hits, ``query_files_loose`` — split on whitespace +
             ``/`` and AND across tokens, so ``foo bar`` finds
             ``//x/foo_bar`` and ``//x/foo/bar/baz`` alike.
          3. If still 0 hits, ``suggest_corrections`` returns near
             leaf names (Levenshtein ≤ 2) so the user can recover
             from a typo without retyping the whole path.
        """
        import time as _time
        t0 = _time.monotonic()
        suggestions: list[str] = []
        try:
            current_cap = _CAP_TIERS[self._cap_idx % len(_CAP_TIERS)]
            # Effective limit for the underlying SQL: ``None`` => a
            # large sentinel so we don't have to special-case the
            # signature; SQLite happily accepts a 10M LIMIT.
            eff = 10_000_000 if current_cap is None else int(current_cap)
            # `?<query>` content-grep mode: forwarded to p4 grep,
            # results converted to SearchHit so the rest of the
            # render path is unchanged. Heavier than the local
            # index but the modal already debounces typing and
            # ``exclusive=True`` cancels stale workers, so a typing
            # storm doesn't queue up parallel grep scans.
            if query.startswith("nl:"):
                # Natural-language mode (R3 / FS-v3-1). Rule-based
                # intent parsing only — no LLM. Translates the
                # natural-language phrase to filtered substring
                # queries against either the files or changes
                # table. Embedding-backed re-ranking is left for a
                # future iteration; the rule-based base is enough
                # for the common cases on the roadmap.
                nl_text = query[3:].strip()
                cur_user = ""
                try:
                    cur_user = getattr(self._p4, "user", "") or ""
                except Exception:  # noqa: BLE001
                    pass
                intent = _parse_nl_query(nl_text, current_user=cur_user)
                kw = " ".join(intent["keywords"])
                hits = []
                if intent["wants_cls"]:
                    rows = self._index.query_changes(
                        kw, limit=min(eff, 500),
                    ) if kw else []
                    if not rows:
                        try:
                            seeded = self._p4.run(
                                "changes", "-l", "-m", "500",
                            )
                            self._index.upsert_changes(seeded)
                        except Exception:  # noqa: BLE001
                            seeded = []
                        rows = self._index.query_changes(
                            kw, limit=min(eff, 500),
                        ) if kw else []
                    for cr in rows:
                        if intent["time_from"] and cr["time"] < intent["time_from"]:
                            continue
                        if intent["time_to"] and cr["time"] > intent["time_to"]:
                            continue
                        if intent["user"] and intent["user"].lower() not in (cr["user"] or "").lower():
                            continue
                        hits.append(SearchHit(
                            depot_path=f"cl/{cr['change']}",
                            head_time=cr["time"], head_user=cr["user"],
                            head_action="", type="",
                        ))
                else:
                    raw_hits = self._index.query_files_filtered(
                        substr=kw or None,
                        regex=None,
                        user=intent["user"],
                        ftype=None,
                        limit=eff,
                    )
                    for h in raw_hits:
                        if intent["time_from"] and h.head_time < intent["time_from"]:
                            continue
                        if intent["time_to"] and h.head_time > intent["time_to"]:
                            continue
                        hits.append(h)
                elapsed_ms = int((_time.monotonic() - t0) * 1000)
                self.app.call_from_thread(
                    self._on_query_result, query, hits, elapsed_ms,
                    suggestions,
                )
                return
            if query.startswith("cl:"):
                # Changelist-description mode. Look in the local
                # ``changes`` table first; if empty (cold cache, very
                # likely on first run since the indexer doesn't fill
                # changes yet), fall back to a one-shot ``p4 changes
                # -m N -l`` and upsert what we got so subsequent
                # queries are instant. Result rows are converted to
                # SearchHit with depot_path="cl/<N>" — the modal's
                # path / preview pipeline treats that as a normal
                # row and we open the Submitted CL detail viewer
                # on Enter instead of a file.
                needle = query[3:].strip()
                hits = []
                if needle:
                    rows = self._index.query_changes(
                        needle, limit=min(eff, 500),
                    )
                    if not rows:
                        # Lazy seed: pull recent CLs from the server
                        # and stuff them into the changes table so
                        # follow-up keystrokes hit the index.
                        try:
                            seeded = self._p4.run(
                                "changes", "-l", "-m", "500",
                            )
                        except Exception:  # noqa: BLE001
                            seeded = []
                        try:
                            self._index.upsert_changes(seeded)
                        except Exception:  # noqa: BLE001
                            pass
                        rows = self._index.query_changes(
                            needle, limit=min(eff, 500),
                        )
                    for cr in rows:
                        hits.append(SearchHit(
                            depot_path=f"cl/{cr['change']}",
                            head_time=cr.get("time", 0),
                            head_user=cr.get("user", ""),
                            head_action="",
                            type="",
                        ))
                elapsed_ms = int((_time.monotonic() - t0) * 1000)
                self.app.call_from_thread(
                    self._on_query_result, query, hits, elapsed_ms,
                    suggestions,
                )
                return
            if query.startswith("?"):
                pattern = query[1:].strip()
                if not pattern:
                    elapsed_ms = int((_time.monotonic() - t0) * 1000)
                    self.app.call_from_thread(
                        self._on_query_result, query, [], elapsed_ms,
                        suggestions,
                    )
                    return
                # Streaming grep — first match shows up in tens of
                # milliseconds even on huge depots. ``cancel_flag``
                # is consulted before every row delivery so a fresh
                # keystroke (which schedules a new worker via the
                # debounced ``_run_query_worker``) tells the server-
                # side iterator to stop. Without it a typing burst
                # could leave the previous grep running invisibly
                # for the duration of a whole-depot walk.
                self._grep_cancel = getattr(self, "_grep_cancel", None)
                if self._grep_cancel is not None:
                    try:
                        self._grep_cancel.set()
                    except Exception:  # noqa: BLE001
                        pass
                import threading as _threading
                cancel = _threading.Event()
                self._grep_cancel = cancel
                seen: set[str] = set()
                streamed: list[SearchHit] = []

                def _flush_partial(snapshot: list[SearchHit]) -> None:
                    # UI-thread callable; renders what's arrived so
                    # far without disturbing the user's cursor or
                    # the live status line of the modal.
                    if (self._query or "").strip() != query:
                        # Stale stream — don't paint.
                        return
                    self._hits = list(snapshot)
                    self._suggestions = []
                    self._render_results(elapsed_ms=0)
                    if snapshot and self._cursor_idx == 0:
                        self._load_preview_for(
                            snapshot[0].depot_path,
                        )

                last_flush_at = [_time.monotonic()]

                def _on_match(row, _s=streamed, _seen=seen,
                              _q=query, _last=last_flush_at,
                              _c=cancel) -> None:
                    df = row.get("depotFile") if isinstance(row, dict) else None
                    if not df:
                        return
                    if df in _seen:
                        return
                    _seen.add(df)
                    line = str(
                        row.get("matchedLine") or row.get("line") or ""
                    ).rstrip("\n")
                    try:
                        lineno = int(row.get("lineNumber") or 0)
                    except (TypeError, ValueError):
                        lineno = 0
                    _s.append(SearchHit(
                        depot_path=str(df),
                        head_time=0,
                        head_user="",
                        head_action="",
                        type="",
                        match_line=line,
                        match_lineno=lineno,
                    ))
                    # Throttle UI flushes to ~6 fps — every match
                    # repainting the OptionList is hostile to the
                    # eyeball.
                    now = _time.monotonic()
                    if now - _last[0] >= 0.15:
                        _last[0] = now
                        try:
                            self.app.call_from_thread(
                                _flush_partial, list(_s),
                            )
                        except Exception:  # noqa: BLE001
                            _c.set()

                try:
                    self._p4.grep_stream(
                        pattern,
                        scope="//...",
                        on_match=_on_match,
                        cancelled=cancel.is_set,
                        case_insensitive=(pattern == pattern.lower()),
                        max_matches=min(eff, 500),
                    )
                except Exception:  # noqa: BLE001
                    pass
                elapsed_ms = int((_time.monotonic() - t0) * 1000)
                # Final flush — same shape as the partial flushes so
                # the user-facing render is consistent.
                self.app.call_from_thread(
                    self._on_query_result, query, list(streamed),
                    elapsed_ms, suggestions,
                )
                return
            parsed = _parse_search_query(query)
            if (parsed["user"] or parsed["ftype"] or parsed["regex"]):
                # Any explicit filter present → run the filtered
                # path. Loose / suggestions don't apply because the
                # user has narrowed the scope on purpose.
                hits = self._index.query_files_filtered(
                    substr=parsed["substr"],
                    regex=parsed["regex"],
                    user=parsed["user"],
                    ftype=parsed["ftype"],
                    limit=eff,
                )
            else:
                hits = self._index.query_files(query, limit=eff)
                if not hits:
                    hits = self._index.query_files_loose(
                        query, limit=eff,
                    )
                if not hits:
                    suggestions = self._index.suggest_corrections(query)
        except Exception:  # noqa: BLE001
            hits = []
        elapsed_ms = int((_time.monotonic() - t0) * 1000)
        self.app.call_from_thread(
            self._on_query_result, query, hits, elapsed_ms, suggestions,
        )

    def _on_query_result(
        self,
        query: str,
        hits: list,
        elapsed_ms: int,
        suggestions: list[str] | None = None,
    ) -> None:
        """Apply a worker's result, but only if the user hasn't typed
        more since this query left. Otherwise drop silently — the
        newer worker is either running or done already."""
        if (self._query or "").strip() != query:
            return
        self._hits = list(hits)
        self._suggestions = list(suggestions or [])
        self._cursor_idx = 0
        self._render_results(elapsed_ms=elapsed_ms)
        if hits:
            self._load_preview_for(hits[0].depot_path)
        else:
            self._show_no_preview()
        # Successful queries (anything that finished — empty hits is
        # fine, indicates a useful "explored that direction" event)
        # get pushed into the recent-query stack for Ctrl+P / N.
        # Reset the per-instance history cursor so the next Ctrl+P
        # starts at the head of the stack instead of mid-walk.
        self._push_history(query)
        self._history_pos = -1

    # --- result rendering ----------------------------------------------

    def _render_results(self, *, elapsed_ms: int) -> None:
        try:
            lst = self.query_one("#results", OptionList)
        except Exception:  # noqa: BLE001
            return
        lst.clear_options()
        if not self._query.strip():
            lst.add_option(Option(
                " type a filename fragment to begin ", disabled=True,
            ))
            self._refresh_stats(elapsed_ms=elapsed_ms, total=0)
            return
        if not self._hits:
            lst.add_option(Option(
                f" no matches for {self._query!r} ", disabled=True,
            ))
            # Typo recovery: if the SearchIndex came back with near-
            # leaf suggestions, surface them as actionable "did you
            # mean…?" rows. ``id`` is prefixed with ``__suggest__``
            # so :meth:`action_open_in_tree` can intercept selection
            # and rewrite the Input value instead of trying to open
            # the suggestion as a depot path.
            for sug in getattr(self, "_suggestions", None) or []:
                lst.add_option(Option(
                    f"   did you mean: {sug}  (Enter to use)",
                    id=f"__suggest__{sug}",
                ))
            self._refresh_stats(elapsed_ms=elapsed_ms, total=0)
            return
        # Content-grep hits carry the first matched line so we can
        # render an inline diff-style preview (FS-v3-3). Strip the
        # leading `?` from the highlight key so the match phrase is
        # the bit that gets bold-yellowed in both the path (which
        # rarely contains it) and the inline line.
        highlight_key = self._query
        if highlight_key.startswith("?"):
            highlight_key = highlight_key[1:].strip()
        for hit in self._hits:
            path_text = _highlight_path(hit.depot_path, highlight_key)
            if hit.match_line:
                line = hit.match_line
                # CJK-aware truncate to one ~80-cell line — match
                # column ranking in the path uses the same budget.
                from ..utils import truncate_cells as _tc
                line = _tc(line, 100)
                inline = _highlight_path(line, highlight_key)
                from rich.text import Text as _T
                combined = _T()
                combined.append_text(path_text)
                lineno_tag = (
                    f"  :{hit.match_lineno}  "
                    if hit.match_lineno else "  ▸ "
                )
                combined.append("\n   ", style="dim")
                combined.append(lineno_tag, style="dim")
                combined.append_text(inline)
                lst.add_option(Option(combined, id=hit.depot_path))
            else:
                lst.add_option(Option(path_text, id=hit.depot_path))
        current_cap = _CAP_TIERS[self._cap_idx % len(_CAP_TIERS)]
        if current_cap is not None and len(self._hits) >= current_cap:
            lst.add_option(Option(
                f" — showing first {current_cap} (Ctrl+Shift+L for "
                "more) —",
                disabled=True,
            ))
        self._refresh_stats(
            elapsed_ms=elapsed_ms, total=len(self._hits),
        )
        # Reset cursor + load preview for top result.
        try:
            lst.highlighted = 0
        except Exception:  # noqa: BLE001
            pass

    def _refresh_stats(self, *, elapsed_ms: int, total: int) -> None:
        try:
            stats = self.query_one("#stats", Static)
        except Exception:  # noqa: BLE001
            return
        suffix = (
            f" · {total} match(es) · {elapsed_ms} ms"
            if total or elapsed_ms else ""
        )
        idx = (
            f" [Index: {self._index_status}]"
            if self._index_status else ""
        )
        stats.update(
            "  type to search · ↑↓ navigate · Enter open in tree · "
            f"Ctrl+Enter viewer · n/N jump match · Ctrl+R rebuild"
            f"{suffix}{idx}"
        )

    # --- results cursor → preview ----------------------------------

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted,
    ) -> None:
        if event.option_list.id != "results":
            return
        oid = event.option.id
        if not oid:
            return
        # Find idx for later n/N jump support
        for i, h in enumerate(self._hits):
            if h.depot_path == oid:
                self._cursor_idx = i
                break
        self._load_preview_for(oid)

    # --- preview --------------------------------------------------------

    def _show_no_preview(self) -> None:
        try:
            title = self.query_one("#preview_title", Static)
            log = self.query_one("#preview_log", RichLog)
            stat = self.query_one("#preview_status", Static)
        except Exception:  # noqa: BLE001
            return
        title.update(" (no file selected) ")
        log.clear()
        stat.update("")
        self._preview_match_lines = []
        self._preview_match_pos = -1

    def _load_preview_for(self, depot_path: str) -> None:
        # Pull cached content synchronously; only network-bound paths
        # go to the worker.
        if depot_path in self._preview_cache:
            lines = self._preview_cache.pop(depot_path)
            # LRU bump.
            self._preview_cache[depot_path] = lines
            self._render_preview(depot_path, lines, from_cache=True)
            return
        # Show "loading" stub immediately so the user sees feedback
        # before the worker returns.
        self._render_preview_loading(depot_path)
        self._fetch_preview(depot_path)

    def _render_preview_loading(self, depot_path: str) -> None:
        try:
            title = self.query_one("#preview_title", Static)
            log = self.query_one("#preview_log", RichLog)
            stat = self.query_one("#preview_status", Static)
        except Exception:  # noqa: BLE001
            return
        title.update(f" {depot_path} ")
        log.clear()
        log.write(Text("  loading…", style="dim"))
        stat.update("")

    @work(thread=True, group="search_preview_fetch", exclusive=True)
    def _fetch_preview(self, depot_path: str) -> None:
        try:
            result = self._p4.run("print", "-q", depot_path)
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(
                self._render_preview_error, depot_path, str(e),
            )
            return
        text_parts: list[str] = []
        for item in result:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, (bytes, bytearray)):
                try:
                    text_parts.append(bytes(item).decode("utf-8"))
                except UnicodeDecodeError:
                    text_parts.append(
                        bytes(item).decode("utf-8", errors="replace"),
                    )
        joined = "".join(text_parts)
        sample = joined[:8192]
        if sample and sample.count("\x00") > max(1, len(sample) // 100):
            lines = [
                f"[Binary file — {len(joined)} bytes]",
                "Cannot display in preview pane.",
            ]
        else:
            lines = joined.splitlines()
        # Cache eviction.
        if len(self._preview_cache) >= PREVIEW_CACHE_SIZE:
            self._preview_cache.popitem(last=False)
        self._preview_cache[depot_path] = lines
        self.app.call_from_thread(
            self._render_preview, depot_path, lines, False,
        )

    def _render_preview_error(
        self, depot_path: str, message: str,
    ) -> None:
        try:
            title = self.query_one("#preview_title", Static)
            log = self.query_one("#preview_log", RichLog)
            stat = self.query_one("#preview_status", Static)
        except Exception:  # noqa: BLE001
            return
        title.update(f" {depot_path} ")
        log.clear()
        log.write(Text(message, style="red"))
        stat.update("  preview unavailable")
        self._preview_match_lines = []
        self._preview_match_pos = -1

    def _render_preview(
        self,
        depot_path: str,
        lines: list[str],
        from_cache: bool,
    ) -> None:
        # Stale-result guard — user may have scrolled past this row
        # while the print worker was in flight.
        if (self._cursor_idx < len(self._hits)
                and self._hits[self._cursor_idx].depot_path != depot_path):
            return
        try:
            title = self.query_one("#preview_title", Static)
            log = self.query_one("#preview_log", RichLog)
            stat = self.query_one("#preview_status", Static)
        except Exception:  # noqa: BLE001
            return
        title.update(f" {depot_path} ")
        log.clear()
        query = self._query.strip()
        match_lines: list[int] = []
        # Render up to the viewport cap; the rest is reachable via
        # RichLog's own scrollback if needed in v2.
        viewport = lines[:PREVIEW_VIEWPORT_LINES]
        for idx, line in enumerate(viewport, start=1):
            spans = find_match_spans(line, query) if query else []
            if spans:
                match_lines.append(idx)
            log.write(_highlight_line(idx, line, spans))
        self._preview_match_lines = match_lines
        # Jump to first match in this preview so the user lands on
        # something meaningful for content-style queries.
        if match_lines:
            self._preview_match_pos = 0
            try:
                # RichLog exposes its scrollable region via
                # scroll_to (line is 0-indexed in the log's internal
                # buffer; our 1-indexed display means subtract one).
                log.scroll_to(y=max(0, match_lines[0] - 4), animate=False)
            except Exception:  # noqa: BLE001
                pass
        else:
            self._preview_match_pos = -1
        cache_note = "  (cached)" if from_cache else ""
        if len(lines) > PREVIEW_VIEWPORT_LINES:
            cap_note = (
                f"  · {len(lines)} lines total — first "
                f"{PREVIEW_VIEWPORT_LINES} shown"
            )
        else:
            cap_note = f"  · {len(lines)} lines"
        match_note = (
            f"  · {len(match_lines)} match line(s)" if match_lines else ""
        )
        minimap = _build_minimap(len(viewport), match_lines)
        # Status line: file + counts on first half, minimap on the
        # second half. Spaces between guarantee they don't run into
        # each other when terminal width is small — the minimap
        # truncates gracefully because it's a fixed 40 cells.
        stat.update(
            f"  {depot_path}{cap_note}{match_note}{cache_note}"
            f"   {minimap}"
        )

    # --- match jump (n / N) -------------------------------------------

    def action_next_match(self) -> None:
        if not self._preview_match_lines:
            self.app.notify("No matches in preview.", timeout=3)
            return
        self._preview_match_pos = (
            (self._preview_match_pos + 1) % len(self._preview_match_lines)
        )
        self._scroll_to_match_pos()

    def action_prev_match(self) -> None:
        if not self._preview_match_lines:
            self.app.notify("No matches in preview.", timeout=3)
            return
        n = len(self._preview_match_lines)
        self._preview_match_pos = (
            (self._preview_match_pos - 1) % n
        )
        self._scroll_to_match_pos()

    def _scroll_to_match_pos(self) -> None:
        try:
            log = self.query_one("#preview_log", RichLog)
        except Exception:  # noqa: BLE001
            return
        target_line = self._preview_match_lines[self._preview_match_pos]
        try:
            log.scroll_to(y=max(0, target_line - 4), animate=False)
        except Exception:  # noqa: BLE001
            pass

    # --- enter / close ------------------------------------------------

    def action_open_in_tree(self) -> None:
        # Always pick whatever the OptionList cursor is on, even if
        # the user is still typing in the Input.
        try:
            lst = self.query_one("#results", OptionList)
        except Exception:  # noqa: BLE001
            self.dismiss(None)
            return
        idx = lst.highlighted
        if idx is None:
            self.dismiss(None)
            return
        # "Did you mean…" rows are pseudo-results — their option id
        # starts with __suggest__. Selecting one rewrites the query
        # so the user can keep refining without retyping.
        try:
            opt = lst.get_option_at_index(idx)
            oid = getattr(opt, "id", None) or ""
        except Exception:  # noqa: BLE001
            oid = ""
        if oid.startswith("__suggest__"):
            replacement = oid[len("__suggest__"):]
            try:
                inp = self.query_one("#query", Input)
                inp.value = replacement
                inp.cursor_position = len(replacement)
                inp.focus()
            except Exception:  # noqa: BLE001
                pass
            return
        if self._cursor_idx < 0 or self._cursor_idx >= len(self._hits):
            self.dismiss(None)
            return
        self.dismiss(self._hits[self._cursor_idx].depot_path)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        if event.option_list.id != "results":
            return
        oid = event.option.id
        if not oid:
            return
        # Did-you-mean rows: rewrite the Input instead of dismissing.
        if oid.startswith("__suggest__"):
            replacement = oid[len("__suggest__"):]
            try:
                inp = self.query_one("#query", Input)
                inp.value = replacement
                inp.cursor_position = len(replacement)
                inp.focus()
            except Exception:  # noqa: BLE001
                pass
            return
        self.dismiss(oid)

    def action_open_viewer(self) -> None:
        # Defer to the App via dismiss with a tagged dict — the App
        # handler distinguishes "open in tree" (string return) from
        # "open viewer" (dict return). Keeps the modal simple.
        if (self._cursor_idx < 0
                or self._cursor_idx >= len(self._hits)):
            return
        path = self._hits[self._cursor_idx].depot_path
        self.dismiss({"viewer": path})

    def action_diff_hit(self) -> None:
        # Diff the highlighted hit against the have revision (item 3).
        if 0 <= self._cursor_idx < len(self._hits):
            self.dismiss({"diff": self._hits[self._cursor_idx].depot_path})

    def action_get_hit(self) -> None:
        # Get-latest the highlighted hit (chunked / resilient) (item 3).
        if 0 <= self._cursor_idx < len(self._hits):
            self.dismiss({"get": self._hits[self._cursor_idx].depot_path})

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

    def action_rebuild_index(self) -> None:
        # App-level concern; surface a signal it can pick up.
        self.dismiss({"rebuild": True})

    def action_history_prev(self) -> None:
        """Ctrl+P — step backward through the saved query stack."""
        self._step_history(-1)

    def action_history_next(self) -> None:
        """Ctrl+N — step forward; -1 brings the Input back to its
        live value (the last thing the user typed before stepping)."""
        self._step_history(+1)

    def _step_history(self, delta: int) -> None:
        try:
            hist = list(getattr(self.app, "_search_history", []) or [])
        except Exception:  # noqa: BLE001
            hist = []
        if not hist:
            try:
                self.app.notify("No saved queries yet", timeout=3)
            except Exception:  # noqa: BLE001
                pass
            return
        n = len(hist)
        new_pos = self._history_pos + delta
        if new_pos < -1:
            new_pos = -1
        if new_pos >= n:
            new_pos = n - 1
        self._history_pos = new_pos
        try:
            inp = self.query_one("#query", Input)
        except Exception:  # noqa: BLE001
            return
        if new_pos == -1:
            return
        new_val = hist[new_pos]
        inp.value = new_val
        try:
            inp.cursor_position = len(new_val)
        except Exception:  # noqa: BLE001
            pass

    def action_cycle_cap(self) -> None:
        """Cycle the result-cap tier and re-run the current query.

        200 → 2 K → unlimited → 200. Reflects in the status line so
        the user knows what tier is active without opening the menu.
        Toasts the new tier for accessibility on narrow terminals
        where the status line truncates.
        """
        self._cap_idx = (self._cap_idx + 1) % len(_CAP_TIERS)
        new_cap = _CAP_TIERS[self._cap_idx]
        label = _CAP_TIER_LABELS.get(new_cap, str(new_cap))
        try:
            self.app.notify(f"Result cap: {label}", timeout=3)
        except Exception:  # noqa: BLE001
            pass
        # Re-run the current query — for the unlimited tier this can
        # be a heavy SQL scan, but it's an explicit user gesture so
        # the wait is anticipated.
        if (self._query or "").strip():
            self._run_query_worker(self._query.strip())
        else:
            self._render_results(elapsed_ms=0)


# --- formatting helpers ------------------------------------------------


def _build_minimap(
    total_lines: int,
    match_lines: list[int],
    width: int = 40,
) -> str:
    """Compact minimap of match positions across the preview.

    Each character represents one chunk of ``ceil(total_lines / width)``
    lines. ``•`` (U+2022) marks a chunk that contains at least one
    match line, ``·`` (U+00B7) a chunk without matches. Returned as
    a single ``str`` so it slots into the preview status line
    without needing its own widget — the eye picks up the
    match-distribution shape at a glance, similar to vim's
    hlsearch + minimap pair.

    Edge cases:
      * total_lines == 0  → empty string.
      * len(match_lines) == 0 → all dim dots (still useful — shows
        "no matches in this viewport").
    """
    if total_lines <= 0:
        return ""
    chunk = max(1, (total_lines + width - 1) // width)
    bars = []
    hits = set()
    for ln in match_lines:
        # 1-indexed line number → 0-indexed chunk.
        idx = max(0, (ln - 1) // chunk)
        if idx < width:
            hits.add(idx)
    for i in range(width):
        # Skip slots beyond the actual viewport so the bar shrinks
        # rather than padding fake "empty" cells past EOF.
        if i * chunk >= total_lines:
            break
        bars.append("•" if i in hits else "·")
    return "[" + "".join(bars) + "]"


def _highlight_path(path: str, query: str) -> Text:
    """Render ``path`` with all substring matches of ``query``
    rendered in bold yellow. Falls back to plain text on bad inputs."""
    spans = find_match_spans(path, query)
    if not spans:
        return Text(path)
    rich = Text()
    i = 0
    for start, end in spans:
        if start > i:
            rich.append(path[i:start])
        rich.append(path[start:end], style="bold yellow")
        i = end
    if i < len(path):
        rich.append(path[i:])
    return rich


def _highlight_line(line_no: int, line: str, spans) -> Text:
    """Render ``line`` with a left gutter ``  NN  | `` followed by
    the line body, highlighting any ``spans`` ranges with reverse
    yellow."""
    gutter = Text(f"{line_no:>6}  | ", style="dim cyan")
    if not spans:
        return gutter.append(line)
    body = Text()
    i = 0
    for start, end in spans:
        if start > i:
            body.append(line[i:start])
        body.append(line[start:end], style="black on yellow")
        i = end
    if i < len(line):
        body.append(line[i:])
    return gutter.append(body)
