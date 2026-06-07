"""Unit tests for p4v_tui.merge3 — conflict-marker parse + reconstruct."""
from __future__ import annotations

from p4v_tui.merge3 import (
    BASE,
    BOTH,
    THEIRS,
    YOURS,
    Common,
    Conflict,
    conflicts,
    has_conflicts,
    parse_conflict_markers,
    reconstruct,
)

CONFLICT_TEXT = "\n".join([
    "top line",
    ">>>> ORIGINAL //d/f#1",
    "base a",
    "==== THEIRS //d/f#2",
    "their a",
    "their b",
    "==== YOURS //d/f",
    "your a",
    "<<<<",
    "bottom line",
])


class TestParse:
    def test_no_markers_single_common(self):
        segs = parse_conflict_markers("line1\nline2")
        assert len(segs) == 1 and isinstance(segs[0], Common)
        assert not has_conflicts(segs)

    def test_round_trips_without_markers(self):
        text = "a\nb\nc\n"
        segs = parse_conflict_markers(text)
        assert reconstruct(segs, []) == text

    def test_three_way_split(self):
        segs = parse_conflict_markers(CONFLICT_TEXT)
        assert [type(s).__name__ for s in segs] == ["Common", "Conflict", "Common"]
        c = conflicts(segs)[0]
        assert c.base == ["base a"]
        assert c.theirs == ["their a", "their b"]
        assert c.yours == ["your a"]

    def test_has_conflicts(self):
        assert has_conflicts(parse_conflict_markers(CONFLICT_TEXT))


class TestReconstruct:
    def setup_method(self):
        self.segs = parse_conflict_markers(CONFLICT_TEXT)

    def test_choose_yours(self):
        out = reconstruct(self.segs, [YOURS])
        assert out == "top line\nyour a\nbottom line"

    def test_choose_theirs(self):
        out = reconstruct(self.segs, [THEIRS])
        assert out == "top line\ntheir a\ntheir b\nbottom line"

    def test_choose_base(self):
        out = reconstruct(self.segs, [BASE])
        assert out == "top line\nbase a\nbottom line"

    def test_choose_both_is_yours_then_theirs(self):
        out = reconstruct(self.segs, [BOTH])
        assert out == "top line\nyour a\ntheir a\ntheir b\nbottom line"

    def test_missing_choice_defaults_to_yours(self):
        assert reconstruct(self.segs, []) == reconstruct(self.segs, [YOURS])


class TestPositionalFallback:
    def test_unlabelled_markers_use_order(self):
        text = "\n".join([
            ">>>>", "b", "====", "t", "====", "y", "<<<<",
        ])
        c = conflicts(parse_conflict_markers(text))[0]
        assert (c.base, c.theirs, c.yours) == (["b"], ["t"], ["y"])


class TestMultipleConflicts:
    def test_two_hunks_independent_choices(self):
        text = "\n".join([
            ">>>> ORIGINAL", "b1", "==== THEIRS", "t1", "==== YOURS", "y1", "<<<<",
            "mid",
            ">>>> ORIGINAL", "b2", "==== THEIRS", "t2", "==== YOURS", "y2", "<<<<",
        ])
        segs = parse_conflict_markers(text)
        assert len(conflicts(segs)) == 2
        out = reconstruct(segs, [YOURS, THEIRS])
        assert out == "y1\nmid\nt2"


class TestDataclassesUsable:
    def test_manual_segments(self):
        segs = [Common(["x"]), Conflict(base=["b"], theirs=["t"], yours=["y"])]
        assert reconstruct(segs, [THEIRS]) == "x\nt"


# The exact bytes a live `p4 resolve -af` wrote into a conflicting file
# (captured from a probe conflict, CL 56826, both backends). This pins the
# parser against *real* Perforce marker output — note the asymmetric paths
# (ORIGINAL/THEIRS reference the source depot path, YOURS the client-syntax
# target) and the surrounding common lines. See docs/handoff-manual-tests.md.
REAL_P4_MARKERS = "\n".join([
    "alpha",
    ">>>> ORIGINAL //depot/p4v-tui/tests/_probe_merge/base.txt#1",
    "ORIGINAL-LINE",
    "==== THEIRS //depot/p4v-tui/tests/_probe_merge/base.txt#2",
    "MAINLINE-CHANGE",
    "==== YOURS //playground/scripts/p4v-tui/tests/_probe_merge/feature.txt",
    "FEATURE-CHANGE",
    "<<<<",
    "gamma",
    "",
])


class TestRealPerforceMarkers:
    def test_parses_real_resolve_af_output(self):
        segs = parse_conflict_markers(REAL_P4_MARKERS)
        assert has_conflicts(segs)
        cs = conflicts(segs)
        assert len(cs) == 1
        c = cs[0]
        assert c.base == ["ORIGINAL-LINE"]
        assert c.theirs == ["MAINLINE-CHANGE"]
        assert c.yours == ["FEATURE-CHANGE"]

    def test_common_lines_preserved(self):
        segs = parse_conflict_markers(REAL_P4_MARKERS)
        # The first/last segments are the unconflicted surrounding lines.
        assert isinstance(segs[0], Common) and segs[0].lines == ["alpha"]
        assert isinstance(segs[-1], Common) and segs[-1].lines == ["gamma", ""]

    def test_reconstruct_each_side(self):
        segs = parse_conflict_markers(REAL_P4_MARKERS)
        assert reconstruct(segs, [YOURS]) == "alpha\nFEATURE-CHANGE\ngamma\n"
        assert reconstruct(segs, [THEIRS]) == "alpha\nMAINLINE-CHANGE\ngamma\n"
        assert reconstruct(segs, [BASE]) == "alpha\nORIGINAL-LINE\ngamma\n"
        assert reconstruct(segs, [BOTH]) == (
            "alpha\nFEATURE-CHANGE\nMAINLINE-CHANGE\ngamma\n"
        )
