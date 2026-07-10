"""Live test for the shared-state reconcile round trip.

`SharedStateChangelist.track()` is the p4-side half of the cross-machine
permalink/bookmark sync: each shared-state JSON write is `p4 reconcile`-d
into a dedicated *numbered* CL so it never lands in the shared
`admin@shared` default changelist (see CLAUDE.md). The pure
description builder is covered by `test_shared_state_cl.py`; this exercises
the actual `reconcile -c <CL> <path>` against a live server on both
backends and asserts the returned row's action is captured.

Gated behind `PYTEST_ALLOW_WRITES=1` like the other live-write tests. To
avoid littering the shared depot with tombstones on every run, it
**reverts** (never submits) the probe file and deletes the probe CL, so a
successful run leaves no trace. (The submit half — `submit_if_dirty` — was
verified manually; it's plain `update_changelist_description` + `submit`,
both covered elsewhere.)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from p4v_tui.shared_state_cl import SharedStateChangelist


WRITES_OPT_IN = os.environ.get("PYTEST_ALLOW_WRITES") == "1"

# The shared-state dir is inside the client view (project root); a probe
# file there is reconcilable. Unique-ish name so a stale run is obvious.
_PROBE_REL = "shared-state/_pytest_probe_state.json"


@pytest.mark.skipif(
    not WRITES_OPT_IN,
    reason="set PYTEST_ALLOW_WRITES=1 to run live write tests "
           "(reconciles + reverts one probe file per backend)",
)
def test_shared_state_track_round_trip(live_backend):
    """`track()` should create a numbered CL and record the reconcile action.

    Verifies the non-obvious bit: `p4 reconcile -c <CL> <path>` returns a
    row whose `action` key `track()` reads — and that it works identically
    on the P4Python and CLI backends (the row shape differs between them,
    but the `.get("action")` extraction must land on both).
    """
    repo_root = Path(__file__).resolve().parent.parent
    probe = repo_root / _PROBE_REL
    probe.parent.mkdir(exist_ok=True)
    probe.write_text(
        f'{{"probe": "{live_backend.backend_name}"}}\n', encoding="utf-8",
    )

    cl = SharedStateChangelist()
    created: str | None = None
    try:
        cl.track(live_backend, str(probe))
        created = cl.cl_number
        assert created is not None and created.isdigit(), (
            f"track() did not create a numbered CL (got {created!r})"
        )
        assert cl.has_changes(), "track() recorded no reconcile action"
        entries = cl.entries()
        assert len(entries) == 1
        _path, action = entries[0]
        # A brand-new file reconciles as an add.
        assert action == "add", f"expected 'add', got {action!r}"
    finally:
        # Revert (don't submit) so the depot stays pristine across runs.
        if created is not None:
            try:
                live_backend.run("revert", "-c", created, str(probe))
            except Exception:  # noqa: BLE001
                pass
            try:
                live_backend.run("change", "-d", created)
            except Exception as cleanup_exc:  # noqa: BLE001
                print(
                    f"WARN: test_shared_state_track_round_trip"
                    f"[{live_backend.backend_name}] failed to drop probe "
                    f"CL {created}: {cleanup_exc!r}\n"
                    f"  Manual fix: `p4 revert {probe}; p4 change -d {created}`",
                    file=sys.stderr,
                )
        try:
            probe.unlink()
        except OSError:
            pass


# An existing depot-tracked shared-state file for the machine-B case.
_EDIT_TARGET_REL = "shared-state/bookmarks.json"


@pytest.mark.skipif(
    not WRITES_OPT_IN,
    reason="set PYTEST_ALLOW_WRITES=1 to run live write tests "
           "(reconciles + reverts one probe file per backend)",
)
def test_shared_state_readonly_edit_reconciles_as_edit(live_backend):
    """The machine-B leg of the cross-machine sync: a *synced, read-only*
    shared-state copy is modified via the stores' atomic temp+``os.replace``
    write (which must bypass the read-only bit — POSIX replace needs only
    directory perms), and ``track()`` must then reconcile it as an **edit**
    (the open-for-add case is covered by the round-trip test above).

    Reverts (never submits) and restores the original bytes + mode, so a
    green run leaves the workspace exactly as found.
    """
    import stat

    repo_root = Path(__file__).resolve().parent.parent
    target = repo_root / _EDIT_TARGET_REL
    if not target.exists():
        pytest.skip(f"{_EDIT_TARGET_REL} not synced in this workspace")
    # Shared client (see CLAUDE.md): bail out rather than fight another
    # session that already has the file open.
    try:
        opened = live_backend.run("opened", str(target))
    except Exception:  # noqa: BLE001 -- "file(s) not opened" raises on CLI
        opened = []
    if any(isinstance(r, dict) and r.get("depotFile") for r in opened):
        pytest.skip(f"{_EDIT_TARGET_REL} is already opened in this client")

    original = target.read_bytes()
    orig_mode = stat.S_IMODE(target.stat().st_mode)
    created: str | None = None
    try:
        os.chmod(target, 0o444)          # simulate the synced read-only copy

        # The exact write shape BookmarkStore/PermalinkRegistry use:
        # temp file + atomic os.replace (a trailing newline is a real
        # content change but keeps the JSON valid).
        tmp = target.with_name(target.name + ".pytest-tmp")
        tmp.write_bytes(original + b"\n")
        os.replace(tmp, target)
        assert target.read_bytes() != original, "write did not land"

        cl = SharedStateChangelist()
        cl.track(live_backend, str(target))
        created = cl.cl_number
        assert created is not None and created.isdigit(), (
            f"track() did not create a numbered CL (got {created!r})"
        )
        entries = cl.entries()
        assert len(entries) == 1
        _path, action = entries[0]
        assert action == "edit", f"expected 'edit', got {action!r}"
    finally:
        if created is not None:
            try:
                live_backend.run("revert", "-c", created, str(target))
            except Exception:  # noqa: BLE001
                pass
            try:
                live_backend.run("change", "-d", created)
            except Exception as cleanup_exc:  # noqa: BLE001
                print(
                    f"WARN: test_shared_state_readonly_edit"
                    f"[{live_backend.backend_name}] failed to drop probe "
                    f"CL {created}: {cleanup_exc!r}\n"
                    f"  Manual fix: `p4 revert {target}; "
                    f"p4 change -d {created}`",
                    file=sys.stderr,
                )
        # Revert restores depot content; belt-and-braces in case it didn't.
        try:
            if target.read_bytes() != original:
                os.chmod(target, orig_mode | stat.S_IWUSR)
                target.write_bytes(original)
        except OSError:
            pass
        try:
            os.chmod(target, orig_mode)
        except OSError:
            pass
