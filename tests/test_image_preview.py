"""Unit tests for the image / binary preview pure logic.

No Textual, no Perforce — feeds raw bytes to the magic-byte detector,
the half-block ANSI-art renderer, and the hex dumper, asserting on the
``rich.text.Text`` shape / styles produced.
"""
from __future__ import annotations

from io import BytesIO

import pytest

from p4v_tui.image_preview import (
    detect_image_type,
    image_caption,
    render_hex,
    render_image,
)

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402  (after importorskip)


def _png_bytes(w: int, h: int, color=(255, 0, 0)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


# --- detection -----------------------------------------------------------

def test_detect_png():
    assert detect_image_type(_png_bytes(2, 2)) == "png"


def test_detect_jpeg():
    buf = BytesIO()
    Image.new("RGB", (2, 2), (0, 255, 0)).save(buf, format="JPEG")
    assert detect_image_type(buf.getvalue()) == "jpeg"


def test_detect_gif_bmp():
    for fmt, name in (("GIF", "gif"), ("BMP", "bmp")):
        buf = BytesIO()
        Image.new("RGB", (2, 2), (0, 0, 255)).save(buf, format=fmt)
        assert detect_image_type(buf.getvalue()) == name


def test_detect_webp_riff_container():
    # Hand-built RIFF/WEBP header (we don't depend on a webp encoder).
    data = b"RIFF\x00\x00\x00\x00WEBPVP8 "
    assert detect_image_type(data) == "webp"


def test_detect_rejects_text_and_empty():
    assert detect_image_type(b"") is None
    assert detect_image_type(b"#!/bin/sh\necho hi\n") is None
    assert detect_image_type(b"PK\x03\x04") is None  # zip, not an image


# --- rendering -----------------------------------------------------------

def test_render_preserves_aspect_within_bounds():
    # A wide image clamps to max_cols; rows follow from the half-block
    # pairing (2 px per text row).
    lines = render_image(_png_bytes(200, 100), max_cols=40, max_rows=40)
    assert lines, "expected at least one rendered row"
    assert all(len(line) == len(lines[0]) for line in lines), "ragged rows"
    assert len(lines[0]) <= 40
    # 200×100 scaled to width 40 → 20 px tall → 10 text rows.
    assert len(lines) == 10


def test_render_uses_half_block_glyph_with_fg_and_bg():
    lines = render_image(_png_bytes(4, 4, (255, 0, 0)), max_cols=4, max_rows=4)
    plain = lines[0].plain
    assert plain and set(plain) == {"▀"}
    # Each cell carries a foreground (top px) and background (bottom px)
    # colour; for a solid red image both are red.
    span = lines[0].spans[0]
    style = span.style
    assert style.color is not None and style.bgcolor is not None
    assert style.color.triplet == (255, 0, 0)
    assert style.bgcolor.triplet == (255, 0, 0)


def test_render_handles_tiny_one_pixel_image():
    lines = render_image(_png_bytes(1, 1), max_cols=10, max_rows=10)
    assert len(lines) >= 1
    assert len(lines[0]) >= 1


def test_render_composites_alpha_over_black():
    buf = BytesIO()
    # Fully transparent → should composite to black, not raise.
    Image.new("RGBA", (4, 4), (255, 255, 255, 0)).save(buf, format="PNG")
    lines = render_image(buf.getvalue(), max_cols=4, max_rows=4)
    assert lines[0].spans[0].style.color.triplet == (0, 0, 0)


def test_render_raises_on_garbage():
    with pytest.raises(Exception):
        render_image(b"\x89PNG\r\n\x1a\nnot really a png", max_cols=4, max_rows=4)


# --- caption -------------------------------------------------------------

def test_caption_reports_dims_and_size():
    data = _png_bytes(12, 34)
    cap = image_caption(data, "png")
    assert "PNG" in cap
    assert "12×34px" in cap
    assert f"{len(data):,} bytes" in cap


# --- hex dump ------------------------------------------------------------

def test_hex_dump_format():
    lines = render_hex(b"ABC\x00\xff", width=16)
    assert lines[0].startswith("00000000  ")
    assert "41 42 43 00 ff" in lines[0]
    # ASCII column: printable shown, non-printable as '.'
    assert lines[0].endswith("ABC..")


def test_hex_dump_truncates():
    lines = render_hex(bytes(5000), max_bytes=256, width=16)
    assert any("truncated" in ln for ln in lines)
