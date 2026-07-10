"""Tree navigation: the workspace-tree file/dir namespace fix.

The workspace tree keys directory nodes by client syntax (//<client>/…)
but file leaves by depot path (//depot/…). Navigating to a workspace
file used to settle the cursor on the file's *containing directory*
because the final (client-syntax) segment never exact-matched the
depot-keyed leaf. The basename fallback in P4Tree._match_child fixes
that; these tests pin both the pure matcher and the end-to-end walk.
"""
from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from p4v_tui.widgets.p4_tree import _basename
from p4v_tui.widgets.workspace_tree import WorkspaceTree


# --- pure helper --------------------------------------------------------

def test_basename_both_separators():
    assert _basename("//depot/a/b/utils.py") == "utils.py"
    assert _basename("C:\\w\\a\\utils.py") == "utils.py"
    assert _basename("//client/a/") == "a"
    assert _basename("") == ""


# --- end-to-end walk ----------------------------------------------------

# A workspace tree rooted at //client with one subdir (client syntax) and
# one file leaf inside it (depot syntax) — exactly the mismatched-namespace
# shape that broke navigation.
_LAYOUT = {
    "//client": (["//client/src"], []),
    "//client/src": (
        [],
        [{"depotFile": "//depot/proj/src/utils.py",
          "clientFile": "//client/src/utils.py",
          "headRev": "3"}],
    ),
}


class _StubWorkspaceTree(WorkspaceTree):
    def _fetch_node_data(self, path):
        return _LAYOUT.get(path, ([], []))


class _Harness(App):
    def compose(self) -> ComposeResult:
        tree = _StubWorkspaceTree(p4=None, id="workspace_tree")
        yield tree

    def on_mount(self) -> None:
        tree = self.query_one("#workspace_tree", _StubWorkspaceTree)
        tree.set_root_path("//client", label="//client/")


async def _drive(coro_asserts):
    app = _Harness()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        tree = app.query_one("#workspace_tree", _StubWorkspaceTree)
        tree.root.expand()
        # Let the lazy loads (root, then src) settle.
        for _ in range(10):
            await pilot.pause()
        await coro_asserts(pilot, app, tree)


def test_navigate_lands_on_file_leaf_not_parent_dir():
    async def asserts(pilot, app, tree):
        # Navigate using the *client-syntax* path (what _navigate_tree_to
        # passes after `p4 where`).
        tree.navigate_to_path("//client/src/utils.py")
        for _ in range(10):
            await pilot.pause()
        node = tree.cursor_node
        assert node is not None
        # Cursor must be on the file leaf (depot-keyed), not the //client/src
        # directory — that's the bug this fixes.
        assert node.data == "//depot/proj/src/utils.py"
        assert not node.allow_expand  # it's the leaf, not the dir

    asyncio.run(_drive(asserts))


def test_navigate_to_directory_still_lands_on_dir():
    async def asserts(pilot, app, tree):
        tree.navigate_to_path("//client/src")
        for _ in range(10):
            await pilot.pause()
        node = tree.cursor_node
        assert node is not None
        assert node.data == "//client/src"
        assert node.allow_expand

    asyncio.run(_drive(asserts))


def test_mark_node_pending_prefixes_glyph_and_clear_restores():
    """Optimistic per-row action marker (perceived performance)."""
    async def asserts(pilot, app, tree):
        tree.navigate_to_path("//client/src/utils.py")
        for _ in range(10):
            await pilot.pause()
        node = tree.cursor_node
        assert node is not None and not node.allow_expand
        original = tree._plain(node.label)
        # mark -> glyph prefix
        tree.mark_node_pending(node)
        assert tree._plain(node.label).startswith(tree.PENDING_GLYPH)
        # idempotent: a second mark doesn't double the glyph
        tree.mark_node_pending(node)
        assert tree._plain(node.label).count(tree.PENDING_GLYPH.strip()) == 1
        # clear restores the original label exactly
        tree.clear_node_pending(node)
        assert tree._plain(node.label) == original

    asyncio.run(_drive(asserts))


def test_mark_node_pending_is_noop_on_directory_and_root():
    async def asserts(pilot, app, tree):
        before_root = tree._plain(tree.root.label)
        tree.mark_node_pending(tree.root)
        assert tree._plain(tree.root.label) == before_root  # root untouched
        dir_node = next(
            (c for c in tree.root.children if c.allow_expand), None)
        assert dir_node is not None
        before = tree._plain(dir_node.label)
        tree.mark_node_pending(dir_node)
        assert tree._plain(dir_node.label) == before  # folders reload wholesale

    asyncio.run(_drive(asserts))
