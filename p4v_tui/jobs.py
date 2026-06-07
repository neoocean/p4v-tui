"""Job runner for chunked, interleavable P4 work.

Long-running operations (sync, reconcile, revert over a large tree) are
modeled as :class:`Job` objects whose work is split into ordered chunks.
The :class:`JobRunner` holds a single worker thread that pulls
``(priority, sequence, callable)`` items off a heap; lower-numbered
priorities run first, with FIFO order inside a priority class.

Interactive shortcuts that need to feel responsive enqueue with
``PRIORITY_INTERACTIVE`` (0); chunked work uses ``PRIORITY_CHUNKED`` (10),
so a keypress dispatched mid-sync is served before the next sync chunk.

After each chunk runs, the job re-enqueues its own next chunk via
:meth:`JobRunner.submit_command`. Anything that landed in the heap during
the chunk's execution gets pulled first if its priority is lower.

Cancellation: setting ``Job.cancelled = True`` causes the runner to drop
remaining chunks at the next pull (already-executing chunks finish; this
runner does not preempt mid-chunk, so chunks should be small).
"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterator, Optional


PRIORITY_INTERACTIVE = 0   # keypresses, UI-driven one-shot calls
PRIORITY_CHUNKED = 10      # background long operations split into chunks


class Job(ABC):
    """A unit of work that can be split into smaller chunks.

    Subclasses implement :meth:`chunks` returning an iterator of zero-arg
    callables. The runner consumes one callable at a time; each is one
    "chunk" of work (e.g. sync 50 files, reconcile one subdir).
    """

    def __init__(
        self,
        name: str,
        priority: int = PRIORITY_CHUNKED,
    ) -> None:
        self.name = name
        self.priority = priority
        self.done_chunks = 0
        self.total_chunks: Optional[int] = None  # None when unknown upfront
        self.cancelled = False
        self.failed = False
        self.last_error: Optional[BaseException] = None
        # Wall-clock start of the first chunk, used for ETA averaging.
        # Set lazily by JobRunner the first time a chunk runs.
        self.start_time: Optional[float] = None

    @abstractmethod
    def chunks(self) -> Iterator[Callable[[], Any]]:
        ...

    @property
    def progress(self) -> tuple[int, Optional[int]]:
        return (self.done_chunks, self.total_chunks)

    @property
    def eta_seconds(self) -> Optional[float]:
        """Linear extrapolation: avg-time-per-completed-chunk × chunks-left.

        Returns None until at least one chunk has finished and the total
        is known. Recomputed on every read, so the UI gets an estimate
        that grows when the network slows down (subsequent chunks taking
        longer raise the running average).
        """
        if (self.total_chunks is None or self.start_time is None
                or self.done_chunks <= 0):
            return None
        elapsed = time.time() - self.start_time
        if elapsed <= 0:
            return None
        per_chunk = elapsed / self.done_chunks
        return per_chunk * max(0, self.total_chunks - self.done_chunks)

    @property
    def finished(self) -> bool:
        return (
            self.cancelled
            or self.failed
            or (
                self.total_chunks is not None
                and self.done_chunks >= self.total_chunks
            )
        )


class JobRunner:
    """Single-worker priority queue for chunked + interactive work.

    Heap items: ``(priority, sequence, callable, optional_job)``.
    Sequence numbers break priority ties to give FIFO inside a class.
    """

    def __init__(self, cmd_log=None) -> None:
        self._heap: list = []
        self._cv = threading.Condition()
        self._seq = itertools.count()
        self._worker: Optional[threading.Thread] = None
        self._shutdown = False
        self._on_progress: Optional[Callable[[Job], None]] = None
        # Optional CmdLog: when present, every Job gets an entry that
        # acts as parent for the p4 commands its chunks fire.
        self._cmd_log = cmd_log
        # Jobs that have been submitted but not yet reached a terminal
        # state (done / failed / cancelled). stop() flips them all to
        # cancelled so the worker drops queued chunks instead of running
        # them after the user asked to quit.
        self._active_jobs: set = set()

    # --- lifecycle ---------------------------------------------------------

    def start(
        self, on_progress: Optional[Callable[[Job], None]] = None,
    ) -> None:
        """Start the worker thread (idempotent)."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._on_progress = on_progress
        self._shutdown = False
        self._worker = threading.Thread(
            target=self._loop, daemon=True, name="JobRunner",
        )
        self._worker.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._cv:
            self._shutdown = True
            # Cancel every still-active job so any queued chunks bail
            # out instead of running after we've asked to shut down.
            # Chunks already mid-execution finish naturally — saved
            # state still reflects their work, so the next launch can
            # offer to resume them.
            for job in list(self._active_jobs):
                try:
                    job.cancelled = True
                except Exception:  # noqa: BLE001
                    pass
            self._cv.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=timeout)
            self._worker = None

    # --- enqueue -----------------------------------------------------------

    def submit_command(
        self,
        fn: Callable[[], Any],
        *,
        priority: int = PRIORITY_INTERACTIVE,
        job: Optional[Job] = None,
    ) -> None:
        """Enqueue a single callable. Lower priority runs first."""
        with self._cv:
            heapq.heappush(
                self._heap, (priority, next(self._seq), fn, job),
            )
            self._cv.notify()

    def submit_job(self, job: Job) -> None:
        """Enqueue a chunked job.

        Each chunk runs and then re-enqueues the next chunk at the job's
        priority — so any interactive items that landed on the heap during
        the chunk get served before the next chunk.
        """
        chunks_iter = job.chunks()
        log = self._cmd_log
        job_log_id = log.begin_job(job.name) if log is not None else None
        # Closed-over single-element list so end_job is idempotent across
        # the multiple paths (cancel / done / failure / StopIteration).
        ended = [False]
        with self._cv:
            self._active_jobs.add(job)

        def end_job_once(failed: bool, err: Optional[str] = None) -> None:
            with self._cv:
                self._active_jobs.discard(job)
            if ended[0] or log is None or job_log_id is None:
                ended[0] = True
                return
            log.end_job(job_log_id, failed=failed, error=err)
            ended[0] = True

        def run_next_chunk() -> None:
            if job.cancelled or self._shutdown:
                end_job_once(failed=False)
                return
            try:
                chunk = next(chunks_iter)
            except StopIteration:
                end_job_once(failed=False)
                self._notify(job)
                return
            if job.start_time is None:
                # ETA averaging starts from the first chunk that actually
                # runs (skip the time spent waiting in the queue).
                job.start_time = time.time()
            if log is not None:
                log.set_current_job(job_log_id)
            try:
                chunk()
                job.done_chunks += 1
            except BaseException as e:  # noqa: BLE001
                job.failed = True
                job.last_error = e
                end_job_once(failed=True, err=str(e))
                if log is not None:
                    log.set_current_job(None)
                self._notify(job)
                return
            finally:
                if log is not None:
                    log.set_current_job(None)
            # Push progress + ETA inputs to the cmd log entry so the
            # Command Monitor can render an updated estimate.
            if log is not None and job_log_id is not None:
                log.update_job_progress(
                    job_log_id,
                    done=job.done_chunks,
                    total=job.total_chunks,
                    start_time=job.start_time,
                )
            self._notify(job)
            self.submit_command(
                run_next_chunk, priority=job.priority, job=job,
            )

        self.submit_command(
            run_next_chunk, priority=job.priority, job=job,
        )

    # --- internals ---------------------------------------------------------

    def _notify(self, job: Job) -> None:
        cb = self._on_progress
        if cb is None:
            return
        try:
            cb(job)
        except Exception:  # noqa: BLE001
            # Never let a UI callback take down the worker.
            pass

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._heap and not self._shutdown:
                    self._cv.wait()
                if self._shutdown and not self._heap:
                    return
                _, _, fn, _ = heapq.heappop(self._heap)
            try:
                fn()
            except Exception:  # noqa: BLE001
                # Each fn is responsible for its own error handling;
                # we swallow here so the loop keeps draining.
                pass

    # --- diagnostics -------------------------------------------------------

    def queue_depth(self) -> int:
        with self._cv:
            return len(self._heap)
