"""Unit tests for p4v_tui.utils — pure display helpers, no I/O.

These pin the small string/number helpers that table rendering and the
JobStatusBar rely on. They run with no Perforce server and no Textual app.
"""
from __future__ import annotations

from rich.cells import cell_len

from p4v_tui.utils import (
    first_nonblank_line,
    format_eta,
    is_creation_action,
    truncate_cells,
)


class TestFirstNonblankLine:
    def test_empty_and_none(self):
        assert first_nonblank_line("") == ""
        assert first_nonblank_line(None) == ""  # type: ignore[arg-type]

    def test_skips_leading_blank_lines(self):
        # `p4 changes -L` descriptions often start with a newline.
        assert first_nonblank_line("\n\n   hello \nworld") == "hello"

    def test_strips_surrounding_whitespace(self):
        assert first_nonblank_line("   spaced   ") == "spaced"

    def test_all_blank(self):
        assert first_nonblank_line("\n  \n\t\n") == ""


class TestFormatEta:
    def test_none_and_unparseable(self):
        assert format_eta(None) == ""
        assert format_eta("abc") == ""  # type: ignore[arg-type]

    def test_out_of_range(self):
        assert format_eta(-5) == ""
        assert format_eta(86401) == ""  # > 1 day → unreliable

    def test_sub_minute(self):
        assert format_eta(0) == "0s"
        assert format_eta(30) == "30s"
        assert format_eta(59.4) == "59s"  # rounds

    def test_sub_hour(self):
        assert format_eta(90) == "1m 30s"
        assert format_eta(3599) == "59m 59s"

    def test_hours(self):
        assert format_eta(3661) == "1h 1m"
        assert format_eta(7200) == "2h 0m"


class TestTruncateCells:
    def test_nonpositive_budget(self):
        assert truncate_cells("anything", 0) == ""
        assert truncate_cells("anything", -3) == ""

    def test_short_text_unchanged(self):
        assert truncate_cells("hello", 10) == "hello"

    def test_ascii_truncation_fits_budget(self):
        out = truncate_cells("hello world", 8)
        assert cell_len(out) <= 8
        assert out.endswith("…")

    def test_cjk_double_width_respected(self):
        # Each Hangul syllable is 2 display cells; the result must never
        # overflow the cell budget even though it has fewer characters.
        out = truncate_cells("한국어테스트", 5)
        assert cell_len(out) <= 5
        assert out.endswith("…")

    def test_budget_smaller_than_ellipsis(self):
        # Custom multi-cell ellipsis wider than the budget.
        assert truncate_cells("long text here", 1, ellipsis="..") == "."


class TestIsCreationAction:
    def test_plain_creations(self):
        assert is_creation_action("add") is True
        assert is_creation_action("branch") is True
        assert is_creation_action("import") is True

    def test_move_add_is_creation(self):
        # the edge case: a rename's destination. A naive
        # `in ("add","branch")` missed it, so a move/add onto a
        # resurrected path (rev > 1) got an empty/wrong "previous rev".
        assert is_creation_action("move/add") is True

    def test_any_slash_add_variant(self):
        assert is_creation_action("branch/add") is True

    def test_non_creation_actions(self):
        for a in ("edit", "integrate", "delete", "move/delete",
                  "purge", "archive"):
            assert is_creation_action(a) is False, a

    def test_move_delete_is_not_creation(self):
        # guards against an over-broad rule sweeping the rename SOURCE
        assert is_creation_action("move/delete") is False

    def test_whitespace_and_empty_and_none(self):
        assert is_creation_action("  move/add  ") is True
        assert is_creation_action("") is False
        assert is_creation_action(None) is False  # type: ignore[arg-type]
