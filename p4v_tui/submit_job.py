"""Resilient submit job that survives server restarts mid-submit.

Wraps ``p4 submit -c <CL>`` so that:

* A connection drop during submit reconnects-and-retries over a long
  window — ``P4Service.run`` is invoked with a generous ``max_attempts``
  so a multi-minute server restart is absorbed.
* If the server actually committed but the ack was lost, the next
  retry's "no such pending changelist" / "already submitted" error is
  recognized as success rather than reported as failure (idempotency
  check against the current pending list for this client).
* A genuine command error (locked files, resolve required, etc.) is
  raised and the job marked failed.
"""
from __future__ import annotations

from .jobs import Job
from .p4client import P4Exception, P4Service


class ResilientSubmitJob(Job):
    def __init__(
        self,
        p4: P4Service,
        change: str,
        max_attempts: int = 60,
    ) -> None:
        super().__init__(name=f"Submit @{change}")
        self._p4 = p4
        self._change = str(change)
        self._max_attempts = max_attempts
        self.total_chunks = 1
        self.result_change: str | None = None

    def chunks(self):
        yield self._do_submit

    def _do_submit(self) -> None:
        try:
            res = self._p4.run(
                "submit", "-c", self._change,
                max_attempts=self._max_attempts,
            )
        except P4Exception as e:
            if self._is_already_submitted_recovery(e):
                self.result_change = self._change
                return
            raise
        # Walk the result for the canonical 'submittedChange' marker.
        for r in res:
            if isinstance(r, dict) and r.get("submittedChange"):
                self.result_change = str(r["submittedChange"])
                return
        # No marker found — fall back to the original CL number.
        self.result_change = self._change

    def _is_already_submitted_recovery(self, exc: BaseException) -> bool:
        """Return True if the failure looks like a lost-ack situation
        and the CL is no longer pending on the server (== submitted).
        """
        msg = str(exc).lower()
        looks_like_already = (
            "no such" in msg
            or "already submitted" in msg
            or "no such changelist" in msg
        )
        if not looks_like_already:
            return False
        try:
            pending = self._p4.pending_changes(client=self._p4.client)
        except P4Exception:
            # Can't verify right now — be conservative and report failure.
            return False
        return all(str(r.get("change")) != self._change for r in pending)
