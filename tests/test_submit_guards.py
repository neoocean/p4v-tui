"""Unit tests for p4v_tui.submit_guards — pure pre-submit checks."""
from __future__ import annotations

from p4v_tui.submit_guards import (
    DEFAULT_LARGE_FILE_BYTES,
    GuardWarning,
    SubmitFile,
    evaluate_submit_guards,
    format_guard_warnings,
    has_blocking,
)


def _f(path, **kw):
    return SubmitFile(depot_path=path, **kw)


class TestEvaluate:
    def test_empty_changelist_blocks(self):
        ws = evaluate_submit_guards([])
        assert [w.code for w in ws] == ["empty"]
        assert ws[0].level == "block"

    def test_clean_changelist_no_warnings(self):
        files = [_f("//d/a.py", action="edit", size_bytes=1024)]
        assert evaluate_submit_guards(files) == []

    def test_unresolved_blocks(self):
        files = [
            _f("//d/a.py", action="edit", unresolved=True),
            _f("//d/b.py", action="edit"),
        ]
        ws = evaluate_submit_guards(files)
        assert any(w.code == "unresolved" and w.level == "block" for w in ws)

    def test_large_file_warns_not_blocks(self):
        files = [_f("//d/big.bin", action="add", size_bytes=DEFAULT_LARGE_FILE_BYTES)]
        ws = evaluate_submit_guards(files)
        assert [w.code for w in ws] == ["large_file"]
        assert ws[0].level == "warn"

    def test_large_file_threshold_is_inclusive(self):
        just_under = [_f("//d/x", size_bytes=DEFAULT_LARGE_FILE_BYTES - 1)]
        assert evaluate_submit_guards(just_under) == []

    def test_custom_threshold(self):
        files = [_f("//d/x", size_bytes=2000)]
        ws = evaluate_submit_guards(files, large_file_bytes=1000)
        assert [w.code for w in ws] == ["large_file"]

    def test_unknown_size_never_triggers_large(self):
        files = [_f("//d/x", size_bytes=None)]
        assert evaluate_submit_guards(files) == []

    def test_blocks_ordered_before_warns(self):
        files = [
            _f("//d/big.bin", size_bytes=DEFAULT_LARGE_FILE_BYTES),
            _f("//d/a.py", unresolved=True),
        ]
        codes = [w.code for w in evaluate_submit_guards(files)]
        assert codes.index("unresolved") < codes.index("large_file")


class TestHelpers:
    def test_has_blocking(self):
        assert has_blocking([GuardWarning("block", "x", "m")])
        assert not has_blocking([GuardWarning("warn", "x", "m")])
        assert not has_blocking([])

    def test_format_empty(self):
        assert format_guard_warnings([]) == ""

    def test_format_has_markers(self):
        out = format_guard_warnings([
            GuardWarning("block", "unresolved", "needs resolve"),
            GuardWarning("warn", "large_file", "too big"),
        ])
        assert "⛔" in out and "⚠" in out
        assert "needs resolve" in out and "too big" in out

    def test_sample_truncation_in_message(self):
        files = [SubmitFile(depot_path=f"//d/f{i}", unresolved=True) for i in range(5)]
        msg = evaluate_submit_guards(files)[0].message
        assert "and 2 more" in msg
