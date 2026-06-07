"""Unit tests for the pure menu builder in p4v_tui.app_menus.

build_pending_menu() decides which Pending-CL context-menu entries are
shown for a local / default / remote changelist. This gating used to be
buried in a 233-line method only reachable through a running TUI; these
tests pin it directly.
"""
from __future__ import annotations

from p4v_tui.app_menus import build_pending_menu


def _actions(change, **kw):
    items, _title = build_pending_menu(change, **kw)
    return [it.action for it in items if it.label != "__sep__"]


def _title(change, **kw):
    return build_pending_menu(change, **kw)[1]


class TestLocalNonDefault:
    KW = dict(is_default=False, is_remote=False, row_client="")

    def test_has_full_action_set(self):
        acts = _actions("12345", **self.KW)
        for expected in (
            "submit", "submit_resolve", "view_cl", "revert_cl",
            "revert_unchanged", "re_resolve", "move_all_to", "diff_have",
            "edit_desc", "delete_cl", "shelve", "shelve_update",
            "shelve_delete", "unshelve", "copy_swarm_cl", "open_swarm_cl",
            "new_pending_cl", "print", "print_preview",
            "refresh_pending", "refresh_one",
        ):
            assert expected in acts, expected

    def test_title_plain(self):
        assert _title("12345", **self.KW) == "Pending CL 12345"

    def test_no_remote_only_action(self):
        assert "view_remote" not in _actions("12345", **self.KW)


class TestDefaultChangelist:
    KW = dict(is_default=True, is_remote=False, row_client="")

    def test_submit_present_but_resolve_absent(self):
        acts = _actions("default", **self.KW)
        assert "submit" in acts
        assert "submit_resolve" not in acts

    def test_edit_and_delete_hidden(self):
        acts = _actions("default", **self.KW)
        assert "edit_desc" not in acts
        assert "delete_cl" not in acts

    def test_shelving_hidden(self):
        acts = _actions("default", **self.KW)
        assert not {"shelve", "shelve_update", "shelve_delete", "unshelve"} & set(acts)

    def test_swarm_hidden_for_default(self):
        # Swarm URLs key off a real CL number; the default CL has none.
        acts = _actions("default", **self.KW)
        assert "copy_swarm_cl" not in acts
        assert "open_swarm_cl" not in acts

    def test_core_file_ops_still_present(self):
        acts = _actions("default", **self.KW)
        for expected in ("view_cl", "revert_cl", "move_all_to", "diff_have"):
            assert expected in acts, expected


class TestRemoteChangelist:
    KW = dict(is_default=False, is_remote=True, row_client="surface")

    def test_only_safe_actions_exposed(self):
        acts = _actions("777", **self.KW)
        assert set(acts) >= {"view_remote", "edit_desc", "delete_cl"}

    def test_client_bound_actions_hidden(self):
        # Submit / revert / shelve / move / re-resolve / diff-have all bind
        # opened files in the *current* client → must be hidden for a CL
        # owned by another workspace.
        acts = set(_actions("777", **self.KW))
        assert not acts & {
            "submit", "submit_resolve", "revert_cl", "revert_unchanged",
            "re_resolve", "move_all_to", "diff_have", "shelve", "unshelve",
        }

    def test_title_annotates_remote_workspace(self):
        assert "remote workspace 'surface'" in _title("777", **self.KW)

    def test_swarm_present_for_numbered_remote(self):
        assert "copy_swarm_cl" in _actions("777", **self.KW)
