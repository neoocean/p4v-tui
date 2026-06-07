"""Synthetic Perforce backend for manual/README screenshots.

Implements the ``p4client._Backend`` contract with hand-written demo data
so the real ``P4VApp`` renders a believable workspace **without ever
touching a live server**. Every depot path, client, user and host here is
fictional (``//depot/demo/...``, ``alice``, ``alice-mbp``) — no personal
depot or workspace tree can leak into a committed image. The generator
(`gen_screenshots.py`) injects this via ``P4Service(backend=DemoBackend())``.

Dispatch is on the p4 argv (``run_tagged``/``run_text``/``fetch_form``),
which is the single seam every façade convenience method funnels through —
so covering the verbs the screenshotted screens issue (``info``,
``changes``, ``opened``, ``describe``, ``depots``, ``dirs``, ``files``,
``fstat``, ``filelog``, ``print``, ``where``) is enough. Returned shapes
match P4Python's (multi-value fields as lists), which the CLI-path
``_flatten_numbered`` then treats as a no-op.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from p4v_tui.p4client import _Backend  # noqa: E402

# ── fictional identity (shown in the ConnectionBar) ──────────────────────
PORT = "ssl:perforce.example.com:1666"
USER = "alice"
CLIENT = "alice-mbp"
CLIENT_ROOT = "/Users/alice/work/demo"
CLIENT_HOST = "alice-mbp"
OTHER_CLIENT = "alice-linux"          # a second workspace of the same user

# ── fictional depot layout ───────────────────────────────────────────────
# Directory tree (depot side). Leaf lists are file basenames with a head rev.
_DIRS = {
    "//": ["//depot"],
    "//depot": ["//depot/demo"],
    "//depot/demo": ["//depot/demo/src", "//depot/demo/docs",
                     "//depot/demo/assets"],
    "//depot/demo/src": [],
    "//depot/demo/docs": [],
    "//depot/demo/assets": [],
}
# files keyed by parent dir → list of (basename, headRev, haveRev, openAction)
#   openAction None = synced/closed; "edit"/"add" = open in a pending CL;
#   haveRev < headRev (and not open) renders as out-of-date.
_FILES = {
    "//depot/demo/src": [
        ("app.py", 7, 7, "edit"),
        ("config.py", 4, 4, "edit"),
        ("search_index.py", 9, 9, None),
        ("utils.py", 3, 2, None),          # out of date (have 2 < head 3)
        ("p4client.py", 12, 12, None),
    ],
    "//depot/demo/docs": [
        ("README.md", 5, 5, None),
        ("MANUAL.md", 2, 2, "add"),
        ("CHANGES.md", 8, 8, None),
    ],
    "//depot/demo/assets": [
        ("logo.png", 1, 1, None),
        ("theme.tcss", 6, 6, None),
    ],
}

# ── changelists ──────────────────────────────────────────────────────────
# Pending: two on the current client (alice-mbp), one on another of the
# user's workspaces (alice-linux) — that one renders dim + "↗" marker.
_PENDING = [
    {"change": "4231", "user": USER, "client": CLIENT,
     "time": "1717900200",
     "desc": "Add input validation to the config loader\n\n"
             "Reject malformed TOML early and surface a friendly\n"
             "message instead of a traceback."},
    {"change": "4228", "user": USER, "client": CLIENT,
     "time": "1717820100",
     "desc": "Refactor search index: split build vs incremental update"},
    {"change": "4219", "user": USER, "client": OTHER_CLIENT,
     "time": "1717650000",
     "desc": "WIP: experiment with on-disk LRU cache for fstat"},
]
_SUBMITTED = [
    {"change": "4205", "user": USER, "client": CLIENT, "time": "1717560000",
     "desc": "Fix CJK column truncation in the pending table"},
    {"change": "4198", "user": "bob", "client": "bob-ws", "time": "1717470000",
     "desc": "Add side-by-side diff modal for submitted changelists"},
    {"change": "4187", "user": USER, "client": CLIENT, "time": "1717383600",
     "desc": "Chunked, resumable sync with on-disk progress state"},
    {"change": "4160", "user": "carol", "client": "carol-ws",
     "time": "1717210800", "desc": "Initial import of the demo project"},
]
# Files per changelist (depotFile, rev, action, type) for the detail pane
# and `describe`.
_CL_FILES = {
    "4231": [("//depot/demo/src/config.py", "5", "edit", "text"),
             ("//depot/demo/docs/MANUAL.md", "1", "add", "text")],
    "4228": [("//depot/demo/src/search_index.py", "9", "edit", "text"),
             ("//depot/demo/src/app.py", "7", "edit", "text")],
    "4219": [("//depot/demo/src/p4client.py", "12", "edit", "text")],
    "4205": [("//depot/demo/src/app.py", "6", "edit", "text"),
             ("//depot/demo/src/utils.py", "3", "edit", "text")],
    "4198": [("//depot/demo/src/sxs_diff.py", "1", "add", "text")],
    "4187": [("//depot/demo/src/sync_job.py", "1", "add", "text"),
             ("//depot/demo/src/chunking.py", "1", "add", "text")],
    "4160": [("//depot/demo/src/app.py", "1", "add", "text"),
             ("//depot/demo/docs/README.md", "1", "add", "text")],
}

_SAMPLE_FILE = '''\
"""Demo application entry point."""
from __future__ import annotations

import sys

from .config import load_config


def main(argv: list[str]) -> int:
    cfg = load_config()
    if cfg.error:
        print(f"config error: {cfg.error}", file=sys.stderr)
        return 1
    print(f"connected to {cfg.connection.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


def _now_minus(days: int) -> str:
    # Deterministic timestamps are baked into the data above; this helper
    # is unused at runtime but documents the intent for future edits.
    return str(1717900200 - days * 86400)


class DemoBackend(_Backend):
    name = "demo"
    max_concurrent_calls = 1

    def __init__(self) -> None:
        self._connected = False

    # --- connection (all no-ops; nothing real is opened) ------------------
    @property
    def port(self) -> str:
        return PORT

    @property
    def user(self) -> str:
        return USER

    @property
    def client(self) -> str:
        return CLIENT

    @property
    def charset(self) -> str:
        return "utf8"

    @property
    def connected(self) -> bool:
        return self._connected

    def configure(self, *, port=None, user=None, client=None,
                  charset=None) -> None:
        pass

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def version_info(self) -> str:
        return "demo backend (synthetic data for screenshots)"

    def login_status(self) -> dict | None:
        return {"User": USER, "Ticket": "valid"}

    # --- the one real seam: dispatch on the p4 argv -----------------------
    def run_tagged(self, args):
        a = [str(x) for x in args]
        cmd = a[0] if a else ""

        if cmd == "info":
            return [{
                "userName": USER, "clientName": CLIENT,
                "serverAddress": "perforce.example.com:1666",
                "serverVersion":
                    "P4D/LINUX26AARCH64/2024.1/2625466 (2024/06/20)",
                "serverUptime": "412:33:07",
                "clientRoot": CLIENT_ROOT, "clientHost": CLIENT_HOST,
            }]

        if cmd == "depots":
            return [{"name": "depot", "type": "local",
                     "map": "/p4/depot/..."}]

        if cmd == "dirs":
            glob = a[1] if len(a) > 1 else ""
            parent = glob[:-2] if glob.endswith("/*") else glob
            ns = _ns_of(parent)              # preserve client vs depot namespace
            depot_parent = _to_depot(parent)
            return [{"dir": _from_depot(d, ns)}
                    for d in _DIRS.get(depot_parent, [])]

        if cmd == "files":
            glob = a[-1]
            if glob.endswith("/...") or glob == "//...":
                # Recursive enumerate (search-index build). Return every
                # demo file under the prefix, in depot namespace.
                prefix = "" if glob == "//..." else glob[:-4]
                out = []
                for parent, files in _FILES.items():
                    if not parent.startswith(prefix):
                        continue
                    for base, head, _have, _act in files:
                        out.append({"depotFile": f"{parent}/{base}",
                                    "rev": str(head), "type": "text"})
                return out
            # files -e <glob> (depot tree, single directory)
            parent = glob[:-2] if glob.endswith("/*") else glob
            ns = _ns_of(parent)
            depot_parent = _to_depot(parent)
            out = []
            for base, head, _have, _act in _FILES.get(depot_parent, []):
                out.append({"depotFile": _from_depot(
                    f"{depot_parent}/{base}", ns),
                    "rev": str(head), "type": "text", "action": "edit"})
            return out

        if cmd == "counter":
            return [{"counter": "change", "value": "4231"}]

        if cmd == "fstat":
            glob = a[-1]
            parent = glob[:-2] if glob.endswith("/*") else glob
            ns = _ns_of(parent)              # client path → map back to depot
            depot_parent = _to_depot(parent)
            out = []
            for base, head, have, act in _FILES.get(depot_parent, []):
                depot = f"{depot_parent}/{base}"
                row = {
                    "depotFile": depot,
                    "clientFile": _from_depot(depot, ns)
                    if ns != "//depot" else depot.replace(
                        "//depot", f"//{CLIENT}"),
                    "headRev": str(head), "haveRev": str(have),
                    "headAction": "edit", "headType": "text",
                }
                if act:
                    row["action"] = act
                    row["change"] = _open_change_for(depot)
                out.append(row)
            return out

        if cmd == "changes":
            return self._changes(a)

        if cmd == "opened":
            # opened -c <change>
            change = a[-1]
            if change == "default":
                return []
            return [
                {"depotFile": df, "rev": rev, "action": act, "type": typ,
                 "change": change, "client": CLIENT}
                for (df, rev, act, typ) in _CL_FILES.get(change, [])
            ]

        if cmd == "describe":
            change = a[-1]
            files = _CL_FILES.get(change, [])
            base = _change_by_num(change)
            return [{
                "change": change, "user": base.get("user", USER),
                "client": base.get("client", CLIENT),
                "time": base.get("time", "1717900200"),
                "desc": base.get("desc", ""),
                "status": "submitted" if change in
                          {c["change"] for c in _SUBMITTED} else "pending",
                "depotFile": [f[0] for f in files],
                "rev": [f[1] for f in files],
                "action": [f[2] for f in files],
                "type": [f[3] for f in files],
            }]

        if cmd == "filelog":
            depot = a[-1]
            return [self._filelog(depot)]

        if cmd == "print":
            depot = a[-1]
            return [{"depotFile": depot, "rev": "7", "type": "text"},
                    _SAMPLE_FILE]

        if cmd == "where":
            depot = a[-1]
            return [{"depotFile": depot,
                     "clientFile": depot.replace("//depot", f"//{CLIENT}"),
                     "path": depot.replace("//depot", CLIENT_ROOT)}]

        if cmd == "labels":
            return [{"label": "release-1.0"}, {"label": "nightly"}]

        # Unknown verb → empty result (the app degrades gracefully).
        return []

    def run_text(self, args):
        a = [str(x) for x in args]
        if a and a[0] == "describe":
            return _SAMPLE_DIFF
        if a and a[0] == "diff":
            return _SAMPLE_DIFF
        return ""

    def fetch_form(self, kind, key=None):
        if kind == "client":
            return {
                "Client": CLIENT, "Owner": USER, "Host": CLIENT_HOST,
                "Root": CLIENT_ROOT,
                "View": [f"//depot/demo/... //{CLIENT}/demo/..."],
            }
        if kind == "change":
            base = _change_by_num(str(key)) if key else {}
            return {
                "Change": str(key) if key else "new",
                "User": USER, "Client": CLIENT, "Status": "pending",
                "Description": base.get("desc", ""),
                "Files": [f[0] for f in _CL_FILES.get(str(key), [])],
            }
        return {}

    def save_form(self, kind, form, *, force=False):
        return ["Change 9999 created."]

    def grep_stream(self, pattern, scope, on_match, cancelled, *,
                    case_insensitive, max_matches):
        return 0

    # --- helpers ----------------------------------------------------------
    def _changes(self, a):
        if "-s" in a:
            status = a[a.index("-s") + 1]
            rows = _PENDING if status == "pending" else _SUBMITTED
            if "-u" in a:                       # cross-workspace pending
                return list(rows)
            if "-c" in a:                       # current client only
                cli = a[a.index("-c") + 1]
                return [r for r in rows if r.get("client") == cli]
            return list(rows)
        # folder history: `changes -L -m N //depot/...`
        return list(_SUBMITTED)

    def _filelog(self, depot):
        # Synthesise a short revision history (newest first).
        revs, changes, actions, times, users, clients, types, descs = (
            [], [], [], [], [], [], [], [])
        history = [
            ("7", "4231", "edit", "1717900200", USER, "Add input validation"),
            ("6", "4205", "edit", "1717560000", USER, "Fix CJK truncation"),
            ("5", "4187", "edit", "1717383600", USER, "Chunked sync"),
            ("4", "4160", "add", "1717210800", "carol", "Initial import"),
        ]
        for rev, ch, act, t, u, d in history:
            revs.append(rev); changes.append(ch); actions.append(act)
            times.append(t); users.append(u); clients.append(CLIENT)
            types.append("text"); descs.append(d)
        return {"depotFile": depot, "rev": revs, "change": changes,
                "action": actions, "time": times, "user": users,
                "client": clients, "type": types, "desc": descs}


_SAMPLE_DIFF = """\
==== //depot/demo/src/config.py#5 (text) ====

@@ -12,6 +12,9 @@ def load_config(path):
     data = tomllib.loads(text)
-    return Config(**data)
+    cfg = Config(**data)
+    if not cfg.connection.port:
+        cfg.error = "no P4PORT configured"
+    return cfg
"""


def _ns_of(path: str) -> str:
    """Return the namespace root of ``path`` (a client root or ``//depot``)."""
    for pfx in (f"//{CLIENT}", f"//{OTHER_CLIENT}"):
        if path.startswith(pfx):
            return pfx
    return "//depot"


def _to_depot(path: str) -> str:
    for pfx in (f"//{CLIENT}", f"//{OTHER_CLIENT}"):
        if path.startswith(pfx):
            return "//depot" + path[len(pfx):]
    return path


def _from_depot(depot_path: str, ns_prefix: str) -> str:
    """Translate a ``//depot/...`` path back into ``ns_prefix``'s namespace."""
    if ns_prefix == "//depot" or not depot_path.startswith("//depot"):
        return depot_path
    return ns_prefix + depot_path[len("//depot"):]


def _open_change_for(depot: str) -> str:
    for ch, files in _CL_FILES.items():
        if any(f[0] == depot for f in files):
            return ch
    return "4231"


def _change_by_num(num: str) -> dict:
    for r in _PENDING + _SUBMITTED:
        if r["change"] == num:
            return r
    return {}
