"""Chunked variants of bulk operations: revert, reconcile, clean.

Revert mirrors :class:`p4v_tui.sync_job.ChunkedSyncJob`'s
enumerate-then-batch pattern, with chunking shape coming from a
:class:`~p4v_tui.chunking.ChunkingStrategy` (count / size / single).

Reconcile and clean default to walking *subdirectories* — one
``p4 reconcile -a -e -d <subdir>/...`` per chunk — because their work
isn't a flat file list (they discover added / deleted files server
side). Subdir mode is the natural unit there. The same strategy
object can override that with ``count`` / ``size`` / ``single``: the
job then enumerates the file set up front (via ``p4 fstat``) and
batches it like revert does.

These are queued through the JobRunner so other interactive commands
can interleave between chunks. Resilient connection retry is inherited
from ``P4Service.run``.
"""
from __future__ import annotations

from typing import Callable, Iterator

from .chunking import (
    ChunkingStrategy, DEFAULT_FILES_PER_CHUNK,
    estimate_chunk_count, iter_file_batches,
)
from .jobs import Job
from .p4client import P4Exception, P4Service


DEFAULT_BATCH_SIZE = DEFAULT_FILES_PER_CHUNK


def _strip_trailing_recursion(target: str) -> str:
    """Turn ``//path/...`` or ``//path/`` into ``//path``."""
    t = target
    while t.endswith("/") or t.endswith("..."):
        if t.endswith("..."):
            t = t[:-3]
        t = t.rstrip("/")
    return t or target


def _resolve_strategy(
    strategy: ChunkingStrategy | None,
    batch_size: int | None,
    *,
    default_mode: str = "count",
) -> ChunkingStrategy:
    """Pick the strategy a job should run with.

    ``strategy`` arg wins. Otherwise, a legacy ``batch_size`` is
    converted into a fixed-count strategy. Otherwise, a fresh strategy
    seeded with ``default_mode``.
    """
    if strategy is not None:
        return strategy
    if batch_size is not None:
        return ChunkingStrategy(
            mode="count",
            files_per_chunk=max(1, int(batch_size)),
        )
    return ChunkingStrategy(mode=default_mode)


class ChunkedRevertJob(Job):
    """Revert files opened under a depot path, batched per strategy."""

    def __init__(
        self,
        p4: P4Service,
        target: str,
        batch_size: int | None = None,
        strategy: ChunkingStrategy | None = None,
    ) -> None:
        super().__init__(name=f"Revert {target}")
        self._p4 = p4
        self._target = target
        self._strategy = _resolve_strategy(strategy, batch_size)
        self._pending: list[str] = []
        self._sizes: dict[str, int] = {}

    @property
    def strategy(self) -> ChunkingStrategy:
        return self._strategy

    @property
    def _batch_size(self) -> int:
        return max(1, self._strategy.files_per_chunk)

    def chunks(self) -> Iterator[Callable[[], None]]:
        yield self._enumerate
        while self._pending:
            batches = iter_file_batches(
                self._pending, self._strategy,
                size_lookup=self._sizes.get,
            )
            try:
                batch = next(batches)
            except StopIteration:
                break
            self._pending = self._pending[len(batch):]
            yield (lambda b=batch: self._revert_batch(b))

    def _enumerate(self) -> None:
        try:
            rows = self._p4.run("opened", self._target)
        except P4Exception:
            rows = []
        files = [r["depotFile"] for r in rows
                 if isinstance(r, dict) and r.get("depotFile")]
        self._pending = files

        # Size-based chunking on revert needs a separate fstat round
        # trip — `p4 opened` doesn't include fileSize. Skip the call
        # unless the strategy actually needs sizes; for count / single
        # the lookup is unused.
        if self._strategy.mode == "size" and files:
            self._sizes = self._fetch_sizes(files)

        self.total_chunks = 1 + estimate_chunk_count(
            self._pending, self._strategy,
            size_lookup=self._sizes.get,
        )

    def _fetch_sizes(self, files: list[str]) -> dict[str, int]:
        sizes: dict[str, int] = {}
        # Batch the fstat call so a 10k-file revert doesn't stuff a
        # single command line. 200 paths per call is well under typical
        # arg limits and still keeps the round trips few.
        FSTAT_BATCH = 200
        for i in range(0, len(files), FSTAT_BATCH):
            batch = files[i:i + FSTAT_BATCH]
            try:
                rows = self._p4.run("fstat", "-T", "depotFile,fileSize",
                                    *batch)
            except P4Exception:
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                df = r.get("depotFile")
                if not df:
                    continue
                try:
                    sizes[df] = int(r.get("fileSize") or 0)
                except (TypeError, ValueError):
                    sizes[df] = 0
        return sizes

    def _revert_batch(self, batch) -> None:
        if not batch:
            return
        self._p4.run("revert", *batch)


class _ChunkedSubdirJob(Job):
    """Base for jobs that walk top-level subdirectories of a depot path.

    Reconcile and clean are inherently *subdir-walking* commands —
    ``p4 reconcile -a -e -d`` and ``p4 clean`` discover added /
    modified / deleted files server-side from the local filesystem.
    Pre-enumerating to a flat file list (via ``fstat``) and running
    them per-batch would silently skip the "delete unknown local
    file" / "add new local file" cases. So this job always chunks by
    subdirectory.

    The strategy parameter is accepted for API symmetry with the
    other chunked jobs and surfaces in :meth:`describe` so the user
    knows count / size / single doesn't apply here.
    """

    _COMMAND_NAME = ""        # subclasses set
    _COMMAND_ARGS: tuple = ()  # subclasses set, e.g. ("reconcile", "-a", "-e", "-d")
    _BENIGN_FRAGMENTS: tuple[str, ...] = (
        "no file(s) to reconcile",
        "no file(s) to clean",
        "no such file(s)",
    )

    def __init__(
        self,
        p4: P4Service,
        target: str,
        strategy: ChunkingStrategy | None = None,
    ) -> None:
        super().__init__(name=f"{self._COMMAND_NAME} {target}")
        self._p4 = p4
        self._target = target
        # Reconcile/clean only ever chunk by subdir — coerce any
        # configured non-subdir mode to subdir so config doesn't
        # silently change semantics.
        if strategy is None or strategy.mode != "subdir":
            self._strategy = ChunkingStrategy(mode="subdir")
        else:
            self._strategy = strategy
        self._pending_dirs: list[str] = []

    @property
    def strategy(self) -> ChunkingStrategy:
        return self._strategy

    def chunks(self) -> Iterator[Callable[[], None]]:
        yield self._enumerate
        while self._pending_dirs:
            d = self._pending_dirs.pop(0)
            yield (lambda dd=d: self._run_one(dd))

    def _enumerate(self) -> None:
        base = _strip_trailing_recursion(self._target)
        if base == self._target and not base.startswith("//"):
            # Single-file or non-depot path — process as one chunk.
            self._pending_dirs = [self._target]
        else:
            try:
                subdirs = self._p4.dirs(f"{base}/*")
            except P4Exception:
                subdirs = []
            if subdirs:
                self._pending_dirs = [f"{d}/..." for d in subdirs]
            else:
                self._pending_dirs = [self._target]
        self.total_chunks = 1 + len(self._pending_dirs)

    def _run_one(self, path: str) -> None:
        try:
            self._p4.run(*self._COMMAND_ARGS, path)
        except P4Exception as e:
            msg = str(e).lower()
            if any(frag in msg for frag in self._BENIGN_FRAGMENTS):
                # Empty / nothing-to-do — not an error, skip.
                return
            raise


class ChunkedReconcileJob(_ChunkedSubdirJob):
    """``p4 reconcile -a -e -d`` — subdir-walked by default."""

    _COMMAND_NAME = "Reconcile"
    _COMMAND_ARGS = ("reconcile", "-a", "-e", "-d")


class ChunkedCleanJob(_ChunkedSubdirJob):
    """``p4 clean`` — subdir-walked by default.

    Restores any locally-modified files, removes any local files unknown
    to the depot. Destructive — caller should confirm.
    """

    _COMMAND_NAME = "Clean"
    _COMMAND_ARGS = ("clean",)
