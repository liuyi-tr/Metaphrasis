"""Border effects while translating: a soft white outer glow plus a short highlight that runs clockwise along the border (about 2 s per lap).

finish(): glow and highlight fade out together over 0.3 s, then the callback runs on the main thread (to reveal the translation).
"""
import time

from PIL import Image, ImageChops

import frame_style
import layered


def _ease_out(p):
    return 1 - (1 - p) ** 3


class BorderGlow(layered.LayeredAnimation):
    LAP = 2.0  # seconds per lap
    FADE_IN = 0.18
    FADE_OUT = 0.30  # finished: fade out
    CANCEL_FADE = 0.15  # cancelled / failed: fade out

    def __init__(self, screen_box, scale, notify, seamless=False, below=None):
        """screen_box: selection (x, y, w, h); notify(fn): run fn on the main thread.

        seamless: True when this follows a selection that already showed the same glow, so start at full brightness without fading in.
        below: stay under this window (the selection overlay) until it closes, then set to None to come back on top.
        """
        x, y, w, h = screen_box
        st = frame_style.style(scale)
        self.box_w, self.box_h, self.scale, self.m = w, h, scale, st.margin
        self.notify = notify
        self.fade_in = 0.0 if seamless else self.FADE_IN
        self.below = below
        self.finish_at = None
        self.cancel_at = None
        self.on_finished = None
        self._prev = None
        super().__init__(x - st.margin, y - st.margin, w + 2 * st.margin, h + 2 * st.margin)

    # -- main thread --
    def finish(self, on_finished=None):
        if self.finish_at is None and self.cancel_at is None:
            self.on_finished = on_finished
            self.finish_at = time.perf_counter()

    def close(self):
        if self.finish_at is None and self.cancel_at is None:
            self.cancel_at = time.perf_counter()

    # -- animation thread --
    def prepare(self):
        st = frame_style.style(self.scale)
        self.glow = frame_style.assemble(1, self.box_w, self.box_h, self.scale)
        half = st.b / 2  # the highlight runs along the centre of the border line
        self.path = frame_style.RoundedPath(self.m + half, self.m + half, self.m + self.box_w - half,
                                            self.m + self.box_h - half, st.r - half)
        self.light_len = max(60 * self.scale, min(200 * self.scale, self.path.length * 0.14))

    def draw(self, now):
        updates = []
        if self.frames == 0:
            updates.append((0, 0, self.width, self.height, layered.white_bgra(self.glow)))
        alpha = _ease_out(min(1.0, (now - self.start) / self.fade_in)) if self.fade_in else 1.0
        if self.finish_at is not None:
            p = (now - self.finish_at) / self.FADE_OUT
            if p >= 1:
                if self.on_finished:
                    self.notify(self.on_finished)
                return None
            alpha *= 1 - _ease_out(p)
        elif self.cancel_at is not None:
            p = (now - self.cancel_at) / self.CANCEL_FADE
            if p >= 1:
                return None
            alpha *= 1 - p

        # highlight: only repaint the small patch covered by the previous and current frame
        center = (now - self.start) / self.LAP % 1 * self.path.length
        x0, y0, x1, y1, light = frame_style.light_mask(self.path, center, self.light_len, self.scale,
                                                       (self.width, self.height))
        ux0, uy0, ux1, uy1 = x0, y0, x1, y1
        if self._prev:
            px0, py0, px1, py1 = self._prev
            ux0, uy0, ux1, uy1 = min(ux0, px0), min(uy0, py0), max(ux1, px1), max(uy1, py1)
        self._prev = (x0, y0, x1, y1)
        layer = Image.new("L", (ux1 - ux0, uy1 - uy0), 0)
        layer.paste(light, (x0 - ux0, y0 - uy0))
        region = ImageChops.screen(self.glow.crop((ux0, uy0, ux1, uy1)), layer)
        updates.append((ux0, uy0, ux1, uy1, layered.white_bgra(region)))
        return alpha, updates


class DimLayer(layered.LayeredAnimation):
    """Keeps dimming everything outside the selection by 40% while translating (as during selection), so the white border, glow and highlight stay visible.

    Sits right below the result window: popups and toasts that open later stay above it and are not dimmed.
    """

    DIM = 0.40
    FADE_IN = 0.18
    FADE_OUT = 0.30
    CANCEL_FADE = 0.15

    def __init__(self, virtual_screen, screen_box, below_hwnd, seamless=False):
        vx, vy, vw, vh = virtual_screen
        x, y, w, h = screen_box
        self.hole = (x - vx, y - vy, x - vx + w, y - vy + h)
        self.below = below_hwnd
        self.fade_in = 0.0 if seamless else self.FADE_IN
        self.finish_at = None
        self.cancel_at = None
        super().__init__(vx, vy, vw, vh)

    def finish(self):
        if self.finish_at is None and self.cancel_at is None:
            self.finish_at = time.perf_counter()

    def close(self):
        if self.finish_at is None and self.cancel_at is None:
            self.cancel_at = time.perf_counter()

    def prepare(self):
        alpha = Image.new("L", (self.width, self.height), round(255 * self.DIM))
        alpha.paste(0, self.hole)  # the selection itself is shown by the result window above; cut a hole here
        black = Image.new("L", alpha.size, 0)
        self.data = Image.merge("RGBA", (black, black, black, alpha)).tobytes()

    def draw(self, now):
        updates = [(0, 0, self.width, self.height, self.data)] if self.frames == 0 else []
        alpha = _ease_out(min(1.0, (now - self.start) / self.fade_in)) if self.fade_in else 1.0
        for at, length, ease in ((self.finish_at, self.FADE_OUT, True), (self.cancel_at, self.CANCEL_FADE, False)):
            if at is not None:
                p = (now - at) / length
                if p >= 1:
                    return None
                alpha *= 1 - (_ease_out(p) if ease else p)
        return alpha, updates
