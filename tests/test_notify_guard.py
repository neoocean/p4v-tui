"""Audit F5 — P4VApp.notify defaults Rich markup OFF.

Toasts carry server/user-derived strings (depot paths, p4 error text);
the override must forward ``markup=False`` to Textual's App.notify unless
a caller explicitly opts in with ``markup=True``.
"""
from __future__ import annotations


def _capture(monkeypatch):
    import textual.app
    captured: dict = {}

    def fake_notify(self, message, **kwargs):  # mimics App.notify shape
        captured.clear()
        captured["message"] = message
        captured.update(kwargs)

    monkeypatch.setattr(textual.app.App, "notify", fake_notify)
    return captured


def test_notify_defaults_markup_false(monkeypatch):
    from p4v_tui.app import P4VApp
    captured = _capture(monkeypatch)
    app = P4VApp.__new__(P4VApp)  # no __init__: only exercise the override
    P4VApp.notify(app, "hello", severity="error", timeout=5)
    assert captured["message"] == "hello"
    assert captured["markup"] is False
    assert captured["severity"] == "error"
    assert captured["timeout"] == 5


def test_notify_explicit_markup_true_preserved(monkeypatch):
    from p4v_tui.app import P4VApp
    captured = _capture(monkeypatch)
    app = P4VApp.__new__(P4VApp)
    P4VApp.notify(app, "styled", markup=True)
    assert captured["markup"] is True


def test_notify_markup_off_renders_brackets_literally(monkeypatch):
    """A depot-pathish string with '[...]' is forwarded verbatim (the
    markup=False default means Textual won't interpret it)."""
    from p4v_tui.app import P4VApp
    captured = _capture(monkeypatch)
    app = P4VApp.__new__(P4VApp)
    msg = "Read failed: //depot/[weird]/file.txt — no such file"
    P4VApp.notify(app, msg)
    assert captured["message"] == msg
    assert captured["markup"] is False
