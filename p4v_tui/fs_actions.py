"""OS-specific filesystem hand-offs.

Two operations that hand the user back to native tooling:

* :func:`show_in_filesystem` — open the platform's file manager and
  highlight the given path (Windows Explorer with /select, macOS
  Finder with -R, Linux xdg-open of the parent dir).
* :func:`open_command_window` — open a terminal window at the given
  directory (cmd.exe on Windows, Terminal on macOS, common
  ``x-terminal-emulator`` candidates on Linux).

Both are best-effort: if the underlying program is missing or the path
doesn't exist locally they return False and the App surfaces a toast.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def show_in_filesystem(local_path: str) -> bool:
    p = Path(local_path)
    if not p.exists():
        return False
    try:
        if sys.platform == "win32":
            if p.is_file():
                subprocess.Popen(
                    ["explorer.exe", f"/select,{p}"],
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                )
            else:
                subprocess.Popen(["explorer.exe", str(p)])
        elif sys.platform == "darwin":
            if p.is_file():
                subprocess.Popen(["open", "-R", str(p)])
            else:
                subprocess.Popen(["open", str(p)])
        else:  # assume Linux / *BSD
            target = p if p.is_dir() else p.parent
            if shutil.which("xdg-open") is None:
                return False
            subprocess.Popen(["xdg-open", str(target)])
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def open_with_external(
    command: str, args_template: str, local_path: str,
) -> bool:
    """Spawn ``command`` with ``args_template`` rendered against
    ``local_path`` and return True if the process started.

    ``args_template`` placeholders:
      * ``{path}``  → the full local path (quoted by shlex)
      * ``{dir}``   → the parent directory
      * ``{name}``  → the basename
      * If template is empty, defaults to just ``{path}``.

    The editor is launched detached — we don't wait for it. Failure
    to start (missing exe, bad template) returns False so the caller
    can surface a toast.
    """
    import shlex
    p = Path(local_path)
    if not p.exists():
        return False
    template = (args_template or "{path}").strip()
    try:
        rendered = template.format(
            path=str(p), dir=str(p.parent), name=p.name,
        )
    except (KeyError, IndexError, ValueError):
        return False
    # Use shlex so the user can quote args correctly in their template
    # (cross-platform with posix=False on Windows so Windows paths
    # with backslashes survive).
    use_posix = sys.platform != "win32"
    try:
        argv = [command] + shlex.split(rendered, posix=use_posix)
    except ValueError:
        return False
    if shutil.which(command) is None and not Path(command).exists():
        return False
    try:
        subprocess.Popen(argv)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def open_command_window(local_dir: str) -> bool:
    p = Path(local_dir)
    if not p.exists():
        return False
    if p.is_file():
        p = p.parent
    try:
        if sys.platform == "win32":
            # `start` spawns a detached cmd window already at the path.
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "cmd.exe"],
                cwd=str(p),
            )
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Terminal", str(p)])
        else:
            # Try common terminal emulators in order of likelihood.
            for term in (
                "x-terminal-emulator", "gnome-terminal",
                "konsole", "xfce4-terminal", "alacritty",
                "kitty", "xterm",
            ):
                if shutil.which(term):
                    subprocess.Popen([term], cwd=str(p))
                    return True
            return False
    except (OSError, subprocess.SubprocessError):
        return False
    return True
