"""Jira issue linkage helpers (pure).

The user's shop tracks work in Jira, not Perforce jobs. The Jira ↔
Perforce integration (and Smart Commits) link a changelist to an issue
by finding the issue key in the CL **description**, so "attach a Jira
issue at submit" reduces to: ensure the description references a valid
key, and offer the browse URL.

All functions here are pure (no Perforce, no app, no network) and unit
tested. ``known_projects`` lets callers filter out false positives like
``UTF-8`` / ``SHA-1`` that match the generic key shape.
"""
from __future__ import annotations

import re

# PROJECT-123. Project key: an uppercase letter followed by >=1 more
# uppercase-alphanumeric chars, then "-" and the issue number.
_JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def is_valid_jira_key(s: str) -> bool:
    """True if ``s`` is exactly a Jira key (no surrounding text)."""
    return bool(s) and _JIRA_KEY_RE.fullmatch(s.strip()) is not None


def extract_jira_keys(
    text: str,
    known_projects: list[str] | None = None,
) -> list[str]:
    """Return Jira keys found in ``text``, de-duplicated in first-seen order.

    When ``known_projects`` is given, only keys whose project prefix is in
    that set are kept — this filters generic look-alikes (``UTF-8`` etc.).
    """
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _JIRA_KEY_RE.finditer(text):
        key = m.group(0)
        if key not in seen:
            seen.add(key)
            out.append(key)
    if known_projects:
        allowed = {p.strip().upper() for p in known_projects if p.strip()}
        out = [k for k in out if k.split("-", 1)[0].upper() in allowed]
    return out


def _normalize_prefix(prefix: str) -> str:
    """Normalize a depot-path prefix for matching: drop a trailing ``...``
    and ensure a single trailing ``/`` so ``//d/todo`` and ``//d/todo/...``
    both become ``//d/todo/`` (matches files *under* the subtree)."""
    p = (prefix or "").strip()
    if p.endswith("..."):
        p = p[:-3]
    if not p.endswith("/"):
        p += "/"
    return p


def project_for_path(
    depot_path: str,
    path_projects: dict[str, str],
) -> str | None:
    """Return the Jira project mapped to ``depot_path``, or None.

    ``path_projects`` maps a depot-path prefix to a project key. The
    longest matching prefix wins, so a nested mapping overrides a broader
    one.
    """
    best: str | None = None
    best_len = -1
    dp = depot_path or ""
    for prefix, project in (path_projects or {}).items():
        np = _normalize_prefix(prefix)
        if dp.startswith(np) and len(np) > best_len:
            best, best_len = project, len(np)
    return best


def projects_for_paths(
    paths: list[str],
    path_projects: dict[str, str],
) -> list[str]:
    """Distinct projects covering ``paths``, in first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        proj = project_for_path(p, path_projects)
        if proj and proj.upper() not in seen:
            seen.add(proj.upper())
            out.append(proj)
    return out


def build_jira_url(base_url: str, key: str) -> str:
    """Render the issue browse URL: ``{base}/browse/{KEY}``."""
    base = (base_url or "").rstrip("/")
    return f"{base}/browse/{key}"


def ensure_jira_trailer(description: str, key: str) -> str:
    """Return ``description`` with a ``Jira: KEY`` trailer, idempotently.

    If the key is already referenced anywhere in the text, the
    description is returned unchanged. An empty description becomes just
    the trailer line.
    """
    desc = description or ""
    if key in extract_jira_keys(desc):
        return desc
    if not desc.strip():
        return f"Jira: {key}"
    return f"{desc.rstrip()}\n\nJira: {key}"
