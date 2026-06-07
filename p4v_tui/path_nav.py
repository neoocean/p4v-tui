"""Pure helpers for "go to path" tree navigation (item 9).

The app lets the user paste a Perforce depot path or a local filesystem
path and jumps the tree to it. This module owns the (testable) string
classification; the actual tree walk + local↔depot translation lives in
the app, which reuses the existing Find-File navigation path.

It also owns :func:`plan_goto_fallback`, the pure branch logic for the
fuzzy fallback when an exact path lookup misses (the "직접 path 입력에도
동일 fallback" roadmap step — see that function's docstring).
"""
from __future__ import annotations

import re

from .permalink import parse_permalink

_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _strip_rev(depot: str) -> str:
    """Drop a trailing ``#rev`` / ``@change`` qualifier from a depot path.

    We navigate to the file/dir, not a specific revision, so
    ``//d/f.txt#5`` and ``//d/...@1234`` collapse to their path.
    """
    for sep in ("#", "@"):
        idx = depot.rfind(sep)
        if idx > 1 and "/" not in depot[idx:]:
            depot = depot[:idx]
    return depot


def classify_path(raw: str) -> tuple[str, str]:
    """Classify a pasted path string for tree navigation.

    Returns ``(kind, normalized)`` where ``kind`` is one of:

    * ``"permalink"`` — an immutable permalink (``//@p/<id>``); the
      normalized value is the bare id string (see :mod:`p4v_tui.permalink`)
    * ``"depot"``   — a Perforce depot path (``//...``)
    * ``"local"``   — an absolute local filesystem path
    * ``"empty"``   — nothing usable
    * ``"unknown"`` — couldn't classify (relative path / junk)

    Leading/trailing whitespace and a single layer of matching quotes
    are stripped; a depot path's trailing revision qualifier is removed.
    """
    s = (raw or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    if not s:
        return ("empty", "")
    vid = parse_permalink(s)
    if vid is not None:
        return ("permalink", vid)
    if s.startswith("//"):
        return ("depot", _strip_rev(s))
    if s.startswith(("/", "~")) or _WIN_DRIVE.match(s):
        return ("local", s)
    return ("unknown", s)


def plan_goto_fallback(
    loose_hits: list[str],
    suggestions: list[str],
) -> tuple[str, list[str]]:
    """Decide what Go-to-path should do when an exact lookup found nothing.

    This is the "직접 path 입력에도 동일 fallback" roadmap step: when the
    user pastes a fragment (``"unknown"``) or a depot path that no longer
    resolves (typo / moved / wrong slash), the app reuses the Fast Search
    fuzzy ladder — :meth:`SearchIndex.query_files_loose` (token-AND) then
    :meth:`SearchIndex.suggest_corrections` (Levenshtein on leaf names) —
    and hands the results here to pick a UI action.

    ``loose_hits`` is the token-AND match list (depot paths, best first);
    ``suggestions`` is the typo-recovery leaf list, only meaningful when
    ``loose_hits`` is empty. Returns ``(action, payload)``:

    * ``("navigate", [path])`` — exactly one loose hit; jump straight to it.
    * ``("pick", paths)``      — several loose hits; let the user choose.
    * ``("suggest", leaves)``  — no loose hit, but near-miss leaf names to
      show as a "did you mean…" hint (non-navigable).
    * ``("none", [])``         — nothing to offer; the caller reports a miss.

    Kept pure (no index / Textual access) so the branch logic is unit
    tested directly; the app does the index query + screen wiring.
    """
    hits = [h for h in (loose_hits or []) if h]
    if len(hits) == 1:
        return ("navigate", [hits[0]])
    if len(hits) > 1:
        return ("pick", hits)
    sugg = [s for s in (suggestions or []) if s]
    if sugg:
        return ("suggest", sugg)
    return ("none", [])
