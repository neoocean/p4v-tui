"""Unit tests for the pure (non-SQLite) helpers in p4v_tui.search_index.

`find_match_spans` drives result/preview highlighting; `_smart_case_lower`
is the smart-case rule; `_identity_for` derives the per-(port, client)
index filename. None of these touch the database, so no fixture is needed.
"""
from __future__ import annotations

from p4v_tui.search_index import SearchIndex, _identity_for, find_match_spans


class TestFindMatchSpans:
    def test_empty_inputs(self):
        assert find_match_spans("", "x") == []
        assert find_match_spans("text", "") == []
        assert find_match_spans("text", "   ") == []

    def test_multiple_hits(self):
        assert find_match_spans("hello hello", "lo") == [(3, 5), (9, 11)]

    def test_smart_case_lowercase_query_is_insensitive(self):
        # all-lowercase needle → matches regardless of case
        assert find_match_spans("Hello HELLO", "hello") == [(0, 5), (6, 11)]

    def test_uppercase_query_is_case_sensitive(self):
        # needle has uppercase → only exact-case matches
        assert find_match_spans("Hello hello", "Hello") == [(0, 5)]

    def test_offsets_index_into_original_string(self):
        spans = find_match_spans("aXa", "a")
        assert spans == [(0, 1), (2, 3)]


class TestSmartCaseLower:
    def test_all_lowercase_is_insensitive(self):
        assert SearchIndex._smart_case_lower("abc") == ("abc", True)

    def test_any_uppercase_is_sensitive(self):
        assert SearchIndex._smart_case_lower("Abc") == ("Abc", False)


class TestIdentityFor:
    def test_deterministic(self):
        assert _identity_for("ssl:host:1666", "ws") == _identity_for("ssl:host:1666", "ws")

    def test_different_clients_differ(self):
        assert _identity_for("ssl:host:1666", "ws-a") != _identity_for("ssl:host:1666", "ws-b")

    def test_different_ports_differ(self):
        assert _identity_for("ssl:a:1666", "ws") != _identity_for("ssl:b:1666", "ws")

    def test_filesystem_safe(self):
        # Slashes / colons in a client name must not leak into the slug.
        slug = _identity_for("ssl:host:1666", "weird/client:name")
        assert "/" not in slug
        assert ":" not in slug

    def test_empty_client_has_placeholder(self):
        assert _identity_for("ssl:host:1666", "").startswith("noclient__")
