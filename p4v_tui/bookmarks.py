"""Path bookmarks, backed by permalinks.

A bookmark stores a permalink id (see :mod:`p4v_tui.permalink`) plus a
display label, never a raw path — so when the bookmarked file is later
moved or renamed, jumping to the bookmark still resolves to its current
location (the app follows the move history at navigation time).

This module is the pure JSON-backed store; the registry mapping and
move-following live elsewhere. Tolerant of a missing / corrupt file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Bookmark:
    permalink_id: str
    label: str


class BookmarkStore:
    def __init__(
        self,
        path: str | Path,
        *,
        after_write: "Callable[[Path], None] | None" = None,
    ) -> None:
        self.path = Path(path)
        # See PermalinkRegistry: called after a successful save so the app
        # can p4-track the versioned shared-state file. None for pure use.
        self._after_write = after_write
        self._items: list[Bookmark] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, list):
            return
        items: list[Bookmark] = []
        for d in data:
            if isinstance(d, dict) and d.get("permalink_id") is not None:
                items.append(
                    Bookmark(str(d["permalink_id"]), str(d.get("label", "")))
                )
        self._items = items

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write (see PermalinkRegistry._save): temp + replace so
            # a crash mid-write can't truncate and lose every bookmark.
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(
                json.dumps(
                    [{"permalink_id": b.permalink_id, "label": b.label}
                     for b in self._items],
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError:
            return
        if self._after_write is not None:
            try:
                self._after_write(self.path)
            except Exception:  # noqa: BLE001 -- tracking is best-effort
                pass

    def add(self, permalink_id: int | str, label: str) -> bool:
        """Add a bookmark. Returns True if new, False if it already existed
        (in which case the label is refreshed)."""
        vid = str(permalink_id)
        for b in self._items:
            if b.permalink_id == vid:
                b.label = label
                self._save()
                return False
        self._items.append(Bookmark(vid, label))
        self._save()
        return True

    def remove(self, permalink_id: int | str) -> bool:
        vid = str(permalink_id)
        before = len(self._items)
        self._items = [b for b in self._items if b.permalink_id != vid]
        if len(self._items) != before:
            self._save()
            return True
        return False

    def list(self) -> list[Bookmark]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)
