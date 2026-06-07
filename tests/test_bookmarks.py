"""Unit tests for p4v_tui.bookmarks.BookmarkStore."""
from __future__ import annotations

from p4v_tui.bookmarks import BookmarkStore


class TestBookmarkStore:
    def test_add_and_list(self, tmp_path):
        s = BookmarkStore(tmp_path / "b.json")
        assert s.add("1000", "//d/a") is True
        assert [(b.permalink_id, b.label) for b in s.list()] == [("1000", "//d/a")]
        assert len(s) == 1

    def test_persists(self, tmp_path):
        BookmarkStore(tmp_path / "b.json").add("1000", "//d/a")
        s2 = BookmarkStore(tmp_path / "b.json")
        assert [b.permalink_id for b in s2.list()] == ["1000"]

    def test_add_dedupes_and_refreshes_label(self, tmp_path):
        s = BookmarkStore(tmp_path / "b.json")
        s.add("1000", "//d/a")
        assert s.add("1000", "//d/a-renamed") is False
        items = s.list()
        assert len(items) == 1 and items[0].label == "//d/a-renamed"

    def test_remove(self, tmp_path):
        s = BookmarkStore(tmp_path / "b.json")
        s.add("1000", "//d/a")
        assert s.remove("1000") is True
        assert s.list() == []
        assert s.remove("1000") is False  # already gone

    def test_int_id_coerced_to_str(self, tmp_path):
        s = BookmarkStore(tmp_path / "b.json")
        s.add(1000, "//d/a")
        assert s.list()[0].permalink_id == "1000"
        assert s.remove(1000) is True

    def test_missing_file_empty(self, tmp_path):
        assert BookmarkStore(tmp_path / "nope.json").list() == []

    def test_corrupt_file_tolerated(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text("not json {", encoding="utf-8")
        s = BookmarkStore(p)
        assert s.list() == []
        s.add("1", "//d/x")  # still usable
        assert len(s) == 1

    def test_order_preserved(self, tmp_path):
        s = BookmarkStore(tmp_path / "b.json")
        for i in range(3):
            s.add(str(1000 + i), f"//d/{i}")
        assert [b.permalink_id for b in s.list()] == ["1000", "1001", "1002"]

    def test_after_write_hook_fires(self, tmp_path):
        seen = []
        s = BookmarkStore(
            tmp_path / "b.json", after_write=lambda p: seen.append(p),
        )
        s.add("1000", "//d/a")
        assert seen == [tmp_path / "b.json"]

    def test_atomic_write_leaves_no_tmp_residue(self, tmp_path):
        s = BookmarkStore(tmp_path / "b.json")
        s.add("1000", "//d/a")
        s.add("1001", "//d/b")
        assert list(tmp_path.glob("*.tmp")) == []
        # Reload proves the committed file is complete + parseable.
        assert len(BookmarkStore(tmp_path / "b.json")) == 2
