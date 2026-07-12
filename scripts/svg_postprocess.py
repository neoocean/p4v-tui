"""Font-independent post-processing for Textual/Rich-exported terminal SVGs.

Ported from the pytmux screenshot infrastructure
(`scripts/gen_screenshots.py` there) so p4v-tui's landing-site screenshots
render crisply everywhere — including GitHub's `<img>` "image security
mode" and browsers that don't apply the SVG's embedded `@font-face`.

`postprocess(svg)` runs three idempotent passes:

  ① `_fix_cjk_textlength` — Rich's `export_svg` computes `<text>` `textLength`
     from `len(text)`, not display cells, so wide (2-cell) glyphs get squeezed
     to half width and overlap. We split each mixed `<text>` into width-uniform
     runs and re-anchor them on the cell grid.
  ② `_bake_glyphs` — bake every `<text>` into font-independent geometry:
       • U+2500–259F box-drawing / block / shade → computed <line>/<rect>
         (fills the cell exactly, so box outlines never break and blocks have
         no seams — the thing font glyphs get wrong on the web).
       • narrow ASCII/symbols → Fira Code glyph <path> (bundled in scripts/fonts).
       • CJK (2-cell) → Apple SD Gothic Neo glyph <path>, centred in its box.
     Glyphs the fonts lack (or when fontTools/fonts are absent, e.g. non-macOS)
     stay as `<text>` with the font-family fallback below.

Requires `fonttools` and the bundled Fira Code faces (scripts/fonts/); the CJK
and symbol fallbacks use macOS system fonts. Without them, only pass ① runs and
`<text>` is kept (still correct, just viewer-font-dependent). macOS-local
generation is authoritative.
"""
from __future__ import annotations

import html as _html
import os
import re as _re

from rich.cells import cell_len as _cell_len

# ── font-family fallback: insert a CJK face into the chain ────────────────
_FONT_FAMILY_RE = _re.compile(r'font-family:\s*Fira Code,\s*monospace')
_FONT_FAMILY_NEW = 'font-family: Fira Code, "Apple SD Gothic Neo", monospace'

_TEXT_RE = _re.compile(r'<text\b([^>]*)>([^<]*)</text>')
_X_RE = _re.compile(r'\bx="([0-9.]+)"')
_TL_RE = _re.compile(r'\btextLength="([0-9.]+)"')


def _svg_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace("\xa0", "&#160;"))


def _fix_cjk_textlength(svg):
    """Split wide-mixed <text> into width-uniform runs re-anchored on the grid.

    Idempotent: an already-processed wide run (has lengthAdjust=
    "spacingAndGlyphs") is skipped, else re-running would double its textLength.
    """

    def repl(m):
        attrs, content = m.group(1), m.group(2)
        if "spacingAndGlyphs" in attrs:
            return m.group(0)            # already processed → leave (idempotent)
        xm = _X_RE.search(attrs)
        tlm = _TL_RE.search(attrs)
        if not xm or not tlm:
            return m.group(0)            # no x/textLength → leave
        text = _html.unescape(content)
        n = len(text)
        cells = _cell_len(text)
        if n == 0 or cells == 0 or cells == n:
            return m.group(0)            # no wide chars → leave
        line_x = float(xm.group(1))
        cell_w = float(tlm.group(1)) / n  # px per cell (Rich's char_width)
        # group into maximal same-width runs (0-width combiners absorbed narrow)
        runs, cur, cur_wide = [], [], None
        for ch in text:
            wide = _cell_len(ch) >= 2
            if cur and wide != cur_wide:
                runs.append((cur_wide, "".join(cur)))
                cur = []
            cur.append(ch)
            cur_wide = wide
        if cur:
            runs.append((cur_wide, "".join(cur)))
        out, col = [], 0
        for wide, seg in runs:
            seg_cells = _cell_len(seg)
            seg_x = line_x + col * cell_w
            seg_attrs = _TL_RE.sub(f'textLength="{seg_cells * cell_w:g}"',
                                   _X_RE.sub(f'x="{seg_x:g}"', attrs))
            if wide:
                seg_attrs += ' lengthAdjust="spacingAndGlyphs"'
            out.append(f"<text{seg_attrs}>{_svg_escape(seg)}</text>")
            col += seg_cells
        return "".join(out)

    return _TEXT_RE.sub(repl, svg)


# ---------- <text> → font-independent vector/shape baking ----------
_CJK_TTC = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
_CJK_FACE = {"regular": 0, "bold": 6}
_CJK_FILL = 0.90        # fraction of the 2-cell box a CJK glyph fills
_SYM_TTF = "/System/Library/Fonts/Apple Symbols.ttf"  # ▸ ⚙ etc. fallback
_MENLO_TTC = "/System/Library/Fonts/Menlo.ttc"        # ❯ ✻ ✕ etc. fallback
_MENLO_FACE = {"regular": 0, "bold": 1}
_FIRA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FIRA_TTF = {"regular": os.path.join(_FIRA_DIR, "FiraCode-Regular.ttf"),
             "bold": os.path.join(_FIRA_DIR, "FiraCode-Bold.ttf")}
_CLASS_CSS_RE = _re.compile(r"\.(terminal-\d+-r\d+)\s*\{([^}]*)\}")
_MATRIX_FS_RE = _re.compile(r"-matrix\s*\{[^}]*?font-size:\s*([\d.]+)")
_CSS_FILL_RE = _re.compile(r"fill:\s*([^;]+)")
_CLASS_RE = _re.compile(r'\bclass="([^"]*)"')
_Y_RE = _re.compile(r'\by="([0-9.]+)"')
_CLIP_RE = _re.compile(r'\bclip-path="([^"]*)"')
_CLIPDEF_RE = _re.compile(
    r'<clipPath id="([^"]*-line-\d+)">\s*<rect x="0" y="([0-9.]+)"'
    r' width="\d+" height="([0-9.]+)"')
_bake_font_cache = {}


def _bake_font(kind, weight):
    """Font info dict for ('fira'|'cjk'|'sym'|'menlo', weight), or None."""
    key = (kind, weight)
    if key in _bake_font_cache:
        return _bake_font_cache[key]
    info = None
    try:
        from fontTools.ttLib import TTFont, TTCollection
        from fontTools.pens.svgPathPen import SVGPathPen
        if kind == "cjk":
            f = TTCollection(_CJK_TTC).fonts[_CJK_FACE[weight]]
        elif kind == "sym":
            f = TTFont(_SYM_TTF)
        elif kind == "menlo":
            f = TTCollection(_MENLO_TTC).fonts[_MENLO_FACE[weight]]
        else:
            f = TTFont(_FIRA_TTF[weight])
        info = {
            "cmap": f.getBestCmap(),
            "glyphset": f.getGlyphSet(),
            "hmtx": f["hmtx"].metrics,
            "upm": f["head"].unitsPerEm,
            "pen": SVGPathPen,
        }
    except Exception:                                 # noqa: BLE001
        info = None
    _bake_font_cache[key] = info
    return info


def _glyph_path(kind, ch, weight, x, y, fsize, fill, *,
                center_in=None, cell_top=None, pitch=None):
    """Glyph outline as <path>. None if font/glyph absent, '' if empty (space)."""
    info = _bake_font(kind, weight)
    if info is None:
        return None
    gn = info["cmap"].get(ord(ch))
    if gn is None:
        return None
    s = fsize / info["upm"]
    pen = info["pen"](info["glyphset"])
    info["glyphset"][gn].draw(pen)
    d = pen.getCommands()
    if not d:
        return ""                                     # blank path (space etc.)
    adv0 = info["hmtx"][gn][0] * s
    if center_in is not None and adv0 > 0:
        s *= _CJK_FILL * center_in / adv0             # uniform scale (no stretch)
        x += max(0.0, (center_in - info["hmtx"][gn][0] * s) / 2.0)
        if cell_top is not None and pitch is not None:
            from fontTools.pens.boundsPen import BoundsPen
            bp = BoundsPen(info["glyphset"])
            info["glyphset"][gn].draw(bp)
            if bp.bounds:
                _, ymn, _, ymx = bp.bounds
                y = cell_top + (pitch - (ymx - ymn) * s) / 2.0 + ymx * s
    fa = f' fill="{fill}"' if fill else ""
    return (f'<path{fa} transform="translate({x:g} {y:g}) '
            f'scale({s:g} {-s:g})" d="{d}"/>')


_LT = 1.8       # light box-drawing stroke width
_DOFF = 1.7     # double-line centre offset
_BLK_EPS = 0.8  # block-fill overlap (bleed into neighbour to kill AA seams)


def _box_block(o, x0, top, w, h, fill):
    """A U+2500–259F glyph as shapes (None if unsupported → font path fallback)."""
    c = fill or "#000000"
    r, b = x0 + w, top + h
    cx, cy = x0 + w / 2, top + h / 2
    E = _BLK_EPS

    def ln(d):
        return f'<path d="{d}" stroke="{c}" stroke-width="{_LT:g}" fill="none"/>'

    def rc(x, yy, ww, hh, op=None):
        o2 = f' fill-opacity="{op}"' if op is not None else ""
        return f'<rect x="{x:g}" y="{yy:g}" width="{ww:g}" height="{hh:g}" fill="{c}"{o2}/>'

    # ----- box-drawing (light) -----
    if o == 0x2500:  # ─
        return ln(f"M{x0:g},{cy:g} H{r:g}")
    if o == 0x2502:  # │
        return ln(f"M{cx:g},{top:g} V{b:g}")
    if o == 0x250C:  # ┌
        return ln(f"M{cx:g},{b:g} L{cx:g},{cy:g} L{r:g},{cy:g}")
    if o == 0x2510:  # ┐
        return ln(f"M{cx:g},{b:g} L{cx:g},{cy:g} L{x0:g},{cy:g}")
    if o == 0x2514:  # └
        return ln(f"M{cx:g},{top:g} L{cx:g},{cy:g} L{r:g},{cy:g}")
    if o == 0x2518:  # ┘
        return ln(f"M{cx:g},{top:g} L{cx:g},{cy:g} L{x0:g},{cy:g}")
    if o == 0x251C:  # ├
        return ln(f"M{cx:g},{top:g} V{b:g} M{cx:g},{cy:g} H{r:g}")
    if o == 0x2524:  # ┤
        return ln(f"M{cx:g},{top:g} V{b:g} M{x0:g},{cy:g} H{cx:g}")
    if o == 0x252C:  # ┬
        return ln(f"M{x0:g},{cy:g} H{r:g} M{cx:g},{cy:g} V{b:g}")
    if o == 0x2534:  # ┴
        return ln(f"M{x0:g},{cy:g} H{r:g} M{cx:g},{top:g} V{cy:g}")
    if o == 0x253C:  # ┼
        return ln(f"M{x0:g},{cy:g} H{r:g} M{cx:g},{top:g} V{b:g}")
    # rounded corners
    rr = min(w, h) * 0.45
    if o == 0x256D:  # ╭
        return ln(f"M{cx:g},{b:g} L{cx:g},{cy + rr:g} Q{cx:g},{cy:g} "
                  f"{cx + rr:g},{cy:g} L{r:g},{cy:g}")
    if o == 0x256E:  # ╮
        return ln(f"M{cx:g},{b:g} L{cx:g},{cy + rr:g} Q{cx:g},{cy:g} "
                  f"{cx - rr:g},{cy:g} L{x0:g},{cy:g}")
    if o == 0x2570:  # ╰
        return ln(f"M{cx:g},{top:g} L{cx:g},{cy - rr:g} Q{cx:g},{cy:g} "
                  f"{cx + rr:g},{cy:g} L{r:g},{cy:g}")
    if o == 0x256F:  # ╯
        return ln(f"M{cx:g},{top:g} L{cx:g},{cy - rr:g} Q{cx:g},{cy:g} "
                  f"{cx - rr:g},{cy:g} L{x0:g},{cy:g}")
    # ----- box-drawing (double) -----
    d = _DOFF
    if o == 0x2550:  # ═
        return (ln(f"M{x0:g},{cy - d:g} H{r:g}") + ln(f"M{x0:g},{cy + d:g} H{r:g}"))
    if o == 0x2551:  # ║
        return (ln(f"M{cx - d:g},{top:g} V{b:g}") + ln(f"M{cx + d:g},{top:g} V{b:g}"))
    if o == 0x2554:  # ╔
        return (ln(f"M{cx - d:g},{b:g} L{cx - d:g},{cy - d:g} L{r:g},{cy - d:g}")
                + ln(f"M{cx + d:g},{b:g} L{cx + d:g},{cy + d:g} L{r:g},{cy + d:g}"))
    if o == 0x2557:  # ╗
        return (ln(f"M{cx + d:g},{b:g} L{cx + d:g},{cy - d:g} L{x0:g},{cy - d:g}")
                + ln(f"M{cx - d:g},{b:g} L{cx - d:g},{cy + d:g} L{x0:g},{cy + d:g}"))
    if o == 0x255A:  # ╚
        return (ln(f"M{cx - d:g},{top:g} L{cx - d:g},{cy + d:g} L{r:g},{cy + d:g}")
                + ln(f"M{cx + d:g},{top:g} L{cx + d:g},{cy - d:g} L{r:g},{cy - d:g}"))
    if o == 0x255D:  # ╝
        return (ln(f"M{cx + d:g},{top:g} L{cx + d:g},{cy + d:g} L{x0:g},{cy + d:g}")
                + ln(f"M{cx - d:g},{top:g} L{cx - d:g},{cy - d:g} L{x0:g},{cy - d:g}"))
    # ----- blocks (fill) -----
    if o == 0x2588:  # █
        return rc(x0, top, w + E, h + E)
    if o == 0x2580:  # ▀
        return rc(x0, top, w + E, h / 2 + E)
    if o == 0x2584:  # ▄
        return rc(x0, cy, w + E, h / 2 + E)
    if o == 0x2590:  # ▐
        return rc(cx, top, w / 2 + E, h + E)
    _LEFT = {0x258F: 1, 0x258E: 2, 0x258D: 3, 0x258C: 4,
             0x258B: 5, 0x258A: 6, 0x2589: 7}
    if o in _LEFT:
        return rc(x0, top, w * _LEFT[o] / 8 + E, h + E)
    _LOW = {0x2581: 1, 0x2582: 2, 0x2583: 3, 0x2584: 4,
            0x2585: 5, 0x2586: 6, 0x2587: 7}
    if o in _LOW:
        fr = _LOW[o] / 8
        return rc(x0, b - h * fr, w + E, h * fr + E)
    if o == 0x2594:  # ▔
        return rc(x0, top, w + E, h / 8 + E)
    if o == 0x2595:  # ▕
        return rc(r - w / 8, top, w / 8 + E, h + E)
    _SHADE = {0x2591: 0.25, 0x2592: 0.5, 0x2593: 0.75}
    if o in _SHADE:
        return rc(x0, top, w + E, h + E, op=_SHADE[o])
    # quadrants
    if o == 0x2598:  # ▘
        return rc(x0, top, w / 2 + E, h / 2 + E)
    if o == 0x259D:  # ▝
        return rc(cx, top, w / 2 + E, h / 2 + E)
    if o == 0x2596:  # ▖
        return rc(x0, cy, w / 2 + E, h / 2 + E)
    if o == 0x2597:  # ▗
        return rc(cx, cy, w / 2 + E, h / 2 + E)
    if o == 0x259B:  # ▛
        return rc(x0, top, w + E, h / 2 + E) + rc(x0, cy, w / 2 + E, h / 2 + E)
    if o == 0x259C:  # ▜
        return rc(x0, top, w + E, h / 2 + E) + rc(cx, cy, w / 2 + E, h / 2 + E)
    if o == 0x2599:  # ▙
        return rc(x0, top, w / 2 + E, h + E) + rc(cx, cy, w / 2 + E, h / 2 + E)
    if o == 0x259F:  # ▟
        return rc(cx, top, w / 2 + E, h + E) + rc(x0, cy, w / 2 + E, h / 2 + E)
    return None


def _bake_glyphs(svg):
    """Bake every <text> to font-independent vectors/shapes (unbakeable kept).

    ① U+2500–259F → shapes  ② narrow → Fira path  ③ CJK → ASDGN path (centred).
    Idempotent: an already-baked SVG only has its remaining <text> reprocessed.
    """
    fsm = _MATRIX_FS_RE.search(svg)
    if not fsm:
        return svg
    fsize = float(fsm.group(1))
    weight_of, fill_of = {}, {}
    for cls, css in _CLASS_CSS_RE.findall(svg):
        weight_of[cls] = ("bold" if ("bold" in css or "font-weight: 7" in css)
                          else "regular")
        fm = _CSS_FILL_RE.search(css)
        if fm:
            fill_of[cls] = fm.group(1).strip()
    clip_top = {}
    rects = []
    for cid, ry, rh in _CLIPDEF_RE.findall(svg):
        clip_top[cid] = float(ry)
        rects.append(float(ry))
    rects.sort()
    pitch = (rects[1] - rects[0]) if len(rects) >= 2 else fsize * 1.22
    cw_votes = {}
    for am, content in ((m.group(1), m.group(2)) for m in _TEXT_RE.finditer(svg)):
        tlm = _TL_RE.search(am)
        if not tlm:
            continue
        n = _cell_len(_html.unescape(content))
        if n:
            cw = round(float(tlm.group(1)) / n, 3)
            cw_votes[cw] = cw_votes.get(cw, 0) + 1
    cell_w = max(cw_votes, key=cw_votes.get) if cw_votes else fsize * 0.61
    ascent = fsize * 0.925

    def repl(m):
        attrs, content = m.group(1), m.group(2)
        text = _html.unescape(content)
        xm = _X_RE.search(attrs)
        ym = _Y_RE.search(attrs)
        clsm = _CLASS_RE.search(attrs)
        if not (xm and ym and clsm) or not text:
            return m.group(0)
        x, y, cls = float(xm.group(1)), float(ym.group(1)), clsm.group(1)
        weight = weight_of.get(cls, "regular")
        fill = fill_of.get(cls)
        clipm = _CLIP_RE.search(attrs)
        clip = clipm.group(1) if clipm else None
        top = None
        if clip:
            cid = clip[clip.find("#") + 1:].rstrip(")")
            top = clip_top.get(cid)
        if top is None:
            top = y - ascent
        out, col = [], 0
        for ch in text:
            cells = _cell_len(ch)
            if cells <= 0:
                continue
            cx0 = x + col * cell_w
            col += cells
            o = ord(ch)
            frag = None
            if ch == " ":
                frag = ""
            elif cells >= 2:                          # ③ CJK
                frag = _glyph_path("cjk", ch, weight, cx0, y, fsize, fill,
                                   center_in=cells * cell_w,
                                   cell_top=top, pitch=pitch)
            elif 0x2500 <= o <= 0x259F:               # ① shapes
                frag = _box_block(o, cx0, top, cell_w, pitch, fill)
            if frag is None and cells < 2:            # ② narrow → Fira → symbol
                for _k in ("fira", "sym", "menlo"):
                    frag = _glyph_path(_k, ch, weight, cx0, y, fsize, fill)
                    if frag is not None:
                        break
            if frag is None:                          # unbakeable → keep <text>
                frag = (f'<text class="{cls}" x="{cx0:g}" y="{y:g}" '
                        f'textLength="{cells * cell_w:g}" '
                        f'lengthAdjust="spacingAndGlyphs">'
                        f'{_svg_escape(ch)}</text>')
            out.append(frag)
        return "".join(out)

    return _TEXT_RE.sub(repl, svg)


def postprocess(svg):
    """font-family fallback → CJK textLength fix → glyph baking (idempotent)."""
    svg = _FONT_FAMILY_RE.sub(_FONT_FAMILY_NEW, svg)
    svg = _fix_cjk_textlength(svg)
    svg = _bake_glyphs(svg)
    return svg


def postprocess_file(path):
    """Rewrite ``path`` in place with :func:`postprocess` (no-op on read error)."""
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return
    new = postprocess(svg)
    if new != svg:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
