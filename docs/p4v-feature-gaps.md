# p4v vs p4v-tui — feature gaps

What Helix **p4v** (the GUI) offers that **p4v-tui** does **not** — the
inverse view of `DESIGN.md`'s full coverage matrix. DESIGN.md scores
every p4v surface ✅ / 🟡 / ❌ / ⏭; this file pulls out only the
non-✅ rows so the gaps are readable in one place, and adds a few p4v
capabilities the matrix doesn't surface as its own rows.

> Source of truth is still `DESIGN.md` § "p4v Feature Coverage". When a
> gap below closes (or a new one opens), update the matrix there first,
> then mirror the one-liner here. Don't let the two drift.

Legend (same as DESIGN.md):

- ❌ **Not implemented** — no equivalent in the TUI today, not a
  deliberate exclusion.
- 🟡 **Partial** — present but reduced scope vs p4v.
- ⏭ **Out of scope** — intentionally not shipping in TUI form;
  delegated to the canonical `p4` CLI or simply not a daily-developer
  surface.

---

## ❌ Not implemented

These are genuine functional gaps, not deliberate exclusions — a p4v
user would notice their absence.

| p4v feature | Why it's missing / nearest workaround |
|---|---|
| **Job association on a changelist** (`p4 fix`, the "Jobs" tab of a CL) | There is no Jobs view in this build, so there's no picker to attach a job to a pending/submitted CL. Use `p4 fix -c <CL> <job>` on the CLI. |
| **Job search** (find/filter jobs by jobspec fields) | Same root cause — no Jobs view. `p4 jobs -e <expr>` on the CLI. |
| **Edit / Add / Remove a connection from a GUI** | Connections are defined by hand-editing `[[profile]]` blocks in `p4v-tui.toml` (the in-app Preferences editor, `Ctrl+,`, edits the TOML but does not add/remove profiles through a dialog). p4v has a full connection manager. |

## 🟡 Partial — present but narrower than p4v

| p4v feature | What the TUI does | What's missing vs p4v |
|---|---|---|
| **Branch Files** | `p4 populate` into a fresh CL, auto-submitted (context menu). | No branch-spec editor, no preview/diff of what will be branched, no "branch via branch mapping" selection. |
| **Recent connections** | The `[[profile]]` list in TOML serves the same purpose. | No automatically-maintained MRU list; the user curates the profiles by hand. |
| **SSO / Helix Authentication Service** | Inherited from the `p4` environment — if your shell session is authenticated, the TUI rides on it. | No in-app SSO prompt / browser hand-off; the user authenticates outside the TUI. |
| **Labels** | `LabelPickerModal` lists labels and tags files ("Tag with Label"); Get Revision can sync by label. | No label *editor* (create/modify the label spec, its View, options) — that's an admin/spec surface left to `p4 label`. |
| **Custom Tools menu** | `[[external_editor]]` covers "Open With…" on a file. | No general "run command X against the current selection / $P4PORT" tool definitions with argument substitution. |
| **Merge tool integration (P4Merge / external 3-way)** | An in-app 3-way merge editor (`Ctrl+E`, `merge3` + `MergeEditorModal`) plus a configurable `[[external_editor]]` that opens the local copy. | No launch of P4Merge (or another GUI merge tool) wired as *the* resolve tool with all four file inputs handed to it. The in-app editor covers the common case; a pixel-diff/image merge does not exist. |

## ⏭ Out of scope — deliberately delegated

These are intentional exclusions. The TUI stays focused on the working
developer's daily loop; admin / spec-editing and the auth/security
boundary stay with the canonical `p4` CLI, where the spec format is
already authoritative.

| p4v surface | Delegated to |
|---|---|
| **Workspaces** (create/manage/switch client specs) | `p4 client` |
| **Branch Mappings** (manage / spec editor) | `p4 branch` |
| **Streams & Stream Graph** (create, switch, stream-to-stream merge/copy, graphical stream view) | `p4 stream` / CLI integrate |
| **Jobs** (list / jobspec / link to CL) | `p4 job`, `p4 jobs`, `p4 fix` |
| **Users / Groups / Permissions / Protections** | `p4 user`, `p4 group`, `p4 protect` |
| **Triggers / server admin** | `p4 triggers`, `p4 configure`, etc. |
| **Login / Logout / Set Password** | `p4 login` / `p4 logout` / `p4 passwd` — keeps the security boundary in one well-understood place |
| **Tickets management** | `p4` CLI |
| **Print / Print Preview** of a file or diff | not a terminal-appropriate surface |

## Additional p4v capabilities not broken out in the matrix

Real p4v features that DESIGN.md's matrix doesn't list as their own
rows. Called out here so the comparison is honest; treated as
**candidate gaps** (assessment, not yet promoted to matrix rows).

| p4v feature | Status in TUI | Note |
|---|---|---|
| **Interactive Reconcile / Clean preview** — p4v opens a dialog that previews the add/edit/delete set and lets you check/uncheck individual files before reconciling | 🟡 | The TUI runs Reconcile / Clean **chunked, all-or-nothing** (with a confirm for Clean). There is no per-file preview-and-select dialog; you reconcile everything the scan finds. |
| **Image / binary file preview** — p4v renders images and uses content-type-aware viewers | ❌ | The file viewer is text-only (5 MB cap, pygments highlight for known extensions, plain text otherwise). Binary/image leaves open as plain text or not at all. |
| **Filter / sort controls on the Pending & Submitted lists** (by user, workspace, date range, path) | 🟡 | Fast Search has `cl:` description search and `@user:` / `type:` / `/regex/` filters, and the Pending table groups local-then-remote workspaces — but the CL tables themselves have no p4v-style column filter/sort dropdowns. |
| **Distributed versioning (DVCS): `p4 clone` / `fetch` / `push` / `unsubmit` / `resubmit`** | ⏭ | Not addressed; this build targets a classic centralized server workflow. Use the `p4` CLI for DVCS personal-server operations. |
| **Graphical Revision Graph / Time-lapse** (zoomable canvas, thumbnails) | 🟡 | Both exist in **text mode** (Revision Graph = text integration tree; Time-lapse = keyboard revision walker). The graphical/scrubber affordances of the GUI versions are not reproduced. |

---

## Summary

The daily-developer core — get / edit / add / delete / revert /
reconcile / clean / move / diff / branch-copy-integrate / resolve /
shelve / annotate / time-lapse / revision-graph / undo / find / filter,
plus pending & submitted CL workflow and a resilience layer p4v lacks —
is fully covered. The remaining gaps cluster in three places:

1. **Jobs** — the single biggest ❌ (no Jobs view ⇒ no job search and no
   `p4 fix` association from the UI).
2. **Admin / spec editing** — workspaces, branch mappings, streams,
   labels (editor), users/groups, triggers — deliberately ⏭, left to
   `p4`.
3. **Rich GUI affordances** — image/binary preview, interactive
   reconcile selection, graphical revision graph / time-lapse,
   GUI connection & merge-tool management — partial or absent by the
   nature of a terminal UI.
