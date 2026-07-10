"""Pure helpers for the Branch Files (``p4 populate``) flow.

p4v's Branch dialog can branch either by an explicit source→target pair
*or* via a named branch mapping, previews what will be created, and only
then submits. The TUI previously ran ``p4 populate -d <desc> <src> <tgt>``
straight to an auto-submit with no preview and no branch-mapping option.

This module builds the ``populate`` argv (dry-run or real, pair-mode or
branch-mapping mode) and parses the dry-run result into a flat file list.
Keeping it pure makes the argv shape — which is easy to get subtly wrong
across the two ``populate`` forms — unit-testable without a server.
"""
from __future__ import annotations


def build_populate_args(
    *,
    source: str = "",
    target: str = "",
    branch: str = "",
    description: str = "",
    dry_run: bool = False,
) -> tuple[str, ...]:
    """Assemble the ``p4 populate`` argv.

    Two mutually-exclusive forms:

    * **pair mode** (no ``branch``): ``populate [-n] [-d desc] src tgt`` —
      both ``source`` and ``target`` are required.
    * **branch-mapping mode** (``branch`` set): ``populate [-n] [-d desc]
      -b <branch> [tgt]`` — the mapping defines the view; ``target`` is an
      optional restriction.

    ``-n`` makes it a dry run (preview only, no submit). Raises
    ``ValueError`` when a required path is missing so the caller surfaces
    a clear message instead of letting ``p4`` reject a malformed command.
    """
    args: list[str] = ["populate"]
    if dry_run:
        args.append("-n")
    if description:
        args += ["-d", description]

    if branch:
        args += ["-b", branch]
        if target:
            args.append(target)
    else:
        if not source or not target:
            raise ValueError(
                "source and target are required when no branch mapping is set"
            )
        args += [source, target]
    return tuple(args)


def parse_populate_preview(rows: list) -> list[str]:
    """Pull the depot paths out of a ``populate -n`` dry-run result.

    Each branched file is a dict carrying ``depotFile`` (the *target*
    path that would be created). Non-dict rows and info banners are
    skipped. Order preserved.
    """
    out: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        df = r.get("depotFile") or r.get("toFile")
        if df:
            out.append(str(df))
    return out
