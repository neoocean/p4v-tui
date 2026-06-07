"""Pure 3-way conflict-marker engine for the in-app merge editor (item 1).

``p4 resolve -am`` leaves a conflicting file in the workspace with
Perforce's textual markers:

    >>>> ORIGINAL //depot/file#n
    <base / common-ancestor lines>
    ==== THEIRS //depot/file#m
    <their lines>
    ==== YOURS //depot/file
    <your lines>
    <<<<

This module parses that into a flat segment list (common text +
conflict hunks) and reconstructs a merged file from a per-hunk choice.
Both directions are pure (no Perforce, no UI) and unit tested; the modal
collects the choices and the app writes the result back + accepts it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

OPEN = ">>>>"
SEP = "===="
CLOSE = "<<<<"


@dataclass
class Common:
    """Lines outside any conflict (identical in both sides)."""
    lines: list[str] = field(default_factory=list)


@dataclass
class Conflict:
    """One conflict hunk with its three sides (any may be empty)."""
    base: list[str] = field(default_factory=list)
    theirs: list[str] = field(default_factory=list)
    yours: list[str] = field(default_factory=list)


def _label_of(marker_line: str) -> str:
    """Pull ORIGINAL / THEIRS / YOURS out of a marker line (upper)."""
    parts = marker_line.split()
    return parts[1].upper() if len(parts) >= 2 else ""


def _make_conflict(sections: list[tuple[str, list[str]]]) -> Conflict:
    """Map labelled sections to a Conflict; fall back to positional."""
    c = Conflict()
    by_label = {label: lines for label, lines in sections}
    if "ORIGINAL" in by_label or "THEIRS" in by_label or "YOURS" in by_label:
        c.base = by_label.get("ORIGINAL", [])
        c.theirs = by_label.get("THEIRS", [])
        c.yours = by_label.get("YOURS", [])
    else:
        # No recognisable labels — assume base / theirs / yours order.
        ordered = [lines for _l, lines in sections]
        c.base = ordered[0] if len(ordered) > 0 else []
        c.theirs = ordered[1] if len(ordered) > 1 else []
        c.yours = ordered[2] if len(ordered) > 2 else []
    return c


def parse_conflict_markers(text: str) -> list:
    """Parse marker text into a list of :class:`Common` / :class:`Conflict`."""
    lines = (text or "").split("\n")
    segments: list = []
    buf: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.startswith(OPEN):
            if buf:
                segments.append(Common(buf))
                buf = []
            sections: list[tuple[str, list[str]]] = []
            cur_label = _label_of(line)
            cur: list[str] = []
            i += 1
            while i < n and not lines[i].startswith(CLOSE):
                if lines[i].startswith(SEP):
                    sections.append((cur_label, cur))
                    cur_label = _label_of(lines[i])
                    cur = []
                else:
                    cur.append(lines[i])
                i += 1
            sections.append((cur_label, cur))
            if i < n and lines[i].startswith(CLOSE):
                i += 1  # consume the closing marker
            segments.append(_make_conflict(sections))
        else:
            buf.append(line)
            i += 1
    if buf:
        segments.append(Common(buf))
    return segments


def has_conflicts(segments: list) -> bool:
    return any(isinstance(s, Conflict) for s in segments)


def conflicts(segments: list) -> list:
    return [s for s in segments if isinstance(s, Conflict)]


# Per-hunk choices.
YOURS, THEIRS, BASE, BOTH = "yours", "theirs", "base", "both"


def _hunk_lines(c: Conflict, choice: str) -> list[str]:
    if choice == YOURS:
        return c.yours
    if choice == THEIRS:
        return c.theirs
    if choice == BASE:
        return c.base
    if choice == BOTH:
        return list(c.yours) + list(c.theirs)
    return c.yours  # safe default


def reconstruct(segments: list, choices: list[str]) -> str:
    """Rebuild file text, replacing each Conflict by its chosen side.

    ``choices`` is parallel to :func:`conflicts` (one entry per hunk, in
    order). A short/empty list defaults remaining hunks to "yours".
    """
    out: list[str] = []
    ci = 0
    for seg in segments:
        if isinstance(seg, Common):
            out.extend(seg.lines)
        else:
            choice = choices[ci] if ci < len(choices) else YOURS
            out.extend(_hunk_lines(seg, choice))
            ci += 1
    return "\n".join(out)
