"""Live CRUD tests for changelist forms across both backends.

Gated behind `PYTEST_ALLOW_WRITES=1` so a casual `pytest` run won't
litter a shared depot with probe changelists. The CRUD path always
cleans up after itself (creates a CL, modifies its description,
fetches it back, then `p4 change -d`s it) so a successful run leaves
no trace; an aborted run still leaves at most one empty pending CL
that's harmless and easy to spot.
"""
from __future__ import annotations

import os

import pytest


WRITES_OPT_IN = os.environ.get("PYTEST_ALLOW_WRITES") == "1"


@pytest.mark.skipif(
    not WRITES_OPT_IN,
    reason="set PYTEST_ALLOW_WRITES=1 to run live write tests "
           "(creates + deletes one probe changelist per backend)",
)
def test_changelist_form_round_trip(live_backend):
    """Create → fetch → update → fetch → delete a probe CL.

    Verifies the form CRUD path on whichever backend is parametrized:
    P4Python goes through `fetch_change` / `input=form` / `run -i`;
    the CLI backend goes through `p4 -G change -o` (marshalled out)
    plus `p4 change -i` (text form via stdin) — symmetry between the
    two is the actual thing under test.
    """
    desc = (
        f"pytest CRUD probe ({live_backend.backend_name}) — "
        f"safe to delete\nsecond line"
    )
    new_cl = live_backend.create_changelist(desc)
    try:
        assert new_cl.isdigit(), f"create_changelist returned {new_cl!r}"

        form = live_backend.get_changelist_form(new_cl)
        assert form.get("Change") == new_cl
        assert form.get("Status") == "pending"
        # `p4` normalises trailing newlines on Description; strip
        # before comparing so both backends look the same.
        assert (
            str(form.get("Description", "")).rstrip("\n").rstrip()
            == desc.rstrip()
        )

        updated_desc = (
            f"updated by {live_backend.backend_name} CRUD test\nthird line"
        )
        live_backend.update_changelist_description(new_cl, updated_desc)
        form2 = live_backend.get_changelist_form(new_cl)
        assert (
            str(form2.get("Description", "")).rstrip("\n").rstrip()
            == updated_desc.rstrip()
        )
    finally:
        # Always try to clean up so a half-run doesn't leave junk on
        # the server. `run("change", "-d", N)` requires the CL be
        # empty — which it is, since we never added Files. If
        # cleanup fails (server hiccup, permission glitch, anything
        # else surprising), surface it as a WARN on stderr instead
        # of silently swallowing — a stale probe CL on a shared
        # depot is exactly the kind of thing an operator wants to
        # know about so they can drop it manually.
        try:
            live_backend.run("change", "-d", new_cl)
        except Exception as cleanup_exc:  # noqa: BLE001
            import sys
            print(
                f"WARN: test_changelist_form_round_trip"
                f"[{live_backend.backend_name}] failed to clean up "
                f"probe CL {new_cl}: {cleanup_exc!r}\n"
                f"  Manual fix: `p4 change -d {new_cl}`",
                file=sys.stderr,
            )
