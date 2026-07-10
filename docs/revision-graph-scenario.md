# Revision Graph scenario

How p4v-tui reproduces Helix **p4v**'s *Revision Graph* — what the GUI
feature does, what a terminal can and can't show of it, and exactly how
the text-mode equivalent is built.

The view lives in `p4v_tui/widgets/revision_graph_modal.py`
(`RevisionGraphModal`). It is opened from the tree context menu
(`Revision Graph…`) via `P4VApp._open_rev_graph` in `app.py`. The data
comes from a single `p4 filelog -i -l` through the pluggable backend
(`P4Service.run`); there is no separate index or daemon.

---

## What p4v's Revision Graph is

In the p4v GUI, Revision Graph is a **node-and-edge canvas**: every
revision of a file is a node, laid out in columns by depot path
(branch) and rows by time, with arrows connecting a revision to the
revisions it was **branched / merged / copied** from or into. You can
zoom, pan, hover a node for its changelist/description, and trace a
line of development across renames and branches. It answers two
questions a flat history can't:

- **Where did this file come from?** — the branch/copy edge that
  created it, pointing back at the source file + revision range.
- **What landed in revision N?** — the merge edges feeding into a
  given node, each naming the other file and the `srev..erev` range
  that was integrated.

The underlying data is entirely `p4 filelog -i` (integrations
followed). The *picture* is a presentation of that data; the data
itself is plain text the moment you ask `filelog` to include
integration records.

---

## The problem: a canvas doesn't fit a terminal

A zoomable 2-D graph with free node placement, thumbnails, and mouse
hover is not a terminal surface. Reproducing the pixels is a non-goal
(tracked as the one remaining 🟡 in `docs/p4v-feature-gaps.md`:
"graphical/zoomable Revision Graph & Time-lapse"). But the *information*
— the integration edges and what they connect — is what makes the
feature useful, and that survives a text rendering cleanly.

So the TUI keeps the **data model** (revisions + typed integration
edges) and drops the **2-D layout**, rendering instead a top-to-bottom,
newest-first listing where each revision is a small block and each
integration is an arrowed, labelled line under it. You read the same
provenance; you scroll instead of pan.

---

## The model: an indented integration tree

One full-height modal (`95% × 90%`), a `RichLog` body, a one-line
status footer. Per revision the body writes:

```
rev #N   CL=12345   alice   2026-05-08 14:03   [integrate]
  <first line of the changelist description>
  ↙ branch from     //depot/main/foo.cpp#5
  ↙ merge  from     //depot/branch-B/foo.cpp#7..10
  ↗ copy   into     //depot/release/foo.cpp#1
```

| Element | Source field | Notes |
|---|---|---|
| `rev #N` | `rev` | The file's own revision number. |
| `CL=` | `change` | Changelist that produced the revision. |
| user / date | `user` / `time` | `time` is an epoch int → `%Y-%m-%d %H:%M`. |
| `[action]` | `action` | `add` / `edit` / `integrate` / `branch` / `delete` … |
| description | `desc` | First line only (`-l` gives the full text; we show line 1). |
| edge lines | `how` + `file`/`srev`/`erev` | One arrowed line per integration record on that revision. |

**Arrow direction** encodes flow, mirroring the GUI's edge arrowheads:

- `↙` — **incoming**: this revision was created/fed *from* another
  file (`branch from`, `merge from`, `copy from`, `moved from`), plus
  the relation-less oddballs (`ignored`, `undid`, `undone by`).
- `↗` — **outgoing**: this revision was integrated *into* another file
  (`branch into`, `copy into`, `merge into`, `moved into`).

The decision (`_edge_arrow`) keys on the **`into` token**:
`"↗" if "into" in str(how).split() else "↙"`. The real `filelog` `how`
strings are two words with **no trailing space** (`branch into`), so the
earlier space-padded test `" into " in how` never matched and every
outgoing edge silently drew the *incoming* arrow — a real rendering bug,
fixed here and pinned by `tests/test_revision_graph.py`. `how` is the
sole discriminant, so the arrow needs no other state.

**Revision span** (`_format_rev_span`) renders the `srev`/`erev` pair
on the other side of the edge:

- both empty → no span,
- equal, or only `srev` → `#N`,
- only `erev` → `#M`,
- both present and different → `#N..M` (one leading `#`, e.g.
  `#7..10`).

`#` prefixes are stripped on input and re-added once, so a server that
returns `5` and one that returns `#5` both render `#5`.

The footer summarises: `N revision(s), M integration edge(s)` — a
quick "is this file heavily integrated?" signal.

---

## Implementation walk-through

### 1. Entry point and the file-only guard

`_open_rev_graph(depot_path)` refuses a directory
(`endswith("/...")` or `"/"`): a folder graph is a wall of unrelated
trees and noise in a text rendering, so it `notify`s a warning and
returns instead of opening the modal. This matches the context-menu
item being offered on file rows only (`depot_tree.py` /
`workspace_tree.py` action `rev_graph`). Files pass through to
`push_screen(RevisionGraphModal(depot_path, self.p4))`.

### 2. The one command

```python
rows = self._p4.run("filelog", "-i", "-l", "-m", "200", self._depot_path)
```

- `-i` — **follow integrations**: this is what turns a flat history
  into a graph; without it there are no edges.
- `-l` — full changelist descriptions (we display line 1, but `-l`
  guarantees the field is present and untruncated).
- `-m 200` — cap depth. A long-lived file with thousands of revisions
  would otherwise stall both the fetch and the render; 200 is deep
  enough to span typical branch history, bounded enough to stay snappy.

It runs in a `@work(thread=True, exclusive=True)` worker so the fetch
never blocks the UI, and a second open of the same modal supersedes the
first. All server errors funnel to `_render_error` (red line + `error`
status) via `call_from_thread`; an empty/non-dict result goes to
`_render_empty`.

### 3. Parallel-array → per-revision collapse

`filelog`'s tagged output is **column-major**: one dict for the file
with parallel arrays keyed `rev`, `change`, `user`, …, and the
integration records as **nested** arrays (`how`, `file`, `srev`,
`erev` — each a list-of-lists, one inner list per revision).
`_extract_revs` transposes that into one dict per revision so rendering
is a simple per-row loop. Two tiny helpers absorb the ragged-array
reality:

- `_idx(d, key, i)` — scalar field at index `i`, `""` past the end.
- `_idx_list(d, key, i)` — the nested integration field at index `i`,
  always normalised to a `list[str]` (wraps a lone scalar, maps `None`
  → `[]`), so the edge loop never has to special-case shapes.

This is the load-bearing bit: backends and server versions vary in
whether a single-element field comes back as a scalar or a 1-list, and
`_idx_list` makes that irrelevant.

### 4. Render

`_render_revs` clears the log, writes each revision via `_write_rev`,
tallies edges, updates the footer. `_write_rev` writes the header line
(cyan bold), the description (if any), then one yellow edge line per
`how[k]`, pairing it with `file[k]/srev[k]/erev[k]` defensively
(`k < len(...)` guards, because the four arrays are not guaranteed
equal length). A blank line separates revisions.

### 5. Dismissal — and the Textual trap it sidesteps

`Esc` / `Backspace` / `q` (and `ㅂ`, the Hangul key on the same
physical key as `q`) close the modal, wired **both** as `Binding`s and
in `on_key` with `event.stop()`. The Hangul alias matters because a
Korean-IME terminal delivers `ㅂ` rather than `q`; without it a common
real-world keyboard can't close the modal with the advertised key.

`RevisionGraphModal` composes its own `RichLog` directly — note this is
*not* a fresh `ModalScreen`-wrapping-`RichLog` written from scratch,
which would hit the Textual 8.x `render_strips`-returns-`None` hang
documented in `docs/MEMORY.md`. It works here because the `RichLog` is
the body of a normal modal with a title/status around it and is
populated only via `call_from_thread` after mount, not the bare
pattern the gotcha warns about. If you refactor this modal, re-read
that gotcha before changing the body widget.

---

## Worked example

`//depot/release/foo.cpp` that was branched from `main`, took a couple
of merges back from a feature branch, then was edited:

```
Revision Graph · //depot/release/foo.cpp
─────────────────────────────────────────────────────────────
rev #3   CL=4821   bob     2026-05-20 09:12   [edit]
  Fix null deref in foo()

rev #2   CL=4790   alice   2026-05-18 16:40   [integrate]
  Merge bugfixes from feature/login
  ↙ merge  from     //depot/dev/feature-login/foo.cpp#8..11

rev #1   CL=4602   alice   2026-05-02 11:05   [branch]
  Branch release/2026.05 from main
  ↙ branch from     //depot/main/foo.cpp#42

  3 revision(s), 2 integration edge(s)
```

Reading top-down: the file exists because rev #1 branched it from
`main#42` (`↙ branch from`); rev #2 pulled `feature-login#8..11` in
(`↙ merge from`); rev #3 was a local edit with no integration. That is
exactly the provenance the GUI's arrows would show, linearised.

---

## Where it stops (and why that's fine)

- **No 2-D layout / zoom / pan / thumbnails.** Intentional — the
  remaining 🟡 in the feature-gaps doc. The terminal shows the edges,
  not the canvas.
- **File-only.** Directory revision graphs are a non-goal (too noisy in
  text); guarded at the entry point.
- **Depth-capped at 200 revisions.** A deliberate latency bound. If a
  file's full deep history is ever needed, raise the `-m` cap — but the
  render is linear in revisions × edges, so very deep files will scroll
  a long way.
- **First description line only.** The block stays one-revision-tall;
  the full description is one keystroke away in History / annotate.

---

## Tests

The pure helpers here — `_extract_revs` (parallel-array transpose),
`_format_rev_span` (the `#N..M` cases), the `↙`/`↗` arrow rule
(`_edge_arrow`), and the ragged-array normalisers `_idx`/`_idx_list` —
are deterministic and backend-shaped exactly the kind of logic the
project keeps "pure + unit-tested" (see CLAUDE.md), and are covered by
**`tests/test_revision_graph.py`** (28 cases). The `_idx`/`_idx_list`
normalisation is the most fragile surface — it exists precisely because
backends disagree on scalar-vs-1-list — so the table drives synthetic
`filelog` dicts (both nested-list and bare-scalar edge shapes, ragged
trailing columns) through the collapse and asserts the per-rev output.

Writing those tests is also what surfaced the arrow bug above: the
arrow assertions, seeded with the **real** `how` strings observed via
`p4 -ztag filelog -i` on the live server (`branch into`, `moved into`,
…), failed against the shipped `" into "` rule — the space-padded match
never fired, so every outgoing edge drew `↙`. The fix
(`"into" in how.split()`) and its regression cases landed together. The
rendering itself (RichLog writes, colours, layout) still rests on manual
checks against real branched/merged files
(`docs/handoff-manual-tests.md`).
