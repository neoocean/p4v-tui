# Perceived-performance (체감 성능) scenario

How p4v-tui can *feel* faster on a slow / high-latency / flaky link —
without necessarily being faster in wall-clock terms. The project's
headline value is **resilience** (it doesn't break on a bad network);
this doc is the sibling concern: on that same bad network, does it feel
**responsive**, or does it feel **stuck**?

Perceived performance is a separate axis from throughput. A `p4 changes`
that takes 1.2 s over a phone tether is *correct* and *resilient*
already — the question this doc asks is what the user sees during those
1.2 s, and whether the next thing they do feels acknowledged. On a
desktop LAN none of this matters (everything is sub-50 ms); on iPhone
Blink over cellular it's the whole experience.

Companion docs: [`narrow-terminal-scenario.md`](narrow-terminal-scenario.md)
(the remote/small-screen UX this most affects),
[`p4-cli-fallback-scenario.md`](p4-cli-fallback-scenario.md) (the backend
contract). Architecture: `DESIGN.md`.

> **Implementation status.** Being built out incrementally; see the
> ✅/⏳ markers per scenario and the status column in the priority table.
> The pure feel-policy lives in `p4v_tui/perf_feel.py`
> (unit-tested in `tests/test_perf_feel.py`), the app wiring + e2e in
> `tests/test_e2e_perf.py`.

---

## The latency-feel budget

Rough human-perception thresholds this doc designs against:

| Budget | Feels like | Design rule |
|---|---|---|
| **< 100 ms** | instant | a keystroke/action must produce *some* visible change within this window, even if the real work is still in flight |
| **0.1 – 1 s** | a small wait, attention kept | show that work started; don't blank the screen |
| **1 – 10 s** | a real wait, attention drifts | show *what* is happening + that it's progressing (not hung); offer escape |
| **> 10 s** | abandoned unless told otherwise | the resilience machinery (chunking/resume/reconnect) already owns this — surface it |

The resilience layer is strong at the bottom of that table (long ops are
chunked, resumable, non-blocking — `jobs.py` priority queue, `@work`
threads). **The gap is the top two rows**: the sub-second feedback that
makes a laggy app feel alive rather than frozen.

---

## What we already have (and where it stops)

Grounding the gaps in the current machinery:

- **Off-thread everything.** Every p4 call runs under `@work(thread=True)`
  and marshals back via `call_from_thread` (`app.py` `_connect_and_load`,
  `_load_pending`, `_load_submitted`, history loads). The UI event loop
  never blocks on the network — good. *But "not blocked" ≠ "looks busy":*
  a thread is fetching and the screen just sits there.
- **Job priority split.** `jobs.py` serves `PRIORITY_INTERACTIVE = 0`
  before `PRIORITY_CHUNKED = 10`, so a keypress jumps the queue during a
  bulk sync. Great for *actual* responsiveness; invisible to the user.
- **Feedback that exists** — but is **partial / hidden**:
  - Tree-expansion spinner (`widgets/p4_tree.py`, `_LOADING_FRAMES`,
    0.12 s) — the *only* inline "working" animation, and only for tree
    nodes.
  - `ConnectionBar` (`app_shared.py`) — static `Server/User/Workspace/Root`;
    placeholder `" Connecting…"` at startup, then frozen.
  - Command Monitor (F2) + Log panel — full progress/ETA, but **behind a
    keypress** and, in narrow mode, on a page you have to navigate to.
  - Error toasts via `notify` — only on failure.
  - **Orphaned `#job_status` CSS** in `styles.tcss` with *no widget* —
    a status-line slot was scaffolded and never wired. This doc adopts it.
- **Caches that already exist** — `_last_pending_rows`,
  `_last_submitted_rows`, `_last_history_rows`, the Fast Search SQLite
  index (WAL), the p4client read-cache (2-min TTL), the search preview
  LRU. **Refresh is already non-destructive:** the worker fetches *first*,
  then `call_from_thread(_render_*)` clears + repopulates the table in a
  single synchronous UI callback — so the previous rows stay on screen
  for the whole round-trip and are replaced atomically, never blanking.
  (This invalidates the original P0.2 premise — see below.)
- **Eager warm-up at connect.** `_on_connected` already fires
  `_load_pending()` **and** `_load_submitted()` (off-thread), so by the
  time the user switches to Submitted it's usually warm. **History** is
  the only lazy tab — and it has no default target to prefetch (it loads
  when the user picks a file/folder via `Ctrl+T`). The search index opens
  on first `Ctrl+F`. So the only genuinely *cold* load is the first
  History view, and that now shows the P0.1 indicator while it fetches.

So the machinery is sound; what's missing is the **perception layer**.

---

## P0 — make in-flight work visible & non-destructive

### P0.1 A global in-flight indicator (adopt `#job_status`) — ✅ CL 58776

**Symptom.** Trigger a refresh, a tab switch, a Get Revision — anything
that fires a `@work` load — and on a slow link nothing on screen changes
until the data lands. The user can't tell "working" from "hung," and
re-presses (which queues *another* load).

**Proposal.** Wire the orphaned `#job_status` line into a small global
**activity indicator**: an animated glyph + short label
(`⠋ Loading pending…`) shown whenever ≥1 interactive worker is in flight,
hidden when the count returns to zero. Drive it from a tiny counter
incremented/decremented around the `@work` loads (or a helper that wraps
them), animated by one shared `set_interval` (reuse the tree's
`_LOADING_FRAMES` cadence). In **narrow mode** it belongs next to the
breadcrumb (the Log page isn't visible there); in wide mode it can live
in the header/connection-bar row.

```
 ⠋ Loading submitted…              ← only while a worker is in flight
```

Cheap, global, and it answers the single most important question on a
laggy link: *is something happening?* This is the P2.4 "in-flight
indicator" from `narrow-terminal-improvements.md`, promoted to P0 here
because it's the foundation the rest of the feedback hangs off.

### P0.2 Stale-while-revalidate — ↩ re-scoped (already non-destructive)

**Original symptom (turned out false).** The plan assumed `_render_*`
cleared the table at the *start* of a render and so flashed empty for a
full RTT on refresh. Reading the code: the worker fetches *first*, then
`call_from_thread(_render_*)` does `clear()` + `add_row(...)` in **one
synchronous UI callback** — Textual doesn't repaint between the clear and
the repopulate, and the previous rows stay up during the (slow) fetch
because the render hasn't run yet. So **refresh already behaves like
stale-while-revalidate**; there's no blank flash to fix, and the
cursor-restore code even reads the *old* rows mid-render.

**What remains.** The only genuinely blank state is a *cold* load (no
cache yet). Pending + Submitted are warmed eagerly at connect (see
above), so the lone cold path is the first History view — and that now
shows the P0.1 activity indicator while it fetches. No separate
stale-while-revalidate machinery is warranted; building it would add a
fetch/render split for zero observable gain.

### P0.3 Latency-adaptive feedback threshold (no spinner flicker) — ✅ CL 58776

**Symptom.** If P0.1 shows the indicator the instant any worker starts,
sub-100 ms operations on a fast link flash it for one frame — visual
noise that paradoxically reads as *slower*. Conversely a 1 s+ wait needs
*more* than a glyph.

**Proposal.** Tier the feedback by elapsed time, not by start:
- **< ~150 ms** — show nothing (the op will finish before the eye
  registers a spinner; flashing it is worse than silence).
- **150 ms – 1 s** — show the P0.1 glyph + label.
- **> 1 s** — escalate the label ("still loading… server is slow") so a
  genuine stall reads as *acknowledged*, not hung.
- **> ~8 s** — point at the escape hatch (F2 monitor / cancel, see P2.2).

A single `set_timer(0.15, …)` per load that's cancelled if the load
finishes first gives the "instant ops stay silent" behaviour for free.

---

## P1 — anticipate and acknowledge

### P1.1 Background prefetch after connect — ↩ re-scoped (already warm)

**Original symptom (mostly already solved).** The plan assumed
Submitted/History load lazily on first tab switch. Reading the code:
`_on_connected` already fires `_load_pending()` **and** `_load_submitted()`
off-thread at connect, so Submitted is warm before the user ever switches
to it — the prefetch is already there.

**What remains (and why it's not worth building).** History is the only
lazy tab, but it has **no default target to prefetch** — it loads against
whichever file/folder the user selects (`Ctrl+T`), so there's nothing to
warm ahead of time. Speculatively prefetching, say, the last-viewed
target would be guesswork with a real cost on a metered link. The cold
first-History feel is instead covered by the P0.1 indicator. Net: nothing
to implement here beyond what already ships.

### P1.2 Reconnect / backoff state in the ConnectionBar — ✅ CL 58782

**Symptom.** During a reconnect with exponential backoff the bar stayed
frozen on its last good string; a multi-second stall was indistinguishable
from a hang.

**Shipped.** `P4Service` gained two optional service-level callbacks
(`_on_retry` / `_on_recover`, both default `None` — no behaviour change)
that `_run_resilient` falls back to when a call doesn't pass its own
`on_retry`. `_on_retry` fires per reconnect attempt; `_on_recover` fires
once when a call that *had* to retry finally succeeds. The app wires them
in `_on_connected` to update the bar — `⟳ Reconnecting… (attempt N/max)`
during the stall, restored to the normal `Server/User/Workspace/Root`
line on recovery (the good `P4Info` is stashed in `_p4_info`). A stall
the resilient runner is working through now *reads* as "working on it."
Callbacks run on the worker thread and marshal to the UI via
`call_from_thread`; parity tests confirm the default-`None` path is
unchanged on both backends.

### P1.3 Optimistic action acknowledgment — ✅ CL 58781 + CL 58786

**Symptom.** Open-for-edit / Get / Revert / Add fire an async p4 call;
on a 1–2 s link the user triggers it and… nothing, for a beat. (Chunked
ops already feed the JobRunner → Log/Command-Monitor; the gap was the
*inline* single-file actions in `_run_file_action`.)

**Shipped — global ack (CL 58781).** `_run_file_action` raises the global
activity indicator with a per-action verb the instant it dispatches —
"Opening for edit…", "Reverting…", "Get latest…" (`_action_label`).

**Shipped — per-row marker (CL 58786).** The affected file leaf also
gets an optimistic `⟳` prefix the moment the action is dispatched
(`P4Tree.mark_node_pending`), so the *specific row* lights up — clearer
than a global line, and the only feedback for "which file". The reconcile
is free: every action ends in `_refresh_after_action` → `reload_node`,
which rebuilds the row from fresh `fstat` and so replaces the optimistic
glyph with the real marker (success *or* failure — that's the rollback).
The one path that returns *before* the reload (a failed CL-create
pre-step) clears the glyph explicitly. Two deliberate scope limits keep
it honest: it's **files only** (folders/root reload wholesale), and it
shows a neutral "in flight" glyph — **not** a predicted end-state marker —
so it can never display a state the server didn't confirm.

---

## P2 — adaptive & escape hatches

### P2.1 Adaptive auto-refresh cadence — ✅ CL 58779

**Symptom.** The 30 s pending auto-refresh fired a fixed interval
regardless of link health; on a slow/contended link the refresh competes
with interactive calls and makes *them* feel slow.

**Shipped.** The fixed `set_interval` became a self-rescheduling
`set_timer`: each tick picks its next gap via
`perf_feel.next_refresh_interval`, which scales the configured base by
recent pending-load latency (sampled into `_recent_latencies_ms`). It
only ever **backs off** — `min_sec == base` guarantees it never refreshes
faster than configured — capped at 4× base (≤ 1 h) so a transient spike
can't park it forever. The existing `_pending_load_in_flight` coalescing
still prevents ticks from stacking; a skipped tick still reschedules.

### P2.2 Cancellable interactive loads (Esc) — ⏭ deferred (would be cosmetic)

**Symptom.** A slow load can't be abandoned; the user waits or force-quits.

**Why it's deferred, not shipped.** Two reasons, both load-bearing:

1. **It wouldn't actually cancel anything.** The loads are
   `@work(thread=True)` workers blocked in a *synchronous* p4 socket
   call. Textual can mark such a worker cancelled, but it can't interrupt
   a blocking syscall — the `p4 changes` keeps running to completion in
   the background, still holding a `_call_sem` permit. So `Esc` could
   only *discard the result and hide the spinner*, not stop the work — a
   "cancel" that doesn't cancel is worse than none.
2. **`Esc` is already spoken for.** There's no base-screen `Esc` binding
   by design — `Esc` dismisses the tree-filter overlay and closes modals.
   Stealing it for load-cancel would collide with those.

What this scenario was really chasing — "a slow op shouldn't feel
trapping" — is now covered without a fake cancel: P0.1/P0.3 make the
stall *legible* ("still working… / slow link"), P1.2 shows reconnect
progress, and the resilient runner bounds genuine hangs (`max_attempts`
+ CLI timeout). Revisit only if true cancellation becomes possible
(e.g. a backend that supports interrupting an in-flight RPC).

### P2.3 Large-list render cost

**Symptom.** Sort/filter runs on the UI thread in `_render_*`. Fine for a
typical < 50-row pending list; a big folder-history or a 100-row
submitted set could cost a visible beat on a slow device (Blink on an
older phone).

**Proposal.** Measure first; if it bites, move the sort off-thread (into
the worker, before `call_from_thread`) and/or cap+paginate the rendered
set. Low priority — listed for completeness, gated on a real measurement.

---

## Cross-cutting

**Measure, don't guess.** Perceived wins are easy to imagine and hard to
confirm. The Command Monitor / `CmdLog` already timestamps commands; add
a lightweight per-load duration capture so "feels slow" claims can be
checked against real RTTs, and so P0.3's thresholds and P2.1's adaptation
have data to key off. A debug overlay (env-gated) showing last-N load
durations would make the whole axis observable.

**Keep decisions pure + testable.** Mirror the navigator pattern: the
*policy* (when to show the indicator, threshold tiers, adaptive interval
math, stale-vs-fresh choice) lives in small pure helpers with unit tests;
the app does the Textual/p4 wiring. A pure `should_show_activity(elapsed)`
/ `next_refresh_interval(latencies)` is trivial to test and keeps the
feel-logic out of the worker callbacks.

**Don't regress resilience or correctness.** The one path that shows
*not-yet-confirmed* state is P1.3's optimistic row marker — and it's
deliberately a **neutral "in flight" glyph, not a predicted end-state**,
reconciled against the authoritative server result by the post-action
`reload_node` (which also serves as the rollback on failure). So a
dropped/failed call can never leave a fictional status on screen. The
feel layer sits *on top of* the resilience layer and must never weaken
it — which is also why P2.2 (a "cancel" that couldn't really cancel) was
declined rather than faked.

---

## Priority summary

| # | Improvement | Status | Symptom it kills | Touches |
|---|---|---|---|---|
| P0.1 | Global in-flight indicator | ✅ CL 58776 | "is it working or hung?" | `#job_status`, `_begin/_end_activity`, `perf_feel.render_activity` |
| P0.2 | Stale-while-revalidate | ↩ not needed | table flashes blank on refresh | *(false premise: refresh render is atomic — old rows stay until replaced)* |
| P0.3 | Adaptive feedback threshold | ✅ CL 58776 | spinner flicker / silent stalls | `perf_feel.should_show_activity` / `activity_label` |
| P1.1 | Prefetch after connect | ↩ already warm | cold first tab switch | *(Submitted already eager at connect; History has no prefetch target)* |
| P1.2 | Reconnect state in bar | ✅ CL 58782 | stall reads as hang | `P4Service._on_retry/_on_recover` → `ConnectionBar` |
| P1.3 | Optimistic action ack | ✅ CL 58781 + CL 58786 | click → dead beat | indicator + `⟳` row marker on `_run_file_action`; reconcile via reload |
| P2.1 | Adaptive refresh cadence | ✅ CL 58779 | background steals foreground | `perf_feel.next_refresh_interval` + `_recent_latencies_ms` |
| P2.2 | Cancellable loads (Esc) | ⏭ deferred | can't bail a slow fetch | *(thread workers can't interrupt a blocking p4 call — would be cosmetic)* |
| P2.3 | Large-list render cost | ⏭ measure first | beat on big lists / old phones | *(no measured problem yet; sort is < 50 rows)* |

**P0 is the core thesis:** the app is already responsive *underneath*
(off-thread, prioritized, resilient) — P0 just makes that responsiveness
**visible** (indicator) and **flicker-free** (threshold). That, plus the
adaptive refresh (P2.1), the reconnect-state bar (P1.2), and the inline
action ack (P1.3 partial), converts "feels frozen on cellular" into
"feels alive" at low risk and without touching the data path.

**Outcome of building it out.** Reading the code while implementing
collapsed several scenarios into "already handled" rather than new work —
which is the honest result, not a shortfall:

- **P0.2 / P1.1 — already satisfied.** Refresh render is atomic (no blank
  flash) and Pending/Submitted are warmed at connect; the only cold path
  (first History) is covered by the P0.1 indicator.
- **P1.3 — fully shipped.** Inline-action *acknowledgment* (global
  indicator) and the per-row optimistic `⟳` *decoration* both landed; the
  reconcile/rollback fell out for free because every action already ends
  in a `reload_node` that rebuilds the row from fresh `fstat`.
- **P2.2 — deferred as cosmetic.** Thread workers can't interrupt a
  blocking p4 call, so a "cancel" couldn't really cancel.
- **P2.3 — gated on a measurement** that hasn't shown a problem.

The net shipped surface is small and high-leverage: one pure module
(`perf_feel`), one indicator widget, one adaptive timer, one reconnect
hook — exactly the "perception layer" the rest of the doc argued was the
real gap.
