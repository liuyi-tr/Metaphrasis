"""The program's icon, drawn in code (no picture file): a fountain-pen nib that has just written  >_  by hand, black on
a white rounded square. It is drawn once, large, and scaled down to whatever size is asked for."""
import math

from PIL import Image, ImageDraw

INK, PAPER, EDGE = (17, 24, 39, 255), (255, 255, 255, 255), (203, 208, 216, 255)
_S = 1024  # the size it is drawn at
_master = None


def _cubic(p0, p1, p2, p3, n=30):
    points = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
        points.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return points


def _nib(height):
    """A fountain-pen nib pointing down, as a mask (white = metal): a sharp point, tines, shoulders, a waist, and a
    band where it meets the pen; the slit runs from the breather hole to the point. The picture is square, so that it
    can be turned about its middle. Coordinates below: the nib is 2 units tall, its point at (0, 1)."""
    k = height / 2
    side = int(height) + 8
    mask = Image.new("L", (side, side), 0)
    d = ImageDraw.Draw(mask)
    c = side / 2

    def at(x, y):
        return c + x * k, c + y * k

    right = (_cubic((0.27, -0.66), (0.27, -0.50), (0.56, -0.48), (0.56, -0.22)) +   # the waist out to the shoulder
             _cubic((0.56, -0.22), (0.56, 0.00), (0.23, 0.58), (0.0, 1.0)))         # the tine down to the point
    d.polygon([at(x, y) for x, y in right] + [at(-x, y) for x, y in reversed(right)], fill=255)
    d.rounded_rectangle(at(-0.36, -1.0) + at(0.36, -0.76), radius=0.06 * k, fill=255)   # the band
    hx, hy = at(0, -0.12)
    r = 0.105 * k
    d.ellipse((hx - r, hy - r, hx + r, hy + r), fill=0)                             # the breather hole
    tx, ty = at(0, 1.0)
    half = 0.028 * k
    d.polygon([(hx - half, hy), (hx + half, hy), (tx + half / 2, ty), (tx - half / 2, ty)], fill=0)   # the slit
    return mask


def _stroke(d, points, width):
    """One stroke of the pen along the points: round, and thinner where it starts and ends, as ink is."""
    last = len(points) - 1
    for i, (x, y) in enumerate(points):
        r = width / 2 * (0.58 + 0.42 * math.sin(math.pi * i / last))
        d.ellipse((x - r, y - r, x + r, y + r), fill=INK)


def _turn(a, b, c, n=160):
    """A stroke from a to c that turns at b, the corner a little rounded, as a hand makes it."""
    before = (a[0] + (b[0] - a[0]) * 0.88, a[1] + (b[1] - a[1]) * 0.88)
    after = (b[0] + (c[0] - b[0]) * 0.12, b[1] + (c[1] - b[1]) * 0.12)
    points = []
    for i in range(n + 1):
        t = i / n
        if t < 0.42:
            u = t / 0.42
            points.append((a[0] + (before[0] - a[0]) * u, a[1] + (before[1] - a[1]) * u))
        elif t > 0.58:
            u = (t - 0.58) / 0.42
            points.append((after[0] + (c[0] - after[0]) * u, after[1] + (c[1] - after[1]) * u))
        else:
            u = (t - 0.42) / 0.16
            points.append(((1 - u) ** 2 * before[0] + 2 * (1 - u) * u * b[0] + u * u * after[0],
                           (1 - u) ** 2 * before[1] + 2 * (1 - u) * u * b[1] + u * u * after[1]))
    return points


def _bowed(a, b, bow, n=140):
    """A stroke from a to b that bows a little to one side, as a hand draws a "straight" line."""
    cx = (a[0] + b[0]) / 2 - (b[1] - a[1]) * bow
    cy = (a[1] + b[1]) / 2 + (b[0] - a[0]) * bow
    return [((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * cx + t * t * b[0], (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * cy + t * t * b[1])
            for t in (i / n for i in range(n + 1))]


def _draw():
    s = _S
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 5, fill=PAPER, outline=EDGE, width=int(s * 0.016))
    width = s * 0.062
    _stroke(d, _turn((s * 0.170, s * 0.572), (s * 0.330, s * 0.700), (s * 0.176, s * 0.840)), width)   # >
    end = (s * 0.560, s * 0.842)
    _stroke(d, _bowed((s * 0.390, s * 0.852), end, -0.06), width)                                        # _
    # the pen, its point resting where the underscore ends, leaning the way one holds it
    height, lean = s * 0.56, 30
    tip = (end[0] + s * 0.004, end[1] - s * 0.008)
    a = math.radians(lean)
    cx, cy = tip[0] + height / 2 * math.sin(a), tip[1] - height / 2 * math.cos(a)
    mask = _nib(height).rotate(-lean, resample=Image.BICUBIC)
    icon.paste(Image.new("RGBA", mask.size, INK), (round(cx - mask.width / 2), round(cy - mask.height / 2)), mask)
    return icon


def draw(size):
    """The icon as a size x size RGBA picture."""
    global _master
    if _master is None:
        _master = _draw()
    return _master.resize((size, size), Image.LANCZOS)
