"""Selection look: rounded thin white border, soft white outer glow, running highlight, size capsule.

Border and glow are white, so only an L (alpha) layer is needed. For speed and crispness at any size, a small template is drawn once (border 4x supersampled) and assembled nine-patch style: corners as is, edges stretched.
"""
import math
import os
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

RADIUS = 8
BORDER = 1.5
BORDER_ALPHA = 0.60
GLOW_SIGMA = 5.0  # about 10 px
GLOW_ALPHA = 0.25
SUPERSAMPLE = 4

FONT_LIGHT = r"C:\Windows\Fonts\msyhl.ttc"  # Microsoft YaHei Light
FONT_REGULAR = r"C:\Windows\Fonts\msyh.ttc"


class Style:
    """Sizes scaled to the screen DPI."""

    def __init__(self, scale):
        self.scale = scale
        self.r = RADIUS * scale
        self.b = BORDER * scale
        self.sigma = GLOW_SIGMA * scale
        self.margin = math.ceil(3 * self.sigma + self.b) + 1  # how far the glow extends outside
        self.corner = self.margin + math.ceil(self.r + self.b) + 2  # side of a nine-patch corner tile
        self.inner_corner = math.ceil(self.r + self.b) + 2  # the part of a corner tile that lies inside the selection


@lru_cache(maxsize=4)
def style(scale):
    return Style(scale)


def _render_layers(scale, iw, ih):
    """Draw the selection look for an inner size of iw x ih directly: returns (border, glow, rounded interior) L layers with the selection at (margin, margin)."""
    st = style(scale)
    m, ss = st.margin, SUPERSAMPLE
    size = (iw + 2 * m, ih + 2 * m)
    rect = (m * ss, m * ss, (m + iw) * ss - 1, (m + ih) * ss - 1)
    big = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    ImageDraw.Draw(big).rounded_rectangle(rect, radius=st.r * ss, outline=255, width=max(1, round(st.b * ss)))
    line = big.reduce(ss)  # solid border, edges anti-aliased
    inside_big = Image.new("L", big.size, 0)
    ImageDraw.Draw(inside_big).rounded_rectangle(rect, radius=st.r * ss, fill=255)
    inside = inside_big.reduce(ss)
    border = line.point(lambda v: round(v * BORDER_ALPHA))
    blur = line.filter(ImageFilter.GaussianBlur(st.sigma))  # glow: blur the border line
    peak = blur.getextrema()[1] or 1
    glow = blur.point(lambda v: min(255, round(v * 255 * GLOW_ALPHA / peak)))
    glow = ImageChops.multiply(glow, ImageChops.invert(inside))  # keep only the part outside the frame; no white haze over the text inside
    return border, glow, inside


@lru_cache(maxsize=4)
def _template(scale):
    st = style(scale)
    inner = 2 * (st.corner - st.margin) + 4
    return _render_layers(scale, inner, inner)


def assemble(kind, w, h, scale):
    """Assemble the template nine-patch style into a full layer for inner size w x h (including the glow margin). kind: 0 border, 1 glow, 2 rounded interior."""
    st = style(scale)
    m, c = st.margin, st.corner
    if w < 2 * (c - m) + 2 or h < 2 * (c - m) + 2:  # too small: draw directly
        return _render_layers(scale, w, h)[kind]
    t = _template(scale)[kind]
    tw, th = t.size
    big_w, big_h = w + 2 * m, h + 2 * m
    out = Image.new("L", (big_w, big_h), 255 if kind == 2 else 0)
    mx, my = tw // 2, th // 2
    out.paste(t.crop((0, 0, c, c)), (0, 0))
    out.paste(t.crop((tw - c, 0, tw, c)), (big_w - c, 0))
    out.paste(t.crop((0, th - c, c, th)), (0, big_h - c))
    out.paste(t.crop((tw - c, th - c, tw, th)), (big_w - c, big_h - c))
    out.paste(t.crop((mx, 0, mx + 1, c)).resize((big_w - 2 * c, c)), (c, 0))
    out.paste(t.crop((mx, th - c, mx + 1, th)).resize((big_w - 2 * c, c)), (c, big_h - c))
    out.paste(t.crop((0, my, c, my + 1)).resize((c, big_h - 2 * c)), (0, c))
    out.paste(t.crop((tw - c, my, tw, my + 1)).resize((c, big_h - 2 * c)), (big_w - c, c))
    return out


def _white(alpha):
    white = Image.new("L", alpha.size, 255)
    return Image.merge("RGBA", (white, white, white, alpha))


def _fits_template(w, h, scale):
    st = style(scale)
    return w >= 2 * (st.corner - st.margin) + 2 and h >= 2 * (st.corner - st.margin) + 2


@lru_cache(maxsize=4)
def _frame_template(scale):
    """Template with border and glow combined (L), plus the four cut-out corners."""
    border, glow, _ = _template(scale)
    alpha = ImageChops.screen(border, glow)
    c = style(scale).corner
    tw, th = alpha.size
    corners = [alpha.crop(b) for b in ((0, 0, c, c), (tw - c, 0, tw, c), (0, th - c, c, th), (tw - c, th - c, tw, th))]
    return alpha, corners


def frame_alpha_pieces(w, h, scale):
    """Nine-patch pieces (alpha L layers) of border + glow that assemble into a frame of any size.

    Returns [(x, y, L image), ...] relative to the selection's top-left (the glow extends outside, so coordinates can be negative). Corners are reused as is; only the four edges are stretched from 1 px strips, so every frame is cheap.
    """
    st = style(scale)
    m, c = st.margin, st.corner
    big_w, big_h = w + 2 * m, h + 2 * m
    if not _fits_template(w, h, scale):  # too small: draw the whole thing
        border, glow, _ = _render_layers(scale, w, h)
        return [(-m, -m, ImageChops.screen(border, glow))]
    alpha, corners = _frame_template(scale)
    tw, th = alpha.size
    mx, my = tw // 2, th // 2
    return [
        (-m, -m, corners[0]), (big_w - c - m, -m, corners[1]),
        (-m, big_h - c - m, corners[2]), (big_w - c - m, big_h - c - m, corners[3]),
        (c - m, -m, alpha.crop((mx, 0, mx + 1, c)).resize((big_w - 2 * c, c))),
        (c - m, big_h - c - m, alpha.crop((mx, th - c, mx + 1, th)).resize((big_w - 2 * c, c))),
        (-m, c - m, alpha.crop((0, my, c, my + 1)).resize((c, big_h - 2 * c))),
        (big_w - c - m, c - m, alpha.crop((tw - c, my, tw, my + 1)).resize((c, big_h - 2 * c))),
    ]


def hint_label(text, scale):
    """Usage hint for the bottom-right corner (RGBA): translucent dark rounded background, small white text."""
    ss = 4
    font = _capsule_font(round(13 * scale * ss))
    h = round(30 * scale) * ss
    w = int(font.getlength(text) + 2 * round(12 * scale) * ss)
    img = Image.new("RGBA", (w, h), (31, 41, 55, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=round(8 * scale) * ss, fill=(31, 41, 55, 215))
    draw.text((w / 2, h / 2), text, font=font, fill=(243, 244, 246, 255), anchor="mm")
    return img.reduce(ss)


def border_pieces(w, h, scale):
    """Border-only pieces (drawn inside the selection) for the result window: relative to the selection's top-left, all within the selection."""
    st = style(scale)
    m = st.margin
    alpha = assemble(0, w, h, scale).crop((m, m, m + w, m + h))
    k = min(st.inner_corner, w // 2, h // 2)
    boxes = [(0, 0, k, k), (w - k, 0, w, k), (0, h - k, k, h), (w - k, h - k, w, h),
             (k, 0, w - k, k), (k, h - k, w - k, h), (0, k, k, h - k), (w - k, k, w, h - k)]
    pieces = []
    for box in boxes:
        if box[2] > box[0] and box[3] > box[1]:
            a = alpha.crop(box)
            white = Image.new("L", a.size, 255)
            pieces.append((box[0], box[1], Image.merge("RGBA", (white, white, white, a))))
    return pieces


def corner_masks(w, h, scale):
    """Rounded-corner masks for the four corners: [(x, y, L mask)] relative to the selection's top-left; 255 = inside the rounded corner."""
    st = style(scale)
    m, k = st.margin, st.inner_corner
    spots = ((0, 0), (w - k, 0), (0, h - k), (w - k, h - k))
    if not _fits_template(w, h, scale):  # too small: draw whole, then cut
        inside = _render_layers(scale, w, h)[2].crop((m, m, m + w, m + h))
        k = min(k, w // 2, h // 2)
        return [(x, y, inside.crop((x, y, x + k, y + k))) for x, y in ((0, 0), (w - k, 0), (0, h - k), (w - k, h - k))]
    inside = _template(scale)[2]
    iw = inside.width - 2 * m
    ih = inside.height - 2 * m
    src = ((m, m), (m + iw - k, m), (m, m + ih - k), (m + iw - k, m + ih - k))
    return [(x, y, inside.crop((sx, sy, sx + k, sy + k))) for (x, y), (sx, sy) in zip(spots, src)]


# ---------- running highlight ----------
class RoundedPath:
    """Centre line of a rounded rectangle's border, addressable by distance travelled. Starts on the top edge after the top-left arc, clockwise."""

    def __init__(self, x0, y0, x1, y1, r):
        r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
        self.parts = [  # (kind, length, params)
            ("line", x1 - x0 - 2 * r, (x0 + r, y0, 1, 0)),
            ("arc", math.pi * r / 2, (x1 - r, y0 + r, r, -90)),
            ("line", y1 - y0 - 2 * r, (x1, y0 + r, 0, 1)),
            ("arc", math.pi * r / 2, (x1 - r, y1 - r, r, 0)),
            ("line", x1 - x0 - 2 * r, (x1 - r, y1, -1, 0)),
            ("arc", math.pi * r / 2, (x0 + r, y1 - r, r, 90)),
            ("line", y1 - y0 - 2 * r, (x0, y1 - r, 0, -1)),
            ("arc", math.pi * r / 2, (x0 + r, y0 + r, r, 180)),
        ]
        self.length = sum(p[1] for p in self.parts)

    def point(self, d):
        d %= self.length
        for kind, length, args in self.parts:
            if d <= length or length == 0 and d == 0:
                if kind == "line":
                    x, y, dx, dy = args
                    return x + dx * d, y + dy * d
                cx, cy, r, a0 = args
                a = math.radians(a0 + (d / length * 90 if length else 0))
                return cx + r * math.cos(a), cy + r * math.sin(a)
            d -= length
        return self.point(0)


def light_mask(path, center, length, scale, bounds):
    """A highlight segment of length `length` centred at perimeter position `center` (bright white, fading at both ends, soft halo).

    Returns (x0, y0, x1, y1, L layer) within bounds (w, h).
    """
    ss = 2
    core = 1.25 * scale  # core radius
    halo = 2.5 * scale  # halo
    n = max(8, int(length / 1.0))
    samples = [(i / (n - 1), path.point(center + (i / (n - 1) - 0.5) * length)) for i in range(n)]
    pad = core + 3 * halo + 2
    xs = [p[0] for _, p in samples]
    ys = [p[1] for _, p in samples]
    x0, y0 = max(0, int(min(xs) - pad)), max(0, int(min(ys) - pad))
    x1, y1 = min(bounds[0], int(max(xs) + pad) + 1), min(bounds[1], int(max(ys) + pad) + 1)
    img = Image.new("L", ((x1 - x0) * ss, (y1 - y0) * ss), 0)
    draw = ImageDraw.Draw(img)
    rr = core * ss
    # paint from dark to bright so bright points win (a max); the ends fade naturally
    for u, (x, y) in sorted(samples, key=lambda s: math.sin(math.pi * s[0])):
        a = round(255 * math.sin(math.pi * u) ** 2)
        if a:
            px, py = (x - x0) * ss, (y - y0) * ss
            draw.ellipse((px - rr, py - rr, px + rr, py + rr), fill=a)
    glow = img.filter(ImageFilter.GaussianBlur(halo * ss)).point(lambda v: min(255, round(v * 1.4)))
    return x0, y0, x1, y1, ImageChops.lighter(img, glow).reduce(ss)


# ---------- size capsule ----------
@lru_cache(maxsize=2)
def _capsule_font(px):
    path = FONT_LIGHT if os.path.exists(FONT_LIGHT) else FONT_REGULAR
    return ImageFont.truetype(path, px) if os.path.exists(path) else ImageFont.load_default(px)


def capsule(text, scale):
    """Small translucent dark rounded capsule with thin white text (RGBA)."""
    ss = 4
    font = _capsule_font(round(12 * scale * ss))
    tw = font.getlength(text)
    h = round(22 * scale) * ss
    w = int(tw + 2 * round(10 * scale) * ss)
    img = Image.new("RGBA", (w, h), (17, 24, 39, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=(17, 24, 39, 178))
    draw.text((w / 2, h / 2), text, font=font, fill=(255, 255, 255, 240), anchor="mm")
    return img.reduce(ss)
