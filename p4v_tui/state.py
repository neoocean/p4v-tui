"""Persistent UI state for p4v-tui.

A small JSON file in ``~/.p4v-tui/state.json`` remembers things that
should survive across launches — currently which left/right tab the user
last had selected.

Reads are best-effort: a missing or corrupt file falls back to ``{}``.
Writes are atomic via tmp-rename and silently swallow OS errors so a
read-only home directory never breaks the app.
"""
from __future__ import annotations

import json
from pathlib import Path


STATE_PATH = Path.home() / ".p4v-tui" / "state.json"


def load_state() -> dict:
    try:
        if not STATE_PATH.is_file():
            return {}
        with STATE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(data: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        tmp.replace(STATE_PATH)
    except OSError:
        pass
