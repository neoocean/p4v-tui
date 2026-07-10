"""Terminal ANSI-art preview for image / binary depot files (pure logic).

p4v can render images inline; a terminal can't show real pixels, but it
*can* approximate one with half-block ANSI art. Each character cell uses
the upper-half-block glyph ``▀`` with a foreground colour (the top pixel)
and a background colour (the bottom pixel), so one text row encodes two
pixel rows — doubling the vertical resolution a naive "one cell per pixel"
renderer would get.

The functions here are deliberately UI-agnostic: they take raw bytes and
return ``rich.text.Text`` lines (plus a short metadata caption). The
Textual file viewer feeds those straight into its existing ``RichLog``
batched-render path, so there is no new render surface to fight Textual's
8.x quirks (see ``docs/MEMORY.md``).

Pillow does the decode/resize; it's a lazy import so a build without it
(or a server that never serves images) pays nothing. Detection is by
magic bytes — extensions lie, and ``p4 print`` doesn't hand us one.
"""
from __future__ import annotations

from io import BytesIO

from rich.color import Color
from rich.style import Style
from rich.text import Text

# Upper-half block. Foreground paints the top pixel, background the bottom
# pixel of each 1×2 pixel cell.
_HALF_BLOCK = "▀"  # ▀

# (signature, name). WEBP is special-cased (RIFF container) below.
_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"\x00\x00\x01\x00", "ico"),
]


def detect_image_type(data: bytes) -> str | None:
    """Return a short image-format name from the leading magic bytes, or
    ``None`` if ``data`` doesn't look like an image format we can decode.

    Magic-byte sniffing rather than the extension: ``p4 print`` gives us
    raw content with no filename, and depot filetypes lie often enough
    (a ``.dat`` that's really a PNG) that the bytes are the only honest
    signal.
    """
    if not data:
        return None
    # WEBP: "RIFF"<4-byte size>"WEBP".
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    for sig, name in _MAGIC:
        if data.startswith(sig):
            return name
    return None


def render_image(
    data: bytes,
    *,
    max_cols: int = 80,
    max_rows: int = 46,
) -> list[Text]:
    """Decode ``data`` and return half-block ANSI-art lines fitting within
    ``max_cols`` × ``max_rows`` character cells.

    Aspect ratio is preserved. Because each cell stacks two pixels
    vertically (``▀`` upper-half block), the image is resized to at most
    ``max_cols`` pixels wide and ``2 * max_rows`` pixels tall. Transparent
    images are composited over black so the alpha doesn't read as garbage.

    Raises on a decode failure (callers fall back to the binary summary).
    """
    from PIL import Image  # lazy — optional runtime dependency

    img = Image.open(BytesIO(data))
    img.load()
    # Flatten any animation / palette / alpha onto an opaque RGB canvas.
    img = img.convert("RGBA")
    if img.width == 0 or img.height == 0:
        return []
    background = Image.new("RGBA", img.size, (0, 0, 0, 255))
    img = Image.alpha_composite(background, img).convert("RGB")

    target_w = max(1, max_cols)
    target_h = max(1, max_rows) * 2  # two pixel rows per character row
    scale = min(target_w / img.width, target_h / img.height)
    new_w = max(1, round(img.width * scale))
    new_h = max(2, round(img.height * scale))
    if new_h % 2:  # need an even number of pixel rows to pair cleanly
        new_h += 1
    img = img.resize((new_w, new_h))
    px = img.load()

    lines: list[Text] = []
    for y in range(0, new_h, 2):
        line = Text(no_wrap=True)
        for x in range(new_w):
            top = px[x, y]
            bottom = px[x, y + 1] if y + 1 < new_h else top
            line.append(
                _HALF_BLOCK,
                style=Style(
                    color=Color.from_rgb(top[0], top[1], top[2]),
                    bgcolor=Color.from_rgb(bottom[0], bottom[1], bottom[2]),
                ),
            )
        lines.append(line)
    return lines


def image_caption(data: bytes, img_type: str) -> str:
    """One-line metadata caption (format · pixel dimensions · byte size).

    Pixel dimensions come from a Pillow header read; if that fails we
    still report the format and size so the caption never raises.
    """
    dims = ""
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as probe:
            dims = f"{probe.width}×{probe.height}px · "
    except Exception:  # noqa: BLE001
        dims = ""
    return f"{img_type.upper()} · {dims}{len(data):,} bytes"


def render_hex(data: bytes, *, max_bytes: int = 2048, width: int = 16) -> list[str]:
    """Classic ``offset  hex  ascii`` hex dump of the first ``max_bytes``.

    For binary files that aren't images we still beat "Cannot display":
    a hex window lets the user eyeball a header / magic / embedded string
    without leaving the TUI. Bounded so a multi-MB blob can't stall.
    """
    chunk = data[:max_bytes]
    out: list[str] = []
    for off in range(0, len(chunk), width):
        row = chunk[off : off + width]
        hex_part = " ".join(f"{b:02x}" for b in row)
        hex_part = hex_part.ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        out.append(f"{off:08x}  {hex_part}  {ascii_part}")
    if len(data) > max_bytes:
        out.append("")
        out.append(f"--- (truncated — showing first {max_bytes:,} of {len(data):,} bytes) ---")
    return out
