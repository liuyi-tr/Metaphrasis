"""Draw the translation back onto the screenshot: cover the original with the background colour and write the translation in place, font size fitted automatically.

Layout is preserved as far as possible: bold stays bold, centred button text stays centred; a single line that runs longer than the original uses the empty space to its right first and only then shrinks.
"""
import math
import os
import re
import statistics
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFont

_F = 'C:\\Windows\\Fonts\\'
FONT_CANDIDATES = {  # fonts per target language, (regular, bold); first installed pair wins
    "zh": [(_F + "msyh.ttc", _F + "msyhbd.ttc"), (_F + "Deng.ttf", _F + "Dengb.ttf"), (_F + "simhei.ttf", None), (_F + "simsun.ttc", None)],
    "zh-tw": [(_F + "msjh.ttc", _F + "msjhbd.ttc"), (_F + "msyh.ttc", _F + "msyhbd.ttc")],
    "ja": [(_F + "YuGothM.ttc", _F + "YuGothB.ttc"), (_F + "meiryo.ttc", _F + "meiryob.ttc"), (_F + "msgothic.ttc", None), (_F + "msyh.ttc", _F + "msyhbd.ttc")],
    "ko": [(_F + "malgun.ttf", _F + "malgunbd.ttf"), (_F + "msyh.ttc", _F + "msyhbd.ttc")],
    "latin": [(_F + "segoeui.ttf", _F + "segoeuib.ttf"), (_F + "arial.ttf", _F + "arialbd.ttf"), (_F + "msyh.ttc", _F + "msyhbd.ttc")],
}
_current_lang = "zh"  # set by render_translation per call; _font picks the family from it
BOLD_THRESHOLD = 0.13  # stroke width / font size above this counts as bold (regular fonts measure at most ~0.129; better to miss than to misjudge)
BG_TOLERANCE = 10  # colour distance to the background that still counts as background (screenshots are lossless, so a flat background is exactly uniform)

# Latin words, numbers and URLs are not split; everything else wraps per character
_TOKEN = re.compile(r"[A-Za-z0-9_\-+.#/:@%&'’]+|\s+|.", re.S)
# punctuation that must not start a line; it hangs on the previous line
_NO_LINE_START = set("，。、；：！？）》」』】〕”’…—,.;:!?)]}%")
_INK_RUN = re.compile(rb"\xff+")


@lru_cache(maxsize=8)
def _font_files(lang="zh"):
    for regular, bold in FONT_CANDIDATES.get(lang, FONT_CANDIDATES["latin"]):
        if os.path.exists(regular):
            return regular, bold if bold and os.path.exists(bold) else None
    return None, None


@lru_cache(maxsize=512)
def _font_for(size, bold, lang):
    regular, bold_file = _font_files(lang)
    if regular is None:
        return ImageFont.load_default(size)
    return ImageFont.truetype(bold_file if bold and bold_file else regular, size)


def _font(size, bold=False):
    return _font_for(size, bold, _current_lang)


def normalize(text):
    """Is the translation effectively identical to the source (code, brand names)? Then don't redraw."""
    return re.sub(r"[\W_]+", "", text).lower()


def wrap(text, font, max_width):
    lines = []
    for para in text.splitlines() or [""]:
        cur = ""
        for tok in _TOKEN.findall(para):
            if not cur and tok.isspace():
                continue
            cand = cur + tok
            if font.getlength(cand) <= max_width or (cur and tok in _NO_LINE_START):
                cur = cand
                continue
            if cur:
                lines.append(cur.rstrip())
                cur = ""
            if tok.isspace():
                continue
            while len(tok) > 1 and font.getlength(tok) > max_width:  # force-split an overlong word
                cut = len(tok) - 1
                while cut > 1 and font.getlength(tok[:cut]) > max_width:
                    cut -= 1
                lines.append(tok[:cut])
                tok = tok[cut:]
            cur = tok
        lines.append(cur.rstrip())
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    return lines


def _line_height(size):
    return max(size + 2, round(size * 1.28))


def _fit(text, width, height, max_size, min_size, bold):
    """Largest font size in [min_size, max_size] that fits width x height."""
    best = None
    lo, hi = min_size, max_size
    while lo <= hi:
        mid = (lo + hi) // 2
        lines = wrap(text, _font(mid, bold), width)
        if len(lines) * _line_height(mid) <= height:
            best = (mid, lines)
            lo = mid + 1
        else:
            hi = mid - 1
    return best


# ---------- colours ----------
def _median_color(pixels):
    n = len(pixels)
    return tuple(sorted(p[c] for p in pixels)[n // 2] for c in range(3))


def _ring_pixels(img, box, thickness=2):
    """Pixels in a ring just outside the box, used to estimate the background colour."""
    x0, y0, x1, y1 = box
    w, h = img.size
    regions = []
    if y0 - thickness >= 0:
        regions.append((x0, y0 - thickness, x1, y0))
    if y1 + thickness <= h:
        regions.append((x0, y1, x1, y1 + thickness))
    if x0 - thickness >= 0:
        regions.append((x0 - thickness, y0, x0, y1))
    if x1 + thickness <= w:
        regions.append((x1, y0, x1 + thickness, y1))
    pixels = []
    for r in regions:
        if r[2] > r[0] and r[3] > r[1]:
            pixels.extend(img.crop(r).get_flattened_data())
    return pixels


def _sample(img, box, limit=8000):
    region = img.crop(box)
    area = region.width * region.height
    if area > limit:
        s = math.sqrt(limit / area)
        region = region.resize((max(1, int(region.width * s)), max(1, int(region.height * s))), Image.NEAREST)
    return list(region.get_flattened_data())


def _luminance(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _readable_on(bg):
    return (0, 0, 0) if _contrast((0, 0, 0), bg) >= _contrast((255, 255, 255), bg) else (255, 255, 255)


def _colors(img, text_box, cover_box):
    ring = _ring_pixels(img, cover_box)
    if ring:
        bg = _median_color(ring)
    else:  # the box fills the whole screenshot: use the most common colour
        bg = max(img.crop(cover_box).quantize(16).convert("RGB").getcolors(1 << 16))[1]
    pixels = _sample(img, text_box)
    dists = [sum((p[c] - bg[c]) ** 2 for c in range(3)) for p in pixels]
    far = max(dists, default=0)
    if far < 60 ** 2:
        return bg, _readable_on(bg)
    fg = _median_color([p for p, d in zip(pixels, dists) if d >= far * 0.5])
    if _contrast(fg, bg) < 3:
        fg = _readable_on(bg)
    return bg, fg


# ---------- layout analysis ----------
_ASCENDERS = set("bdfhklt")  # lowercase letters taller than capitals
_DESCENDERS = set("gjpqy")  # letters with descenders


def _em(line):
    """Estimate the source line height in pixels. OCR boxes shrink with the glyphs they contain; scale back by the kinds of letters present.

    With a box containing both ascenders and descenders as 1, measured over 7 fonts: only tall letters like l/d ~ 0.83, only capitals and digits ~ 0.77, only descenders ~ 0.81, only a/c/e-type lowercase ~ 0.75 (conservative values; better too small than too large).
    """
    h = line.box[3] - line.box[1]
    text = line.text
    if not text.isascii():
        return h
    ascender = any(c in _ASCENDERS for c in text)
    capital = any(c.isupper() or c.isdigit() for c in text)
    deep = any(c in _DESCENDERS for c in text)
    if deep:
        return h if ascender or capital else h / 0.81
    if ascender:
        return h / 0.83
    if capital:
        return h / 0.77
    return h / 0.75


def _row_ems(lines):
    """Text on the same row with similar height usually shares a font size (a row of buttons, a table header).

    OCR boxes only ever come out too short, never too tall, so take the largest estimate in the row to avoid single words being drawn small.
    """
    ems = {l.id: _em(l) for l in lines}
    result = {}
    for a in lines:
        ha, ca = a.box[3] - a.box[1], (a.box[1] + a.box[3]) / 2
        best = ems[a.id]
        for b in lines:
            hb, cb = b.box[3] - b.box[1], (b.box[1] + b.box[3]) / 2
            if abs(ca - cb) <= 0.3 * min(ha, hb) and max(ha, hb) <= 1.4 * min(ha, hb):
                best = max(best, min(ems[b.id], ems[a.id] * 1.4))
        result[a.id] = best
    return result


def _stroke_width(img, box, bg, fg):
    """Typical stroke width in pixels from horizontal scans: bold is clearly wider."""
    x0, y0, x1, y1 = box
    if y1 - y0 < 6 or x1 <= x0:
        return 0.0
    region = img.crop(box)
    diff = ImageChops.difference(region, Image.new("RGB", region.size, bg)).convert("L")
    full = ImageChops.difference(Image.new("RGB", (1, 1), fg), Image.new("RGB", (1, 1), bg)).convert("L").getpixel((0, 0))
    if full == 0:
        return 0.0
    mask = diff.point(lambda v: 255 if v > full / 2 else 0).tobytes()
    w = region.width
    runs = [len(m.group(0)) for row in range(region.height)
            for m in _INK_RUN.finditer(mask, row * w, (row + 1) * w)]
    return statistics.median(runs) if runs else 0.0


def _is_background(p, bg):
    return max(abs(p[c] - bg[c]) for c in range(3)) <= BG_TOLERANCE


def _free_span(px, size, cover, bg):
    """Scan outwards from the text to see how far the same background colour continues (e.g. to a button's edges)."""
    x0, y0, x1, y1 = cover
    width = size[0]
    left, right = 0, width
    for k in range(5):
        y = min(y1 - 1, y0 + (y1 - y0) * k // 4)
        x = x0 - 1
        while x >= 0 and _is_background(px[x, y], bg):
            x -= 1
        left = max(left, x + 1)
        x = x1
        while x < width and _is_background(px[x, y], bg):
            x += 1
        right = min(right, x)
    return left, right


def _centered(members, text_box, span, line_h):
    """Is the source centred? Single line: left and right margins about equal; multiple lines: the line centres line up."""
    x0, _, x1, _ = text_box
    if len(members) == 1:
        lm, rm = x0 - span[0], span[1] - x1
        return lm >= 4 and rm >= 4 and abs(lm - rm) <= max(4, 0.15 * max(lm, rm))
    centers = [(m.box[0] + m.box[2]) / 2 for m in members]
    widths = [m.box[2] - m.box[0] for m in members]
    return max(centers) - min(centers) <= max(3, line_h * 0.3) and max(widths) - min(widths) > line_h


def _analyze(img, px, members, row_ems):
    """Position, colours, weight and alignment of one text block (computed once per translation)."""
    width, height = img.size
    x0 = min(m.box[0] for m in members)
    y0 = min(m.box[1] for m in members)
    x1 = max(m.box[2] for m in members)
    y1 = max(m.box[3] for m in members)
    ems = sorted(row_ems[m.id] for m in members)
    line_h = ems[len(ems) // 2]
    pad = max(2, round((y1 - y0) / len(members) * 0.15))
    cover = (max(0, x0 - pad), max(0, y0 - pad), min(width, x1 + pad), min(height, y1 + pad))
    bg, fg = _colors(img, (x0, y0, x1, y1), cover)
    bold = sum(_stroke_width(img, m.box, bg, fg) / _em(m) > BOLD_THRESHOLD for m in members) * 2 > len(members)
    span = _free_span(px, img.size, cover, bg)
    centered = _centered(members, (x0, y0, x1, y1), span, line_h)
    return (x0, y0, x1, y1), line_h, pad, cover, bg, fg, bold, span, centered


_BULLETS = "•·●○◦▪■□◆◇►▶➢✓✔-–—*"


def _strip_dup_bullet(source, translation):
    """The source bullet was left outside the OCR box (still visible in the image) and the translation brings its own: drop the translation's, or there would be two."""
    t = translation.lstrip()
    if t[:1] in _BULLETS and t[1:2].isspace() and source.lstrip()[:1] not in _BULLETS:
        return t[1:].lstrip()
    return translation


def render_translation(img, lines, blocks, memo=None, lang="zh"):
    """Return a new image with every text block covered and replaced by its translation.

    memo: pass the same dict when rendering the same screenshot repeatedly (streaming) to reuse the analysis.
    lang: target language code (settings.lang_code); selects the font family.
    """
    global _current_lang
    _current_lang = lang if lang in FONT_CANDIDATES else "latin"
    img = img.convert("RGB")
    out = img.copy()
    draw = ImageDraw.Draw(out)
    px = img.load()
    width, height = img.size
    by_id = {l.id: l for l in lines}
    memo = {} if memo is None else memo
    if "row_ems" not in memo:
        memo["row_ems"] = _row_ems(lines)
    plans = []

    for block in blocks:
        members = [by_id[i] for i in block.line_ids if i in by_id]
        if not members:
            continue
        source = " ".join(m.text for m in members)
        translation = _strip_dup_bullet(source, block.translation)
        if normalize(source) == normalize(translation):
            continue
        key = tuple(m.id for m in members)
        if key not in memo:
            memo[key] = _analyze(img, px, members, memo["row_ems"])
        (x0, y0, x1, y1), line_h, pad, cover, bg, fg, bold, span, centered = memo[key]
        margin = max(2, pad)
        if len(members) == 1:
            # single line: may use the same-coloured empty space to the right (both sides when centred)
            area = (span[0] + margin if centered else cover[0] + 1, max(cover[2] - 1, span[1] - margin))
        else:
            area = (cover[0] + 1, cover[2] - 1)
        center_x = (span[0] + span[1]) / 2 if len(members) == 1 else (x0 + x1) / 2

        max_size = max(10, round(line_h * 0.95))
        min_size = min(max_size, max(10, round(line_h * 0.5)))
        inner_w = max(8, area[1] - area[0])
        # the OCR box hugs the glyphs and can be shorter than a real line: reserve at least the height the original size needs (expanding from the text's centre line)
        inner_h = max(8, cover[3] - cover[1], len(members) * _line_height(max_size))
        fitted = _fit(translation, inner_w, inner_h, max_size, min_size, bold)
        if fitted is None:
            # doesn't fit at all: minimum size, extend the background downwards, but never over another text block below
            limit = height
            for l in lines:
                if l.id not in block.line_ids and l.box[1] >= cover[3] - 1 and l.box[2] > cover[0] and l.box[0] < cover[2]:
                    limit = min(limit, max(cover[3], l.box[1] - pad))
            size = min_size
            text_lines = wrap(translation, _font(size, bold), inner_w)
            need = len(text_lines) * _line_height(size)
            if cover[1] + need + 2 > limit:  # would touch the block below: shrink further, down to 8 px
                smaller = _fit(translation, inner_w, limit - cover[1] - 2, min_size, 8, bold)
                if smaller is not None:
                    size, text_lines = smaller
                    need = len(text_lines) * _line_height(size)
            cover = (cover[0], cover[1], cover[2], min(height, cover[1] + need + 2))
            inner_h = cover[3] - cover[1]
        else:
            size, text_lines = fitted
        plans.append((cover, bg, fg, size, bold, text_lines, inner_h, area[0], center_x if centered else None, block))

    # cover all originals first, then write, so neighbouring blocks don't overwrite each other's text
    for cover, bg, *_ in plans:
        draw.rectangle((cover[0], cover[1], cover[2] - 1, cover[3] - 1), fill=bg)
    for cover, bg, fg, size, bold, text_lines, inner_h, left, center_x, block in plans:
        font = _font(size, bold)
        lh = _line_height(size)
        top = (cover[1] + cover[3]) / 2 - lh * len(text_lines) / 2  # vertically centred on the original
        for i, text in enumerate(text_lines):
            x = left if center_x is None else center_x - font.getlength(text) / 2
            draw.text((x, top + lh * (i + 0.5)), text, font=font, fill=fg, anchor="lm")
    return out
