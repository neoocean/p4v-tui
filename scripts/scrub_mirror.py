#!/usr/bin/env python3
"""scrub_mirror.py — sanitize source for the public GitHub mirror.

Adapted from docker-monitor's ``strip-private-blocks.py`` (DESIGN §B.149).
The **Perforce** tree keeps verbatim internal content (accurate runbook /
agent guidance); this script produces the *public-mirror* view by
rewriting operator-private identifiers. It is invoked by
``sync-to-github.sh`` between ``p4 sync`` and ``git add`` — never against
the operator's main working tree.

Two layers, driven by an external JSON config (the denylist source):

  Layer 1 — ``replacements``: a ``{real: placeholder}`` map. Each literal
            ``real`` is substituted with a *readable* ``placeholder``
            (e.g. ``admin@shared`` → ``admin@shared``) so the public
            docs still read naturally. Applied longest-key-first so
            specific paths win over their prefixes
            (``//depot/gamma`` before ``//depot``).

  Layer 2 — ``redact``: a list of literals replaced with ``<redacted>``
            (for values with no sensible public placeholder).

  PUBLIC keep-spans — text wrapped in ``…``
            is preserved verbatim and shielded from both layers (rare:
            a denylist literal that is legitimately public in one spot).

Fail-CLOSED: a missing / unparseable / empty config aborts with a
non-zero exit so the caller (``set -e``) blocks the push rather than
leaking. Modes: ``--dir <tree>`` (in-place over text files) and
``--stdin`` (filter, used for the commit message).

    scrub_mirror.py --dir <mirror>   --config scripts/mirror-scrub.json
    scrub_mirror.py --stdin          --config scripts/mirror-scrub.json

Exit codes: 0 ok · 2 config/args error · 4 per-file I/O errors.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_PUBLIC_OPEN = ""
_PUBLIC_CLOSE = ""
_PUBLIC_SENTINEL = "__MIRRORKEEP_{idx}__"
_REDACTED = "<redacted>"

_TEXT_EXTENSIONS = {
    ".md", ".py", ".sh", ".txt", ".json", ".toml", ".ini", ".cfg",
    ".conf", ".yaml", ".yml", ".tcss", ".example", ".rst", ".svg",
}
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
}
# Files never scrubbed (matched by exact basename). ``CNAME`` is a GitHub
# Pages control file whose entire contents are, by definition, a single
# public hostname (the custom domain). It is extensionless, so it would
# otherwise be caught by the ``suffix == ""`` branch below and have the
# ``<redacted>`` in ``p4v-tui.<redacted>.org`` redacted to ``<redacted>`` —
# which silently breaks the published site's domain. Nothing secret ever
# belongs in a CNAME, so exempting it by name is safe.
_SKIP_FILES = {"CNAME"}


def _fatal(msg: str) -> "NoReturn":  # noqa: F821
    """Print to stderr and exit 2 (config/args error — fail-closed)."""
    sys.stderr.write(f"[scrub] FATAL: {msg}\n")
    raise SystemExit(2)


def _load_config(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (replacements, redact). Fail-closed on any problem."""
    if not path.is_file():
        _fatal(f"config not found at {path} — refusing to push.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _fatal(f"config unreadable/unparseable: {exc!r} — refusing to push.")
    if not isinstance(data, dict):
        _fatal("config root must be an object.")
    repl = data.get("replacements", {})
    redact = data.get("redact", [])
    if not isinstance(repl, dict) or not isinstance(redact, list):
        _fatal("'replacements' must be an object and 'redact' a list.")
    pairs = [(str(k), str(v)) for k, v in repl.items() if str(k)]
    # Longest key first so specific literals win over their prefixes.
    pairs.sort(key=lambda kv: (-len(kv[0]), kv[0]))
    reds = sorted({str(x) for x in redact if str(x)}, key=lambda s: -len(s))
    if not pairs and not reds:
        _fatal("config has no replacements or redactions — refusing to "
               "push (an empty denylist is almost certainly a mistake).")
    return pairs, reds


def _protect_public(text: str) -> tuple[str, list[str]]:
    out: list[str] = []
    spans: list[str] = []
    pos = 0
    while True:
        i = text.find(_PUBLIC_OPEN, pos)
        if i < 0:
            out.append(text[pos:])
            break
        j = text.find(_PUBLIC_CLOSE, i + len(_PUBLIC_OPEN))
        if j < 0:
            # Unbalanced open protects nothing — leave literal (fail-open
            # here would silently suppress redaction of the rest).
            sys.stderr.write(
                f"[scrub] WARN: unbalanced {_PUBLIC_OPEN!r}; span not "
                "protected.\n")
            out.append(text[pos:i + len(_PUBLIC_OPEN)])
            pos = i + len(_PUBLIC_OPEN)
            continue
        out.append(text[pos:i])
        out.append(_PUBLIC_SENTINEL.format(idx=len(spans)))
        spans.append(text[i + len(_PUBLIC_OPEN):j])
        pos = j + len(_PUBLIC_CLOSE)
    return "".join(out), spans


def _restore_public(text: str, spans: list[str]) -> str:
    for idx, inner in enumerate(spans):
        text = text.replace(_PUBLIC_SENTINEL.format(idx=idx), inner)
    return text


def scrub_text(text: str, pairs: list[tuple[str, str]],
               reds: list[str]) -> tuple[str, int]:
    text, public = _protect_public(text)
    hits = 0
    for real, placeholder in pairs:
        if real in text:
            hits += text.count(real)
            text = text.replace(real, placeholder)
    for real in reds:
        if real in text:
            hits += text.count(real)
            text = text.replace(real, _REDACTED)
    text = _restore_public(text, public)
    return text, hits


def _iter_text_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if fname in _SKIP_FILES:
                continue
            p = Path(dirpath) / fname
            if p.suffix.lower() in _TEXT_EXTENSIONS:
                yield p
            elif p.suffix == "":
                try:
                    if p.stat().st_size <= 256 * 1024:
                        yield p
                except OSError:
                    pass


def _process_dir(root: Path, pairs, reds) -> int:
    n_files = n_changed = total_hits = n_errors = 0
    for path in _iter_text_files(root):
        n_files += 1
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary / unreadable — skip silently
        scrubbed, hits = scrub_text(original, pairs, reds)
        if scrubbed != original:
            try:
                # p4 sync leaves files read-only (0o444); make writable.
                mode = path.stat().st_mode & 0o777
                if not (mode & 0o200):
                    os.chmod(path, mode | 0o200)
                path.write_text(scrubbed, encoding="utf-8")
            except OSError as exc:
                sys.stderr.write(f"[scrub] ERROR writing {path}: {exc!r}\n")
                n_errors += 1
                continue
            n_changed += 1
            total_hits += hits
    sys.stdout.write(
        f"[scrub] scanned {n_files}, modified {n_changed}, "
        f"{total_hits} substitutions, {n_errors} errors\n")
    return 0 if n_errors == 0 else 4


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dir", help="In-place scrub a directory tree.")
    mode.add_argument("--stdin", action="store_true",
                      help="Filter stdin → stdout (commit messages).")
    ap.add_argument("--config", required=True,
                    help="Path to the scrub config JSON (denylist source).")
    args = ap.parse_args(argv)
    pairs, reds = _load_config(Path(args.config))
    if args.stdin:
        scrubbed, _ = scrub_text(sys.stdin.read(), pairs, reds)
        sys.stdout.write(scrubbed)
        return 0
    return _process_dir(Path(args.dir), pairs, reds)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
