"""Unit tests for p4v_tui.chunking — strategy parsing + batching.

The chunking layer decides how a bulk op (sync / revert / reconcile) is
split for the JobRunner. These tests pin the strategy normalisation (bad
config must degrade, never crash) and the batching maths, with no server.
"""
from __future__ import annotations

from p4v_tui.chunking import (
    DEFAULT_BYTES_PER_CHUNK,
    DEFAULT_FILES_PER_CHUNK,
    ChunkingConfig,
    ChunkingStrategy,
    estimate_chunk_count,
    iter_file_batches,
)


class TestChunkingStrategyNormalisation:
    def test_defaults(self):
        s = ChunkingStrategy()
        assert s.mode == "count"
        assert s.files_per_chunk == DEFAULT_FILES_PER_CHUNK
        assert s.bytes_per_chunk == DEFAULT_BYTES_PER_CHUNK

    def test_invalid_mode_falls_back_to_count(self):
        # Bad config should never crash; it clamps to a safe default.
        assert ChunkingStrategy(mode="bogus").mode == "count"

    def test_subunit_values_clamped_to_minimum(self):
        assert ChunkingStrategy(files_per_chunk=0).files_per_chunk == 1
        assert ChunkingStrategy(files_per_chunk=-9).files_per_chunk == 1
        assert ChunkingStrategy(bytes_per_chunk=0).bytes_per_chunk == 1


class TestChunkingStrategyFromDict:
    def test_none_returns_default(self):
        assert ChunkingStrategy.from_dict(None) == ChunkingStrategy()

    def test_mode_is_lowercased_and_trimmed(self):
        assert ChunkingStrategy.from_dict({"mode": "  SIZE "}).mode == "size"

    def test_bad_mode_uses_fallback_mode(self):
        fb = ChunkingStrategy(mode="single")
        assert ChunkingStrategy.from_dict({"mode": "nope"}, fallback=fb).mode == "single"

    def test_partial_override_shadows_only_given_fields(self):
        fb = ChunkingStrategy(mode="size", files_per_chunk=10, bytes_per_chunk=999)
        out = ChunkingStrategy.from_dict({"files_per_chunk": 7}, fallback=fb)
        assert out.mode == "size"            # inherited
        assert out.files_per_chunk == 7      # overridden
        assert out.bytes_per_chunk == 999    # inherited

    def test_unparseable_numbers_fall_back(self):
        out = ChunkingStrategy.from_dict({"files_per_chunk": "xx"})
        assert out.files_per_chunk == DEFAULT_FILES_PER_CHUNK


class TestDescribe:
    def test_each_mode_has_text(self):
        assert "per chunk" in ChunkingStrategy(mode="count").describe()
        assert "MB" in ChunkingStrategy(mode="size").describe()
        assert ChunkingStrategy(mode="single").describe() == "1 file per chunk"
        assert "subdirectory" in ChunkingStrategy(mode="subdir").describe()


class TestChunkingConfig:
    def test_for_job_falls_back_to_default(self):
        default = ChunkingStrategy(mode="count")
        special = ChunkingStrategy(mode="single")
        cfg = ChunkingConfig(default=default, per_job={"sync": special})
        assert cfg.for_job("sync") is special
        assert cfg.for_job("revert") is default


class TestIterFileBatches:
    def test_empty_yields_nothing(self):
        assert list(iter_file_batches([], ChunkingStrategy())) == []

    def test_count_mode(self):
        files = [f"//d/{i}" for i in range(5)]
        s = ChunkingStrategy(mode="count", files_per_chunk=2)
        assert list(iter_file_batches(files, s)) == [
            files[0:2], files[2:4], files[4:5],
        ]

    def test_single_mode(self):
        files = ["a", "b", "c"]
        s = ChunkingStrategy(mode="single")
        assert list(iter_file_batches(files, s)) == [["a"], ["b"], ["c"]]

    def test_subdir_falls_back_to_count_for_flat_list(self):
        files = ["a", "b", "c"]
        s = ChunkingStrategy(mode="subdir", files_per_chunk=2)
        assert list(iter_file_batches(files, s)) == [["a", "b"], ["c"]]

    def test_size_mode_packs_by_bytes(self):
        files = ["a", "b", "c", "d"]
        sizes = {"a": 30, "b": 30, "c": 30, "d": 5}
        s = ChunkingStrategy(mode="size", bytes_per_chunk=50)
        out = list(iter_file_batches(files, s, size_lookup=sizes.get))
        # a(30)+b(30)=60 > 50 → a alone, then b+? b(30)+c(30)=60>50 → b, c+d(35)
        assert out == [["a"], ["b"], ["c", "d"]]

    def test_size_mode_without_lookup_degrades_to_count(self):
        files = ["a", "b", "c"]
        s = ChunkingStrategy(mode="size", files_per_chunk=2)
        assert list(iter_file_batches(files, s)) == [["a", "b"], ["c"]]

    def test_oversized_single_file_gets_own_chunk(self):
        files = ["big", "x"]
        sizes = {"big": 999, "x": 1}
        s = ChunkingStrategy(mode="size", bytes_per_chunk=50)
        assert list(iter_file_batches(files, s, size_lookup=sizes.get)) == [["big"], ["x"]]


class TestEstimateChunkCount:
    def test_matches_iter_for_count(self):
        files = [str(i) for i in range(7)]
        s = ChunkingStrategy(mode="count", files_per_chunk=3)
        assert estimate_chunk_count(files, s) == len(list(iter_file_batches(files, s)))

    def test_matches_iter_for_single(self):
        files = ["a", "b"]
        s = ChunkingStrategy(mode="single")
        assert estimate_chunk_count(files, s) == 2

    def test_matches_iter_for_size(self):
        files = ["a", "b", "c", "d"]
        sizes = {"a": 30, "b": 30, "c": 30, "d": 5}
        s = ChunkingStrategy(mode="size", bytes_per_chunk=50)
        assert estimate_chunk_count(files, s, size_lookup=sizes.get) == len(
            list(iter_file_batches(files, s, size_lookup=sizes.get))
        )

    def test_empty(self):
        assert estimate_chunk_count([], ChunkingStrategy()) == 0
