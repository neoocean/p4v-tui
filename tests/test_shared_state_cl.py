"""Unit tests for p4v_tui.shared_state_cl."""
from __future__ import annotations

import threading
import time

import pytest

from p4v_tui.shared_state_cl import (
    SharedStateChangelist,
    build_description,
)


class _StubP4:
    """In-memory stand-in for P4Service.

    Records every call so tests can assert on the routing decisions
    without standing up a Perforce server.
    """

    def __init__(
        self,
        *,
        cl: str = "424242",
        reconcile_rows: list[list] | None = None,
        create_raises: bool = False,
        update_raises: bool = False,
        submit_raises: bool = False,
    ) -> None:
        self._cl = cl
        self._reconcile_rows = list(reconcile_rows or [])
        self._create_raises = create_raises
        self._update_raises = update_raises
        self._submit_raises = submit_raises
        self.created_with: list[str] = []
        self.descriptions: list[tuple[str, str]] = []
        self.runs: list[tuple] = []

    def create_changelist(self, description: str) -> str:
        if self._create_raises:
            raise RuntimeError("create_changelist boom")
        self.created_with.append(description)
        return self._cl

    def update_changelist_description(
        self, change: str, new_description: str,
    ) -> None:
        if self._update_raises:
            raise RuntimeError("update_description boom")
        self.descriptions.append((str(change), new_description))

    def run(self, *args):
        self.runs.append(tuple(args))
        if args and args[0] == "reconcile":
            return self._reconcile_rows.pop(0) if self._reconcile_rows else []
        if args and args[0] == "submit":
            if self._submit_raises:
                raise RuntimeError("submit boom")
            return [{"submittedChange": self._cl}]
        return []


class TestBuildDescription:
    def test_empty_returns_placeholder(self):
        desc = build_description([])
        assert "변경 없음" in desc

    def test_single_entry_lists_file(self):
        desc = build_description([("shared-state/permalinks.json", "edit")])
        assert "수정" in desc
        assert "permalinks.json" in desc
        assert "shared-state/permalinks.json" in desc

    def test_multiple_entries_preserve_order(self):
        desc = build_description([
            ("a/permalinks.json", "add"),
            ("a/bookmarks.json", "edit"),
        ])
        lines = desc.splitlines()
        idx_permalinks = next(
            i for i, l in enumerate(lines) if "permalinks.json" in l
        )
        idx_bookmarks = next(
            i for i, l in enumerate(lines) if "bookmarks.json" in l
        )
        assert idx_permalinks < idx_bookmarks
        assert "추가" in desc
        assert "수정" in desc

    def test_duplicate_path_latest_action_wins(self):
        desc = build_description([
            ("p.json", "add"),
            ("p.json", "edit"),
        ])
        # The earlier "add" line should be gone; only the "edit" remains.
        action_lines = [
            l for l in desc.splitlines() if "p.json" in l and l.lstrip().startswith("-")
        ]
        assert len(action_lines) == 1
        assert "수정" in action_lines[0]
        assert "추가" not in action_lines[0]

    def test_unknown_action_passes_through(self):
        desc = build_description([("p.json", "weird/verb")])
        assert "weird/verb" in desc

    def test_korean_labels_cover_common_actions(self):
        for action, label in [
            ("add", "추가"),
            ("edit", "수정"),
            ("delete", "삭제"),
            ("move/add", "이동(추가)"),
            ("move/delete", "이동(삭제)"),
        ]:
            desc = build_description([(f"x-{action}.json", action)])
            assert label in desc, f"missing label for {action!r}"


class TestSharedStateChangelistTrack:
    def test_lazy_cl_creation(self, tmp_path):
        """No file ever reconciled → no CL is created (don't litter pendings)."""
        cl = SharedStateChangelist()
        assert cl.cl_number is None
        assert cl.has_changes() is False
        assert cl.entries() == []

    def test_first_track_creates_cl_and_records_action(self):
        p4 = _StubP4(
            cl="900001",
            reconcile_rows=[[{"action": "edit", "depotFile": "//x"}]],
        )
        cl = SharedStateChangelist()
        cl.track(p4, "/path/permalinks.json")
        assert cl.cl_number == "900001"
        assert p4.runs == [("reconcile", "-c", "900001", "/path/permalinks.json")]
        assert cl.has_changes() is True
        assert cl.entries() == [("/path/permalinks.json", "edit")]

    def test_subsequent_tracks_reuse_same_cl(self):
        p4 = _StubP4(
            reconcile_rows=[
                [{"action": "edit"}],
                [{"action": "edit"}],
            ],
        )
        cl = SharedStateChangelist()
        cl.track(p4, "/x/a.json")
        cl.track(p4, "/x/b.json")
        # create_changelist was called exactly once.
        assert len(p4.created_with) == 1

    def test_reconcile_with_no_action_is_not_recorded(self):
        """A pristine file (reconcile returns empty) leaves entries empty
        — and we still don't want a phantom CL when nothing changed."""
        p4 = _StubP4(reconcile_rows=[[]])
        cl = SharedStateChangelist()
        cl.track(p4, "/x/clean.json")
        # NB: ensure_cl ran (we need a CL to reconcile *into*), but
        # because no action was recorded, has_changes() stays False —
        # submit_if_dirty will leave the CL empty rather than push it.
        assert cl.has_changes() is False

    def test_reconcile_failure_is_swallowed(self):
        class _Boom(_StubP4):
            def run(self, *args):
                if args and args[0] == "reconcile":
                    raise RuntimeError("reconcile boom")
                return super().run(*args)

        p4 = _Boom()
        cl = SharedStateChangelist()
        cl.track(p4, "/x/a.json")  # must not raise
        assert cl.has_changes() is False

    def test_create_changelist_failure_is_swallowed(self):
        p4 = _StubP4(create_raises=True)
        cl = SharedStateChangelist()
        cl.track(p4, "/x/a.json")  # must not raise
        assert cl.cl_number is None
        assert cl.has_changes() is False
        assert p4.runs == []  # never even attempted reconcile

    def test_headAction_falls_back_when_action_missing(self):
        """Some reconcile rows surface ``headAction`` instead of
        ``action`` (server quirk). The tracker should accept either."""
        p4 = _StubP4(reconcile_rows=[[{"headAction": "edit"}]])
        cl = SharedStateChangelist()
        cl.track(p4, "/x/a.json")
        assert cl.entries() == [("/x/a.json", "edit")]


class TestSharedStateChangelistSubmit:
    def test_submit_no_changes_returns_none(self):
        p4 = _StubP4()
        cl = SharedStateChangelist()
        assert cl.submit_if_dirty(p4) is None
        assert p4.runs == []
        assert p4.descriptions == []

    def test_submit_writes_detailed_description_then_submits(self):
        p4 = _StubP4(
            cl="555",
            reconcile_rows=[[{"action": "edit"}]],
        )
        cl = SharedStateChangelist()
        cl.track(p4, "/repo/shared-state/permalinks.json")
        result = cl.submit_if_dirty(p4)
        assert result == "555"
        # The description got rewritten before submit.
        assert len(p4.descriptions) == 1
        change, desc = p4.descriptions[0]
        assert change == "555"
        assert "permalinks.json" in desc
        assert "수정" in desc
        # Submit was called with the same CL number.
        assert ("submit", "-c", "555") in p4.runs
        # And the tracker is reset so a follow-up call is a no-op.
        assert cl.cl_number is None
        assert cl.has_changes() is False
        assert cl.submit_if_dirty(p4) is None

    def test_submit_still_runs_when_description_update_fails(self):
        """The user explicitly wants the CL submitted on exit; losing the
        nice description is acceptable, orphaning the CL is not."""
        p4 = _StubP4(
            cl="777",
            reconcile_rows=[[{"action": "add"}]],
            update_raises=True,
        )
        cl = SharedStateChangelist()
        cl.track(p4, "/x/a.json")
        assert cl.submit_if_dirty(p4) == "777"
        assert ("submit", "-c", "777") in p4.runs

    def test_submit_propagates_real_submit_failures(self):
        """If ``p4 submit`` itself fails we need the caller to know so
        the CL stays pending — silently swallowing would leave the user
        wondering what happened to their changes."""
        p4 = _StubP4(
            cl="999",
            reconcile_rows=[[{"action": "edit"}]],
            submit_raises=True,
        )
        cl = SharedStateChangelist()
        cl.track(p4, "/x/a.json")
        with pytest.raises(RuntimeError, match="submit boom"):
            cl.submit_if_dirty(p4)
        # State isn't cleared on failure → a retry can submit the same CL.
        assert cl.cl_number == "999"
        assert cl.has_changes() is True


class TestSharedStateChangelistConcurrency:
    def test_concurrent_tracks_create_single_cl(self):
        """Burst of parallel writes must not race on CL creation —
        otherwise we'd leak empty pending CLs into the depot."""
        p4 = _StubP4(
            cl="123",
            reconcile_rows=[[{"action": "edit"}] for _ in range(8)],
        )
        # Serialise behind a real lock to make the race repeatable.
        cl = SharedStateChangelist()
        threads = [
            threading.Thread(target=cl.track, args=(p4, f"/x/{i}.json"))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(p4.created_with) == 1
        assert len(cl.entries()) == 8

    def test_wait_idle_blocks_until_track_returns(self):
        """A slow reconcile in flight must keep wait_idle() blocked so the
        quit path can't submit a half-recorded CL."""
        started = threading.Event()
        release = threading.Event()

        class _SlowP4(_StubP4):
            def run(self, *args):
                if args and args[0] == "reconcile":
                    started.set()
                    release.wait(timeout=5)
                return super().run(*args)

        p4 = _SlowP4(reconcile_rows=[[{"action": "edit"}]])
        cl = SharedStateChangelist()
        t = threading.Thread(target=cl.track, args=(p4, "/slow.json"))
        t.start()
        started.wait(timeout=2)
        # While the reconcile is still running, wait_idle must time out.
        assert cl.wait_idle(timeout=0.1) is False
        release.set()
        t.join(timeout=5)
        # Once it lands, wait_idle returns immediately.
        assert cl.wait_idle(timeout=2.0) is True

    def test_wait_idle_returns_true_when_already_idle(self):
        cl = SharedStateChangelist()
        # Never tracked anything — should not block.
        t0 = time.monotonic()
        assert cl.wait_idle(timeout=1.0) is True
        assert time.monotonic() - t0 < 0.5
