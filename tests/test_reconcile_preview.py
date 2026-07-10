"""Unit tests for the reconcile/clean dry-run preview parser + jobs."""
from __future__ import annotations

from p4v_tui.bulk_jobs import CleanFilesJob, ReconcileFilesJob
from p4v_tui.reconcile_preview import PreviewEntry, parse_preview


# --- parse_preview ------------------------------------------------------

def test_parse_basic_actions():
    rows = [
        {"action": "edit", "depotFile": "//d/a.py", "clientFile": "/w/a.py"},
        {"action": "add", "clientFile": "/w/new.py"},
        {"action": "delete", "depotFile": "//d/gone.py",
         "clientFile": "/w/gone.py"},
    ]
    out = parse_preview(rows)
    assert [e.action for e in out] == ["edit", "add", "delete"]
    # add has no depot path → spec falls back to client path
    assert out[1].spec == "/w/new.py"
    assert out[0].spec == "/w/a.py"


def test_parse_skips_info_and_actionless_rows():
    rows = [
        {"code": "info", "data": "//d/... - no file(s) to reconcile."},
        {"depotFile": "//d/x", "clientFile": "/w/x"},  # no action
        {"action": "", "clientFile": "/w/y"},          # blank action
        {"action": "edit", "clientFile": "/w/z"},
    ]
    out = parse_preview(rows)
    assert len(out) == 1
    assert out[0].client_file == "/w/z"


def test_parse_skips_non_dicts_and_pathless():
    rows = ["garbage", {"action": "edit"}, None]
    assert parse_preview(rows) == []


def test_display_format():
    e = PreviewEntry(action="delete", depot_file="//d/a", client_file="/w/a")
    assert e.display == "delete      //d/a"


# --- explicit-files jobs ------------------------------------------------

class _RecordingP4:
    def __init__(self):
        self.calls = []

    def run(self, *args):
        self.calls.append(args)
        return []


def test_reconcile_files_job_batches_and_passes_flags():
    p4 = _RecordingP4()
    files = [f"/w/f{i}" for i in range(5)]
    job = ReconcileFilesJob(p4, files, batch_size=2)
    assert job.total_chunks == 3  # ceil(5/2)
    for chunk in job.chunks():
        chunk()
    # Every call starts with the reconcile flags then the batch.
    assert all(c[:4] == ("reconcile", "-a", "-e", "-d") for c in p4.calls)
    seen = [f for c in p4.calls for f in c[4:]]
    assert seen == files
    assert [len(c) - 4 for c in p4.calls] == [2, 2, 1]


def test_clean_files_job_command():
    p4 = _RecordingP4()
    job = CleanFilesJob(p4, ["/w/a", "/w/b"], batch_size=10)
    assert job.total_chunks == 1
    for chunk in job.chunks():
        chunk()
    assert p4.calls == [("clean", "/w/a", "/w/b")]


def test_explicit_job_strategy_describe_available():
    job = ReconcileFilesJob(_RecordingP4(), ["/w/a"], batch_size=50)
    assert job.strategy is not None
    assert job.strategy.describe()  # non-empty, used in the queue toast
