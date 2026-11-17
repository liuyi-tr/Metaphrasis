"""Draw the translation back onto the screenshot: cover the original with the background colour and write the translation in place, font size fitted automatically.

Layout is preserved as far as possible: every translated line sits on the baseline of the source line it replaces (so a row of labels stays level and a paragraph keeps its line spacing), bold stays bold, centred button text stays centred; a single line that runs longer than the original uses the empty space to its right first and only then shrinks.

Windows itself draws the text (gdi_text), so it is as sharp as the text around it; PIL is the fallback.
"""
import logging
import math
import os
import re
import statistics
import unicodedata
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

try:
    import gdi_text
except Exception:  # no GDI here: PIL draws the text
    gdi_text = None

log = logging.getLogger(__name__)
_use_gdi = gdi_text is not None

_F = 'C:\\Windows\\Fonts\\'
FONT_CANDIDATES = {  # PIL fallback: font files per target language, (regular, bold); first installed pair wins
    "zh": [(_F + "msyh.ttc", _F + "msyhbd.ttc"), (_F + "Deng.ttf", _F + "Dengb.ttf"), (_F + "simhei.ttf", None), (_F + "simsun.ttc", None)],
    "zh-tw": [(_F + "msjh.ttc", _F + "msjhbd.ttc"), (_F + "msyh.ttc", _F + "msyhbd.ttc")],
    "ja": [(_F + "YuGothM.ttc", _F + "YuGothB.ttc"), (_F + "meiryo.ttc", _F + "meiryob.ttc"), (_F + "msgothic.ttc", None), (_F + "msyh.ttc", _F + "msyhbd.ttc")],
    "ko": [(_F + "malgun.ttf", _F + "malgunbd.ttf"), (_F + "msyh.ttc", _F + "msyhbd.ttc")],
    "latin": [(_F + "segoeui.ttf", _F + "segoeuib.ttf"), (_F + "arial.ttf", _F + "arialbd.ttf"), (_F + "msyh.ttc", _F + "msyhbd.ttc")],
}
_current_lang = "zh"  # set by render_translation per call; _font picks the family from it
BOLD_THRESHOLD = 0.13  # stroke width / font size above this counts as bold. Measured on 5,000 sans-serif lines of known weight (browser-drawn and GDI-drawn): on light backgrounds 0% of regular text and 94% of bold lie above it, on dark ones 4% and 99% (light-on-dark text is drawn heavier, so semibold stays regular on light backgrounds and turns bold on dark)
BG_TOLERANCE = 10  # colour distance to the background that still counts as background (screenshots are lossless, so a flat background is exactly uniform)

# Latin words, numbers and URLs are not split; everything else wraps per character
_TOKEN = re.compile(r"[A-Za-z0-9_\-+.#/:@%&'’]+|\s+|.", re.S)
# punctuation that must not start a line; it hangs on the previous line
_NO_LINE_START = set("，。、；：！？）》」』】〕”’…—,.;:!?)]}%")
_INK_RUN = re.compile(rb"[\x30-\xff]+")  # a run of pixels the text covers by a fifth or more


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
    if _use_gdi:
        return gdi_text.font(size, bold, _current_lang)
    return _font_for(size, bold, _current_lang)


def _mid(font):
    """How far the baseline lies below the vertical middle of a line set in this font."""
    if hasattr(font, "ascent"):
        return (font.ascent - font.descent) / 2
    ascent, descent = font.getmetrics()
    return (ascent - descent) / 2


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
    """Background colour and text colour of a text box."""
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
    ink = [(d, p) for p, d in zip(pixels, dists) if d >= far * 0.5]
    # An icon or emoji inside the box has its own colour: keep the ink that leaves the background in the commonest direction
    dirs = [tuple((p[c] - bg[c]) / math.sqrt(d) for c in range(3)) for d, p in ink]
    main = [statistics.median(u[c] for u in dirs) for c in range(3)]
    length = math.sqrt(sum(v * v for v in main)) or 1
    text = [dp for dp, u in zip(ink, dirs) if sum(a * b for a, b in zip(u, main)) >= 0.975 * length] or ink
    # Edge pixels are blends with the background; on thin text they are the majority and wash the colour out.
    # The real colour is in the stroke cores: the 30% of that ink furthest from the background
    text.sort(key=lambda t: t[0])
    fg = _median_color([p for _, p in text[-max(1, len(text) * 3 // 10):]])
    if _contrast(fg, bg) < 3:
        fg = _readable_on(bg)
    return bg, fg


# ---------- layout analysis ----------
_ASCENDERS = set("bdfhklt")  # lowercase letters taller than capitals
_DESCENDERS = set("gjpqy")  # letters with descenders
_BRACKETS = set("()[]{}|")  # as tall as ascenders and as deep as descenders
_SHALLOW = set(",;_Q$@")  # hang below the baseline too, a little less than letters do
_X_HEIGHT = set("acegmnopqrsuvwxyz.,:;-_~+=<>·•…–— ")  # nothing in these rises above the lowercase height
_PLAIN_LOWER = set("aegmnqruy")  # lowercase letters OCR cannot mistake for their capitals (unlike c o s v w x z)


def _ink_rows(img, box):
    """First and one-past-last pixel row of the ink inside an OCR box.

    OCR boxes are a pixel or two loose, and not by the same amount every time; the ink itself is exact, so two lines in the same style measure the same. Falls back to the box (less its usual slack) when the ink cannot be told apart.
    """
    x0, y0, x1, y1 = box
    top, bottom = max(0, y0 - 2), min(img.height, y1 + 2)
    region = img.crop((x0, top, x1, bottom))
    background = tuple(int(v) for v in ImageStat.Stat(region).median)  # most of a text box is background
    r, g, b = ImageChops.difference(region, Image.new("RGB", region.size, background)).split()
    diff = ImageChops.lighter(ImageChops.lighter(r, g), b)
    strongest = diff.getextrema()[1]
    if strongest >= 40:
        data = diff.point(lambda v: 255 if v > strongest * 0.35 else 0).tobytes()
        w = region.width
        counts = [data.count(b"\xff", row * w, (row + 1) * w) for row in range(region.height)]
        # a row inked from end to end is an underline or a border, not text
        inked = [row for row, n in enumerate(counts) if 0 < n <= 0.8 * w]
        if inked and inked[0] > 0 and inked[-1] < region.height - 1:  # ink touching the edge runs on outside: not this line's alone
            return top + inked[0], top + inked[-1] + 1
    return min(y0 + 1, y1 - 1), max(y1 - 1, y0 + 1)


def _script(text):
    """"cjk", "latin" or "other", by the letters in the text."""
    letters = [c for c in text if c.isalpha()]
    if any(ord(c) >= 0x2E80 for c in letters):
        return "cjk"
    if all(c.isascii() or "LATIN" in unicodedata.name(c, "") for c in letters):
        return "latin"
    return "other"


def _drop(text):
    """How far (in em) the lowest ink of a Latin line hangs below its baseline."""
    if any(c in _DESCENDERS or c in _BRACKETS for c in text):
        return 0.22
    return 0.16 if any(c in _SHALLOW for c in text) else 0.0


def _size(line, ink):
    """Estimate the font size of a source line from the height of its ink.

    How tall the ink is depends on the letters in the line: ink height = (top + drop) * size, with top = 0.74 when there are ascenders or brackets, 0.72 with capitals, digits or dotted letters, 0.52 with nothing but a/c/e-type lowercase. Checked against 16,000 lines of known size (browser-drawn and GDI-drawn, 8 fonts, 12-24 px) and centred on Segoe UI and Arial, whose proportions most interface fonts share: those come out within 1 px 95% of the time, wide faces like Verdana about half a pixel large, serif and narrow faces about 1 px small. Chinese text: ink height = 0.94 * size.
    """
    h = ink[1] - ink[0]
    text = line.text
    script = _script(text)
    if script == "cjk":
        return h / 0.94
    if script == "other":  # not measured: take the ink as spanning ascender to descender
        return h / 0.96
    if any(c in _ASCENDERS or c in _BRACKETS or (c.isalpha() and not c.isascii()) for c in text):
        top = 0.74
    elif all(c in _X_HEIGHT for c in text) and any(c in _PLAIN_LOWER for c in text):
        top = 0.52
    else:
        top = 0.72
    return h / (top + _drop(text))


def _row_sizes(lines, ink):
    """Font size of every source line. Text on one row with similar height usually shares a size (a row of buttons, a table header), and the row's median is steadier than any single line."""
    own = {l.id: _size(l, ink[l.id]) for l in lines}
    result = {}
    for a in lines:
        ha, ca = a.box[3] - a.box[1], (a.box[1] + a.box[3]) / 2
        same = []
        for b in lines:
            hb, cb = b.box[3] - b.box[1], (b.box[1] + b.box[3]) / 2
            if abs(ca - cb) <= 0.3 * min(ha, hb) and max(ha, hb) <= 1.4 * min(ha, hb):
                same.append(own[b.id])
        result[a.id] = statistics.median(same)
    return result


def _baseline(line, ink, size):
    """Estimate the baseline of a source line: the bottom of its ink, less whatever hangs below the baseline.

    Against the same 16,000 lines, 97% of these estimates land within 1 px of the truth.
    """
    script = _script(line.text)
    if script == "cjk":  # ideographs reach a little below the baseline Latin letters stand on
        return ink[1] - 0.11 * size
    if script == "other":  # not measured: the middle of the ink is the middle of the line
        return (ink[0] + ink[1]) / 2 + 0.38 * size
    return ink[1] - _drop(line.text) * size


def _row_baselines(lines, ink, sizes):
    """Baseline of every source line. Lines on one row get a single shared value, so a row of labels cannot come out uneven by the odd pixel."""
    est = {l.id: _baseline(l, ink[l.id], sizes[l.id]) for l in lines}
    order = sorted(lines, key=lambda l: est[l.id])
    result = {}
    i = 0
    while i < len(order):
        first = est[order[i].id]
        tolerance = max(2, 0.15 * sizes[order[i].id])
        j = i + 1
        while j < len(order) and est[order[j].id] - first <= tolerance:
            j += 1
        shared = statistics.median(est[l.id] for l in order[i:j])
        for l in order[i:j]:
            result[l.id] = shared
        i = j
    return result


def _rows(members, baselines, size):
    """Baselines of the rows of one block, top to bottom. Evenly spaced rows (a paragraph) are put on an exact grid."""
    values = sorted(baselines[m.id] for m in members)
    groups = [[values[0]]]
    for v in values[1:]:
        if v - groups[-1][-1] > size * 0.5:
            groups.append([v])
        else:
            groups[-1].append(v)
    rows = [statistics.median(g) for g in groups]
    n = len(rows)
    if n >= 3:
        mean_i, mean_r = (n - 1) / 2, sum(rows) / n
        pitch = sum((i - mean_i) * (r - mean_r) for i, r in enumerate(rows)) / sum((i - mean_i) ** 2 for i in range(n))
        grid = [mean_r + (i - mean_i) * pitch for i in range(n)]
        if max(abs(g - r) for g, r in zip(grid, rows)) <= 1.5:
            return grid
    return rows


def _stroke_width(img, box, bg, fg):
    """Typical stroke width in pixels, to a fraction of a pixel: the ink in each horizontal run through the glyphs, where a pixel the text half covers counts as half.

    Counting whole pixels instead doubles a 1.4 px stem that straddles two pixels: regular text drawn by a browser was taken for bold a third of the time.
    """
    x0, y0, x1, y1 = box
    if y1 - y0 < 6 or x1 <= x0:
        return 0.0
    region = img.crop(box)
    diff = ImageChops.difference(region, Image.new("RGB", region.size, bg)).convert("L")
    full = ImageChops.difference(Image.new("RGB", (1, 1), fg), Image.new("RGB", (1, 1), bg)).convert("L").getpixel((0, 0))
    if full < 16:
        return 0.0
    coverage = diff.point(lambda v: min(255, v * 255 // full)).tobytes()
    w = region.width
    runs = sorted(sum(m.group(0)) for row in range(region.height)
                  for m in _INK_RUN.finditer(coverage, row * w, (row + 1) * w))
    # a little below the median: runs along horizontal strokes are long and pull the middle up
    return runs[len(runs) * 2 // 5] / 255 if runs else 0.0


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


def _centered(members, text_box, span, size):
    """Is the source centred? Single line: left and right margins about equal; multiple lines: the line centres line up."""
    x0, _, x1, _ = text_box
    if len(members) == 1:
        lm, rm = x0 - span[0], span[1] - x1
        return lm >= 4 and rm >= 4 and abs(lm - rm) <= max(4, 0.15 * max(lm, rm))
    centers = [(m.box[0] + m.box[2]) / 2 for m in members]
    widths = [m.box[2] - m.box[0] for m in members]
    return max(centers) - min(centers) <= max(3, size * 0.3) and max(widths) - min(widths) > size


def _analyze(img, px, members, sizes, baselines):
    """Position, font size, colours, weight, alignment and row baselines of one text block (computed once per translation)."""
    width, height = img.size
    x0 = min(m.box[0] for m in members)
    y0 = min(m.box[1] for m in members)
    x1 = max(m.box[2] for m in members)
    y1 = max(m.box[3] for m in members)
    size = statistics.median(sizes[m.id] for m in members)
    pad = max(2, round((y1 - y0) / len(members) * 0.15))
    cover = (max(0, x0 - pad), max(0, y0 - pad), min(width, x1 + pad), min(height, y1 + pad))
    sample = (x0, y0, x1, y1)
    if all(m.text[:1] in _BULLETS for m in members) and x0 + 1.5 * size < x1 - size:
        sample = (round(x0 + 1.5 * size), y0, x1, y1)  # a leading "bullet" is often a coloured icon: keep it out of the text colour
    bg, fg = _colors(img, sample, cover)
    bold = sum(_stroke_width(img, m.box, bg, fg) / sizes[m.id] > BOLD_THRESHOLD for m in members) * 2 > len(members)
    span = _free_span(px, img.size, cover, bg)
    centered = _centered(members, (x0, y0, x1, y1), span, size)
    return (x0, y0, x1, y1), size, pad, cover, bg, fg, bold, span, centered, _rows(members, baselines, size)


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
    global _use_gdi
    if _use_gdi:
        try:
            return _render(img, lines, blocks, memo, lang)
        except OSError:
            log.warning("GDI text drawing failed; using PIL from now on", exc_info=True)
            _use_gdi = False
    return _render(img, lines, blocks, memo, lang)


def _render(img, lines, blocks, memo, lang):
    global _current_lang
    _current_lang = lang if lang in FONT_CANDIDATES else "latin"
    img = img.convert("RGB")
    out = img.copy()
    draw = ImageDraw.Draw(out)
    px = img.load()
    width, height = img.size
    by_id = {l.id: l for l in lines}
    memo = {} if memo is None else memo
    if "sizes" not in memo:
        ink = {l.id: _ink_rows(img, l.box) for l in lines}
        memo["sizes"] = _row_sizes(lines, ink)
        memo["baselines"] = _row_baselines(lines, ink, memo["sizes"])
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
            memo[key] = _analyze(img, px, members, memo["sizes"], memo["baselines"])
        (x0, y0, x1, y1), source_size, pad, cover, bg, fg, bold, span, centered, rows = memo[key]
        margin = max(2, pad)
        if len(members) == 1:
            # single line: may use the same-coloured empty space to the right (both sides when centred)
            area = (span[0] + margin if centered else cover[0] + 1, max(cover[2] - 1, span[1] - margin))
        else:
            area = (cover[0] + 1, cover[2] - 1)
        center_x = (span[0] + span[1]) / 2 if len(members) == 1 else (x0 + x1) / 2

        max_size = max(10, round(source_size))
        min_size = min(max_size, max(10, round(source_size * 0.5)))
        inner_w = max(8, area[1] - area[0])
        font = _font(max_size, bold)
        text_lines = wrap(translation, font, inner_w)
        if len(text_lines) <= len(rows):
            # the usual case: same size as the source, one translated line on each source baseline
            size, bases = max_size, rows[:len(text_lines)]
        else:
            # needs more lines than the source has: shrink until it fits the height the source occupies.
            # The OCR box hugs the glyphs and can be shorter than a real line: reserve at least the height the original size needs
            inner_h = max(8, cover[3] - cover[1], len(rows) * _line_height(max_size))
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
                pitch = _line_height(size)
                middle = cover[1] + 1 + need / 2
            else:
                size, text_lines = fitted
                # spread the lines over the height that is there, no wider than the source's own spacing
                source_pitch = (rows[-1] - rows[0]) / (len(rows) - 1) if len(rows) > 1 else 0
                pitch = max(_line_height(size), min(source_pitch, inner_h / len(text_lines)))
                middle = (rows[0] + rows[-1]) / 2 - _mid(font)  # where the middle of the source text is
            if len(text_lines) <= len(rows):  # fits the source's rows after shrinking: keep each line's middle where it was
                shift = _mid(font) - _mid(_font(size, bold))
                bases = [r - shift for r in rows[:len(text_lines)]]
            else:
                first = middle - pitch * (len(text_lines) - 1) / 2 + _mid(_font(size, bold))
                bases = [first + pitch * i for i in range(len(text_lines))]
        plans.append((cover, bg, fg, size, bold, text_lines, bases, area[0], center_x if centered else None))

    # cover all originals first, then write, so neighbouring blocks don't overwrite each other's text
    for cover, bg, *_ in plans:
        draw.rectangle((cover[0], cover[1], cover[2] - 1, cover[3] - 1), fill=bg)
    items = []
    for cover, bg, fg, size, bold, text_lines, bases, left, center_x in plans:
        font = _font(size, bold)
        for base, text in zip(bases, text_lines):
            x = left if center_x is None else center_x - font.getlength(text) / 2
            items.append((x, base, text, font, fg))
    if _use_gdi:
        return gdi_text.draw(out, items)
    for x, base, text, font, fg in items:
        draw.text((x, base), text, font=font, fill=fg, anchor="ls")
    return out
