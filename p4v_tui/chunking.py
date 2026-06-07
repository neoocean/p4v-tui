"""Chunking strategy for bulk file operations.

A long-running bulk command (sync, revert, reconcile) is split into a
sequence of smaller chunks so the JobRunner can interleave interactive
work between them. *How* it's split is configurable via
:class:`ChunkingStrategy`.

Modes
-----

``count``  — fixed N files per chunk. Predictable command size,
             good default for server-side ops where per-call overhead
             dominates over per-file size (revert / reconcile).
``size``   — chunks bounded by summed file size in bytes. Best for
             ``p4 sync`` over a slow / flaky network: bounds wall-clock
             *per chunk*, so a single huge file doesn't monopolize the
             worker for a long time and a thousand tiny files still
             pack into one round trip.
``single`` — one file per chunk. Maximum responsiveness — interactive
             commands cut in front of the next file every time — at
             the cost of N round trips. Useful on a very flaky link
             where you want a checkpoint after every file.
``subdir`` — one top-level subdirectory per chunk. Only meaningful for
             ``p4 reconcile`` / ``p4 clean`` style jobs that walk a
             tree; included here for symmetry with the other modes
             when configuration is parsed.

Anything that doesn't fit a strategy mode falls back to ``count`` —
e.g. ``size`` requested but no per-file size lookup is available.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterator, Sequence


# Sensible defaults — small enough that a single chunk on a healthy
# link stays under ~1s, large enough that overhead doesn't dominate.
DEFAULT_FILES_PER_CHUNK = 50
DEFAULT_BYTES_PER_CHUNK = 50 * 1024 * 1024  # 50 MB

VALID_MODES = ("count", "size", "single", "subdir")


@dataclass(frozen=True)
class ChunkingStrategy:
    """Configurable chunking strategy.

    Construct with :meth:`from_dict` for TOML loading or call directly
    with explicit kwargs. Defaults match the previous hardcoded
    behavior (50 files per chunk).
    """
    mode: str = "count"
    files_per_chunk: int = DEFAULT_FILES_PER_CHUNK
    bytes_per_chunk: int = DEFAULT_BYTES_PER_CHUNK

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            # Frozen dataclass — use object.__setattr__ to fix up an
            # invalid mode rather than raising. Bad config shouldn't
            # crash the app; fall back to a safe default and let the
            # caller report the problem.
            object.__setattr__(self, "mode", "count")
        if self.files_per_chunk < 1:
            object.__setattr__(self, "files_per_chunk", 1)
        if self.bytes_per_chunk < 1:
            object.__setattr__(self, "bytes_per_chunk", 1)

    # --- introspection ----------------------------------------------------

    def describe(self) -> str:
        """Short human-readable description for status / log lines."""
        if self.mode == "count":
            return f"{self.files_per_chunk} file(s) per chunk"
        if self.mode == "size":
            mb = self.bytes_per_chunk / (1024 * 1024)
            return f"≤ {mb:.0f} MB per chunk"
        if self.mode == "single":
            return "1 file per chunk"
        if self.mode == "subdir":
            return "1 subdirectory per chunk"
        return self.mode

    # --- serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "files_per_chunk": self.files_per_chunk,
            "bytes_per_chunk": self.bytes_per_chunk,
        }

    @classmethod
    def from_dict(
        cls, raw: dict | None,
        *, fallback: "ChunkingStrategy | None" = None,
    ) -> "ChunkingStrategy":
        """Build from a TOML/JSON-loaded dict.

        Missing keys fall back to ``fallback``'s value (or the dataclass
        default if no fallback). This lets a per-job override partially
        shadow the global default without restating every field.
        """
        if not isinstance(raw, dict):
            return fallback or cls()
        base = fallback or cls()
        mode_raw = raw.get("mode", base.mode)
        mode = str(mode_raw).strip().lower() if mode_raw else base.mode
        if mode not in VALID_MODES:
            mode = base.mode
        try:
            files = int(raw.get("files_per_chunk", base.files_per_chunk))
        except (TypeError, ValueError):
            files = base.files_per_chunk
        try:
            num_bytes = int(raw.get("bytes_per_chunk", base.bytes_per_chunk))
        except (TypeError, ValueError):
            num_bytes = base.bytes_per_chunk
        return cls(
            mode=mode,
            files_per_chunk=files,
            bytes_per_chunk=num_bytes,
        )


@dataclass
class ChunkingConfig:
    """Top-level chunking configuration.

    ``default`` applies to every chunked job. ``per_job`` lets a TOML
    consumer override one job type (``sync``, ``force_sync``, ``revert``,
    ``reconcile``, ``clean``) without restating the others.
    """
    default: ChunkingStrategy = field(default_factory=ChunkingStrategy)
    per_job: dict[str, ChunkingStrategy] = field(default_factory=dict)

    def for_job(self, job_kind: str) -> ChunkingStrategy:
        """Return the strategy that should apply to ``job_kind``,
        falling back to the global default."""
        return self.per_job.get(job_kind, self.default)


# --- batching helpers -----------------------------------------------------


def iter_file_batches(
    files: Sequence[str],
    strategy: ChunkingStrategy,
    *,
    size_lookup: Callable[[str], int] | None = None,
) -> Iterator[list[str]]:
    """Yield successive batches of ``files`` according to ``strategy``.

    ``size_lookup`` is required when strategy.mode == "size"; without it
    the size mode degrades gracefully to "count". This keeps callers
    that don't have file sizes (e.g. ``p4 opened``) from being forced
    to do a separate ``fstat`` round trip just to honor a config they
    can't satisfy.
    """
    if not files:
        return

    mode = strategy.mode
    if mode == "subdir":
        # Subdir mode is meaningful at enumeration time, not batching
        # time. By the time we have a flat file list, fall back to count.
        mode = "count"

    if mode == "single":
        for f in files:
            yield [f]
        return

    if mode == "size" and size_lookup is not None:
        budget = strategy.bytes_per_chunk
        batch: list[str] = []
        running = 0
        for f in files:
            try:
                sz = max(0, int(size_lookup(f) or 0))
            except (TypeError, ValueError):
                sz = 0
            # If a single file is bigger than the budget, it goes in a
            # chunk by itself rather than being split — p4 sync is
            # whole-file atomic.
            if batch and (running + sz) > budget:
                yield batch
                batch = []
                running = 0
            batch.append(f)
            running += sz
        if batch:
            yield batch
        return

    # Default + fallback: fixed file count per chunk.
    n = strategy.files_per_chunk
    for i in range(0, len(files), n):
        yield list(files[i:i + n])


def estimate_chunk_count(
    files: Sequence[str],
    strategy: ChunkingStrategy,
    *,
    size_lookup: Callable[[str], int] | None = None,
) -> int:
    """Pre-flight count of how many chunks ``iter_file_batches`` will
    yield. Cheaper than materializing the iterator just to count."""
    if not files:
        return 0
    if strategy.mode == "single":
        return len(files)
    if strategy.mode == "size" and size_lookup is not None:
        # Walk the sizes; we still don't materialize the path lists.
        budget = strategy.bytes_per_chunk
        running = 0
        chunks = 0
        in_batch = False
        for f in files:
            try:
                sz = max(0, int(size_lookup(f) or 0))
            except (TypeError, ValueError):
                sz = 0
            if in_batch and (running + sz) > budget:
                chunks += 1
                running = 0
                in_batch = False
            running += sz
            in_batch = True
        if in_batch:
            chunks += 1
        return chunks
    n = max(1, strategy.files_per_chunk)
    return (len(files) + n - 1) // n
