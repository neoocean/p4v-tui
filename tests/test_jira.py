"""Unit tests for p4v_tui.jira — pure issue-key helpers."""
from __future__ import annotations

from p4v_tui.jira import (
    build_jira_url,
    ensure_jira_trailer,
    extract_jira_keys,
    is_valid_jira_key,
    project_for_path,
    projects_for_paths,
)

_PMAP = {
    "//depot/alpha/": "alpha",
    "//depot/beta/": "beta",
    "//depot/gamma/": "gamma",
}


class TestProjectForPath:
    def test_basic_match(self):
        assert project_for_path("//depot/alpha/x/y.md", _PMAP) == "alpha"
        assert project_for_path("//depot/gamma/a.py", _PMAP) == "gamma"

    def test_no_match(self):
        assert project_for_path("//depot/other/z", _PMAP) is None
        assert project_for_path("//elsewhere/alpha/z", _PMAP) is None

    def test_trailing_ellipsis_prefix_ok(self):
        assert project_for_path("//d/todo/a", {"//d/todo/...": "todo"}) == "todo"

    def test_longest_prefix_wins(self):
        m = {"//d/": "broad", "//d/todo/": "todo"}
        assert project_for_path("//d/todo/x", m) == "todo"

    def test_dir_boundary_not_substring(self):
        # //depot/alphaX must NOT match the //depot/alpha/ prefix.
        assert project_for_path("//depot/alphaX/a", _PMAP) is None


class TestProjectsForPaths:
    def test_distinct_in_order(self):
        paths = [
            "//depot/alpha/a", "//depot/gamma/b",
            "//depot/alpha/c", "//depot/other/d",
        ]
        assert projects_for_paths(paths, _PMAP) == ["alpha", "gamma"]

    def test_empty(self):
        assert projects_for_paths([], _PMAP) == []
        assert projects_for_paths(["//x/y"], {}) == []


class TestIsValidJiraKey:
    def test_valid(self):
        assert is_valid_jira_key("PROJ-123")
        assert is_valid_jira_key("  ABC1-7  ")

    def test_invalid(self):
        assert not is_valid_jira_key("")
        assert not is_valid_jira_key("proj-123")      # lowercase project
        assert not is_valid_jira_key("PROJ-")          # no number
        assert not is_valid_jira_key("X-1 and text")   # surrounding text


class TestExtractJiraKeys:
    def test_basic_and_dedup(self):
        text = "Fix PROJ-1 and PROJ-2; also PROJ-1 again"
        assert extract_jira_keys(text) == ["PROJ-1", "PROJ-2"]

    def test_empty(self):
        assert extract_jira_keys("") == []
        assert extract_jira_keys(None) == []  # type: ignore[arg-type]

    def test_known_projects_filter_removes_lookalikes(self):
        text = "encode UTF-8, hash SHA-1, ticket ABC-42"
        # Without filter, the generic shape matches UTF-8 / SHA-1 too.
        assert "UTF-8" in extract_jira_keys(text)
        # With known projects, only real keys survive.
        assert extract_jira_keys(text, known_projects=["ABC"]) == ["ABC-42"]

    def test_known_projects_case_insensitive(self):
        assert extract_jira_keys("ABC-1", known_projects=["abc"]) == ["ABC-1"]


class TestBuildJiraUrl:
    def test_browse_url(self):
        assert build_jira_url("https://jira.example", "PROJ-9") == \
            "https://jira.example/browse/PROJ-9"

    def test_trailing_slash_stripped(self):
        assert build_jira_url("https://jira.example/", "PROJ-9") == \
            "https://jira.example/browse/PROJ-9"


class TestEnsureJiraTrailer:
    def test_appends_when_absent(self):
        assert ensure_jira_trailer("Did a thing.", "PROJ-5") == \
            "Did a thing.\n\nJira: PROJ-5"

    def test_idempotent_when_present(self):
        desc = "Fix PROJ-5 properly"
        assert ensure_jira_trailer(desc, "PROJ-5") == desc

    def test_empty_description(self):
        assert ensure_jira_trailer("", "PROJ-5") == "Jira: PROJ-5"

    def test_strips_trailing_whitespace_before_trailer(self):
        assert ensure_jira_trailer("line\n\n  ", "AB-1") == "line\n\nJira: AB-1"
