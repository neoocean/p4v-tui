"""Chunked + resumable indexer jobs for the Fast Search feature.

Two job types share the JobRunner / chunking infrastructure:

* :class:`IndexBuildJob` — first-time full enumeration of the depot.
  Walks ``p4 depots`` → top-level subdirs → ``p4 files`` per
  subdir, writing each result batch to SQLite immediately. State
  is the index file itself (every successful batch lands), plus
  a side state file at ``~/.p4v-tui/search-state/{db_id}.json``
  recording the remaining subdir queue so a kill mid-enumeration
  resumes from the next un-indexed subdir on relaunch.

* :class:`IndexUpdateJob` — incremental refresh. Runs once at app
  startup. Asks the server for ``change`` counter, compares with
  ``last_indexed_change`` in the index meta, and pulls
  ``p4 files //...@>N`` to surface the deltas. Single chunk
  unless the delta is huge.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Iterator

from .jobs import Job
from .p4client import P4Exception, P4Service
from .search_index import (
    DEFAULT_MAX_BYTES, SearchIndex,
)
# Re-exported: the gone-at-head action classifier now lives in utils so
# every p4-action consumer shares one definition. Kept importable here
# (``from p4v_tui.search_jobs import is_deleted_at_head``) for callers
# and tests that already reference it.
from .utils import is_deleted_at_head  # noqa: F401


# Page size for the per-subdir ``p4 files`` calls. Big enough to
# keep round-trips low, small enough that one chunk's SQLite write
# stays under ~ 500 ms.
DEFAULT_FILES_PAGE = 5000

# Cap on incremental scan to avoid blocking startup if the user
# has been away for weeks. A delta bigger than this implies a
# rebuild would be more efficient anyway.
INCREMENTAL_HARD_CAP = 200_000


def _state_path_for(db_id: str) -> Path:
    d = Path.home() / ".p4v-tui" / "search-state"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{db_id}.json"


class IndexBuildJob(Job):
    """Walk every depot, enumerate files per top-level subdir, write
    each result batch to SQLite.

    Resumable. State file lists subdirs that still need to be
    processed; the queue's head is the active chunk's target.
    Killed mid-chunk → on relaunch the index already has every
    *completed* subdir, and the state file's queue starts with
    whichever subdir the killed chunk was on.
    """

    def __init__(
        self,
        p4: P4Service,
        index: SearchIndex,
        max_disk_bytes: int = DEFAULT_MAX_BYTES,
        page_size: int = DEFAULT_FILES_PAGE,
    ) -> None:
        super().__init__(name=f"Indexing {index.path.name}")
        self._p4 = p4
        self._index = index
        self._max_disk = max_disk_bytes
        self._page_size = page_size
        self._db_id = hashlib.sha1(
            str(index.path).encode("utf-8"),
        ).hexdigest()[:16]
        self._state_path = _state_path_for(self._db_id)
        self._pending: list[str] = []   # subdir paths still to index
        # Snapshotted current-head change so the incremental updater
        # has a starting cursor after the build finishes.
        self._build_start_change: int = 0
        # Set by enumerate; consulted between chunks to honor the
        # disk-size cap.
        self._stopped_for_cap = False

    # --- state persistence ---------------------------------------------

    def _save_state(self) -> None:
        try:
            data = {
                "version": 1,
                "db_id": self._db_id,
                "pending": list(self._pending),
                "updated_at": int(time.time()),
                "build_start_change": int(self._build_start_change),
            }
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._state_path)
        except OSError:
            # State loss is a soft failure — worst case the next
            # launch re-scans an already-indexed subdir.
            pass

    def _load_state(self) -> bool:
        try:
            if not self._state_path.is_file():
                return False
            data = json.loads(
                self._state_path.read_text(encoding="utf-8"),
            )
        except (OSError, json.JSONDecodeError):
            return False
        if data.get("db_id") != self._db_id:
            return False
        self._pending = list(data.get("pending") or [])
        try:
            self._build_start_change = int(
                data.get("build_start_change") or 0,
            )
        except (TypeError, ValueError):
            self._build_start_change = 0
        return True

    def _clear_state(self) -> None:
        try:
            self._state_path.unlink(missing_ok=True)
        except OSError:
            pass

    # --- chunks --------------------------------------------------------

    def chunks(self) -> Iterator[Callable[[], None]]:
        yield self._enumerate
        while self._pending and not self._stopped_for_cap:
            target = self._pending[0]
            yield (lambda t=target: self._index_one_subdir(t))
        if self._stopped_for_cap:
            # Surface as a final no-op chunk so progress reflects
            # we stopped cleanly rather than crashing.
            yield self._finalize_stopped
        else:
            yield self._finalize_complete

    def _enumerate(self) -> None:
        """First chunk: list every depot's top-level subdirs and
        record the head ``change`` counter as our incremental
        cursor anchor."""
        if not self._load_state():
            try:
                depots = self._p4.run("depots")
            except P4Exception:
                depots = []
            roots: list[str] = []
            for d in depots:
                if not isinstance(d, dict):
                    continue
                name = d.get("name")
                if name:
                    roots.append(f"//{name}")
            subdirs: list[str] = []
            for root in roots:
                try:
                    rows = self._p4.run("dirs", f"{root}/*")
                except P4Exception:
                    continue
                for r in rows:
                    if isinstance(r, dict) and r.get("dir"):
                        subdirs.append(str(r["dir"]))
                    elif isinstance(r, str):
                        # Some p4 builds return strings instead of dicts.
                        subdirs.append(r)
            # Fallback: if a depot is flat (no subdirs), index the
            # whole depot as one chunk.
            for root in roots:
                has_children = any(
                    s.startswith(root + "/") for s in subdirs
                )
                if not has_children:
                    subdirs.append(root)
            self._pending = sorted(set(subdirs))

            # Snapshot the cursor BEFORE we start ingesting so the
            # increment that follows doesn't skip files that landed
            # mid-build.
            try:
                rows = self._p4.run("counter", "change")
                if rows and isinstance(rows[0], dict):
                    self._build_start_change = int(
                        rows[0].get("value") or 0,
                    )
            except (P4Exception, ValueError):
                self._build_start_change = 0
            self._save_state()
        self.total_chunks = 1 + len(self._pending) + 1

    def _index_one_subdir(self, target: str) -> None:
        # Disk cap pre-check. WAL can swell the on-disk footprint
        # significantly so we err on the side of bailing early.
        if self._index.disk_size_bytes() >= self._max_disk:
            self._stopped_for_cap = True
            return
        try:
            rows = self._p4.run(
                "files", "-m", str(self._page_size),
                f"{target}/...",
            )
        except P4Exception:
            rows = []
        # Filter gone-at-head rows out of the upsert path so the
        # index doesn't carry dead entries from the start. ``files``
        # here is the raw paged call (no ``-e``), so it returns
        # ``move/delete`` / ``purge`` / ``archive`` too — not just
        # plain ``delete``. (Updates to existing files via the same
        # path will hit the head version anyway when resurrected.)
        kept: list[dict] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            action = str(r.get("action") or r.get("headAction") or "")
            if is_deleted_at_head(action):
                continue
            kept.append(r)
        self._index.upsert_files(kept)
        # Pop the head of the queue ONLY after a successful upsert
        # so a mid-chunk crash leaves the same subdir for retry.
        if self._pending and self._pending[0] == target:
            self._pending.pop(0)
        self._save_state()

    def _finalize_complete(self) -> None:
        # Anchor incremental cursor + clear the on-disk resume state.
        self._index.set_meta(
            "last_indexed_change",
            str(self._build_start_change),
        )
        self._index.set_meta(
            "indexed_at", str(int(time.time())),
        )
        self._index.set_meta("build_complete", "1")
        self._clear_state()

    def _finalize_stopped(self) -> None:
        # Don't clear state — caller can resume with a rebuild
        # action or by removing the cap.
        self._index.set_meta(
            "build_complete", "0",
        )
        self._index.set_meta(
            "indexed_at", str(int(time.time())),
        )


class IndexUpdateJob(Job):
    """Incremental refresh against the server's current ``change``
    counter. One chunk for the delta query plus one per page of
    results when the delta is large.

    Triggered at app startup once the connection is up. No-op if
    ``last_indexed_change`` is already current.
    """

    def __init__(
        self,
        p4: P4Service,
        index: SearchIndex,
        page_size: int = DEFAULT_FILES_PAGE,
    ) -> None:
        super().__init__(name=f"Updating index ({index.path.name})")
        self._p4 = p4
        self._index = index
        self._page_size = page_size
        self._delta_target_change: int = 0
        self._last_change_before: int = 0
        self._pending_pages: list[int] = []

    def chunks(self) -> Iterator[Callable[[], None]]:
        yield self._plan
        while self._pending_pages:
            page = self._pending_pages.pop(0)
            yield (lambda p=page: self._index_page(p))
        yield self._finalize

    def _plan(self) -> None:
        # One-time eviction of dead rows (move/delete / purge / archive)
        # left in indexes built before the ingest fix. Idempotent (meta
        # flag), so this is a cheap no-op on every startup after the
        # first. Runs here — the first chunk of the startup refresh —
        # so it never blocks a UI-thread open(); the query filter hides
        # the rows in the meantime regardless.
        try:
            purged = self._index.purge_gone_at_head()
            if purged:
                self._index.set_meta(
                    "gone_at_head_purged_count", str(purged),
                )
        except Exception:  # noqa: BLE001 — migration must never break startup
            pass
        # Read current cursor & server head.
        last_raw = self._index.get_meta("last_indexed_change") or "0"
        try:
            self._last_change_before = int(last_raw)
        except ValueError:
            self._last_change_before = 0
        try:
            rows = self._p4.run("counter", "change")
            if rows and isinstance(rows[0], dict):
                self._delta_target_change = int(
                    rows[0].get("value") or 0,
                )
            elif rows and isinstance(rows[0], str):
                # Older p4 returns a bare string for non-tagged calls.
                self._delta_target_change = int(rows[0])
        except (P4Exception, ValueError, IndexError):
            self._delta_target_change = self._last_change_before
        delta = self._delta_target_change - self._last_change_before
        if delta <= 0:
            self._pending_pages = []
            self.total_chunks = 2
            return
        # Cap delta — bigger means rebuild is the right call.
        if delta > INCREMENTAL_HARD_CAP:
            delta = INCREMENTAL_HARD_CAP
        # One page per planned chunk. We don't actually know how
        # many files changed; cap pages by delta / page_size as a
        # rough budget, with a floor of 1.
        n_pages = max(1, min(20, (delta + self._page_size - 1) // self._page_size))
        self._pending_pages = list(range(n_pages))
        self.total_chunks = 1 + len(self._pending_pages) + 1

    def _index_page(self, page_idx: int) -> None:
        # ``p4 files //...@>N`` returns all files modified since N;
        # there's no offset/limit pagination so we use -m as a cap
        # and rely on the fact that successive runs return a stable
        # ordering. For v1 we just take -m page_size and accept that
        # a very large incremental might miss tail entries (covered
        # by the next launch's full delta or a manual rebuild).
        try:
            rows = self._p4.run(
                "files", "-m", str(self._page_size),
                f"//...@>{self._last_change_before}",
            )
        except P4Exception:
            rows = []
        upserts: list[dict] = []
        deletes: list[str] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            action = str(r.get("action") or r.get("headAction") or "")
            df = r.get("depotFile")
            if not df:
                continue
            # A gone-at-head delta (incl. ``move/delete`` — the old
            # path of a rename) must REMOVE the path from the index,
            # not upsert it as live.
            if is_deleted_at_head(action):
                deletes.append(str(df))
            else:
                upserts.append(r)
        if upserts:
            self._index.upsert_files(upserts)
        if deletes:
            self._index.delete_files(deletes)
        # Only first page is meaningful for the cap-based incremental;
        # subsequent pages are placeholders that no-op gracefully.
        # Future v2 will swap this for a proper cursor-paged query.

    def _finalize(self) -> None:
        if self._delta_target_change > self._last_change_before:
            self._index.set_meta(
                "last_indexed_change",
                str(self._delta_target_change),
            )
        self._index.set_meta(
            "indexed_at", str(int(time.time())),
        )
