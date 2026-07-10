"""Unit tests for `p4v_tui.p4client` pure-Python helpers.

These don't need a live server, P4Python, or even the `p4` CLI — they
exercise the marshal decode, the spec-form text serializer, and the
numbered-field flatten/expand round-trip the CLI backend uses to talk
to `p4 -G ... -i`. A few tests also exercise the CLI subprocess
plumbing via a fake binary, so they need a `sh` (POSIX) or batch-file
(Windows) shim but no `p4`.
"""
from __future__ import annotations

import marshal
import sys
import time

import pytest

from p4v_tui import p4client as pc


# --- _decode_marshal --------------------------------------------------------

class TestDecodeMarshal:
    def test_bytes_to_utf8(self):
        assert pc._decode_marshal(b"hello") == "hello"

    def test_korean_utf8_passthrough(self):
        assert pc._decode_marshal("안녕".encode("utf-8")) == "안녕"

    def test_binary_blob_stays_bytes(self):
        # An ill-formed UTF-8 sequence (continuation byte without lead).
        blob = b"\x80\x81\x82"
        out = pc._decode_marshal(blob)
        assert out == blob, "binary payload must not be force-decoded"
        assert isinstance(out, bytes)

    def test_recurses_into_dict(self):
        d = {b"code": b"stat", b"depotFile": b"//x"}
        assert pc._decode_marshal(d) == {"code": "stat", "depotFile": "//x"}

    def test_recurses_into_list(self):
        ls = [b"a", b"b", b"c"]
        assert pc._decode_marshal(ls) == ["a", "b", "c"]


# --- _read_marshal_stream ---------------------------------------------------

class TestReadMarshalStream:
    def test_empty(self):
        assert pc._read_marshal_stream(b"") == []

    def test_single_dict(self):
        buf = marshal.dumps({b"code": b"stat", b"x": b"1"}, 2)
        assert pc._read_marshal_stream(buf) == [{"code": "stat", "x": "1"}]

    def test_concatenated_dicts(self):
        buf = (
            marshal.dumps({b"a": b"1"}, 2)
            + marshal.dumps({b"b": b"2"}, 2)
            + marshal.dumps({b"c": b"3"}, 2)
        )
        assert pc._read_marshal_stream(buf) == [
            {"a": "1"}, {"b": "2"}, {"c": "3"},
        ]

    def test_truncated_tail_ignored(self):
        # Valid dict followed by half of another; the truncated tail
        # must not crash the loop, the valid prefix must be returned.
        good = marshal.dumps({b"k": b"v"}, 2)
        buf = good + good[:5]
        assert pc._read_marshal_stream(buf) == [{"k": "v"}]


# --- _project_tagged_rows ---------------------------------------------------

class TestProjectTaggedRows:
    def test_stat_strips_code_key(self):
        assert pc._project_tagged_rows([{"code": "stat", "a": "1"}]) == [
            {"a": "1"},
        ]

    def test_info_becomes_bare_string(self):
        rows = [{"code": "info", "data": "Change 12345 created.", "level": "0"}]
        assert pc._project_tagged_rows(rows) == ["Change 12345 created."]

    def test_text_becomes_bare_string(self):
        assert pc._project_tagged_rows(
            [{"code": "text", "data": "some text"}]
        ) == ["some text"]

    def test_binary_becomes_bare_bytes(self):
        assert pc._project_tagged_rows(
            [{"code": "binary", "data": b"\x00\x01"}]
        ) == [b"\x00\x01"]

    def test_error_rows_dropped(self):
        rows = [
            {"code": "error", "data": "boom", "severity": "3"},
            {"code": "stat", "x": "1"},
        ]
        assert pc._project_tagged_rows(rows) == [{"x": "1"}]

    def test_non_dict_passthrough(self):
        assert pc._project_tagged_rows(["raw", b"raw"]) == ["raw", b"raw"]


# --- _extract_error_text ----------------------------------------------------

class TestExtractErrorText:
    def test_none_when_no_errors(self):
        assert pc._extract_error_text([{"code": "stat"}]) is None

    def test_severity_warning_ignored(self):
        rows = [{"code": "error", "data": "fyi", "severity": "2"}]
        assert pc._extract_error_text(rows) is None

    def test_severity_error_collected(self):
        rows = [{"code": "error", "data": "boom", "severity": "3"}]
        assert pc._extract_error_text(rows) == "boom"

    def test_multiple_errors_joined(self):
        rows = [
            {"code": "error", "data": "first", "severity": "3"},
            {"code": "error", "data": "second", "severity": "3"},
            {"code": "stat", "x": "y"},
        ]
        assert pc._extract_error_text(rows) == "first\nsecond"

    def test_default_severity_is_error(self):
        # When severity is missing we err on the side of "this is real"
        # so transport failures w/o severity still surface.
        rows = [{"code": "error", "data": "boom"}]
        assert pc._extract_error_text(rows) == "boom"


# --- _flatten_numbered ------------------------------------------------------

class TestFlattenNumbered:
    def test_no_numbered_keys_passthrough(self):
        d = {"Change": "1", "Status": "pending"}
        assert pc._flatten_numbered(d) == d

    def test_collapse_numbered_into_list(self):
        d = {
            "Change": "1",
            "Files0": "//depot/a",
            "Files1": "//depot/b",
            "Files2": "//depot/c",
        }
        assert pc._flatten_numbered(d) == {
            "Change": "1",
            "Files": ["//depot/a", "//depot/b", "//depot/c"],
        }

    def test_out_of_order_keys_sorted_by_index(self):
        d = {"Files2": "c", "Files0": "a", "Files1": "b"}
        assert pc._flatten_numbered(d) == {"Files": ["a", "b", "c"]}

    def test_multiple_numbered_groups_independent(self):
        d = {
            "Files0": "//a",
            "Files1": "//b",
            "Jobs0": "job1",
            "Jobs1": "job2",
        }
        assert pc._flatten_numbered(d) == {
            "Files": ["//a", "//b"],
            "Jobs": ["job1", "job2"],
        }

    def test_field_without_digit_not_collapsed(self):
        # "Date" doesn't match the numbered pattern; passes through.
        d = {"Date": "2026/05/17"}
        assert pc._flatten_numbered(d) == {"Date": "2026/05/17"}


# --- _form_dict_to_text -----------------------------------------------------

class TestFormDictToText:
    def test_simple_single_line_field(self):
        out = pc._form_dict_to_text({"Change": "12345"})
        assert "Change: 12345\n" in out

    def test_description_always_multiline_block(self):
        # Description is on the multi-line whitelist so even a single-
        # line value gets the indented-block treatment that matches the
        # `change -o` formatting.
        out = pc._form_dict_to_text({"Description": "one line"})
        assert "Description:\n\tone line\n" in out

    def test_multiline_string_is_indented(self):
        out = pc._form_dict_to_text({"Description": "line 1\nline 2\nline 3"})
        assert "Description:\n\tline 1\n\tline 2\n\tline 3\n" in out

    def test_empty_string_field_renders_empty(self):
        out = pc._form_dict_to_text({"Description": ""})
        # Description gets the block form even when empty.
        assert "Description:\n" in out

    def test_list_field_indented_block(self):
        out = pc._form_dict_to_text({"Files": ["//a", "//b"]})
        assert "Files:\n\t//a\n\t//b\n" in out

    def test_empty_list_renders_header_only(self):
        out = pc._form_dict_to_text({"Files": []})
        assert out.startswith("Files:\n")

    def test_code_meta_keys_dropped(self):
        # `p4 -G` adds code/severity meta keys; the serializer must
        # drop them so `change -i` doesn't bail with "unknown field code".
        out = pc._form_dict_to_text({
            "code": "stat",
            "severity": "0",
            "Change": "12345",
        })
        assert "code" not in out
        assert "severity" not in out
        assert "Change: 12345" in out

    def test_comment_keys_skipped(self):
        # Lines that would start with `#` are comments p4 strips on
        # input; we never emit them.
        out = pc._form_dict_to_text({"#meta": "ignored", "Change": "1"})
        assert "#meta" not in out
        assert "Change: 1" in out

    def test_insertion_order_preserved(self):
        # Python 3.7+ dicts preserve order; the round-trip through
        # `change -o` → modify → `change -i` should keep the layout
        # the user originally saw.
        keys = ["Change", "Date", "Client", "User", "Status", "Description"]
        form = {k: f"v_{k}" if k != "Description" else "body" for k in keys}
        out = pc._form_dict_to_text(form)
        positions = [out.find(k + ":") for k in keys]
        assert positions == sorted(positions)

    def test_empty_description_takes_short_branch(self):
        # Empty Description must produce the header-only `Description:\n`
        # block — never an indented blank line. A previous revision had
        # an `or [""]` fallback in `text.splitlines()` to "handle" the
        # empty case, but the `if not text` short-circuit above always
        # claimed it first, making that branch dead. This test pins the
        # short-branch behavior so a future refactor doesn't regress.
        out = pc._form_dict_to_text({"Description": ""})
        # Header present, no indented blank line.
        assert "Description:\n" in out
        assert "\t\n" not in out, (
            "empty Description must NOT emit `\\t\\n` after the header"
        )

    def test_newline_only_description(self):
        # Edge case neighbouring the removed dead branch: a Description
        # that's literally "\n" should yield one empty indented line
        # (since splitlines("\n") == [""], which iterates exactly once).
        out = pc._form_dict_to_text({"Description": "\n"})
        # Header + one indented empty line; no extra blank lines.
        assert "Description:\n\t\n" in out


# --- P4Service.connect() façade behaviour ----------------------------------

class _RecordingBackend(pc._Backend):
    """Test-only backend that records every façade call without doing I/O.

    Used by the connect() tests to assert that the façade calls the
    backend in the expected order — connect() should always invoke
    configure() (so a re-configure on an already-open connection
    actually lands the new params), but should only invoke connect()
    when not yet connected. The previous two-branch implementation had
    the same configure() call duplicated in both branches, which was
    easy to misread; pin that the new single-call shape behaves
    identically.
    """

    name = "test"

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self._connected = False
        self._port = ""
        self._user = ""
        self._client = ""
        self._charset = ""

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port(self) -> str:
        return self._port

    @property
    def user(self) -> str:
        return self._user

    @property
    def client(self) -> str:
        return self._client

    @property
    def charset(self) -> str:
        return self._charset

    def configure(self, *, port=None, user=None, client=None, charset=None):
        self.events.append(("configure", port, user, client, charset))
        if port is not None: self._port = port
        if user is not None: self._user = user
        if client is not None: self._client = client
        if charset is not None: self._charset = charset

    def connect(self) -> None:
        self.events.append(("connect",))
        self._connected = True

    def disconnect(self) -> None:
        self.events.append(("disconnect",))
        self._connected = False


class TestServiceConnect:
    def test_first_connect_configures_then_opens(self):
        b = _RecordingBackend()
        svc = pc.P4Service(backend=b)
        svc.connect(port="ssl:p4:1666", user="alice", client="ws1")
        assert b.events == [
            ("configure", "ssl:p4:1666", "alice", "ws1", None),
            ("connect",),
        ]
        assert b.connected

    def test_second_connect_reconfigures_without_reopening(self):
        # Profile picker flow: user switches target while already
        # connected. We expect a single configure() call with the new
        # params and NO second connect() — the previous two-branch
        # version did the same but via duplicated `configure()` calls
        # that read confusingly.
        b = _RecordingBackend()
        svc = pc.P4Service(backend=b)
        svc.connect(port="a:1", user="alice", client="ws1")
        svc.connect(port="b:2", user="bob")
        assert b.events == [
            ("configure", "a:1", "alice", "ws1", None),
            ("connect",),
            ("configure", "b:2", "bob", None, None),
        ]
        assert b.connected
        assert b.port == "b:2"
        assert b.user == "bob"
        # client unchanged — None means "leave alone".
        assert b.client == "ws1"

    def test_connect_with_no_args_just_opens(self):
        b = _RecordingBackend()
        svc = pc.P4Service(backend=b)
        svc.connect()
        # Configure still fires (params are all None — harmless) so
        # the backend's source-of-truth is consulted on its own terms.
        assert b.events == [
            ("configure", None, None, None, None),
            ("connect",),
        ]


# --- p4client import surface (used by p4v.py before constructing the app) --

class TestImportSurface:
    """`p4client` must stay importable without the GUI stack.

    `p4v.py::main` imports `P4SetupError` from this module *before*
    constructing `P4VApp`, so it can render a friendly Korean install
    hint when neither backend is usable. That import path must not
    drag `textual`, `rich`, or P4Python in transitively — otherwise
    the friendly-error layer breaks on the exact systems where the
    user is most likely to need it (missing C extensions, no Textual
    install).
    """

    def test_p4client_top_level_imports_are_minimal(self):
        # Parse the module source and assert the top-level imports are
        # only stdlib. (`P4` is imported inside `_PythonBackend.__init__`
        # so it doesn't show up here.) This catches a future contributor
        # who reaches for `from textual import …` at module level by
        # accident — which would break the friendly error path on
        # textual-less installs.
        import ast
        from pathlib import Path
        src = (
            Path(__file__).resolve().parent.parent
            / "p4v_tui" / "p4client.py"
        ).read_text(encoding="utf-8-sig")
        tree = ast.parse(src)
        top_level_modules: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for n in node.names:
                    top_level_modules.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top_level_modules.add(node.module.split(".")[0])
        # Allowed at module load — stdlib + nothing else.
        stdlib_allowed = {
            "io", "marshal", "os", "re", "shutil", "subprocess",
            "sys", "threading", "time",
            "contextlib", "dataclasses", "typing", "__future__",
        }
        unexpected = top_level_modules - stdlib_allowed
        assert not unexpected, (
            f"p4client.py top-level imports must stay stdlib-only "
            f"(found {unexpected!r}). The friendly-error path in p4v.py "
            f"depends on `from p4v_tui.p4client import P4SetupError` "
            f"working on installs that don't have textual / rich / P4."
        )

    def test_p4_setup_error_resolvable(self):
        # The actual call p4v.py makes — verify it works.
        from p4v_tui.p4client import P4SetupError, P4Exception, P4Service
        assert issubclass(P4SetupError, Exception)
        assert issubclass(P4Exception, Exception)
        assert hasattr(P4Service, "connect")


# --- CLI backend read-cache + concurrency (CL 14) --------------------------

class TestCLICache:
    """Idempotent-read TTL cache: hits avoid the subprocess spawn."""

    def _bare_cli_backend(self):
        """Construct _CLIBackend bypassing __init__ probes (no real `p4` needed)."""
        b = pc._CLIBackend.__new__(pc._CLIBackend)
        b._p4_bin = "/usr/bin/false"   # never invoked in cache-hit path
        b._port = b._user = b._client = b._charset = ""
        b._connected = True
        b._version_str = ""
        b._read_cache = {}
        b._read_cache_lock = __import__("threading").Lock()
        return b

    def test_cache_hit_returns_without_invoke(self, monkeypatch):
        """A cache hit must skip `_invoke` entirely — proves the cache
        is actually doing work, not just stuffing rows into a dict."""
        b = self._bare_cli_backend()
        sentinel = [{"userName": "alice", "clientName": "ws"}]
        # Seed the cache directly so the first call is a hit.
        b._cache_put(("info",), sentinel)
        called = {"n": 0}
        def fake_invoke(*a, **kw):
            called["n"] += 1
            return [], ""
        monkeypatch.setattr(b, "_invoke", fake_invoke)
        result = b.run_tagged(("info",))
        assert called["n"] == 0, "cache hit must NOT call _invoke"
        # Defensive copy: result is a fresh list, not the cache's own.
        assert result == sentinel
        assert result is not sentinel
        result.append({"poison": "value"})
        # Re-fetch returns the original (we didn't poison the cache).
        result2 = b.run_tagged(("info",))
        assert result2 == sentinel

    def test_cache_miss_then_hit(self, monkeypatch):
        b = self._bare_cli_backend()
        invocations = []
        def fake_invoke(args, **kw):
            invocations.append(tuple(args))
            return [{"code": b"stat", "userName": b"alice"}], ""
        monkeypatch.setattr(b, "_invoke", fake_invoke)
        # First call — miss → invokes subprocess (faked).
        r1 = b.run_tagged(("info",))
        assert len(invocations) == 1
        # Second call — hit (within TTL) → no new invocation.
        r2 = b.run_tagged(("info",))
        assert len(invocations) == 1
        assert r1 == r2

    def test_cache_ttl_expiry_re_invokes(self, monkeypatch):
        b = self._bare_cli_backend()
        invocations = []
        def fake_invoke(args, **kw):
            invocations.append(tuple(args))
            return [{"code": b"stat"}], ""
        monkeypatch.setattr(b, "_invoke", fake_invoke)
        # Seed via real `run_tagged` so we use the real cache_put path.
        b.run_tagged(("info",))
        assert len(invocations) == 1
        # Forcibly age the entry past the module TTL.
        with b._read_cache_lock:
            key = ("info",)
            _, payload = b._read_cache[key]
            b._read_cache[key] = (
                __import__("time").time() - pc._CLI_READ_CACHE_TTL_S - 1.0,
                payload,
            )
        b.run_tagged(("info",))
        assert len(invocations) == 2, "stale entry must trigger re-invoke"

    def test_non_cacheable_args_always_invoke(self, monkeypatch):
        b = self._bare_cli_backend()
        invocations = []
        def fake_invoke(args, **kw):
            invocations.append(tuple(args))
            return [{"code": b"stat"}], ""
        monkeypatch.setattr(b, "_invoke", fake_invoke)
        # `files //...` is NOT in _CACHEABLE_ARG_HEADS.
        b.run_tagged(("files", "//x/..."))
        b.run_tagged(("files", "//x/..."))
        assert len(invocations) == 2, (
            "non-whitelisted command must NOT be cached"
        )

    def test_invalidate_clears_cache(self, monkeypatch):
        b = self._bare_cli_backend()
        b._cache_put(("info",), [{"x": "y"}])
        b._cache_put(("client", "-o", "ws"), [{"View": "v"}])
        assert len(b._read_cache) == 2
        b.invalidate_read_cache()
        assert len(b._read_cache) == 0

    def test_args_is_cacheable_classifier(self):
        # Whitelist semantics: prefix-match. Each unique full args
        # tuple gets its own cache slot (so `info` and `info -s`
        # cache separately and don't collide).
        assert pc._CLIBackend._args_is_cacheable(("info",))
        assert pc._CLIBackend._args_is_cacheable(("client", "-o", "ws"))
        # Prefix matches → also cacheable (separate slot, by full key):
        assert pc._CLIBackend._args_is_cacheable(("info", "-s"))
        # Not on the whitelist at all:
        assert not pc._CLIBackend._args_is_cacheable(("files",))
        assert not pc._CLIBackend._args_is_cacheable(("change", "-o", "1"))
        assert not pc._CLIBackend._args_is_cacheable(("client",))
        # Only `client -o <name>`; `client -i` (the write) is NOT a
        # cacheable head, and anyway the `client -i` invocation goes
        # through `save_form`, not `run_tagged`.
        assert not pc._CLIBackend._args_is_cacheable(("client", "-i"))


class TestBackendConcurrency:
    """`max_concurrent_calls` declares the semaphore sizing."""

    def test_python_backend_pooled_concurrency(self):
        # A single P4Python connection isn't thread-safe, so the backend
        # keeps a *pool* of independent connections (one per concurrent
        # call) rather than serialising everything through one. Default
        # P4V_PY_CONCURRENCY is 4; tests don't lock it to a specific
        # value because the env var may have been set externally.
        assert pc._PythonBackend.max_concurrent_calls >= 1

    def test_cli_backend_parallel(self):
        # CLI subprocesses are independent. _CLI_CONCURRENCY default
        # is 4; tests don't lock it to a specific value because the
        # env var may have been set externally.
        assert pc._CLIBackend.max_concurrent_calls >= 1

    def test_p4service_semaphore_matches_backend(self):
        # Façade wires the semaphore from the backend's declared
        # concurrency. Use the recording backend (it inherits the
        # default 1) to keep the test backend-free.
        b = _RecordingBackend()
        svc = pc.P4Service(backend=b)
        # BoundedSemaphore exposes its initial value via _initial_value
        # in CPython; checking via repr/acquire+release is portable.
        # We assert by acquiring all permits — should equal the
        # backend's declared count.
        acquired = 0
        try:
            while svc._call_sem.acquire(blocking=False):
                acquired += 1
        finally:
            for _ in range(acquired):
                svc._call_sem.release()
        assert acquired == b.max_concurrent_calls


class TestPythonBackendPool:
    """Connection-pool mechanics for the Python backend.

    These exercise `_acquire` / `_release` / `configure` without a live
    server — leasing a connection doesn't open a socket (that happens
    lazily on first use), so the pool's bookkeeping is testable offline.
    They do need P4Python importable, since `_PythonBackend.__init__`
    builds a template `P4.P4()` to read env-resolved defaults.
    """

    @pytest.fixture
    def backend(self, has_p4python):
        if not has_p4python:
            pytest.skip("P4Python not installed")
        b = pc._PythonBackend()
        b.connect()
        yield b
        b.disconnect()

    def test_concurrent_leases_are_distinct_connections(self, backend):
        # The whole point of the pool: N concurrent calls each get their
        # own P4 object so one slow command can't block the others.
        leased = [backend._acquire() for _ in range(4)]
        try:
            assert len({id(c.p4) for c in leased}) == 4
        finally:
            for c in leased:
                backend._release(c, broken=False)

    def test_idle_pool_capped_and_reused(self, backend):
        leased = [backend._acquire() for _ in range(backend.max_concurrent_calls)]
        ids = {id(c.p4) for c in leased}
        for c in leased:
            backend._release(c, broken=False)
        # Idle pool never grows past the concurrency cap.
        assert len(backend._idle) == backend.max_concurrent_calls
        # A subsequent lease reuses a pooled connection rather than
        # building a fresh one.
        reused = backend._acquire()
        try:
            assert id(reused.p4) in ids
        finally:
            backend._release(reused, broken=False)

    def test_broken_connection_is_not_repooled(self, backend):
        conn = backend._acquire()
        before = len(backend._idle)
        backend._release(conn, broken=True)
        assert len(backend._idle) == before

    def test_configure_change_flushes_idle_and_bumps_gen(self, backend):
        c = backend._acquire()
        backend._release(c, broken=False)
        assert backend._idle  # something is pooled
        gen_before = backend._gen
        backend.configure(port="ssl:does-not-exist:1666")
        assert backend._gen == gen_before + 1
        assert backend._idle == []          # stale connections dropped
        assert backend.port == "ssl:does-not-exist:1666"

    def test_configure_noop_does_not_bump_gen(self, backend):
        # Re-applying the same params (the reconnect path passes the
        # current values back) must not churn the pool.
        gen_before = backend._gen
        backend.configure(
            port=backend.port, user=backend.user,
            client=backend.client, charset=backend.charset,
        )
        assert backend._gen == gen_before


# --- FileViewerModal line-number prefix (CL 16) ----------------------------

class TestFileViewerLineNumbers:
    """Exercise `_apply_line_numbers` directly without booting Textual.

    The toggle's user-visible effect (footer hint, RichLog re-render)
    needs a live app context — skipped here. The numeric prefix logic
    is pure so we test it in isolation.
    """

    def _make_viewer(self, line_numbers: bool = True):
        # FileViewerModal subclasses ModalScreen which needs a Textual
        # app context to instantiate; bypass __init__ and set only the
        # one attribute the method under test reads.
        from p4v_tui.widgets.file_viewer import FileViewerModal
        v = FileViewerModal.__new__(FileViewerModal)
        v._line_numbers = line_numbers
        return v

    def test_off_passes_through_unchanged(self):
        v = self._make_viewer(line_numbers=False)
        lines = ["a", "b", "c"]
        out = v._apply_line_numbers(lines)
        # Returned list is a copy, not the original (so caller mutation
        # doesn't poison the source).
        assert out == lines
        assert out is not lines

    def test_empty_input_returns_empty(self):
        v = self._make_viewer(line_numbers=True)
        assert v._apply_line_numbers([]) == []

    def test_prefix_width_auto_fits_largest_number(self):
        v = self._make_viewer(line_numbers=True)
        # 12 lines → max number "12" (width 2), but the minimum width
        # is 3 so we don't see the prefix dance on toggling a small
        # file. So expected prefix is "  1  ", "  2  ", … "  12 ".
        out = v._apply_line_numbers([f"line{i}" for i in range(12)])
        # Each output is a Rich Text object — pull the plain string.
        plain = [str(x) for x in out]
        assert plain[0].startswith("  1  ")
        assert plain[-1].startswith(" 12  ")

    def test_prefix_width_grows_for_large_files(self):
        v = self._make_viewer(line_numbers=True)
        out = v._apply_line_numbers(["x" for _ in range(1234)])
        plain = [str(x) for x in out]
        # 1234 → width 4 → prefix " 1234  " for last; "    1  " for first
        assert plain[0].startswith("   1  ")
        assert plain[-1].startswith("1234  ")

    def test_preserves_rich_text_styling(self):
        """A line that arrives as a styled Rich Text must keep its
        styles after the line-number prefix is prepended."""
        from rich.text import Text
        v = self._make_viewer(line_numbers=True)
        styled = Text("hello", style="bold red")
        out = v._apply_line_numbers([styled])
        wrapped = out[0]
        # The wrapped Text now has TWO spans: the dim prefix and the
        # original bold red. Verify via rendering — the original
        # "hello" should still be in there as a styled span.
        assert "hello" in str(wrapped)
        # Span scan: at least one span must carry the bold-red style.
        styles = [str(span.style) for span in wrapped.spans]
        assert any("bold" in s and "red" in s for s in styles), (
            f"styled span lost; got spans={styles!r}"
        )

    def test_default_constructor_arg(self):
        """`line_numbers=True` is the default for the base FileViewerModal.

        Verify by inspecting the constructor signature — we can't
        instantiate without a Textual app, but the inspect machinery
        works on the unbound function.
        """
        import inspect
        from p4v_tui.widgets.file_viewer import FileViewerModal
        sig = inspect.signature(FileViewerModal.__init__)
        assert sig.parameters["line_numbers"].default is True

    def test_logentryviewer_defaults_to_off(self):
        """LogEntryViewerModal opts out of numbering by default.

        Log entries already have their own row-by-row index, so an
        additional left-margin number column would be visual noise.
        """
        import inspect
        from p4v_tui.widgets.log_entry_viewer import (
            LogEntryViewerModal,
        )
        # Locate the `super().__init__(…, line_numbers=…)` call. The
        # simplest check: source inspection.
        src = inspect.getsource(LogEntryViewerModal.__init__)
        assert "line_numbers=False" in src, (
            "LogEntryViewerModal must opt out of line numbers by default "
            "— log entries have their own row index already."
        )


# --- _CLIBackend._invoke timeout -------------------------------------------

def _write_slow_p4(tmp_path):
    """Drop a fake `p4` binary that just sleeps. Returns the path.

    Used to exercise the per-call timeout in `_CLIBackend._invoke`
    without spawning the real p4 (which would actually contact a
    server). The shim is hand-written per platform so we don't need
    /usr/bin/sleep or its Windows equivalent — anything that blocks
    longer than the test's timeout will do.
    """
    if sys.platform == "win32":
        # PowerShell single-liner that sleeps; .ps1 is invoked via
        # `powershell -File` so use a .bat wrapper.
        bat = tmp_path / "slow_p4.bat"
        bat.write_text(
            "@echo off\r\n"
            "powershell -NoProfile -Command \"Start-Sleep -Seconds 60\"\r\n",
            encoding="utf-8",
        )
        return str(bat)
    shim = tmp_path / "slow_p4"
    shim.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    shim.chmod(0o755)
    return str(shim)


def _make_cli_backend_with_bin(p4_bin: str) -> "pc._CLIBackend":
    """Build a _CLIBackend pointing at an arbitrary binary, without
    going through `__init__` (which would call shutil.which + probe).

    The probe paths (`_snapshot_p4_set`, `_probe_version`) would each
    spawn a 60 s sleep against our fake binary, blocking the test
    setup for ages. Skipping `__init__` keeps the test fast.
    """
    b = pc._CLIBackend.__new__(pc._CLIBackend)
    b._p4_bin = p4_bin
    b._port = b._user = b._client = b._charset = ""
    b._connected = True
    b._version_str = ""
    return b


class TestInvokeTimeout:
    def test_invoke_raises_on_timeout(self, tmp_path):
        """A subprocess that exceeds the timeout must surface as P4Exception."""
        slow = _write_slow_p4(tmp_path)
        backend = _make_cli_backend_with_bin(slow)
        started = time.monotonic()
        with pytest.raises(pc.P4Exception) as ei:
            backend._invoke(("info",), timeout=0.5)
        elapsed = time.monotonic() - started
        # Allow generous wall-clock budget (process spawn + kill +
        # drain takes a moment on slow CI) but well below the 60s
        # fake-binary sleep so we know the timeout fired, not the
        # sleep exiting.
        assert elapsed < 10.0, (
            f"timeout should fire fast (was {elapsed:.2f}s)"
        )
        msg = str(ei.value)
        assert "timed out" in msg, f"expected timeout message, got: {msg}"
        assert "P4V_CLI_TIMEOUT" in msg, (
            f"expected hint about env var override, got: {msg}"
        )

    def test_default_timeout_from_env(self, monkeypatch):
        """`P4V_CLI_TIMEOUT` overrides the module-level default at import time."""
        # The constant is read at module load, so we verify the parse
        # path tolerates a bad value without crashing the import.
        # (We can't re-trigger module load cleanly inside one test
        # process, so this is a sanity check on the bounded fallback.)
        assert pc._DEFAULT_CLI_TIMEOUT_S > 0
        assert isinstance(pc._DEFAULT_CLI_TIMEOUT_S, float)


class _RecRunBackend(_RecordingBackend):
    """Recording backend that also captures run_tagged/run_text calls."""

    def run_tagged(self, args):
        self.events.append(("run_tagged", tuple(args)))
        return []

    def run_text(self, args):
        self.events.append(("run_text", tuple(args)))
        return ""


class TestOptionLikePathGuard:
    """Audit F4 — path args that start with '-' must be refused before
    dispatch (p4 has no universal '--' terminator), so a crafted path
    can't be parsed as a flag."""

    def test_helper_classification(self):
        assert pc.is_option_like_path("-rf")
        assert pc.is_option_like_path("--streamviews")
        assert not pc.is_option_like_path("//depot/x")
        assert not pc.is_option_like_path("/local/x")
        assert not pc.is_option_like_path("~/x")
        assert not pc.is_option_like_path("@label")
        assert not pc.is_option_like_path(123)
        assert not pc.is_option_like_path(None)

    def test_option_like_paths_never_reach_backend(self):
        b = _RecRunBackend()
        svc = pc.P4Service(backend=b)
        svc.connect()
        assert svc.dirs("-rf") == []
        assert svc.files("-A") == []
        assert svc.fstat("--all") == []
        assert svc.filelog("-m9999") == []
        assert svc.where("-x") is None
        # The backend's run_tagged must NOT have been invoked for any.
        assert not any(e[0] == "run_tagged" for e in b.events)

    def test_normal_paths_reach_backend(self):
        b = _RecRunBackend()
        svc = pc.P4Service(backend=b)
        svc.connect()
        svc.dirs("//depot/*")
        svc.files("//depot/*")
        assert ("run_tagged", ("dirs", "//depot/*")) in b.events
        assert ("run_tagged", ("files", "-e", "//depot/*")) in b.events


class _FlakyBackend(pc._Backend):
    """Backend that raises a connection error on the first N run calls
    then succeeds — exercises the resilient runner's retry/recover path."""

    name = "flaky"

    def __init__(self, fail_times: int) -> None:
        self._connected = True
        self._fail = fail_times
        self.calls = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port(self) -> str:
        return "ssl:host:1666"

    @property
    def user(self) -> str:
        return "u"

    @property
    def client(self) -> str:
        return "c"

    @property
    def charset(self) -> str:
        return ""

    def configure(self, **kwargs):
        pass

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def run_tagged(self, args):
        self.calls += 1
        if self.calls <= self._fail:
            self._connected = False
            raise pc.P4Exception("TCP receive failed")  # connection fragment
        return [{"ok": "1"}]

    def run_text(self, args):
        return self.run_tagged(args)


class TestReconnectCallbacks:
    def test_retry_then_recover_fires_service_callbacks(self):
        b = _FlakyBackend(fail_times=2)
        svc = pc.P4Service(backend=b)
        retries: list[tuple[int, int]] = []
        recovered: list[bool] = []
        svc._on_retry = lambda a, m, e: retries.append((a, m))
        svc._on_recover = lambda: recovered.append(True)
        result = svc._run_resilient(("info",), base_delay=0.0, max_attempts=5)
        assert result == [{"ok": "1"}]
        assert len(retries) == 2       # two failed attempts -> two retries
        assert recovered == [True]     # recovered exactly once

    def test_no_recover_when_no_retry_needed(self):
        b = _FlakyBackend(fail_times=0)
        svc = pc.P4Service(backend=b)
        recovered: list[bool] = []
        svc._on_recover = lambda: recovered.append(True)
        svc._run_resilient(("info",), base_delay=0.0)
        assert recovered == []         # clean call -> no recover hook

    def test_per_call_on_retry_overrides_service_default(self):
        b = _FlakyBackend(fail_times=1)
        svc = pc.P4Service(backend=b)
        default: list[int] = []
        percall: list[int] = []
        svc._on_retry = lambda a, m, e: default.append(1)
        svc._run_resilient(
            ("info",),
            on_retry=lambda a, m, e: percall.append(1),
            base_delay=0.0,
        )
        assert percall == [1]
        assert default == []           # service default unused when per-call set
