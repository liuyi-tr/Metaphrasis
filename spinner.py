"""Payment-success style loading animation (after iPhone): a green comet arc spins; on success it closes into a solid green disc, draws a white check mark and fades out.

Window, transparency and refresh sync are handled by layered.LayeredAnimation; this only draws.
"""
import math
import time
from functools import lru_cache

from PIL import Image, ImageDraw

import layered

GREEN = (52, 199, 89)  # iOS system green
SUPERSAMPLE = 4  # draw at 4x and downscale for smooth edges


def _ease_out(p):
    return 1 - (1 - p) ** 3


def _ease_out_back(p, overshoot=1.7):
    p -= 1
    return 1 + p * p * ((overshoot + 1) * p + overshoot)


def _clamp01(v):
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def _geometry(size):
    s = size * SUPERSAMPLE
    return s, s / 2, s * 0.36, s * 0.075  # canvas side, centre, ring radius (line centre), line width


@lru_cache(maxsize=8)
def _assets(size):
    """Assets drawn once: a faint base ring and a comet arc with a fading tail (head at 3 o'clock). Each frame only rotates it."""
    s, c, r, w = _geometry(size)
    box = (c - r - w / 2, c - r - w / 2, c + r + w / 2, c + r + w / 2)
    # transparent pixels are green too, so scaling and rotation don't blend in dark edges
    track = Image.new("RGBA", (s, s), (*GREEN, 0))
    ImageDraw.Draw(track).ellipse(box, outline=(*GREEN, 40), width=int(w))
    comet = Image.new("RGBA", (s, s), (*GREEN, 0))
    draw = ImageDraw.Draw(comet)
    steps = 72
    for i in range(steps):  # tail to head, more opaque towards the head
        f = (i + 1) / steps
        a0 = -PaySpinner.COMET * (1 - i / steps)
        a1 = -PaySpinner.COMET * (1 - (i + 1) / steps) + 0.6
        draw.arc(box, a0, a1, fill=(*GREEN, round(255 * f ** 1.8)), width=int(w))
    draw.ellipse((c + r - w / 2, c - w / 2, c + r + w / 2, c + w / 2), fill=(*GREEN, 255))  # round head
    return track, comet


class PaySpinner(layered.LayeredAnimation):
    """Spin -> success() closes the ring and draws the check mark -> fades out and closes; close() fades out. All methods may be called from the main thread at any time."""

    SPIN_PERIOD = 0.9  # seconds per lap
    COMET = 150  # comet arc length (degrees)
    ENTER = 0.22  # appear: grow and fade in
    CLOSE_RING = 0.30  # after success: decelerate and fill the arc into a full circle
    FILL = (0.24, 0.50)  # solid green disc pops in
    CHECK = (0.40, 0.70)  # check mark drawn in one stroke
    FADE = (1.10, 1.36)  # hold, then fade out
    CANCEL_FADE = 0.16  # fade out on cancel

    def __init__(self, center, size):
        self.size = int(size)
        self.success_at = None
        self.closing_at = None
        _assets(self.size)  # prepare assets first so the first frame doesn't hitch
        super().__init__(center[0] - size / 2, center[1] - size / 2, self.size, self.size)

    # -- public API (main thread) --
    def success(self):
        if self.success_at is None and self.closing_at is None:
            self.success_at = time.perf_counter()

    def move_center(self, cx, cy):
        self.move_to(cx - self.size / 2, cy - self.size / 2)

    def close(self):
        """Fade out and close (or close immediately while the check mark is animating)."""
        if self.success_at is not None:
            self.close_now()
        elif self.closing_at is None:
            self.closing_at = time.perf_counter()

    # -- per-frame drawing (animation thread) --
    def draw(self, now):
        frame = self._render(now)
        if frame is None:
            return None
        return 1.0, [(0, 0, self.size, self.size, layered.premultiplied_bgra(frame))]

    def _head_angle(self, now):
        """Angle of the comet head (clockwise, degrees). After success it decelerates smoothly from the current speed, no jump."""
        speed = 360 / self.SPIN_PERIOD
        if self.success_at is None or now <= self.success_at:
            return (now - self.start) * speed
        p = _clamp01((now - self.success_at) / self.CLOSE_RING)
        # a cubic ease-out starts at 3 x distance / duration; set that equal to the speed, so it turns speed x duration / 3 more degrees in total
        return (self.success_at - self.start) * speed + speed * self.CLOSE_RING / 3 * _ease_out(p)

    def _render(self, now):
        s, c, r, w = _geometry(self.size)
        track, comet = _assets(self.size)
        head = self._head_angle(now)
        img = track.copy()
        scale, fade = 1.0, 1.0

        enter = _clamp01((now - self.start) / self.ENTER)
        if enter < 1:  # appear: grow and fade in
            scale, fade = 0.6 + 0.4 * _ease_out(enter), _ease_out(enter)

        if self.success_at is None:
            img.alpha_composite(comet.rotate(-head, resample=Image.BICUBIC, fillcolor=(*GREEN, 0)))
            if self.closing_at is not None:  # cancel: fade out
                p = _clamp01((now - self.closing_at) / self.CANCEL_FADE)
                if p >= 1:
                    return None
                fade *= 1 - p
        else:
            k = now - self.success_at
            p = _ease_out(_clamp01(k / self.CLOSE_RING))
            # the comet fades while a solid arc grows back from the head until the circle is complete
            tail = comet.rotate(-head, resample=Image.BICUBIC, fillcolor=(*GREEN, 0))
            if p > 0:
                tail.putalpha(tail.getchannel("A").point(lambda v: int(v * (1 - p))))
            img.alpha_composite(tail)
            draw = ImageDraw.Draw(img)
            if p > 0:
                self._ring(draw, c, r, w, (*GREEN, 255), head - 360 * p, 360 * p)
            fill_p = _clamp01((k - self.FILL[0]) / (self.FILL[1] - self.FILL[0]))
            if fill_p > 0:
                rr = (r + w / 2) * _ease_out_back(fill_p)
                draw.ellipse((c - rr, c - rr, c + rr, c + rr), fill=(*GREEN, 255))
            check_p = _clamp01((k - self.CHECK[0]) / (self.CHECK[1] - self.CHECK[0]))
            if check_p > 0:
                self._check(draw, c, r, _ease_out(check_p))
            fade_p = _clamp01((k - self.FADE[0]) / (self.FADE[1] - self.FADE[0]))
            if fade_p >= 1:
                return None
            fade *= 1 - _ease_out(fade_p)
            scale *= 1 + 0.08 * _ease_out(fade_p)

        if abs(scale - 1.0) > 1e-3:
            n = max(1, round(s * scale))
            scaled = img.resize((n, n), Image.BICUBIC)
            img = Image.new("RGBA", (s, s), (*GREEN, 0))
            off = (s - n) // 2
            if n <= s:
                img.paste(scaled, (off, off))
            else:
                img = scaled.crop((-off, -off, -off + s, -off + s))
        frame = img.reduce(SUPERSAMPLE)
        if fade < 0.999:
            frame.putalpha(frame.getchannel("A").point(lambda v: int(v * fade)))
        return frame

    @staticmethod
    def _ring(draw, c, r, w, color, start, extent):
        """Draw an arc with round ends. Angles: 0 at 3 o'clock, clockwise."""
        box = (c - r - w / 2, c - r - w / 2, c + r + w / 2, c + r + w / 2)
        if extent >= 359.5:
            draw.ellipse(box, outline=color, width=int(w))
            return
        draw.arc(box, start, start + extent, fill=color, width=int(w))
        for a in (start, start + extent):
            x, y = c + r * math.cos(math.radians(a)), c + r * math.sin(math.radians(a))
            draw.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=color)

    @staticmethod
    def _check(draw, c, r, progress):
        """White check mark, drawn in one stroke as progress goes 0 -> 1."""
        pts = [(c - 0.42 * r, c + 0.02 * r), (c - 0.12 * r, c + 0.32 * r), (c + 0.44 * r, c - 0.30 * r)]
        seg = [math.dist(pts[0], pts[1]), math.dist(pts[1], pts[2])]
        remaining = (seg[0] + seg[1]) * progress
        w = r * 0.17
        path = [pts[0]]
        for (a, b), length in zip((pts[:2], pts[1:]), seg):
            if remaining <= 0:
                break
            f = min(1.0, remaining / length)
            path.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            remaining -= length
        draw.line(path, fill=(255, 255, 255, 255), width=int(w), joint="curve")
        for x, y in (path[0], path[-1]):
            draw.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=(255, 255, 255, 255))
