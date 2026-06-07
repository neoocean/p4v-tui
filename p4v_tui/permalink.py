"""Permalinks — immutable, move-following references to a depot path.

A depot path like ``//depot/alpha/item-1234/...`` can be renamed or
moved, breaking any pasted reference. A *permalink* is a stable local
handle (``//@p/<id>``) that maps to the path it was created at; resolving
it later follows Perforce's move/rename history to wherever the file
lives now (see the app's ``_resolve_moved_path``). Same idea as a web
permalink / DOI: the identifier stays put, the target can move.

This module owns the pure pieces: the permalink grammar and a small
JSON-backed registry. Move-following needs the server and lives in the
app. The registry is idempotent per origin path (registering the same
path twice returns the same id).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable

# Permalink grammar: //@p/<digits>. The "@p" root can't be a real depot
# or client name, so it never collides with an actual depot path. The
# legacy "@v" prefix (the old "virtual address" name) is still accepted
# on read so any address copied before the rename keeps resolving.
_PERMALINK_RE = re.compile(r"^//@[pv]/(\d+)$")

_FIRST_ID = 1000  # human-friendlier than starting at 0/1


def make_permalink(pid: int | str) -> str:
    return f"//@p/{pid}"


def parse_permalink(s: str) -> str | None:
    """Return the id (as a string) if ``s`` is a permalink, else None."""
    m = _PERMALINK_RE.match((s or "").strip())
    return m.group(1) if m else None


class PermalinkRegistry:
    """JSON-backed id ↔ origin-path map.

    Format on disk: ``{"next": <int>, "map": {"<id>": "<origin>"}}``.
    Tolerant of a missing / corrupt file (starts empty).
    """

    def __init__(
        self,
        path: str | Path,
        *,
        after_write: "Callable[[Path], None] | None" = None,
    ) -> None:
        self.path = Path(path)
        # Called with the file path right after a successful save. The app
        # uses it to `p4 reconcile` the versioned shared-state file so the
        # change is tracked + submittable; pure callers leave it None.
        self._after_write = after_write
        self._data: dict = {"next": _FIRST_ID, "map": {}}
        self._load()

    def _load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("map"), dict):
                self._data = {
                    "next": int(data.get("next", _FIRST_ID)),
                    "map": {str(k): str(v) for k, v in data["map"].items()},
                }
        except (OSError, ValueError, TypeError):
            pass  # missing / corrupt → keep the empty default

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: a crash mid-write must never corrupt the
            # registry (which would silently lose every permalink). Write
            # a sibling .tmp then os.replace() it — atomic on POSIX/Windows.
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError:
            return  # best-effort; an unwritable home just loses persistence
        if self._after_write is not None:
            try:
                self._after_write(self.path)
            except Exception:  # noqa: BLE001 -- tracking is best-effort
                pass

    def register(self, origin: str) -> int:
        """Return a stable id for ``origin``, creating one if needed."""
        origin = (origin or "").strip()
        for k, v in self._data["map"].items():
            if v == origin:
                return int(k)
        pid = int(self._data["next"])
        self._data["next"] = pid + 1
        self._data["map"][str(pid)] = origin
        self._save()
        return pid

    def lookup(self, pid: int | str) -> str | None:
        return self._data["map"].get(str(pid))
