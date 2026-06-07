"""Unit tests for p4v_tui.path_nav.classify_path."""
from __future__ import annotations

from p4v_tui.path_nav import classify_path, plan_goto_fallback


class TestClassifyPath:
    def test_depot(self):
        assert classify_path("//depot/foo/bar.txt") == ("depot", "//depot/foo/bar.txt")

    def test_local_unix(self):
        assert classify_path("/home/me/work/a.py") == ("local", "/home/me/work/a.py")

    def test_local_home(self):
        assert classify_path("~/work/a.py") == ("local", "~/work/a.py")

    def test_local_windows_drive(self):
        assert classify_path(r"C:\work\a.py") == ("local", r"C:\work\a.py")

    def test_empty(self):
        assert classify_path("") == ("empty", "")
        assert classify_path("   ") == ("empty", "")

    def test_unknown_relative(self):
        assert classify_path("foo/bar.txt") == ("unknown", "foo/bar.txt")

    def test_strips_whitespace(self):
        assert classify_path("  //d/a  ") == ("depot", "//d/a")

    def test_strips_matching_quotes(self):
        assert classify_path('"//d/a"') == ("depot", "//d/a")
        assert classify_path("'/home/a'") == ("local", "/home/a")

    def test_depot_rev_qualifier_stripped(self):
        assert classify_path("//d/f.txt#5") == ("depot", "//d/f.txt")
        assert classify_path("//d/...@1234") == ("depot", "//d/...")

    def test_depot_without_qualifier_untouched(self):
        assert classify_path("//d/a@b/c") == ("depot", "//d/a@b/c")  # @ inside path kept

    def test_virtual_address(self):
        assert classify_path("//@p/98765") == ("permalink", "98765")
        assert classify_path("  //@p/12  ") == ("permalink", "12")

    def test_virtual_takes_precedence_over_depot(self):
        # //@p/.. starts with // but must classify as virtual, not depot.
        kind, _ = classify_path("//@p/5")
        assert kind == "permalink"


class TestPlanGotoFallback:
    def test_single_loose_hit_navigates(self):
        assert plan_goto_fallback(["//d/foo/bar.txt"], []) == (
            "navigate", ["//d/foo/bar.txt"],
        )

    def test_multiple_loose_hits_pick(self):
        hits = ["//d/a/x.py", "//d/b/x.py", "//d/c/x.py"]
        assert plan_goto_fallback(hits, ["ignored"]) == ("pick", hits)

    def test_no_loose_falls_through_to_suggestions(self):
        assert plan_goto_fallback([], ["foobar", "foobaz"]) == (
            "suggest", ["foobar", "foobaz"],
        )

    def test_loose_wins_over_suggestions(self):
        # When both are present the loose hit (more reliable) is used.
        assert plan_goto_fallback(["//d/f"], ["sugg"]) == (
            "navigate", ["//d/f"],
        )

    def test_nothing_to_offer(self):
        assert plan_goto_fallback([], []) == ("none", [])

    def test_none_inputs_are_safe(self):
        assert plan_goto_fallback(None, None) == ("none", [])

    def test_empty_strings_filtered_out(self):
        # Defensive: blank entries shouldn't be counted as hits/suggestions.
        assert plan_goto_fallback(["", ""], ["", "real"]) == (
            "suggest", ["real"],
        )

    def test_single_after_filtering_blanks(self):
        assert plan_goto_fallback(["", "//d/only.txt"], []) == (
            "navigate", ["//d/only.txt"],
        )
