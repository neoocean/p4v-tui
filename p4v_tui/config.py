"""Configuration loading for p4v-tui.

A TOML file lets users pin server / workspace details without leaning on
shell env or ``P4CONFIG``. Any field absent from the file falls back to the
existing P4 environment lookup, so the previous env-only workflow keeps
working unchanged.

Search order (first match wins):
    ./p4v-tui.toml
    ./.p4v-tui.toml
    ~/.p4v-tui.toml
    ~/.config/p4v-tui/config.toml
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .chunking import ChunkingConfig, ChunkingStrategy


SEARCH_PATHS = (
    Path.cwd() / "p4v-tui.toml",
    Path.cwd() / ".p4v-tui.toml",
    Path.home() / ".p4v-tui.toml",
    Path.home() / ".config" / "p4v-tui" / "config.toml",
)


@dataclass
class ConnectionConfig:
    port: str | None = None
    user: str | None = None
    client: str | None = None
    charset: str | None = None
    name: str | None = None  # display label when picking among profiles


@dataclass
class ExternalEditor:
    """One entry in the Open With… picker.

    ``command`` is the executable (resolved via PATH or absolute);
    ``args`` is a template — see :func:`fs_actions.open_with_external`
    for placeholder syntax. Empty template becomes ``"{path}"``.
    """
    name: str
    command: str
    args: str = ""


@dataclass
class MacroStep:
    """Single step in a user-defined macro.

    ``kind`` selects the operation; the remaining fields are
    interpreted per-kind:

      kind="p4"   args=[…]   — run a raw ``p4 …`` command (no
                               default-CL safety — caller is
                               expected to embed ``-c <CL>`` etc.
                               where appropriate).
      kind="sync"             — chunked + resumable sync (target).
      kind="notify" message=  — surface a toast (useful between
                                steps as a "checkpoint reached"
                                signal).
    """
    kind: str = "p4"
    args: list[str] = field(default_factory=list)
    target: str | None = None
    message: str | None = None


@dataclass
class MacroConfig:
    """User-defined macro: a name + ordered list of steps.

    Macros are loaded from ``[[macro]]`` blocks in the TOML config
    and surfaced both in the "Run Macro…" picker and, when ``key``
    is set, as a direct global keybinding registered at App
    construction time. The key string follows Textual's binding
    syntax (e.g. ``"f9"``, ``"alt+1"``, ``"ctrl+shift+r"``); see
    ``textual.binding`` for the full grammar. Stays optional so
    macros without keys still work via the picker.
    """
    name: str
    steps: list[MacroStep] = field(default_factory=list)
    description: str | None = None
    key: str | None = None


@dataclass
class SwarmConfig:
    """Settings for building Swarm review URLs from depot paths.

    ``base_url`` is the public Swarm host (e.g. ``http://swarm.example``).
    Per-team URL pattern: ``{base}/files{depot_path_no_leading_slash}?v={rev}``.
    """
    base_url: str | None = None


@dataclass
class JiraConfig:
    """Settings for linking changelists to Jira issues.

    ``base_url`` is the Jira host (e.g. ``https://jira.example``); the
    browse URL is ``{base}/browse/{KEY}``. ``projects`` optionally
    restricts which key prefixes count, filtering look-alikes (UTF-8…).
    The whole feature is inert when ``base_url`` is unset.
    """
    base_url: str | None = None
    projects: list[str] = field(default_factory=list)
    # Depot-path prefix → Jira project key. The CL's files pick the
    # project(s) at submit time (longest-prefix match). Empty = use the
    # flat ``projects`` list (or accept any key when that's empty too).
    path_projects: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    connection: ConnectionConfig
    profiles: list[ConnectionConfig]  # explicit [[profile]] entries
    swarm: SwarmConfig
    jira: JiraConfig = field(default_factory=JiraConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    external_editors: list[ExternalEditor] = field(default_factory=list)
    macros: list[MacroConfig] = field(default_factory=list)
    source: Path | None = None  # which file we loaded from, or None if env-only
    error: str | None = None    # parse error message, if any

    @classmethod
    def empty(cls) -> "Config":
        return cls(
            connection=ConnectionConfig(),
            profiles=[],
            swarm=SwarmConfig(),
            jira=JiraConfig(),
            chunking=ChunkingConfig(),
            external_editors=[],
            macros=[],
        )


def load_config(explicit_path: str | Path | None = None) -> Config:
    """Return a :class:`Config` loaded from the first matching TOML file.

    If ``explicit_path`` is given, only that path is consulted. If no file is
    found (or the file fails to parse), an empty config is returned and
    ``error`` may be set so callers can surface the problem to the user.
    """
    paths: tuple[Path, ...]
    if explicit_path is not None:
        paths = (Path(explicit_path),)
    else:
        paths = SEARCH_PATHS

    for path in paths:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        try:
            # Read bytes then strip a leading UTF-8 BOM before handing
            # to tomllib. Perforce can prepend a BOM on sync when a
            # client's P4CHARSET is utf8-bom/auto and the depot file
            # is headType=unicode, even if the depot bytes contain
            # no BOM. tomllib (stdlib, intentionally strict) rejects
            # that BOM as "Invalid statement at line 1, column 1".
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            data = tomllib.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
            cfg = Config.empty()
            cfg.error = f"failed to parse {path}: {e}"
            return cfg
        conn_raw = data.get("connection", {}) or {}
        profile_raw = data.get("profile", []) or []
        # Support either [[profile]] (TOML array of tables) or
        # singular [profile] table — the parser produces a list either
        # way for the array form, and we coerce a single table to a
        # one-element list.
        if isinstance(profile_raw, dict):
            profile_raw = [profile_raw]
        profiles = [
            ConnectionConfig(
                name=p.get("name"),
                port=p.get("port"),
                user=p.get("user"),
                client=p.get("client"),
                charset=p.get("charset"),
            )
            for p in profile_raw
            if isinstance(p, dict) and p.get("port")
        ]
        swarm_raw = data.get("swarm", {}) or {}
        swarm = SwarmConfig(
            base_url=swarm_raw.get("base_url"),
        )
        jira_raw = data.get("jira", {}) or {}
        jira_projects_raw = jira_raw.get("projects", []) or []
        jira_path_raw = jira_raw.get("path_projects", {}) or {}
        jira = JiraConfig(
            base_url=jira_raw.get("base_url"),
            projects=[
                str(p) for p in jira_projects_raw
                if isinstance(jira_projects_raw, list) and str(p).strip()
            ],
            path_projects={
                str(k): str(v)
                for k, v in jira_path_raw.items()
                if isinstance(jira_path_raw, dict) and str(k).strip() and str(v).strip()
            },
        )
        chunking = _parse_chunking(data.get("chunking", {}) or {})
        editors_raw = data.get("external_editor", []) or []
        if isinstance(editors_raw, dict):
            editors_raw = [editors_raw]
        external_editors = [
            ExternalEditor(
                name=str(e.get("name") or e.get("command") or "editor"),
                command=str(e.get("command") or ""),
                args=str(e.get("args") or ""),
            )
            for e in editors_raw
            if isinstance(e, dict) and e.get("command")
        ]
        macros_raw = data.get("macro", []) or []
        if isinstance(macros_raw, dict):
            macros_raw = [macros_raw]
        macros: list[MacroConfig] = []
        for m in macros_raw:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or "").strip()
            if not name:
                continue
            steps_raw = m.get("steps") or []
            steps: list[MacroStep] = []
            for s in steps_raw if isinstance(steps_raw, list) else []:
                if not isinstance(s, dict):
                    continue
                kind = str(s.get("kind") or "p4")
                args_raw = s.get("args") or []
                args = [str(a) for a in args_raw] if isinstance(args_raw, list) else []
                steps.append(MacroStep(
                    kind=kind,
                    args=args,
                    target=str(s["target"]) if s.get("target") else None,
                    message=str(s["message"]) if s.get("message") else None,
                ))
            if not steps:
                continue
            macros.append(MacroConfig(
                name=name,
                steps=steps,
                description=(
                    str(m["description"]) if m.get("description") else None
                ),
                key=str(m["key"]).strip() if m.get("key") else None,
            ))
        return Config(
            connection=ConnectionConfig(
                port=conn_raw.get("port"),
                user=conn_raw.get("user"),
                client=conn_raw.get("client"),
                charset=conn_raw.get("charset"),
            ),
            profiles=profiles,
            swarm=swarm,
            jira=jira,
            chunking=chunking,
            external_editors=external_editors,
            macros=macros,
            source=path,
        )
    return Config.empty()


def write_config(cfg: Config, path: str | Path) -> Path:
    """Serialize ``cfg`` back to a TOML file at ``path``.

    Python 3.12's stdlib ships ``tomllib`` (read-only) but no writer,
    so we emit the small subset of TOML the schema actually uses
    rather than pulling in a third-party dep. Round-trips cleanly
    through :func:`load_config`.

    Existing comments / unrelated keys in the file are NOT preserved
    — the file is rewritten from the in-memory model. Callers that
    care about user comments should warn before overwriting.
    """
    out_path = Path(path)
    chunks: list[str] = [
        "# p4v-tui configuration. Edited by the in-app Preferences UI.",
        "# Hand-edits to this file are also fine — see p4v-tui.toml.example",
        "# for the full reference of every supported key.",
        "",
    ]

    def _emit_str(key: str, value: str | None) -> str | None:
        if not value:
            return None
        # Escape the bare minimum for TOML basic strings.
        escaped = value.replace("\\", "\\\\").replace("\"", "\\\"")
        return f'{key} = "{escaped}"'

    def _emit_int(key: str, value: int | None) -> str | None:
        if value is None:
            return None
        return f"{key} = {int(value)}"

    # [connection] — only emit if at least one field is set, and only
    # when the user isn't using [[profile]] (the picker form takes
    # precedence over [connection] at load time).
    if cfg.connection and not cfg.profiles and any([
        cfg.connection.port, cfg.connection.user,
        cfg.connection.client, cfg.connection.charset,
    ]):
        chunks.append("[connection]")
        for k in ("port", "user", "client", "charset"):
            line = _emit_str(k, getattr(cfg.connection, k))
            if line:
                chunks.append(line)
        chunks.append("")

    # [[profile]] entries
    for prof in cfg.profiles:
        chunks.append("[[profile]]")
        for k in ("name", "port", "user", "client", "charset"):
            line = _emit_str(k, getattr(prof, k))
            if line:
                chunks.append(line)
        chunks.append("")

    # [swarm]
    if cfg.swarm and cfg.swarm.base_url:
        chunks.append("[swarm]")
        line = _emit_str("base_url", cfg.swarm.base_url)
        if line:
            chunks.append(line)
        chunks.append("")

    # [jira]
    if cfg.jira and cfg.jira.base_url:
        chunks.append("[jira]")
        line = _emit_str("base_url", cfg.jira.base_url)
        if line:
            chunks.append(line)
        if cfg.jira.projects:
            projects = ", ".join(f'"{p}"' for p in cfg.jira.projects)
            chunks.append(f"projects = [{projects}]")
        chunks.append("")
        if cfg.jira.path_projects:
            chunks.append("[jira.path_projects]")
            for prefix, project in cfg.jira.path_projects.items():
                chunks.append(f'"{prefix}" = "{project}"')
            chunks.append("")

    # [[external_editor]] entries — Open With… picker
    for ed in cfg.external_editors:
        chunks.append("[[external_editor]]")
        for k in ("name", "command", "args"):
            line = _emit_str(k, getattr(ed, k))
            if line:
                chunks.append(line)
        chunks.append("")

    # [chunking]  — default first, then per-job sub-tables
    if cfg.chunking:
        d = cfg.chunking.default
        chunks.append("[chunking]")
        chunks.append(_emit_str("mode", d.mode))
        chunks.append(_emit_int("files_per_chunk", d.files_per_chunk))
        chunks.append(_emit_int("bytes_per_chunk", d.bytes_per_chunk))
        chunks.append("")
        for job_kind in sorted(cfg.chunking.per_job):
            s = cfg.chunking.per_job[job_kind]
            chunks.append(f"[chunking.{job_kind}]")
            chunks.append(_emit_str("mode", s.mode))
            chunks.append(_emit_int("files_per_chunk", s.files_per_chunk))
            chunks.append(_emit_int("bytes_per_chunk", s.bytes_per_chunk))
            chunks.append("")

    body = "\n".join(c for c in chunks if c is not None)
    # Atomic write so a crash mid-write doesn't leave a half-written
    # config that fails to parse on next launch.
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(out_path)
    return out_path


def default_config_path() -> Path:
    """The path Preferences saves to when there's no current source.

    Picks the per-project location since that's what users hit first
    (it overrides the home-dir one and doesn't follow them between
    machines).
    """
    return Path.cwd() / "p4v-tui.toml"


def _parse_chunking(raw: dict) -> ChunkingConfig:
    """Build a :class:`ChunkingConfig` from the ``[chunking]`` table.

    Top-level keys (``mode``, ``files_per_chunk``, ``bytes_per_chunk``)
    define the global default. Sub-tables under ``[chunking]`` are
    treated as per-job overrides:

      [chunking]
      mode = "count"
      files_per_chunk = 50

      [chunking.sync]
      mode = "size"
      bytes_per_chunk = 104857600

      [chunking.revert]
      mode = "single"

    Any TOML table that isn't a recognised job kind is ignored.
    """
    if not isinstance(raw, dict):
        return ChunkingConfig()
    # Anything that isn't a sub-table is part of the global default.
    flat = {k: v for k, v in raw.items() if not isinstance(v, dict)}
    default = ChunkingStrategy.from_dict(flat)
    per_job: dict[str, ChunkingStrategy] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            continue
        job_kind = str(key).strip().lower()
        per_job[job_kind] = ChunkingStrategy.from_dict(val, fallback=default)
    return ChunkingConfig(default=default, per_job=per_job)


def build_swarm_url(
    base_url: str,
    depot_path: str,
    rev: int | str | None = None,
) -> str:
    """Render the per-team Swarm URL for a depot path.

    Pattern: ``{base}/files{depot_path_with_one_leading_slash}?v={rev}``

    The leading ``//`` of a depot path is collapsed to a single ``/`` so
    the URL ends up like ``http://host/files/depot/foo/bar.txt`` rather
    than ``http://host/files//depot/foo/bar.txt``.
    """
    base = (base_url or "").rstrip("/")
    p = depot_path or ""
    if p.startswith("//"):
        path_part = "/" + p[2:]
    elif p.startswith("/"):
        path_part = p
    else:
        path_part = "/" + p
    url = f"{base}/files{path_part}"
    if rev not in (None, "", 0):
        url += f"?v={rev}"
    return url


def build_swarm_review_url(base_url: str, change: str | int) -> str:
    """Render the Swarm review / changelist URL for a CL number.

    Swarm canonically exposes a changelist at ``{base}/changes/{N}``;
    if a review has been attached the same path 302-redirects to
    ``/reviews/{review_id}``. Using ``/changes/{N}`` therefore works
    whether or not a review exists yet — a strict ``/reviews/{N}`` would
    404 for CLs that haven't been posted for review.
    """
    base = (base_url or "").rstrip("/")
    return f"{base}/changes/{change}"


def is_http_url(url: str) -> bool:
    """True only for ``http``/``https`` URLs.

    Security gate (audit F3): call before handing a URL to the system
    browser so a misconfigured ``[swarm] base_url`` or a crafted depot
    path can't produce a ``file:`` / ``javascript:`` / other unexpected
    scheme that ``webbrowser.open`` would otherwise launch. Also rejects
    schemeless / malformed input.
    """
    try:
        from urllib.parse import urlparse
        parts = urlparse(url or "")
        return parts.scheme in ("http", "https") and bool(parts.netloc)
    except (ValueError, TypeError):
        return False


def discover_profiles(cfg: Config) -> list[ConnectionConfig]:
    """Collect all P4 connection profiles known at startup.

    Order of preference:
      1. ``[[profile]]`` entries in the TOML (explicit multi-server).
      2. The legacy ``[connection]`` table (single profile).
      3. Whatever P4Python sees from env / P4CONFIG / Windows registry —
         only counts as a profile if a non-empty port is detected.

    Returned profiles get a synthetic ``name`` if the TOML didn't supply
    one, so the picker UI can show something readable.
    """
    if cfg.profiles:
        out = []
        for i, p in enumerate(cfg.profiles):
            name = p.name or _default_name(p, i)
            out.append(_with_name(p, name))
        return out

    if cfg.connection and cfg.connection.port:
        return [_with_name(cfg.connection,
                           cfg.connection.name or "configured")]

    # Last resort — let P4Python tell us what env says.
    env = _detect_env_profile()
    return [env] if env is not None else []


def _detect_env_profile() -> ConnectionConfig | None:
    """Best-effort probe of the user's effective P4 environment.

    Tries P4Python first (since it has the full resolution rules
    baked in including P4CONFIG file discovery), then falls back to
    spawning ``p4 set -q`` when P4Python is unavailable — that path
    keeps the env probe working in CLI-fallback-only installs where
    the C extension wheel never got built.
    """
    env = _probe_via_p4python() or _probe_via_p4_set()
    if env is None:
        return None
    port = env.get("port") or ""
    # P4Python defaults to "perforce:1666" when nothing is configured;
    # that's a sentinel rather than a real server address. Treat it as
    # "no profile" unless something else (env or P4CONFIG) replaces it.
    if not port or port.lower() in ("perforce:1666", "1666"):
        return None
    return ConnectionConfig(
        name="environment",
        port=port,
        user=env.get("user") or None,
        client=env.get("client") or None,
    )


def _probe_via_p4python() -> dict | None:
    try:
        import P4
    except ImportError:
        return None
    try:
        p = P4.P4()
    except Exception:  # noqa: BLE001
        return None
    return {
        "port":   getattr(p, "port", "") or "",
        "user":   getattr(p, "user", "") or "",
        "client": getattr(p, "client", "") or "",
    }


def _probe_via_p4_set() -> dict | None:
    """Parse `p4 set -q` for P4PORT / P4USER / P4CLIENT.

    Used when P4Python is missing. ``-q`` keeps the output to bare
    ``KEY=value`` lines (no decorative " (config)" tag), so a simple
    split is enough. Returns None when the binary itself is unavailable
    so callers can fall through to "no env profile".
    """
    import shutil
    import subprocess
    bin_ = shutil.which("p4")
    if not bin_:
        return None
    try:
        cp = subprocess.run(
            [bin_, "set", "-q"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        return None
    env: dict[str, str] = {}
    for line in cp.stdout.decode("utf-8", "replace").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return {
        "port":   env.get("P4PORT", ""),
        "user":   env.get("P4USER", ""),
        "client": env.get("P4CLIENT", ""),
    }


def _with_name(p: ConnectionConfig, name: str) -> ConnectionConfig:
    return ConnectionConfig(
        port=p.port, user=p.user, client=p.client,
        charset=p.charset, name=name,
    )


def _default_name(p: ConnectionConfig, idx: int) -> str:
    if p.port:
        if p.user:
            return f"{p.user}@{p.port}"
        return p.port
    return f"profile {idx + 1}"


SAMPLE_TOML = """\
# p4v-tui configuration. Copy this file to one of:
#   ./p4v-tui.toml           (per-project, takes precedence)
#   ./.p4v-tui.toml
#   ~/.p4v-tui.toml
#   ~/.config/p4v-tui/config.toml
#
# Any field omitted falls back to your existing P4 environment / P4CONFIG.

[connection]
port = "ssl:your-perforce-host:1666"
# user    = "your-username"
# client  = "your-workspace"
# charset = "utf8"
"""
