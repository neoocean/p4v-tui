"""p4v-tui entry point.

A Textual-based TUI mimicking the Perforce P4V GUI client.
"""
from __future__ import annotations

import sys
from pathlib import Path


# Per-module "what is this and why might you need it" blurbs.
# ``_INSTALL_HINTS`` keys are top-level package names; values are
# (summary, packages_to_install, extra_note). Command suggestions are
# built dynamically below so we can adapt to PEP 668 externally-managed
# Python installs (Homebrew on macOS, Debian/Ubuntu, etc.) instead of
# emitting a flat ``pip install`` line the user can't actually run.
_INSTALL_HINTS = {
    "P4": (
        "P4Python (Perforce 공식 Python 바인딩) 이 설치돼 있지 않습니다.",
        ["p4python"],
        # P4Python is now optional — the `p4` CLI backend works as a
        # drop-in fallback. Mention both paths so the user can pick the
        # easier one for their environment.
        "p4v-tui 는 P4Python 또는 `p4` CLI 둘 중 하나만 있으면 동작합니다.\n"
        "P4Python wheel 이 없는 환경 (오래된 Linux / 비공식 Python 빌드) "
        "에서는 보통 `p4` CLI 설치가 더 간단합니다 "
        "(https://www.perforce.com/downloads — 단일 바이너리).\n"
        "강제 백엔드 선택: `P4V_BACKEND=cli python p4v.py` "
        "(또는 `=python`).",
    ),
    "textual": (
        "Textual TUI 프레임워크가 설치돼 있지 않습니다.",
        ["textual>=8.0"],
        None,
    ),
}


def _is_externally_managed() -> bool:
    """Detect a PEP 668 "externally managed" Python.

    Homebrew Python on macOS, Debian / Ubuntu's system Python, and a
    few other distros drop an ``EXTERNALLY-MANAGED`` marker file next
    to the stdlib so ``pip install`` refuses to touch the global
    site-packages. When the user is on one of those interpreters we
    surface the canonical recovery paths (venv / --user /
    --break-system-packages) rather than a flat ``pip install …``
    line that will print a wall-of-text PEP 668 error.
    """
    try:
        stdlib = Path(sys.prefix) / "lib" / (
            f"python{sys.version_info[0]}.{sys.version_info[1]}"
        )
        if (stdlib / "EXTERNALLY-MANAGED").exists():
            return True
        # Homebrew sometimes parks the marker inside a Frameworks-
        # specific subdir; widen the search a level.
        for marker in Path(sys.prefix).rglob("EXTERNALLY-MANAGED"):
            return True
    except OSError:
        pass
    return False


def _install_block(packages: list[str], requirements_path: Path) -> list[str]:
    """Build the ``설치 방법:`` block for the friendly error message.

    Always lists the project venv path (preferred) plus a ``--user``
    fallback. On PEP 668 hosts both paths are required; on a vanilla
    Python a plain ``pip install`` would also work, but showing the
    same venv-first recipe everywhere is consistent and harmless.
    """
    pkgs = " ".join(f"'{p}'" if any(c in p for c in "<>= ") else p
                    for p in packages)
    req = requirements_path
    externally_managed = _is_externally_managed()

    out: list[str] = ["설치 방법:", ""]
    out.append("  방법 A — 프로젝트 전용 venv (권장):")
    out.append("    python3 -m venv .venv")
    out.append("    source .venv/bin/activate")
    out.append(f"    pip install -r {req}")
    out.append("")
    out.append("  방법 B — 사용자 site-packages 에 (--user):")
    out.append(f"    pip install --user -r {req}")
    out.append("")
    if externally_managed:
        out.append(
            "  방법 C — Homebrew / 시스템 Python 에 직접 (PEP 668 우회):"
        )
        out.append(
            f"    pip install --break-system-packages -r {req}"
        )
        out.append(
            "    (시스템 Python 의 패키지 트리를 깨뜨릴 위험 — 가급적 A 사용)"
        )
        out.append("")
        out.append(
            "  ※ 현재 Python 은 PEP 668 'externally-managed' 표시가 있어"
        )
        out.append(
            "    그냥 `pip install …` 만 실행하면 거부됩니다."
        )
    out.append("")
    out.append(f"  (단건 설치만 필요하면 `pip install {pkgs}` 와 동등.)")
    return out


def _print_missing_dependency(exc: ModuleNotFoundError) -> None:
    """Render a friendly, actionable message for a missing dependency.

    Falls back to a generic message for modules we don't have a hint for.
    """
    name = exc.name or ""
    # Top-level package name only (e.g. "P4.something" -> "P4").
    top = name.split(".", 1)[0]
    hint = _INSTALL_HINTS.get(top)
    # requirements.txt next to p4v.py.
    here = Path(__file__).resolve().parent
    requirements_path = here / "requirements.txt"

    lines: list[str] = []
    lines.append("")
    lines.append(f"❌  필요한 Python 모듈을 찾지 못했습니다: {name!r}")
    lines.append("")
    if hint is not None:
        summary, packages, extra = hint
        lines.append(summary)
        lines.append("")
        lines.extend(_install_block(packages, requirements_path))
        lines.append("")
        if extra:
            lines.append(extra)
            lines.append("")
    else:
        # Unmapped transitive dep — same recipe, only the package
        # name swap.
        lines.append("아래 방법 중 하나로 누락된 의존성을 설치해 주세요.")
        lines.append("")
        lines.extend(_install_block([top], requirements_path))
        lines.append("")
    lines.append("설치 후 다시 `python p4v.py` 로 실행하면 됩니다.")
    lines.append("")
    print("\n".join(lines), file=sys.stderr)


def _print_no_backend(message: str) -> None:
    """Render a friendly message when neither P4Python nor `p4` CLI is available.

    Raised by ``p4v_tui.p4client._select_backend()``. We render a short
    block covering both install options instead of dumping the raw
    `P4SetupError` traceback the runtime would otherwise emit.
    """
    print("", file=sys.stderr)
    print(f"❌  Perforce 백엔드를 활성화하지 못했습니다.\n", file=sys.stderr)
    print(message, file=sys.stderr)
    print("", file=sys.stderr)
    print("다음 중 하나만 있으면 됩니다:", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "  • P4Python (Python 바인딩) — `pip install p4python`",
        file=sys.stderr,
    )
    print(
        "  • p4 CLI                 — https://www.perforce.com/downloads",
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    print(
        "강제 선택: `P4V_BACKEND=cli python p4v.py` "
        "(또는 `=python`).",
        file=sys.stderr,
    )
    print("", file=sys.stderr)


def main() -> int:
    # Imported lazily so the ModuleNotFoundError surfaces here, where
    # we can intercept it before the traceback hits the terminal.
    # `p4client` has no textual / P4Python imports at top level — it
    # lazy-imports P4 inside `_PythonBackend.__init__` — so importing
    # `P4SetupError` here is cheap and doesn't drag the GUI deps in
    # ahead of time. (A prior revision duck-typed the exception by
    # class name to dodge an import it was wrong to be afraid of;
    # this is the cleaner shape.)
    try:
        from p4v_tui.app import P4VApp
        from p4v_tui.p4client import P4SetupError
    except ModuleNotFoundError as exc:
        _print_missing_dependency(exc)
        return 1

    try:
        app = P4VApp()
    except P4SetupError as exc:
        # Neither P4Python nor `p4` CLI is usable — surface a Korean
        # install hint covering both options instead of dumping the
        # raw P4SetupError traceback. Any *other* exception escaping
        # the constructor is a real bug; let it propagate.
        _print_no_backend(str(exc))
        return 1

    try:
        app.run()
    except KeyboardInterrupt:
        # Ctrl+C / SIGINT during a long-running command would otherwise
        # dump an asyncio traceback through the bash terminal. JobRunner
        # workers are daemon threads, so they exit with the process; the
        # P4 connection is per-instance and goes away the same way.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
