"""Pure decision helpers for the *perceived-performance* (체감 성능) layer.

These functions decide what the user sees while work is in flight on a
slow / high-latency link — none of them touch Textual or the network, so
the feel-policy (when to show a spinner, how to escalate a stalling
label, how aggressively to auto-refresh) is unit-testable in isolation.
``app.py`` owns the timers, the widget, and the worker wiring and calls
these to decide *what* to render.

See ``docs/perceived-performance-scenario.md`` for the design and the
latency-feel budget these thresholds come from.
"""
from __future__ import annotations

# Braille spinner frames — same family as the tree-expansion spinner so
# the two read as one visual language.
ACTIVITY_FRAMES: tuple[str, ...] = (
    "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
)

# Latency-feel thresholds (milliseconds). Below DELAY the op will almost
# certainly finish before the eye registers a spinner, so flashing one is
# worse than silence. SLOW / VERY_SLOW escalate the label so a genuine
# stall reads as *acknowledged* rather than hung.
ACTIVITY_DELAY_MS = 150
ACTIVITY_SLOW_MS = 1000
ACTIVITY_VERY_SLOW_MS = 8000


def should_show_activity(
    elapsed_ms: float,
    delay_ms: float = ACTIVITY_DELAY_MS,
) -> bool:
    """True once an in-flight op has run long enough to be worth showing.

    Gating on *elapsed* (not on start) is what keeps sub-150 ms ops
    silent — the timer that calls this is cancelled if the op finishes
    first, so a fast op never flashes the indicator.
    """
    return elapsed_ms >= delay_ms


def activity_label(
    base: str | None,
    elapsed_ms: float,
    slow_ms: float = ACTIVITY_SLOW_MS,
    very_slow_ms: float = ACTIVITY_VERY_SLOW_MS,
) -> str:
    """Escalate the in-flight label as an op drags on.

    Same operation, three messages: the plain label early, a "still
    working" nudge past ``slow_ms``, and a pointer at the Command Monitor
    once it's clearly a slow-link stall past ``very_slow_ms``.
    """
    base = (base or "Working").strip() or "Working"
    if elapsed_ms >= very_slow_ms:
        return f"{base} — slow link (F2 for details)"
    if elapsed_ms >= slow_ms:
        return f"{base} — still working…"
    return base


def activity_frame(tick: int, frames: tuple[str, ...] = ACTIVITY_FRAMES) -> str:
    """The spinner glyph for animation step ``tick`` (wraps)."""
    if not frames:
        return ""
    return frames[tick % len(frames)]


def render_activity(
    base: str | None,
    elapsed_ms: float,
    tick: int,
    frames: tuple[str, ...] = ACTIVITY_FRAMES,
    delay_ms: float = ACTIVITY_DELAY_MS,
) -> str:
    """The full ``"⠹ Loading… — still working…"`` strip, or ``""``.

    Returns the empty string when the op hasn't crossed ``delay_ms`` yet
    (caller hides the widget), so this single call answers both "show?"
    and "show what?".
    """
    if not should_show_activity(elapsed_ms, delay_ms):
        return ""
    glyph = activity_frame(tick, frames)
    label = activity_label(base, elapsed_ms)
    return f"{glyph} {label}" if glyph else label


# --- adaptive auto-refresh cadence (P2.1) ---------------------------------

def next_refresh_interval(
    latencies_ms,
    base_sec: float,
    min_sec: float = 5.0,
    max_sec: float = 600.0,
) -> float:
    """Pick an auto-refresh interval that backs off on a slow link.

    On a fast link we want the configured ``base_sec`` cadence; on a slow
    one a fixed cadence makes the background refresh contend with
    interactive calls and *that* is what feels slow. So we stretch the
    interval in proportion to recent observed call latency — a link
    averaging ~2 s/call gets a noticeably longer gap between refreshes —
    clamped to ``[min_sec, max_sec]``.

    ``latencies_ms`` is recent per-call durations (ms). Empty / all-zero
    → the unscaled ``base_sec``. The scale is ``1 + avg_latency_sec``, so
    sub-second calls barely stretch and multi-second calls stretch hard.
    """
    base = max(0.0, base_sec)
    if base <= 0:  # 0 means "auto-refresh disabled" — never resurrect it
        return 0.0
    samples = [x for x in (latencies_ms or []) if isinstance(x, (int, float))]
    avg_ms = sum(samples) / len(samples) if samples else 0.0
    scale = 1.0 + max(0.0, avg_ms) / 1000.0
    return max(min_sec, min(max_sec, base * scale))
