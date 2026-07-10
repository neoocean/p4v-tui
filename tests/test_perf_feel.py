"""Unit tests for the pure perceived-performance feel helpers."""

import pytest

from p4v_tui import perf_feel as pf


# --- activity threshold ---------------------------------------------------

def test_should_show_only_after_delay():
    assert pf.should_show_activity(0) is False
    assert pf.should_show_activity(149) is False
    assert pf.should_show_activity(150) is True
    assert pf.should_show_activity(5000) is True


def test_should_show_custom_delay():
    assert pf.should_show_activity(200, delay_ms=300) is False
    assert pf.should_show_activity(300, delay_ms=300) is True


# --- escalating label -----------------------------------------------------

def test_activity_label_tiers():
    assert pf.activity_label("Loading", 200) == "Loading"
    assert pf.activity_label("Loading", 1000) == "Loading — still working…"
    assert pf.activity_label("Loading", 9000) == (
        "Loading — slow link (F2 for details)")


def test_activity_label_blank_base_falls_back():
    assert pf.activity_label("", 0) == "Working"
    assert pf.activity_label(None, 0) == "Working"
    assert pf.activity_label("   ", 0) == "Working"


def test_activity_label_boundaries():
    # exactly at a threshold escalates (>=)
    assert "still working" in pf.activity_label("X", pf.ACTIVITY_SLOW_MS)
    assert "slow link" in pf.activity_label("X", pf.ACTIVITY_VERY_SLOW_MS)


# --- frame animation ------------------------------------------------------

def test_activity_frame_wraps():
    n = len(pf.ACTIVITY_FRAMES)
    assert pf.activity_frame(0) == pf.ACTIVITY_FRAMES[0]
    assert pf.activity_frame(n) == pf.ACTIVITY_FRAMES[0]
    assert pf.activity_frame(n + 1) == pf.ACTIVITY_FRAMES[1]


def test_activity_frame_empty_frames():
    assert pf.activity_frame(3, frames=()) == ""


# --- combined render ------------------------------------------------------

def test_render_activity_empty_before_delay():
    assert pf.render_activity("Loading", 100, 0) == ""


def test_render_activity_has_glyph_and_label():
    s = pf.render_activity("Loading changelists", 500, 0)
    assert pf.ACTIVITY_FRAMES[0] in s
    assert "Loading changelists" in s


def test_render_activity_escalates_when_slow():
    s = pf.render_activity("Loading", 2000, 2)
    assert "still working" in s


# --- adaptive refresh cadence ---------------------------------------------

def test_next_refresh_interval_no_samples_is_base():
    assert pf.next_refresh_interval([], 30) == 30
    assert pf.next_refresh_interval(None, 30) == 30


def test_next_refresh_interval_disabled_stays_zero():
    # 0 means the user disabled auto-refresh — never resurrect it
    assert pf.next_refresh_interval([5000], 0) == 0.0


def test_next_refresh_interval_backs_off_on_slow_link():
    # avg 2 s/call -> scale 3x -> 30 * 3 = 90
    assert pf.next_refresh_interval([2000, 2000], 30) == 90
    # fast link barely stretches
    assert pf.next_refresh_interval([50, 50], 30) == pytest.approx(31.5)


def test_next_refresh_interval_clamps():
    # huge latency clamps to max_sec
    assert pf.next_refresh_interval([10_000_000], 30, max_sec=600) == 600
    # tiny base clamps up to min_sec
    assert pf.next_refresh_interval([0], 1, min_sec=5) == 5


def test_next_refresh_interval_ignores_non_numeric():
    assert pf.next_refresh_interval(["x", None, 2000], 30) == 90
