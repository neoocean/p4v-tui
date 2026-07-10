"""Tests for the external merge-tool config + the blocking launcher.

The launcher itself is exercised against a tiny stub script (a Python
process that copies 'theirs' → 'merge') so we verify the placeholder
rendering and the blocking read-back contract without needing P4Merge.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from p4v_tui import fs_actions
from p4v_tui.config import (
    Config, ConnectionConfig, MergeTool, load_config, write_config,
)


# --- config round trip --------------------------------------------------

def test_merge_tool_round_trip(tmp_path):
    cfg = Config(
        connection=ConnectionConfig(port="ssl:m:1666"),
        profiles=[],
        swarm=None,
        chunking=None,
        merge_tool=MergeTool(
            command="p4merge", args="{base} {theirs} {yours} {merge}",
            name="P4Merge",
        ),
        source=None,
    )
    target = tmp_path / "p4v-tui.toml"
    write_config(cfg, target)
    loaded = load_config(explicit_path=str(target))
    assert loaded.merge_tool is not None
    assert loaded.merge_tool.command == "p4merge"
    assert loaded.merge_tool.name == "P4Merge"
    assert "{merge}" in loaded.merge_tool.args


def test_merge_tool_absent_when_no_command(tmp_path):
    target = tmp_path / "p4v-tui.toml"
    target.write_text(
        '[merge_tool]\nname = "x"\n', encoding="utf-8",
    )  # no command → inert
    loaded = load_config(explicit_path=str(target))
    assert loaded.merge_tool is None


# --- launcher -----------------------------------------------------------

def _make_copy_tool(tmp_path: Path) -> str:
    """A stub 'merge tool': argv = base theirs yours merge; copies
    theirs → merge and exits 0."""
    script = tmp_path / "copytool.py"
    script.write_text(
        "import sys, shutil\n"
        "shutil.copyfile(sys.argv[2], sys.argv[4])\n",
        encoding="utf-8",
    )
    return str(script)


def test_run_merge_tool_blocks_and_writes_merge(tmp_path):
    base = tmp_path / "b"; base.write_text("base\n")
    theirs = tmp_path / "t"; theirs.write_text("THEIRS WINS\n")
    yours = tmp_path / "y"; yours.write_text("yours\n")
    merge = tmp_path / "m"; merge.write_text("yours\n")
    tool = _make_copy_tool(tmp_path)

    rc = fs_actions.run_merge_tool(
        sys.executable, f"{tool} {{base}} {{theirs}} {{yours}} {{merge}}",
        base=str(base), theirs=str(theirs), yours=str(yours),
        merge=str(merge),
    )
    assert rc == 0
    # Blocking contract: the merge file is written by the time we return.
    assert merge.read_text() == "THEIRS WINS\n"


def test_run_merge_tool_missing_command_raises():
    with pytest.raises(FileNotFoundError):
        fs_actions.run_merge_tool(
            "definitely-not-a-real-binary-xyz", "",
            base="b", theirs="t", yours="y", merge="m",
        )


def test_run_merge_tool_bad_template_raises():
    with pytest.raises(ValueError):
        fs_actions.run_merge_tool(
            sys.executable, "{nope}",
            base="b", theirs="t", yours="y", merge="m",
        )
