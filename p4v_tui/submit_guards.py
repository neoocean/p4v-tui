"""Pre-submit safety guards.

p4v will happily submit a changelist that contains an accidentally-added
500 MB build artifact, or refuse cryptically at the server when files
still need resolving. These guards run *before* the submit confirm so the
user sees the risk and can back out.

The decision logic here is pure (no Perforce, no app) so it is unit
tested; the app gathers ``SubmitFile`` rows best-effort and renders the
result into the existing confirm modal.
"""
from __future__ import annotations

from dataclasses import dataclass

# Default "this file is suspiciously large for a submit" threshold. Most
# source files are kilobytes; a tens-of-MB file in a CL is usually an
# accidental binary / build output / captured log.
DEFAULT_LARGE_FILE_BYTES = 25 * 1024 * 1024  # 25 MB


@dataclass(frozen=True)
class SubmitFile:
    """One opened file in the changelist being submitted."""
    depot_path: str
    action: str = ""
    size_bytes: int | None = None   # local working-copy size, None if unknown
    unresolved: bool = False


@dataclass(frozen=True)
class GuardWarning:
    level: str    # "block" (server will likely reject) | "warn" (advisory)
    code: str     # stable id: "empty" | "unresolved" | "large_file"
    message: str


def _sample(files: list[SubmitFile], limit: int = 3) -> str:
    """Render up to ``limit`` depot paths, then '… and N more'."""
    names = [f.depot_path for f in files[:limit]]
    extra = len(files) - len(names)
    text = ", ".join(names)
    if extra > 0:
        text += f", … and {extra} more"
    return text


def evaluate_submit_guards(
    files: list[SubmitFile],
    *,
    large_file_bytes: int = DEFAULT_LARGE_FILE_BYTES,
) -> list[GuardWarning]:
    """Return the ordered list of guard warnings for a submit.

    ``block`` = the submit will almost certainly fail or is meaningless
    (empty CL, unresolved files); ``warn`` = proceed-with-caution
    (oversized file). Order: blocks first, then warns.
    """
    blocks: list[GuardWarning] = []
    warns: list[GuardWarning] = []

    if not files:
        blocks.append(GuardWarning(
            "block", "empty",
            "This changelist has no open files to submit.",
        ))
        return blocks

    unresolved = [f for f in files if f.unresolved]
    if unresolved:
        blocks.append(GuardWarning(
            "block", "unresolved",
            f"{len(unresolved)} file(s) still need resolve "
            f"(p4 will reject the submit): {_sample(unresolved)}",
        ))

    large = [
        f for f in files
        if f.size_bytes is not None and f.size_bytes >= large_file_bytes
    ]
    if large:
        mb = large_file_bytes / (1024 * 1024)
        blocks_or_warn = GuardWarning(
            "warn", "large_file",
            f"{len(large)} file(s) ≥ {mb:.0f} MB — check for an accidental "
            f"binary/build artifact: {_sample(large)}",
        )
        warns.append(blocks_or_warn)

    return blocks + warns


def has_blocking(warnings: list[GuardWarning]) -> bool:
    return any(w.level == "block" for w in warnings)


def format_guard_warnings(warnings: list[GuardWarning]) -> str:
    """Render warnings as a marker-prefixed block, or '' when there are none."""
    if not warnings:
        return ""
    marker = {"block": "⛔", "warn": "⚠"}
    lines = [f"{marker.get(w.level, '•')} {w.message}" for w in warnings]
    return "\n".join(lines)
