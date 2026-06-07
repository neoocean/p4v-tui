"""Discovery + restoration for chunked jobs persisted across launches.

When a chunked job (currently :class:`ChunkedSyncJob`) is interrupted —
the user closes the app, the machine sleeps, the SSH session drops —
its on-disk state file in ``~/.p4v-tui/sync-state/`` survives. On the
next launch we scan that directory, surface any unfinished work to the
user, and let them resume / discard each item individually.

Public API:
* :func:`discover` returns a list of :class:`PendingJobInfo` snapshots.
* :func:`build_job` reconstructs a Job instance from a saved state.
* :func:`delete_state` removes the state file for one entry.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .p4client import P4Service
from .sync_job import ChunkedSyncJob, _state_dir


@dataclass
class PendingJobInfo:
    """Snapshot of a saved chunked-job state file.

    The ``state_path`` is the source-of-truth file on disk; we keep it
    so the modal can call :func:`delete_state` precisely.
    """
    state_path: Path
    job_type: str
    name: str
    target: str
    completed_count: int
    updated_at: int

    @property
    def age_seconds(self) -> int:
        return max(0, int(time.time()) - self.updated_at)


def discover() -> list[PendingJobInfo]:
    """Return every parseable state file in the state dir, sorted by
    most-recently-updated first."""
    out: list[PendingJobInfo] = []
    for path in sorted(_state_dir().glob("*.json")):
        info = _load_one(path)
        if info is not None:
            out.append(info)
    out.sort(key=lambda i: i.updated_at, reverse=True)
    return out


def _load_one(path: Path) -> Optional[PendingJobInfo]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version", 1)
    if version >= 2:
        job_type = str(data.get("job_type") or "ChunkedSyncJob")
        params = data.get("params") or {}
        target = str(params.get("target") or data.get("target") or "")
        force = bool(params.get("force"))
    else:
        job_type = "ChunkedSyncJob"
        target = str(data.get("target") or "")
        force = False
    if not target:
        return None
    completed = data.get("completed") or []
    name_field = data.get("name")
    if name_field:
        name = str(name_field)
    else:
        prefix = "Force-Sync" if force else "Sync"
        name = f"{prefix} {target}"
    updated = int(data.get("updated_at") or 0)
    return PendingJobInfo(
        state_path=path,
        job_type=job_type,
        name=name,
        target=target,
        completed_count=len(completed)
        if isinstance(completed, list) else 0,
        updated_at=updated,
    )


# Mapping job_type → factory(p4, data) → Job | None.
# Add new chunked job types here as they grow state persistence.
_BUILDERS = {
    "ChunkedSyncJob": ChunkedSyncJob.from_state,
}


def build_job(p4: P4Service, info: PendingJobInfo):
    builder = _BUILDERS.get(info.job_type)
    if builder is None:
        return None
    try:
        with info.state_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return builder(p4, data)


def delete_state(info: PendingJobInfo) -> bool:
    try:
        info.state_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False
