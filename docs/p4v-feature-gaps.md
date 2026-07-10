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

*(none as of 2026-07 — the last two ❌ rows, Job association and Job
search, were re-classified ⏭ after a live-server survey: 7 jobs total,
all closed, none touched since 2025-02. Jobs on this server were a
brief task-integration experiment, since abandoned, so there is no
live demand for an in-TUI Jobs surface. If a jobs-using server ever
becomes a real target, the minimal shape would be a read-only job
picker + `p4 fix -c` from the Pending CL menu.)*

## 🟡 Partial — present but narrower than p4v

| p4v feature | What the TUI does | What's missing vs p4v |
|---|---|---|
| **Recent connections** | The `[[profile]]` list in TOML serves the same purpose, now editable in-app (Preferences → Profiles). | No automatically-maintained MRU list; the user curates the profiles by hand. |
| **SSO / Helix Authentication Service** | Inherited from the `p4` environment — if your shell session is authenticated, the TUI rides on it. | No in-app SSO prompt / browser hand-off; the user authenticates outside the TUI. |
| **Labels** | `LabelPickerModal` lists labels and tags files ("Tag with Label"); Get Revision can sync by label. | No label *editor* (create/modify the label spec, its View, options) — that's an admin/spec surface left to `p4 label`. |
| **Custom Tools menu** | `[[external_editor]]` covers "Open With…" on a file; `[merge_tool]` covers an external 3-way merge tool. | No general "run command X against the current selection / $P4PORT" tool definitions with argument substitution. |
| **Branch Files** | Branch-mapping picker (`p4 branches`) *or* manual src/tgt, a `populate -n` dry-run preview of the files to be created, then submit. | No full branch-*spec editor* (create/modify a branch mapping's view) — that's an admin surface left to `p4 branch`. |
| **Filter / sort controls on the CL lists** | `Shift+M` → Filter/Sort on Pending & Submitted: sort by change/user/date/desc/workspace + filter by user/workspace/desc-substring/regex/date-range; persisted. Plus Fast Search `cl:` / `@user:` / `type:` / `/regex/`. | No **path** filter on the CL tables (would need a `describe` per CL — the table only holds the description). |
| **Merge tool integration (P4Merge / external 3-way)** | In-app 3-way editor (`Ctrl+E`) **plus** an external 3-way tool: `[merge_tool]` config launched from the Resolve modal with `Ctrl+T`, handed base/theirs/yours/merge temp files (blocks, reads the result back). | No *pixel-diff / image* merge. Text 3-way is fully covered by both the in-app editor and the external tool. |

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
| **Jobs** (list / jobspec / link to CL / job search / `p4 fix` association) | `p4 job`, `p4 jobs -e <expr>`, `p4 fix -c <CL> <job>` — declined 2026-07 after a server survey found no live jobs usage (7 jobs, all closed, dormant since 2025-02) |
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
| **Interactive Reconcile / Clean preview** — p4v opens a dialog that previews the add/edit/delete set and lets you check/uncheck individual files before reconciling | ✅ | `reconcile -n` / `clean -n` dry-run → a per-file picker (all checked, with action labels) → only the picked files run; all-checked == the old chunked all-or-nothing. |
| **Image / binary file preview** — p4v renders images and uses content-type-aware viewers | ✅ text-terminal approximation | Image leaves render half-block ANSI art (`image_preview.py`, Pillow); non-image binary shows a bounded hex window. No true pixel rendering — it's a terminal. |
| **Filter / sort controls on the Pending & Submitted lists** | ✅ (path excepted) | `Shift+M` → Filter/Sort: sort by change/user/date/desc/workspace + filter by user/workspace/desc-substring/regex/date-range, persisted. Path filtering is the one omission (needs a per-CL `describe`). |
| **Distributed versioning (DVCS): `p4 clone` / `fetch` / `push` / `unsubmit` / `resubmit`** | ⏭ | Not addressed; this build targets a classic centralized server workflow. Use the `p4` CLI for DVCS personal-server operations. |
| **Graphical Revision Graph / Time-lapse** (zoomable canvas, thumbnails) | 🟡 | Both exist in **text mode** (Revision Graph = text integration tree; Time-lapse = keyboard revision walker). The graphical/scrubber affordances of the GUI versions are not reproduced. |

---

## Summary

The daily-developer core — get / edit / add / delete / revert /
reconcile / clean / move / diff / branch-copy-integrate / resolve /
shelve / annotate / time-lapse / revision-graph / undo / find / filter,
plus pending & submitted CL workflow and a resilience layer p4v lacks —
is fully covered. The 2026-06 batch closed most of the rich-GUI gaps too:
interactive Reconcile/Clean per-file selection, Branch Files preview +
mapping picker, image/binary preview (ANSI art / hex), CL-table
filter/sort, external P4Merge launch (`Ctrl+T`), and a GUI connection
profile editor (Preferences → Profiles). As of 2026-07 there are **no ❌
rows left**: Jobs (the last one) was re-classified ⏭ after a live-server
survey found no jobs usage. Every remaining gap is a deliberate
exclusion — **admin / spec editing** (workspaces, branch mappings,
streams, labels editor, users/groups, triggers, jobs) stays with the
canonical `p4` CLI.

What's left of the "rich GUI affordances" bucket is now narrow and
mostly intrinsic to a terminal: no true pixel image rendering (ANSI art
instead), no graphical/zoomable Revision Graph & Time-lapse (text-mode
equivalents exist), no path filter on the CL tables, and no
pixel/image merge tool.
