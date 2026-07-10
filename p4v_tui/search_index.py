"""SQLite-backed search index for depot filenames.

One DB file per (server, client) tuple under ``~/.p4v-tui/index/``.
SQLite's stdlib bundled FTS5 tokenizer handles the substring /
case-insensitive lookups; the ``files`` table is the source of
truth, ``files_fts`` is a synced virtual table.

Schema v1 — files only. ``changes`` (for ``cl:`` description
search) lands in v2 per the search-scenario doc.

This module is thread-aware: each method opens its own cursor on
a shared connection, and the connection is created in WAL mode so
the UI thread can read while the indexer writes.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = 1

# Hard cap matching the search-scenario doc decision. The indexer
# checks this before every batch insert and stops cleanly when the
# DB file would cross the threshold.
DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100 MB


_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS files (
    depot_path  TEXT PRIMARY KEY,
    lower       TEXT NOT NULL,
    leaf_lower  TEXT NOT NULL,
    type        TEXT,
    head_change INTEGER,
    head_action TEXT,
    head_time   INTEGER,
    head_user   TEXT
);

CREATE INDEX IF NOT EXISTS files_lower      ON files(lower);
CREATE INDEX IF NOT EXISTS files_leaf_lower ON files(leaf_lower);
CREATE INDEX IF NOT EXISTS files_head_time  ON files(head_time DESC);

-- FTS5 virtual table for fast multi-token MATCH queries. Stays
-- synced via INSERT/UPDATE/DELETE triggers so the ``files`` table
-- remains the canonical source.
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    depot_path,
    content='files',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, depot_path)
        VALUES (new.rowid, new.depot_path);
END;
CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, depot_path)
        VALUES ('delete', old.rowid, old.depot_path);
END;
CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, depot_path)
        VALUES ('delete', old.rowid, old.depot_path);
    INSERT INTO files_fts(rowid, depot_path)
        VALUES (new.rowid, new.depot_path);
END;

-- Changelist descriptions. Populated separately from ``files`` by
-- the index-build / update job (later — currently only filled
-- on-demand when a ``cl:`` query lands and we fall back to a live
-- ``p4 changes -m N -l`` walk). ``desc_lower`` accelerates the same
-- LIKE substring match Fast Search already uses for paths.
CREATE TABLE IF NOT EXISTS changes (
    change      INTEGER PRIMARY KEY,
    user        TEXT,
    time        INTEGER,
    client      TEXT,
    desc        TEXT,
    desc_lower  TEXT
);

CREATE INDEX IF NOT EXISTS changes_user ON changes(user);
CREATE INDEX IF NOT EXISTS changes_time ON changes(time DESC);
"""


@dataclass(frozen=True)
class SearchHit:
    depot_path: str
    head_time: int   # epoch seconds; 0 if unknown
    head_user: str
    head_action: str
    type: str
    # When the hit came from a content search (``?`` mode), this
    # is the first matching line's content + line number — used to
    # render an inline diff-style preview directly in the result
    # list, sparing the user from having to move the cursor onto
    # each row just to see why it matched. Empty for filename hits.
    match_line: str = ""
    match_lineno: int = 0


def _identity_for(port: str, client: str) -> str:
    """Stable filesystem-safe slug derived from (port, client).

    Per-user index isolation: two users on the same server hitting
    the same client never share an index file because each session
    has its own ``P4`` env / login.
    """
    raw = f"{port}|{client}".encode("utf-8", errors="replace")
    digest = hashlib.sha1(raw).hexdigest()[:16]
    safe_client = re.sub(r"[^A-Za-z0-9_-]+", "_", client) or "noclient"
    return f"{safe_client}__{digest}"


def index_path_for(port: str, client: str) -> Path:
    base = Path.home() / ".p4v-tui" / "index"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{_identity_for(port, client)}.sqlite"


class SearchIndex:
    """Thin wrapper around the sqlite3 connection.

    Opens lazily; close() releases the file handle. ``upsert_files``
    / ``query_files`` are the hot paths the indexer + UI hit.
    """

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    # --- lifecycle ------------------------------------------------------

    def open(self) -> None:
        if self._conn is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self._path),
            isolation_level=None,        # autocommit, we BEGIN explicitly
            check_same_thread=False,     # JobRunner + UI share the conn
            timeout=30.0,
        )
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    # --- meta -----------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        self.open()
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,),
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.open()
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # --- size / stats ---------------------------------------------------

    def disk_size_bytes(self) -> int:
        try:
            base = self._path.stat().st_size
        except OSError:
            return 0
        # Include WAL + SHM since SQLite holds writes there until
        # checkpoint. WAL can dwarf the main file during heavy ingest.
        total = base
        for suffix in ("-wal", "-shm"):
            try:
                total += self._path.with_name(
                    self._path.name + suffix,
                ).stat().st_size
            except OSError:
                pass
        return total

    def file_count(self) -> int:
        self.open()
        row = self._conn.execute(
            "SELECT COUNT(*) FROM files",
        ).fetchone()
        return int(row[0]) if row else 0

    # --- write ----------------------------------------------------------

    def upsert_files(self, rows: list[dict]) -> int:
        """Insert or replace ``rows``. Returns count actually written.

        Each row must have a ``depotFile`` key; missing-or-empty rows
        are skipped silently so callers can pass raw ``p4 files``
        output straight through.
        """
        if not rows:
            return 0
        self.open()
        prepared: list[tuple] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            df = r.get("depotFile")
            if not df:
                continue
            df_s = str(df)
            try:
                head_change = int(r.get("change") or r.get("headChange") or 0)
            except (TypeError, ValueError):
                head_change = 0
            try:
                head_time = int(r.get("time") or r.get("headTime") or 0)
            except (TypeError, ValueError):
                head_time = 0
            head_action = str(r.get("action") or r.get("headAction") or "")
            head_user = str(r.get("user") or r.get("headUser") or "")
            head_type = str(r.get("type") or r.get("headType") or "")
            leaf = df_s.rsplit("/", 1)[-1]
            prepared.append((
                df_s, df_s.lower(), leaf.lower(),
                head_type, head_change, head_action, head_time,
                head_user,
            ))
        if not prepared:
            return 0
        with self._conn:
            self._conn.executemany(
                "INSERT INTO files("
                "  depot_path, lower, leaf_lower, type,"
                "  head_change, head_action, head_time, head_user"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(depot_path) DO UPDATE SET "
                "  lower=excluded.lower,"
                "  leaf_lower=excluded.leaf_lower,"
                "  type=excluded.type,"
                "  head_change=excluded.head_change,"
                "  head_action=excluded.head_action,"
                "  head_time=excluded.head_time,"
                "  head_user=excluded.head_user",
                prepared,
            )
        return len(prepared)

    def delete_files(self, depot_paths: list[str]) -> int:
        if not depot_paths:
            return 0
        self.open()
        with self._conn:
            self._conn.executemany(
                "DELETE FROM files WHERE depot_path = ?",
                [(p,) for p in depot_paths],
            )
        return len(depot_paths)

    # --- migration ------------------------------------------------------

    # Meta flag marking the one-time gone-at-head purge as done, so the
    # full-table scan it needs (``head_action`` is unindexed) runs once
    # per index rather than every startup.
    _PURGE_FLAG = "gone_at_head_purged"

    def purge_gone_at_head(self) -> int:
        """One-time cleanup of dead rows from indexes built before the
        ``move/delete`` ingest fix.

        Older builds filtered only plain ``action == "delete"`` on
        ingest, so ``move/delete`` (the old path of a rename),
        ``purge`` and ``archive`` rows were stored as if live — on a
        busy depot that is the *majority* of head actions. The query
        filter already hides them, but they still cost disk and slow
        every scan, so this evicts them for good. Idempotent: guarded by
        the ``_PURGE_FLAG`` meta key, so it scans at most once per index.
        Returns the number of rows deleted (0 if already run).

        Mirrors :func:`search_jobs.is_deleted_at_head`; the ``files_ad``
        trigger keeps ``files_fts`` in sync on each DELETE.
        """
        self.open()
        if self.get_meta(self._PURGE_FLAG):
            return 0
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM files WHERE head_action LIKE '%/delete' "
                "   OR head_action IN ('delete', 'purge', 'archive')"
            )
            deleted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, '1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'",
                (self._PURGE_FLAG,),
            )
        return deleted

    # --- query ----------------------------------------------------------

    @staticmethod
    def _smart_case_lower(query: str) -> tuple[str, bool]:
        """Return ``(needle, case_insensitive)``. Smart-case = all
        lowercase query → case-insensitive search; any uppercase →
        case-sensitive (vim style)."""
        if query == query.lower():
            return query, True
        return query, False

    def query_files(
        self,
        query: str,
        *,
        limit: int = 200,
    ) -> list[SearchHit]:
        """Substring search across all indexed paths.

        Implements smart-case (lowercase query → case-insensitive).
        Empty / whitespace-only query returns nothing — caller is
        responsible for showing "type to search" UI state.

        Two-stage flow :

          1. SQL filter on the lowercased ``lower`` column — coarse
             pre-narrowing that returns at most a 5× ``limit`` slab.
             SQLite portably supports ``LIKE`` but lacks built-in
             ``reverse()`` for the leaf-vs-path ranking we want, so
             stage 2 runs in Python.
          2. Python pass : case-sensitive re-filter (when smart-case
             says so) + leaf-match ranking + head_time sort + final
             cap.

        Two-stage matters because most queries match many more rows
        than ``limit``; the SQL stage prunes to a manageable working
        set, the Python stage decides ordering precisely.
        """
        self.open()
        q = (query or "").strip()
        if not q:
            return []
        needle, case_i = self._smart_case_lower(q)
        lower_needle = needle.lower()
        slab_limit = max(int(limit) * 5, int(limit))
        # Exclude gone-at-head rows — SQL mirror of
        # ``search_jobs.is_deleted_at_head``. Plain ``!= 'delete'`` let
        # ``move/delete`` (renamed-away old paths) / ``purge`` /
        # ``archive`` leak as live hits, and any index built before that
        # fix still carries them, so the query stays defensive too.
        sql = (
            "SELECT depot_path, head_time, head_user, head_action, type "
            "FROM files "
            "WHERE lower LIKE '%' || ? || '%' "
            "  AND (head_action IS NULL OR (head_action NOT LIKE '%/delete' AND head_action NOT IN ('delete', 'purge', 'archive'))) "
            "ORDER BY head_time DESC "
            "LIMIT ?"
        )
        rows = self._conn.execute(
            sql, (lower_needle, slab_limit),
        ).fetchall()

        # Python re-filter + ranking.
        results: list[tuple[int, int, SearchHit]] = []
        for r in rows:
            depot_path = str(r[0])
            if not case_i and needle not in depot_path:
                # Case-sensitive mode: SQL pre-filter was on lowered
                # path so we'd over-include. Drop misses now.
                continue
            head_time = int(r[1] or 0)
            head_user = str(r[2] or "")
            head_action = str(r[3] or "")
            head_type = str(r[4] or "")
            leaf = depot_path.rsplit("/", 1)[-1]
            if case_i:
                leaf_hit = lower_needle in leaf.lower()
            else:
                leaf_hit = needle in leaf
            # Tier 0 = leaf match, 1 = path-only match. Lower tier
            # wins; within a tier sort by head_time desc.
            tier = 0 if leaf_hit else 1
            hit = SearchHit(
                depot_path=depot_path,
                head_time=head_time,
                head_user=head_user,
                head_action=head_action,
                type=head_type,
            )
            results.append((tier, -head_time, hit))
        results.sort(key=lambda t: (t[0], t[1]))
        return [h for _t, _ht, h in results[:int(limit)]]

    # --- changelist-description search ----------------------------------

    def upsert_changes(self, rows: list[dict]) -> int:
        """Insert / replace rows in the ``changes`` table.

        Each row may carry ``change``, ``user``, ``time``, ``client``,
        ``desc`` (``p4 changes -l`` output style). Missing-or-empty
        rows are skipped silently so callers can pass raw P4Python
        output straight through, matching :meth:`upsert_files`.
        """
        if not rows:
            return 0
        self.open()
        prepared: list[tuple] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            cn_raw = r.get("change") or r.get("Change") or 0
            try:
                cn = int(cn_raw)
            except (TypeError, ValueError):
                continue
            if cn <= 0:
                continue
            user = str(r.get("user") or r.get("User") or "")
            client = str(r.get("client") or r.get("Client") or "")
            try:
                t = int(r.get("time") or 0)
            except (TypeError, ValueError):
                t = 0
            desc = str(r.get("desc") or r.get("Description") or "")
            prepared.append((cn, user, t, client, desc, desc.lower()))
        if not prepared:
            return 0
        with self._conn:
            self._conn.executemany(
                "INSERT INTO changes("
                "change, user, time, client, desc, desc_lower) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(change) DO UPDATE SET "
                "  user=excluded.user, time=excluded.time, "
                "  client=excluded.client, desc=excluded.desc, "
                "  desc_lower=excluded.desc_lower",
                prepared,
            )
        return len(prepared)

    def query_changes(
        self,
        query: str,
        *,
        limit: int = 200,
    ) -> list[dict]:
        """Substring search on changelist descriptions.

        Returns rows ``{change, user, time, client, desc}`` ordered
        newest-first. Smart-case (lowercase query → case-insensitive,
        same rule as path search).
        """
        self.open()
        q = (query or "").strip()
        if not q:
            return []
        case_i = q == q.lower()
        needle = q.lower() if case_i else q
        col = "desc_lower" if case_i else "desc"
        sql = (
            f"SELECT change, user, time, client, desc "
            f"FROM changes WHERE {col} LIKE '%' || ? || '%' "
            f"ORDER BY time DESC LIMIT ?"
        )
        rows = self._conn.execute(sql, (needle, int(limit))).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append({
                "change": int(r[0]),
                "user": str(r[1] or ""),
                "time": int(r[2] or 0),
                "client": str(r[3] or ""),
                "desc": str(r[4] or ""),
            })
        return out

    # --- filtered search ------------------------------------------------

    def query_files_filtered(
        self,
        *,
        substr: str | None = None,
        regex: "re.Pattern[str] | None" = None,
        user: str | None = None,
        ftype: str | None = None,
        limit: int = 200,
    ) -> list:
        """Filename search with optional ``@user:`` / ``type:`` /
        regex AND-filters.

        Caller has already parsed the raw query and split it into
        a substring token (or ``None``), a compiled regex (or
        ``None``), and the filter values. Both ``user`` and
        ``ftype`` apply substring match against the indexed
        ``head_user`` / ``type`` columns so a partial value is
        forgiving (``type:text`` matches both ``text`` and
        ``text+x``).

        Returns the same :class:`SearchHit` shape as
        :meth:`query_files` so the UI doesn't have to branch by
        search mode.
        """
        self.open()
        # Build SQL WHERE incrementally.
        clauses: list[str] = []
        args: list = []
        if substr:
            clauses.append("lower LIKE '%' || ? || '%'")
            args.append(substr.lower())
        if user:
            clauses.append("LOWER(head_user) LIKE '%' || ? || '%'")
            args.append(user.lower())
        if ftype:
            clauses.append("LOWER(type) LIKE '%' || ? || '%'")
            args.append(ftype.lower())
        clauses.append(
            "(head_action IS NULL OR (head_action NOT LIKE '%/delete' AND head_action NOT IN ('delete', 'purge', 'archive')))"
        )
        sql = (
            "SELECT depot_path, head_time, head_user, head_action, "
            "type FROM files WHERE " + " AND ".join(clauses)
            + " ORDER BY head_time DESC LIMIT ?"
        )
        # Without a substring filter we'd cap at limit*5 like the
        # other methods, but a "@user:alice" query alone can still
        # return tens of thousands of rows on a busy depot — clamp
        # to limit*3 so the SQLite stage finishes in a reasonable
        # time on cold cache.
        slab = max(int(limit) * 3, int(limit))
        args.append(slab)
        rows = self._conn.execute(sql, args).fetchall()

        results: list = []
        for r in rows:
            depot_path = str(r[0])
            if regex is not None and not regex.search(depot_path):
                continue
            head_time = int(r[1] or 0)
            leaf = depot_path.rsplit("/", 1)[-1]
            # Leaf-match boost only when there's an actual needle
            # to anchor on.
            leaf_hit = bool(substr) and substr.lower() in leaf.lower()
            tier = 0 if leaf_hit else 1
            results.append((
                tier, -head_time,
                SearchHit(
                    depot_path=depot_path,
                    head_time=head_time,
                    head_user=str(r[2] or ""),
                    head_action=str(r[3] or ""),
                    type=str(r[4] or ""),
                ),
            ))
        results.sort(key=lambda t: (t[0], t[1]))
        return [h for _a, _b, h in results[:int(limit)]]

    # --- "fuzzy" path tolerance -----------------------------------------
    #
    # Three orthogonal aids for users who don't remember the exact
    # depot path, listed in increasing leniency:
    #
    #   1. ``query_files`` (above) — strict substring.
    #   2. ``query_files_loose`` — tokenize on whitespace + ``/`` and
    #      AND across tokens. Handles "missing slash" / "extra space" /
    #      "partial path" — e.g. ``foo bar`` matches both ``//x/foo_bar``
    #      and ``//x/foo/bar/baz``.
    #   3. ``suggest_corrections`` — typo recovery via Levenshtein on
    #      indexed leaf names. Returns only when the query produced
    #      zero hits at the tighter levels, so the UI can offer "Did
    #      you mean …?" suggestions.
    #
    # All three respect smart-case (lowercase-only query → case-
    # insensitive; any uppercase char → case-sensitive). Together they
    # implement the "퍼포스 경로 자유도" roadmap goal: get the right
    # file even when the user mis-typed the path.

    def query_files_loose(
        self,
        query: str,
        *,
        limit: int = 200,
    ) -> list:
        """Token-AND search across paths.

        The query is split on whitespace and ``/``. Every token must
        appear somewhere in the depot path (substring), in any order.
        Ranking prefers leaf matches and recency, like
        :meth:`query_files`.
        """
        self.open()
        import re as _re
        raw = (query or "").strip()
        if not raw:
            return []
        tokens = [t for t in _re.split(r"[\s/]+", raw) if t]
        if not tokens:
            return []
        # Smart-case applies per-token: if any token has a capital
        # we switch the whole query to case-sensitive so the user's
        # intent ("I typed Foo on purpose") is preserved.
        case_sensitive = any(t != t.lower() for t in tokens)
        if case_sensitive:
            lower_tokens = tokens
            # SQL ``LIKE`` is case-insensitive on ASCII by default for
            # the ``lower`` column we built; we still need a Python
            # re-pass to enforce case-sensitivity.
        else:
            lower_tokens = [t.lower() for t in tokens]

        conds = " AND ".join(
            ["lower LIKE '%' || ? || '%'"] * len(lower_tokens)
        )
        sql = (
            "SELECT depot_path, head_time, head_user, head_action, type "
            f"FROM files WHERE {conds} "
            "  AND (head_action IS NULL OR (head_action NOT LIKE '%/delete' AND head_action NOT IN ('delete', 'purge', 'archive'))) "
            "ORDER BY head_time DESC LIMIT ?"
        )
        slab = int(limit) * 5
        rows = self._conn.execute(
            sql, [t.lower() for t in lower_tokens] + [slab],
        ).fetchall()

        results: list = []
        for r in rows:
            depot_path = str(r[0])
            hay = depot_path if case_sensitive else depot_path.lower()
            if not all(t in hay for t in
                       (tokens if case_sensitive else lower_tokens)):
                continue
            leaf = depot_path.rsplit("/", 1)[-1]
            leaf_hay = leaf if case_sensitive else leaf.lower()
            leaf_hits = sum(
                1 for t in (tokens if case_sensitive else lower_tokens)
                if t in leaf_hay
            )
            head_time = int(r[1] or 0)
            results.append((
                -leaf_hits,   # more leaf hits first
                -head_time,   # then recency
                SearchHit(
                    depot_path=depot_path,
                    head_time=head_time,
                    head_user=str(r[2] or ""),
                    head_action=str(r[3] or ""),
                    type=str(r[4] or ""),
                ),
            ))
        results.sort(key=lambda t: (t[0], t[1]))
        return [h for _a, _b, h in results[:int(limit)]]

    def suggest_corrections(
        self,
        query: str,
        *,
        max_suggestions: int = 5,
    ) -> list[str]:
        """Return leaf-name strings near ``query`` by edit distance.

        Used as a last-resort hint after :meth:`query_files` and
        :meth:`query_files_loose` both produced zero hits. Limited to
        leaves whose length is within ±2 of the query — Levenshtein is
        O(N·M) and we'd otherwise scan the whole index for nothing.
        Filtered to distance ≤ 2 so noisy near-misses don't drown
        plausible candidates.
        """
        self.open()
        raw = (query or "").strip()
        if not raw or len(raw) > 64:
            return []
        needle = raw.lower()
        lo = max(1, len(needle) - 2)
        hi = len(needle) + 2
        try:
            rows = self._conn.execute(
                "SELECT DISTINCT leaf_lower FROM files "
                "WHERE LENGTH(leaf_lower) BETWEEN ? AND ? "
                "LIMIT 20000",
                (lo, hi),
            ).fetchall()
        except Exception:  # noqa: BLE001
            return []

        def _lev(a: str, b: str, max_d: int) -> int:
            la, lb = len(a), len(b)
            if abs(la - lb) > max_d:
                return max_d + 1
            prev = list(range(lb + 1))
            for i, ca in enumerate(a, 1):
                cur = [i] + [0] * lb
                row_min = i
                for j, cb in enumerate(b, 1):
                    cur[j] = min(
                        cur[j - 1] + 1,
                        prev[j] + 1,
                        prev[j - 1] + (0 if ca == cb else 1),
                    )
                    if cur[j] < row_min:
                        row_min = cur[j]
                if row_min > max_d:
                    return max_d + 1
                prev = cur
            return prev[-1]

        candidates: list[tuple[int, str]] = []
        for r in rows:
            tok = str(r[0])
            if not tok:
                continue
            d = _lev(needle, tok, 2)
            if d <= 2 and tok != needle:
                candidates.append((d, tok))
        candidates.sort(key=lambda t: (t[0], t[1]))
        seen: set[str] = set()
        out: list[str] = []
        for _d, tok in candidates:
            if tok in seen:
                continue
            seen.add(tok)
            out.append(tok)
            if len(out) >= max_suggestions:
                break
        return out


def find_match_spans(text: str, query: str) -> list[tuple[int, int]]:
    """Return ``[(start, end), …]`` byte offsets of every match of
    ``query`` in ``text``. Smart-case: same rule as the search.

    Used by the UI to highlight hits in result rows + preview body.
    """
    if not text or not query:
        return []
    needle = query.strip()
    if not needle:
        return []
    hay = text
    if needle == needle.lower():
        # Case-insensitive — operate on lowercased copies but
        # report offsets into the original string.
        lower_text = text.lower()
        lower_needle = needle.lower()
        spans: list[tuple[int, int]] = []
        n = len(lower_needle)
        i = 0
        while True:
            j = lower_text.find(lower_needle, i)
            if j < 0:
                break
            spans.append((j, j + n))
            i = j + max(1, n)
        return spans
    spans: list[tuple[int, int]] = []
    n = len(needle)
    i = 0
    while True:
        j = hay.find(needle, i)
        if j < 0:
            break
        spans.append((j, j + n))
        i = j + max(1, n)
    return spans
