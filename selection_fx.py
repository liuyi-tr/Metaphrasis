"""Visual effects layer during selection: 40% dimming outside, rounded thin white border with glow, size capsule, usage hint in the corner.

Drawn frame by frame on its own thread at the screen refresh rate, reading the live mouse position each frame (no waiting for mouse events) and repainting only the ring around the changed border, so dragging stays smooth at any size.
The frozen screenshot underneath (a Tk window) only receives mouse input and never repaints.
"""
import time

from PIL import Image, ImageChops

import frame_style
import layered
import winapi

DIM_ALPHA = round(255 * 0.40)


def _clip(box, width, height):
    x0, y0, x1, y1 = box
    return max(0, x0), max(0, y0), min(width, x1), min(height, y1)


def _expand(box, d):
    return box[0] - d, box[1] - d, box[2] + d, box[3] + d


def _nonempty(box):
    return box[2] > box[0] and box[3] > box[1]


class SelectionFx(layered.LayeredAnimation):
    """After the mouse is released this layer stays on as the dimming for the waiting phase (border, glow, capsule and hint removed; the waiting effects take over). It is the same window throughout, so switching from selection to waiting doesn't flicker. finish() fades out on completion, close() fades out quickly on cancel."""

    HINT = "拖动鼠标框选要翻译的区域　　Esc / 右键 取消"
    FADE_OUT = 0.30
    CANCEL_FADE = 0.15

    def __init__(self, origin, size, scale):
        """origin, size: top-left and size of the whole virtual desktop (the screenshot is that big)."""
        self.scale = scale
        self.origin = origin
        self.anchor = None  # mouse-down position (relative to the screenshot); None until dragging starts
        self.final = None  # selection fixed at mouse-up
        self.rect = None  # selection currently drawn
        self.waiting = False  # switched to the waiting-phase dimming
        self._waiting_drawn = False
        self.finish_at = None
        self.cancel_at = None
        self._cap = None  # current size capsule: (x0, y0, x1, y1, image)
        self.cursor = winapi.cursor_pos  # reads the mouse position (replaceable in tests)
        st = frame_style.style(scale)
        self.margin, self.inner = st.margin, st.inner_corner
        super().__init__(origin[0], origin[1], size[0], size[1])

    # -- main thread --
    def press(self, x, y):
        self.final = None
        self.anchor = (x, y)

    def reset(self):
        self.anchor = None

    def release(self, box):
        self.final = box

    def to_waiting(self):
        self.waiting = True

    def finish(self):
        if self.finish_at is None and self.cancel_at is None:
            self.finish_at = time.perf_counter()

    def close(self):
        if self.finish_at is None and self.cancel_at is None:
            self.cancel_at = time.perf_counter()

    # -- animation thread --
    def prepare(self):
        # hint in the bottom-right corner of the screen under the mouse
        cx, cy = winapi.cursor_pos()
        _, _, right, bottom = winapi.work_area(cx, cy)
        img = frame_style.hint_label(self.HINT, self.scale)
        gap = round(16 * self.scale)
        x1, y1 = right - self.origin[0] - gap, bottom - self.origin[1] - gap
        self.hint = (x1 - img.width, y1 - img.height, x1, y1, img)

    def _current_rect(self):
        if self.final is not None:
            return self.final
        if self.anchor is None:
            return None
        cx, cy = self.cursor()
        x = min(max(round(cx) - self.origin[0], 0), self.width)
        y = min(max(round(cy) - self.origin[1], 0), self.height)
        ax, ay = self.anchor
        rect = (min(ax, x), min(ay, y), max(ax, x), max(ay, y))
        return rect if rect[2] - rect[0] >= 1 and rect[3] - rect[1] >= 1 else None

    def _capsule_for(self, rect):
        if rect is None:
            return None
        x0, y0, x1, y1 = rect
        img = frame_style.capsule(f"{x1 - x0} × {y1 - y0}", self.scale)
        gap = round(8 * self.scale)
        cy = y0 - img.height - gap if y0 - img.height - gap >= 0 else y0 + gap
        cx = x0 if cy < y0 else x0 + gap
        return cx, cy, cx + img.width, cy + img.height, img

    def _dirty(self, old, new):
        """Region that changes between two frames: both selections expanded by a ring, minus their shared interior (always the transparent hole)."""
        boxes = [_expand(r, self.margin + 1) for r in (old, new) if r]
        if not boxes:
            return []
        u = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        if not (old and new):
            return [u]
        k = self.inner + 1
        a, b = _expand(old, -k), _expand(new, -k)
        inner = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
        if not _nonempty(inner):
            return [u]
        return [r for r in ((u[0], u[1], u[2], inner[1]), (u[0], inner[3], u[2], u[3]),
                            (u[0], inner[1], inner[0], inner[3]), (inner[2], inner[1], u[2], inner[3])) if _nonempty(r)]

    def _compose(self, region, rect, frame, corners, cap, hint=True):
        """Compose one region: dimming (black) + border and glow (white) + size capsule + hint; returns premultiplied BGRA bytes."""
        x0, y0, x1, y1 = region
        size = (x1 - x0, y1 - y0)
        dim = Image.new("L", size, DIM_ALPHA)
        white = Image.new("L", size, 0)
        if rect:
            rx0, ry0, rx1, ry1 = rect
            hole = Image.new("L", size, 0)
            inside = _clip((rx0 - x0, ry0 - y0, rx1 - x0, ry1 - y0), size[0], size[1])
            if _nonempty(inside):
                hole.paste(255, inside)
            for cx, cy, mask in corners:  # rounded corners: don't cut the hole outside the corner arcs
                hole.paste(mask, (rx0 + cx - x0, ry0 + cy - y0))
            dim = ImageChops.multiply(dim, ImageChops.invert(hole))
            for px, py, piece in frame:
                white.paste(piece, (rx0 + px - x0, ry0 + py - y0))
        alpha = ImageChops.screen(white, dim)
        img = Image.merge("RGBa", (white, white, white, alpha))  # black dimming + white border: after premultiplying, the colour equals the amount of white
        for o in (cap, self.hint if hint else None):  # blend only inside the label's patch (no alpha conversion for the rest)
            if not (o and o[0] < x1 and o[2] > x0 and o[1] < y1 and o[3] > y0):
                continue
            ox0, oy0, ox1, oy1, label = o
            box = (max(ox0, x0) - x0, max(oy0, y0) - y0, min(ox1, x1) - x0, min(oy1, y1) - y0)
            part = img.crop(box).convert("RGBA")
            part.alpha_composite(label.crop((box[0] + x0 - ox0, box[1] + y0 - oy0,
                                             box[2] + x0 - ox0, box[3] + y0 - oy0)))
            img.paste(part.convert("RGBa"), box[:2])
        r, g, b, a = img.split()
        return Image.merge("RGBA", (b, g, r, a)).tobytes()

    def _fade(self, now):
        """Fade-out on completion / cancel; returns the overall alpha, None once fully faded."""
        for at, length, eased in ((self.finish_at, self.FADE_OUT, True), (self.cancel_at, self.CANCEL_FADE, False)):
            if at is not None:
                p = (now - at) / length
                if p >= 1:
                    return None
                return 1 - (1 - (1 - p) ** 3 if eased else p)
        return 1.0

    def draw(self, now):
        alpha = self._fade(now)
        if alpha is None:
            return None
        if self.waiting:
            if self._waiting_drawn or self.rect is None:
                return alpha, []
            # switch to waiting: drop border, glow, capsule and hint; keep the dimming (selection cut out as a rounded hole)
            self._waiting_drawn = True
            rect = self.rect
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            corners = frame_style.corner_masks(w, h, self.scale)
            regions = [_expand(rect, self.margin + 1)] + [o[:4] for o in (self._cap, self.hint) if o]
            updates = []
            for region in regions:
                region = _clip(region, self.width, self.height)
                if _nonempty(region):
                    updates.append((*region, self._compose(region, rect, [], corners, None, hint=False)))
            return alpha, updates
        rect = self._current_rect()
        if self.frames == 0:  # first frame: dim the whole screen + hint
            dim = Image.new("L", (self.width, self.height), DIM_ALPHA)
            black = Image.new("L", dim.size, 0)
            updates = [(0, 0, self.width, self.height, Image.merge("RGBA", (black, black, black, dim)).tobytes())]
            hx0, hy0, hx1, hy1, _ = self.hint
            box = _clip((hx0, hy0, hx1, hy1), self.width, self.height)
            if _nonempty(box):
                updates.append((*box, self._compose(box, None, [], [], None)))
            return 1.0, updates
        if rect == self.rect:
            return 1.0, []
        w, h = (rect[2] - rect[0], rect[3] - rect[1]) if rect else (0, 0)
        frame = frame_style.frame_alpha_pieces(w, h, self.scale) if rect else []
        corners = frame_style.corner_masks(w, h, self.scale) if rect else []
        cap = self._capsule_for(rect)
        regions = self._dirty(self.rect, rect)
        for c in (self._cap, cap):  # the capsule's old and new positions need repainting too
            if c:
                regions.append(c[:4])
        updates = []
        for region in regions:
            region = _clip(region, self.width, self.height)
            if _nonempty(region):
                updates.append((*region, self._compose(region, rect, frame, corners, cap)))
        self.rect, self._cap = rect, cap
        return 1.0, updates
