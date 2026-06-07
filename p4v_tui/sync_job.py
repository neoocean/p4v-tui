"""Chunked, resumable sync job.

Wraps ``p4 sync`` so that:

* The work for a target path is split into a preview chunk plus N batches
  of file-level syncs, so the JobRunner can interleave interactive
  commands between batches.
* The shape of those batches comes from a configurable
  :class:`~p4v_tui.chunking.ChunkingStrategy` — fixed file count, byte
  budget, or one-file-per-chunk. ``p4 sync -n`` already returns each
  file's size, so size-based chunking is free for sync.
* Per-file progress is persisted to ``~/.p4v-tui/sync-state/{hash}.json``
  after every successful batch. If the process is killed mid-sync, the
  next launch picks up only files that haven't been confirmed yet.
* A retry of an interrupted sync starts cheap: we re-enumerate via
  ``p4 sync -n``, drop everything already in the state's completed set,
  and only sync what's left.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Iterator, Sequence

from .chunking import (
    ChunkingStrategy, DEFAULT_FILES_PER_CHUNK,
    estimate_chunk_count, iter_file_batches,
)
from .jobs import Job
from .p4client import P4Service


# Legacy name kept for callers that still pass ``batch_size``. New code
# should construct a ChunkingStrategy explicitly.
DEFAULT_BATCH_SIZE = DEFAULT_FILES_PER_CHUNK


def _state_dir() -> Path:
    p = Path.home() / ".p4v-tui" / "sync-state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_path_for(target: str) -> Path:
    h = hashlib.sha1(target.encode("utf-8")).hexdigest()[:16]
    return _state_dir() / f"{h}.json"


class ChunkedSyncJob(Job):
    def __init__(
        self,
        p4: P4Service,
        target: str,
        batch_size: int | None = None,
        force: bool = False,
        strategy: ChunkingStrategy | None = None,
    ) -> None:
        prefix = "Force-Sync" if force else "Sync"
        super().__init__(name=f"{prefix} {target}")
        self._p4 = p4
        self._target = target
        # ``strategy`` wins when supplied; otherwise honor the legacy
        # ``batch_size`` arg (which always meant fixed-count chunking)
        # or fall back to the dataclass default.
        if strategy is not None:
            self._strategy = strategy
        elif batch_size is not None:
            self._strategy = ChunkingStrategy(
                mode="count",
                files_per_chunk=max(1, int(batch_size)),
            )
        else:
            self._strategy = ChunkingStrategy()
        self._force = force
        # Distinct state key per (target, force) so a normal sync's progress
        # doesn't get mistaken for a force-sync's, and vice versa.
        self._state_path = _state_path_for(
            f"{target}|force={1 if force else 0}"
        )
        self._pending: list[str] = []
        self._sizes: dict[str, int] = {}   # depotFile → fileSize from sync -n
        self._completed: set[str] = set()
        self.resumed_from = 0   # how many files were already done from a prior run

    @property
    def strategy(self) -> ChunkingStrategy:
        return self._strategy

    # Back-compat: legacy callers (and tests) still read ``_batch_size``.
    # It only meaningfully reflects fixed-count strategies; for size /
    # single modes it surfaces the configured count as a hint.
    @property
    def _batch_size(self) -> int:
        return max(1, self._strategy.files_per_chunk)

    # --- state persistence ------------------------------------------------

    def _load_state(self) -> dict | None:
        try:
            if not self._state_path.is_file():
                return None
            with self._state_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("target") != self._target:
                return None
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def _save_state(self) -> None:
        # v3 format adds the chunking strategy so a resumed job uses
        # the same chunking the user originally requested. v2 readers
        # ignore the new field, v1 readers still find the legacy
        # target/completed fields.
        data = {
            "version": 3,
            "job_type": "ChunkedSyncJob",
            "params": {
                "target": self._target,
                "batch_size": self._batch_size,    # v2 compat
                "force": self._force,
                "strategy": self._strategy.to_dict(),
            },
            "name": self.name,
            "target": self._target,           # v1 compat
            "updated_at": int(time.time()),
            "completed": sorted(self._completed),
        }
        try:
            tmp = self._state_path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            tmp.replace(self._state_path)
        except OSError:
            # If we can't persist progress we keep going — worst case the
            # next run re-syncs files we already did, which is harmless.
            pass

    @classmethod
    def from_state(cls, p4: P4Service, data: dict) -> "ChunkedSyncJob | None":
        """Recreate a ChunkedSyncJob from a persisted state dict.

        Returns None if the dict isn't a valid sync state (different
        job_type, missing target, etc.). Reads any of v1 / v2 / v3 — a
        missing strategy degrades to the legacy fixed-count behavior.
        """
        version = data.get("version", 1)
        strategy: ChunkingStrategy | None = None
        if version >= 2:
            params = data.get("params") or {}
            target = params.get("target")
            batch_size = int(params.get("batch_size") or DEFAULT_BATCH_SIZE)
            force = bool(params.get("force"))
            strat_raw = params.get("strategy")
            if isinstance(strat_raw, dict):
                strategy = ChunkingStrategy.from_dict(strat_raw)
        else:
            target = data.get("target")
            batch_size = DEFAULT_BATCH_SIZE
            force = False
        if not target:
            return None
        return cls(
            p4, target, batch_size=batch_size, force=force,
            strategy=strategy,
        )

    def _clear_state(self) -> None:
        try:
            self._state_path.unlink(missing_ok=True)
        except OSError:
            pass

    # --- chunks -----------------------------------------------------------

    def chunks(self) -> Iterator[Callable[[], None]]:
        # First chunk: enumerate work, restoring any prior progress.
        yield self._enumerate_chunk

        # Subsequent chunks: drain `_pending` according to the active
        # strategy. We materialize batches lazily inside the loop so a
        # cancellation between chunks can short-circuit cleanly.
        while self._pending:
            batches = iter_file_batches(
                self._pending, self._strategy,
                size_lookup=self._sizes.get,
            )
            try:
                batch = next(batches)
            except StopIteration:
                break
            # Drop the about-to-run batch from _pending so a save_state
            # invoked inside the chunk reflects accurate "remaining"
            # bookkeeping; if the chunk fails the entries stay in
            # _completed=False land and will be re-enumerated next run.
            self._pending = self._pending[len(batch):]
            yield (lambda b=batch: self._sync_batch(b))
        # All batches drained — clean up the on-disk state file.
        self._clear_state()

    def _enumerate_chunk(self) -> None:
        prior = self._load_state()
        if prior is not None:
            self._completed = set(prior.get("completed") or [])
            self.resumed_from = len(self._completed)

        preview_args: list = ["sync", "-n"]
        if self._force:
            preview_args.append("-f")
        preview_args.append(self._target)
        rows = self._p4.run(*preview_args)
        # `p4 sync -n` returns one dict per file that would sync. Some rows
        # are "file(s) up-to-date" warnings (string), filter those out.
        candidates: list[str] = []
        sizes: dict[str, int] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            depot_file = r.get("depotFile")
            if not depot_file:
                continue
            candidates.append(depot_file)
            # ``fileSize`` arrives as a string in P4Python output —
            # tolerate either int-like value or missing entirely.
            try:
                sizes[depot_file] = int(r.get("fileSize") or 0)
            except (TypeError, ValueError):
                sizes[depot_file] = 0

        # Skip files we already completed in a previous interrupted run.
        self._pending = [f for f in candidates if f not in self._completed]
        self._sizes = sizes

        # We now know the total work: 1 enumerate chunk we just ran +
        # however many strategy-shaped chunks the pending list yields.
        self.total_chunks = 1 + estimate_chunk_count(
            self._pending, self._strategy,
            size_lookup=self._sizes.get,
        )

        # If we resumed and there's nothing left to do, save final state
        # cleanup happens in the empty-pending branch of chunks().

    def _sync_batch(self, batch: Sequence[str]) -> None:
        if not batch:
            return
        # ``p4.run`` is the resilient runner — it will reconnect-and-retry
        # transparently on connection failures, so a transient blip during
        # a batch doesn't fail the whole sync.
        args: list = ["sync"]
        if self._force:
            args.append("-f")
        args.extend(batch)
        self._p4.run(*args)
        # Mark successful AFTER the call returns without raising.
        self._completed.update(batch)
        self._save_state()
