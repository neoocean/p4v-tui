"""In-app Preferences editor for the TOML config.

p4v-tui has always read its config from ``./p4v-tui.toml`` (and a few
fallback paths) but offered no way to edit it without leaving the
TUI. Preferences (``Ctrl+,``) opens a tabbed modal that lets the user
inspect the live :class:`Config` and persist changes back to the same
file the app loaded from — or to ``./p4v-tui.toml`` if it loaded from
env only.

The modal does NOT change the live :attr:`P4VApp.config` until Save
is pressed, so cancel really cancels. Save also doesn't touch the
in-flight P4 connection — connection changes apply on next launch
(an explicit notice on the Connection tab).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button, Input, Label, OptionList, Select, Static, TabbedContent, TabPane,
)
from textual.widgets.option_list import Option

from ..chunking import (
    ChunkingConfig, ChunkingStrategy, VALID_MODES,
    DEFAULT_BYTES_PER_CHUNK, DEFAULT_FILES_PER_CHUNK,
)
from ..config import (
    Config, ConnectionConfig, SwarmConfig,
    default_config_path, write_config,
)
from .profile_edit_modal import ProfileEditModal


_CHUNKING_JOB_KINDS = ("sync", "force_sync", "revert", "reconcile", "clean")


class PreferencesModal(ModalScreen[Optional[Config]]):
    """Returns the new :class:`Config` on Save, ``None`` on Cancel."""

    DEFAULT_CSS = """
    PreferencesModal { align: center middle; }
    PreferencesModal > #dialog {
        width: 90%;
        height: 90%;
        border: thick $primary;
        background: $panel;
        padding: 0 1;
    }
    PreferencesModal #title {
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    PreferencesModal #source_path {
        color: $text-muted;
        padding: 0 1;
    }
    PreferencesModal Label.section { text-style: bold; margin-top: 1; }
    PreferencesModal Label.field   { margin-top: 1; }
    PreferencesModal Static.hint   { color: $text-muted; padding: 0 1; }
    PreferencesModal Input         { margin-bottom: 0; }
    PreferencesModal Select        { margin-bottom: 0; }
    PreferencesModal #buttons {
        height: 3;
        align: right middle;
        padding: 0 1;
    }
    PreferencesModal Button { margin-left: 2; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, current: Config) -> None:
        super().__init__()
        self._current = current
        # Editable working copy of the [[profile]] list — mutated by the
        # Profiles tab (Add / Edit / Delete) and folded into the saved
        # Config. A copy so Cancel leaves the live config untouched.
        self._profiles_working: list[ConnectionConfig] = list(
            current.profiles
        )
        # Save target: prefer the file we loaded from; else default.
        self._target_path: Path = (
            current.source if current.source is not None
            else default_config_path()
        )

    def compose(self) -> ComposeResult:
        cfg = self._current
        with Container(id="dialog"):
            yield Static(" Preferences ", id="title")
            yield Static(
                f"Save target: {self._target_path}", id="source_path",
            )
            with TabbedContent(initial="tab_connection"):
                with TabPane("Connection", id="tab_connection"):
                    yield from self._compose_connection_tab(cfg)
                with TabPane("Profiles", id="tab_profiles"):
                    yield from self._compose_profiles_tab(cfg)
                with TabPane("Swarm", id="tab_swarm"):
                    yield from self._compose_swarm_tab(cfg)
                with TabPane("Chunking", id="tab_chunking"):
                    yield from self._compose_chunking_tab(cfg)
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    # --- tab composers ---------------------------------------------------

    def _compose_connection_tab(self, cfg: Config) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                "Edit applies to the [connection] table — the legacy "
                "single-server form. Changes take effect on next launch.",
                classes="hint",
            )
            if cfg.profiles:
                yield Static(
                    f"  Note: {len(cfg.profiles)} [[profile]] entries are "
                    "active in your config; they take precedence over "
                    "[connection]. Edit them in the Profiles tab.",
                    classes="hint",
                )
            conn = cfg.connection or ConnectionConfig()
            yield Label("Port", classes="field")
            yield Input(value=conn.port or "", id="conn_port",
                        placeholder="ssl:host:1666")
            yield Label("User", classes="field")
            yield Input(value=conn.user or "", id="conn_user",
                        placeholder="(blank → P4 env)")
            yield Label("Client / Workspace", classes="field")
            yield Input(value=conn.client or "", id="conn_client",
                        placeholder="(blank → P4 env)")
            yield Label("Charset", classes="field")
            yield Input(value=conn.charset or "", id="conn_charset",
                        placeholder="utf8")

    def _compose_profiles_tab(self, cfg: Config) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                "Multi-server connection profiles ([[profile]]). The "
                "startup picker offers one row per profile; they take "
                "precedence over [connection]. Changes take effect on "
                "next launch.",
                classes="hint",
            )
            yield OptionList(id="profiles_list")
            with Horizontal(id="profile_buttons"):
                yield Button("Add", id="profile_add")
                yield Button("Edit", id="profile_edit")
                yield Button("Delete", id="profile_delete", variant="error")

    def on_mount(self) -> None:
        self._refresh_profiles_list()

    @staticmethod
    def _profile_label(p: ConnectionConfig) -> str:
        name = p.name or "(unnamed)"
        bits = [b for b in (p.port, p.user, p.client) if b]
        detail = f"  ({', '.join(bits)})" if bits else ""
        return f"{name}{detail}"

    def _refresh_profiles_list(self) -> None:
        try:
            ol = self.query_one("#profiles_list", OptionList)
        except Exception:  # noqa: BLE001 — tab not mounted yet
            return
        prev = ol.highlighted
        ol.clear_options()
        if not self._profiles_working:
            ol.add_option(Option("(no profiles — Add one)", disabled=True))
        else:
            for p in self._profiles_working:
                ol.add_option(Option(self._profile_label(p)))
            # Keep the cursor in range after add/delete.
            if prev is not None:
                ol.highlighted = min(prev, len(self._profiles_working) - 1)
            else:
                ol.highlighted = 0

    def _selected_profile_index(self) -> int | None:
        try:
            ol = self.query_one("#profiles_list", OptionList)
        except Exception:  # noqa: BLE001
            return None
        idx = ol.highlighted
        if idx is None or not self._profiles_working:
            return None
        if 0 <= idx < len(self._profiles_working):
            return idx
        return None

    def _add_profile(self) -> None:
        def done(profile: Optional[ConnectionConfig]) -> None:
            if profile is not None:
                self._profiles_working.append(profile)
                self._refresh_profiles_list()

        self.app.push_screen(ProfileEditModal(adding=True), done)

    def _edit_profile(self) -> None:
        idx = self._selected_profile_index()
        if idx is None:
            self.app.notify("Select a profile to edit.", timeout=3)
            return

        def done(profile: Optional[ConnectionConfig]) -> None:
            if profile is not None:
                self._profiles_working[idx] = profile
                self._refresh_profiles_list()

        self.app.push_screen(
            ProfileEditModal(self._profiles_working[idx]), done,
        )

    def _delete_profile(self) -> None:
        idx = self._selected_profile_index()
        if idx is None:
            self.app.notify("Select a profile to delete.", timeout=3)
            return
        removed = self._profiles_working.pop(idx)
        self._refresh_profiles_list()
        self.app.notify(
            f"Removed profile: {self._profile_label(removed)} "
            "(Save to persist).",
            timeout=4,
        )

    def _compose_swarm_tab(self, cfg: Config) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                "Swarm review host. Used by the Copy Swarm URL action; "
                "leave blank to disable.",
                classes="hint",
            )
            base = cfg.swarm.base_url if cfg.swarm else ""
            yield Label("Base URL", classes="field")
            yield Input(value=base or "", id="swarm_base_url",
                        placeholder="http://swarm.example.com")

    def _compose_chunking_tab(self, cfg: Config) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                "Default chunking shape applies to every long bulk job "
                "unless that job has its own override below. "
                "Reconcile / clean ALWAYS chunk by subdir regardless.",
                classes="hint",
            )
            chunking = cfg.chunking or ChunkingConfig()
            d = chunking.default
            yield Label("Default mode", classes="field")
            yield Select(
                [(m, m) for m in VALID_MODES],
                value=d.mode, id="chk_default_mode",
                allow_blank=False,
            )
            yield Label("Default files per chunk (count mode)",
                        classes="field")
            yield Input(value=str(d.files_per_chunk),
                        id="chk_default_files",
                        placeholder=str(DEFAULT_FILES_PER_CHUNK))
            yield Label("Default bytes per chunk (size mode)",
                        classes="field")
            yield Input(value=str(d.bytes_per_chunk),
                        id="chk_default_bytes",
                        placeholder=str(DEFAULT_BYTES_PER_CHUNK))

            yield Label("Per-job overrides", classes="section")
            yield Static(
                "Empty mode means the global default applies. Numeric "
                "fields fall back to the global default when blank.",
                classes="hint",
            )
            for job in _CHUNKING_JOB_KINDS:
                ov = chunking.per_job.get(job)
                yield Label(f"  {job}", classes="field")
                yield Select(
                    [("(use default)", "")] +
                    [(m, m) for m in VALID_MODES],
                    value=(ov.mode if ov else ""),
                    id=f"chk_{job}_mode",
                    allow_blank=False,
                )
                yield Input(
                    value=(str(ov.files_per_chunk) if ov else ""),
                    id=f"chk_{job}_files",
                    placeholder="files per chunk",
                )
                yield Input(
                    value=(str(ov.bytes_per_chunk) if ov else ""),
                    id=f"chk_{job}_bytes",
                    placeholder="bytes per chunk",
                )

    # --- buttons ---------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "save":
            self._save_and_dismiss()
        elif bid == "profile_add":
            self._add_profile()
        elif bid == "profile_edit":
            self._edit_profile()
        elif bid == "profile_delete":
            self._delete_profile()
        elif bid == "cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

    # --- save ------------------------------------------------------------

    def _save_and_dismiss(self) -> None:
        try:
            new_cfg = self._build_new_config()
        except ValueError as e:
            self.app.notify(
                f"Save failed: {e}", severity="error", timeout=10,
            )
            return
        try:
            written = write_config(new_cfg, self._target_path)
        except OSError as e:
            self.app.notify(
                f"Could not write {self._target_path}: {e}",
                severity="error", timeout=10,
            )
            return
        # Re-thread the source path so subsequent re-opens of the
        # modal show "Save target: <real-path>" rather than the
        # default placeholder.
        new_cfg.source = written
        self.dismiss(new_cfg)

    def _build_new_config(self) -> Config:
        # --- connection
        conn = ConnectionConfig(
            name=self._current.connection.name
                if self._current.connection else None,
            port=self._read("conn_port"),
            user=self._read("conn_user"),
            client=self._read("conn_client"),
            charset=self._read("conn_charset"),
        )

        # --- swarm
        swarm = SwarmConfig(base_url=self._read("swarm_base_url"))

        # --- chunking
        d_mode = self._read_select("chk_default_mode") or "count"
        d_files = self._read_int("chk_default_files",
                                 DEFAULT_FILES_PER_CHUNK)
        d_bytes = self._read_int("chk_default_bytes",
                                 DEFAULT_BYTES_PER_CHUNK)
        default_strat = ChunkingStrategy(
            mode=d_mode,
            files_per_chunk=d_files,
            bytes_per_chunk=d_bytes,
        )

        per_job: dict[str, ChunkingStrategy] = {}
        for job in _CHUNKING_JOB_KINDS:
            mode = self._read_select(f"chk_{job}_mode")
            files_raw = self._read(f"chk_{job}_files")
            bytes_raw = self._read(f"chk_{job}_bytes")
            # Skip the override entirely if all three are blank — the
            # job will inherit the default at lookup time.
            if not mode and not files_raw and not bytes_raw:
                continue
            per_job[job] = ChunkingStrategy(
                mode=(mode or default_strat.mode),
                files_per_chunk=(
                    int(files_raw) if files_raw
                    else default_strat.files_per_chunk
                ),
                bytes_per_chunk=(
                    int(bytes_raw) if bytes_raw
                    else default_strat.bytes_per_chunk
                ),
            )

        return Config(
            connection=conn,
            profiles=list(self._profiles_working),  # edited in Profiles tab
            swarm=swarm,
            chunking=ChunkingConfig(default=default_strat,
                                    per_job=per_job),
            source=self._current.source,
        )

    def _read(self, widget_id: str) -> str | None:
        try:
            inp = self.query_one(f"#{widget_id}", Input)
        except Exception:  # noqa: BLE001
            return None
        v = inp.value.strip()
        return v or None

    def _read_select(self, widget_id: str) -> str | None:
        try:
            sel = self.query_one(f"#{widget_id}", Select)
        except Exception:  # noqa: BLE001
            return None
        v = sel.value
        if v in (None, "", Select.BLANK):
            return None
        return str(v)

    def _read_int(self, widget_id: str, fallback: int) -> int:
        raw = self._read(widget_id)
        if raw is None:
            return fallback
        try:
            return int(raw)
        except ValueError as e:
            raise ValueError(
                f"{widget_id} must be an integer, got {raw!r}"
            ) from e
