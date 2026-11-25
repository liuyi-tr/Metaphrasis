"""Screen translation: lives in the tray; press Alt+~ to select a screen region and have an LLM translate it.

Launch with start.bat (through the watchdog); right-click the tray icon for the menu.
"""
import re
import ctypes
import dataclasses
import gc
import io
import logging
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from tkinter import filedialog, ttk

from PIL import Image, ImageEnhance, ImageGrab, ImageTk

import appicon
import autostart
import border_fx
import frame_style
import i18n
import layered
import selection_fx
import settings
import spinner
import winapi
from i18n import t

log = logging.getLogger("fanyi")

UI_FONT = "Microsoft YaHei UI"
ACCENT = "#2563EB"
ACCENT_HOVER = "#1D4ED8"
DARK = "#111827"
TOAST_BG = "#1F2937"
TOAST_FG = "#F3F4F6"
TOAST_BORDER = "#374151"
TOAST_ALPHA = 0.86
INSTALL_BAT = "install.bat"


def setup_logging():
    handler = RotatingFileHandler(settings.LOG_PATH, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # its INFO lines log every request URL
    root.addHandler(handler)
    if sys.stderr is not None:  # also log to the console when run with python.exe
        root.addHandler(logging.StreamHandler())
    sys.excepthook = lambda *exc: log.error("Unhandled exception", exc_info=exc)
    threading.excepthook = lambda a: log.error(
        "Thread %s crashed", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def make_icon(size):
    """The program's icon at size x size (appicon.py draws it: a pen that has written >_, black on white)."""
    return appicon.draw(size)


def refresh_icon_file(path):
    """Write the .ico that shortcuts show. It is rewritten when what is there is not this icon (an earlier version's),
    so a shortcut made long ago gets the new look. True when the file was written."""
    buffer = io.BytesIO()
    make_icon(256).save(buffer, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)])
    data = buffer.getvalue()
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def text_rows(text, width):
    """Rough number of rows the text takes in a box `width` characters wide (CJK counts as 2)."""
    rows = 0
    for para in text.splitlines() or [""]:
        units = sum(2 if ord(c) > 0x2E80 else 1 for c in para)
        rows += max(1, math.ceil(units / width))
    return rows


def reading_seconds(text):
    """Seconds a person needs to read the hint: 1 s base, 0.15 s per CJK character, 0.05 s per other, clamped to 2–6 s."""
    units = sum(0.15 if ord(c) > 0x2E80 else 0.05 for c in text if not c.isspace())
    return min(6.0, max(2.0, 1.0 + units))


def flat_button(parent, text, command, primary=False):
    """Flat button: primary is blue with white text, otherwise white with a light border. Hover and press feedback."""
    if primary:
        colors = {"normal": (ACCENT, "white", ACCENT), "hover": (ACCENT_HOVER, "white", ACCENT_HOVER),
                  "down": ("#1E40AF", "white", "#1E40AF")}
    else:
        colors = {"normal": ("white", "#1F2328", "#D1D5DB"), "hover": ("#F3F4F6", "#1F2328", "#C4C9D0"),
                  "down": ("#E5E7EB", "#1F2328", "#C4C9D0")}
    button = tk.Label(parent, text=text, font=(UI_FONT, 10), cursor="hand2", padx=14, pady=4, bd=0, highlightthickness=1)

    def paint(state):
        bg, fg, border = colors[state]
        button.configure(bg=bg, fg=fg, highlightbackground=border, highlightcolor=border)

    def release(e):
        paint("hover")
        if 0 <= e.x < button.winfo_width() and 0 <= e.y < button.winfo_height():
            command()
    paint("normal")
    button.bind("<Enter>", lambda e: paint("hover"))
    button.bind("<Leave>", lambda e: paint("normal"))
    button.bind("<ButtonPress-1>", lambda e: paint("down"))
    button.bind("<ButtonRelease-1>", release)
    return button


@dataclass
class Job:
    """One screenshot-translation job."""
    id: int
    screen_box: tuple  # (x, y, w, h) in screen coordinates
    crop: Image.Image
    cfg: object
    mode: str  # display mode when the job started: overlay / popup
    overlay: object = None  # window over the selection: shows the original, then the translation in overlay mode
    popup: object = None
    spinner: object = None  # green spinner in the centre (turns into a check mark on success)
    glow: object = None
    dim: object = None
    overlay_hwnd: int = 0


class ConvertRun:
    """Where one input-box conversion stands. The worker thread and the Esc key both move it, and only one of them
    wins: working -> pasting (the result goes in), or working -> cancelled (Esc came first; nothing goes in)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.state = "working"

    def _move(self, to):
        with self._lock:
            if self.state != "working":
                return False
            self.state = to
            return True

    def cancel(self):
        """Esc. True when it was in time: the text has not started going in."""
        return self._move("cancelled")

    def commit(self):
        """The result is about to go in. False when Esc came first."""
        return self._move("pasting")

    @property
    def cancelled(self):
        return self.state == "cancelled"


@dataclass
class Rendered:
    overlay: Image.Image  # screenshot with the translation drawn in; None = nothing new or no text positions
    text: str
    info: str = ""


class Toast:
    """Small toast in the bottom-right corner: translucent, fades in and out, stays out of the way; duration scales with text length.

    A toast with a button (e.g. "Translate anyway") takes focus so Enter / Esc work directly.
    """

    def __init__(self, app, text, anchor=None, seconds=None, button=None):
        self.app = app
        self.alive = True
        self._alpha = 0.0
        previous = winapi.user32.GetForegroundWindow()
        win = self.win = tk.Toplevel(app.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        frame = tk.Frame(win, bg=TOAST_BG, highlightthickness=1, highlightbackground=TOAST_BORDER)
        frame.pack()
        label = tk.Label(frame, text=text, fg=TOAST_FG, bg=TOAST_BG, font=(UI_FONT, 9), justify="left",
                         padx=12, pady=6, wraplength=round(380 * app.scale))
        label.pack(side="left")
        for widget in (frame, label):
            widget.bind("<Button-1>", lambda e: self.dismiss())
        if button:
            caption, command = button
            self._command = command
            flat_button(frame, caption, self._press, primary=True).pack(side="left", padx=(0, 8), pady=5)
            win.bind("<Return>", lambda e: self._press())
            win.bind("<Escape>", lambda e: self.dismiss())
        app.place_in_corner(win, anchor)
        self._fade_to(TOAST_ALPHA, 150)
        win.after(int((seconds or reading_seconds(text)) * 1000), self.dismiss)
        if button:
            app.focus(win)
        else:
            win.update_idletasks()
            if previous and winapi.user32.GetForegroundWindow() != previous:
                winapi.force_foreground(previous)  # don't steal focus

    def _fade_to(self, target, ms, then=None):
        steps = max(1, ms // 15)
        delta = (target - self._alpha) / steps

        def step(n):
            if not self.alive:
                return
            self._alpha = target if n >= steps else self._alpha + delta
            self.win.attributes("-alpha", max(0.0, min(1.0, self._alpha)))
            if n < steps:
                self.win.after(15, step, n + 1)
            elif then:
                then()
        step(1)

    def _press(self):
        if self.alive:
            self.close()
            self._command()

    def dismiss(self):
        """Fade out, then close."""
        if self.alive:
            self._fade_to(0.0, 250, self.close)

    def close(self):
        if self.alive:
            self.alive = False
            self.win.destroy()


class SelectionWindow:
    """Region selection: a frozen screenshot underneath (only receives mouse input, never repaints) and an effects layer on top that draws the dimming, border and size label at the screen refresh rate."""

    MIN_SIZE = 8

    def __init__(self, app, shot, origin, on_done):
        self.app, self.shot, self.origin, self.on_done = app, shot, origin, on_done
        self.start = None
        self.current = None
        self.closed = False
        w, h = shot.size
        win = self.win = tk.Toplevel(app.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{w}x{h}+{origin[0]}+{origin[1]}")
        self.bright = ImageTk.PhotoImage(shot, master=win)
        c = self.canvas = tk.Canvas(win, width=w, height=h, highlightthickness=0, bd=0, cursor="crosshair")
        c.pack()
        c.create_image(0, 0, anchor="nw", image=self.bright)
        self.fx = selection_fx.SelectionFx(origin, (w, h), app.scale)
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Button-3>", lambda e: self.close(None))
        win.bind("<Escape>", lambda e: self.close(None))
        win.after(20, lambda: app.focus(win))

    def _clamp(self, x, y):
        return min(max(x, 0), self.shot.width), min(max(y, 0), self.shot.height)

    def _box(self):
        (sx, sy), (cx, cy) = self.start, self.current
        return min(sx, cx), min(sy, cy), max(sx, cx), max(sy, cy)

    def _press(self, e):
        if not self.closed:
            self.start = self.current = self._clamp(e.x, e.y)
            self.fx.press(*self.start)

    def _drag(self, e):
        if self.start is not None and not self.closed:
            self.current = self._clamp(e.x, e.y)  # the effects layer reads the mouse position itself; just remember it here

    def _release(self, e):
        if self.start is None or self.closed:
            return
        self.current = self._clamp(e.x, e.y)
        x0, y0, x1, y1 = self._box()
        if x1 - x0 < self.MIN_SIZE or y1 - y0 < self.MIN_SIZE:  # a plain click: start over
            self.start = None
            self.fx.reset()
            return
        self.fx.release((x0, y0, x1, y1))
        self.close((x0, y0, x1, y1))

    def close(self, box):
        if self.closed:
            return
        self.closed = True
        hwnd = int(self.win.wm_frame(), 16)
        handoff = self.on_done(box, hwnd, self.fx)
        self._finish_close(handoff, time.perf_counter() + 0.5)

    def _finish_close(self, handoff, deadline):
        """Close the selection window only after the glow and highlight are ready underneath it, so the switch doesn't flicker.

        The effects layer stays: it becomes the dimming for the waiting phase (same window, so no flicker).
        """
        ready, done = handoff or (None, None)
        if ready is None or ready() or time.perf_counter() > deadline:
            if handoff is None:  # selection cancelled: effects layer and screenshot go away together
                if self.fx.hwnd:
                    layered.user32.ShowWindow(self.fx.hwnd, layered.SW_HIDE)
                self.fx.close_now()
            self.win.destroy()
            if done:
                done()
        else:
            self.win.after(5, self._finish_close, handoff, deadline)


class ResultOverlay:
    """Window floating over the selection: shows the original while translating (highlight outside, spinner in the middle), then the translation.

    Rounded, thin translucent white border drawn inside the selection; moves with the window.
    """

    def __init__(self, app, screen_box, original, on_close=None, dimmed=False):
        self.app, self.on_close = app, on_close
        self.on_move = None  # drag notification: new top-left (x, y), so a check mark that hasn't faded yet follows
        self.screen_box, self.original = screen_box, original
        self.image, self.text = original, ""
        self.alive = True
        self.translating = True
        self._peek = False
        x, y, w, h = screen_box
        win = self.win = tk.Toplevel(app.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{w}x{h}+{x}+{y}")
        self.photo_tr = self.photo_src = ImageTk.PhotoImage(original, master=win)
        # no dragging while translating (dimming, glow, highlight and spinner are pinned in place); draggable once the translation shows
        c = self.canvas = tk.Canvas(win, width=w, height=h, highlightthickness=0, bd=0, cursor="arrow")
        c.pack()
        self.item = c.create_image(0, 0, anchor="nw", image=self.photo_tr)
        # while dimmed, the outside of the four rounded corners must be dimmed too, or the corners don't look round (removed when the translation shows)
        self._dim_items, self._dim_photos = [], []
        if dimmed:
            for cx, cy, mask in frame_style.corner_masks(w, h, app.scale):
                part = original.crop((cx, cy, cx + mask.width, cy + mask.height))
                patch = Image.composite(part, ImageEnhance.Brightness(part).enhance(0.6), mask)
                self._dim_photos.append(ImageTk.PhotoImage(patch, master=win))
                self._dim_items.append(c.create_image(cx, cy, anchor="nw", image=self._dim_photos[-1]))
        self._border = []
        for bx, by, img in frame_style.border_pieces(w, h, app.scale):
            photo = ImageTk.PhotoImage(img, master=win)
            self._border.append(photo)
            c.create_image(bx, by, anchor="nw", image=photo)
        self._drag_from = None
        menu = self.menu = tk.Menu(win, tearoff=0, font=(UI_FONT, 9))
        menu.add_command(label=t("复制图片"), accelerator="Ctrl+C", command=self.copy_image)
        menu.add_command(label=t("复制文字"), accelerator="Ctrl+Shift+C", command=self.copy_text)
        menu.add_command(label=t("保存图片…"), accelerator="Ctrl+S", command=self.save_image)
        menu.add_separator()
        menu.add_command(label=t("重新翻译"), command=self.retranslate)
        menu.add_separator()
        menu.add_command(label=t("关闭"), accelerator="Esc", command=self.close)
        for widget in (win, c):
            widget.bind("<Escape>", lambda e: self.close())
            widget.bind("<Button-3>", self._show_menu)
            widget.bind("<Control-c>", self._copy_key)
            widget.bind("<Control-C>", self._copy_key)
            widget.bind("<Control-s>", lambda e: self.save_image())
            widget.bind("<Control-S>", lambda e: self.save_image())
            widget.bind("<KeyPress-space>", lambda e: self._show_original(True))
            widget.bind("<KeyRelease-space>", lambda e: self._show_original(False))
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._drag)
        app.focus(win)

    def _show_original(self, on):
        self._peek = on
        self.canvas.itemconfigure(self.item, image=self.photo_src if on else self.photo_tr)

    def update(self, overlay, text):
        if not self.alive:
            return
        self.text = text
        if overlay is not None:
            self.image = overlay
            self.photo_tr = ImageTk.PhotoImage(overlay, master=self.win)
            if not self._peek:
                self.canvas.itemconfigure(self.item, image=self.photo_tr)

    def undim(self):
        for item in self._dim_items:
            self.canvas.delete(item)
        self._dim_items, self._dim_photos = [], []

    def finish(self, info=""):
        """Translation complete: enable dragging and show the usage hint."""
        self.on_close = None
        self.translating = False
        if self.alive:
            self.canvas.configure(cursor="fleur")
            self.undim()
            hint = t("Esc 关闭 · 空格看原文 · Ctrl+C 复制图片 · 右键更多")
            self.app.toast((info + " · " if info else "") + hint, anchor=self.screen_box)

    def _show_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _copy_key(self, e):
        if e.state & 0x0001:  # with Shift: copy text
            self.copy_text()
        else:
            self.copy_image()
        return "break"

    def _not_ready(self):
        if self.translating and not self.text:
            self.app.toast(t("还在翻译，请稍等"), anchor=self.screen_box)
            return True
        return False

    def copy_image(self):
        if not self._not_ready() and self.app.copy_image(self.image):
            self.app.toast(t("已复制译文图片，可以直接粘贴到微信、QQ、Word 等"), anchor=self.screen_box)

    def copy_text(self):
        if not self._not_ready():
            self.app.copy(self.text)
            self.app.toast(t("已复制译文文字"), anchor=self.screen_box)

    def save_image(self):
        if self._not_ready():
            return
        path = filedialog.asksaveasfilename(
            parent=self.win, title=t("保存翻译后的图片"), defaultextension=".png",
            initialfile=f"{t('翻译')}_{datetime.now():%Y%m%d_%H%M%S}.png",
            filetypes=[(t("PNG 图片"), "*.png"), (t("JPG 图片"), "*.jpg")])
        if path:
            try:
                self.image.convert("RGB").save(path)
                self.app.toast(t("图片已保存"), anchor=self.screen_box)
            except (OSError, ValueError) as err:
                self.app.show_error(t("保存失败：{err}", err=err))
        self.app.focus(self.win)

    def retranslate(self):
        if self.on_close:  # still translating; wait for this one to finish
            self.app.toast(t("这次还没翻译完，请稍等"), anchor=self.screen_box)
            return
        box = (self.win.winfo_x(), self.win.winfo_y()) + tuple(self.screen_box[2:])
        self.close()
        self.app.retranslate(box, self.original)

    def _press(self, e):
        self.app.focus(self.win)
        # still translating: no dragging (the window would move away from its pinned effects and look doubled)
        self._drag_from = None if self.translating else (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag(self, e):
        if self._drag_from and not self.translating:
            dx, dy = self._drag_from
            x, y = e.x_root - dx, e.y_root - dy
            self.win.geometry(f"+{x}+{y}")
            if self.on_move:
                self.on_move(x, y)

    def hide(self):
        if self.alive:
            self.win.withdraw()
        return self.alive

    def show(self):
        if self.alive:
            self.win.deiconify()
            self.win.attributes("-topmost", True)

    def close(self):
        if self.alive:
            self.alive = False
            self.win.destroy()
            self.app.forget(self)
            if self.on_close:  # closed while translating: cancel the job too
                callback, self.on_close = self.on_close, None
                callback()


_CJK = "\u2E80-\u9FFF\u3000-\u303F\uFF00-\uFFEF"
_TOKEN_RE = re.compile(rf"[^\s{_CJK}]+\s*|[{_CJK}]\s*|\s+")
_NO_LINE_START = set("，。、；：？！）」』》〉】”’,.;:?!)")  # punctuation that must not start a line


def wrap_lines(text, font, width):
    """Own line wrapping by pixel width: break anywhere between CJK characters, keep Latin words whole, keep punctuation off the line start.
    Returns [(line text, is_soft_break)]; soft breaks were added by wrapping and are dropped when copying.
    """
    out = []
    for para in text.split("\n"):
        line = ""
        for tok in _TOKEN_RE.findall(para):
            if line and font.measure((line + tok).rstrip()) > width and not (tok.strip() and tok.strip()[0] in _NO_LINE_START and len(tok.strip()) == 1):
                out.append((line, True))
                line = tok.lstrip()
            else:
                line += tok
        out.append((line, False))
    return out


def _round_corners(win):
    """Windows 11: give a borderless window the system rounded corners (no effect on Windows 10, no error either)."""
    try:
        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
    except (AttributeError, OSError, ValueError, tk.TclError):
        pass


POPUP_BORDER = "#D9DCE1"
ERROR_FG = "#B91C1C"


class TextPopup:
    """Plain-text popup: used for popup-mode translations and for error messages. Borderless rounded card, draggable by the title."""

    WIDTH = 56

    def __init__(self, app, title, text, box=None, note="", error=False, open_config=False,
                 streaming=False, on_close=None):
        self.app, self.text, self.note, self.on_close = app, text, note, on_close
        self.image = None  # rendered image (only when text positions exist); can be copied
        self._copied = False
        self.alive = True
        self._drag = None
        win = self.win = tk.Toplevel(app.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=POPUP_BORDER)
        card = tk.Frame(win, bg="white")
        card.pack(fill="both", expand=True, padx=1, pady=1)  # 1 px border

        header = tk.Frame(card, bg="white")
        header.pack(fill="x", padx=(20, 10), pady=(10, 0))
        title_label = tk.Label(header, text=title, bg="white", fg=ERROR_FG if error else "#111827",
                               font=(UI_FONT, 10, "bold"))
        title_label.pack(side="left", pady=2)
        close = tk.Label(header, text="✕", bg="white", fg="#9CA3AF", font=(UI_FONT, 11), cursor="hand2", padx=8)
        close.pack(side="right")
        close.bind("<Enter>", lambda e: close.configure(fg="#111827"))
        close.bind("<Leave>", lambda e: close.configure(fg="#9CA3AF"))
        close.bind("<Button-1>", lambda e: self.close())
        for w in (header, title_label):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        body = tk.Frame(card, bg="white")
        body.pack(fill="both", expand=True, padx=20, pady=(6, 2))
        self._font = tkfont.Font(root=app.root, family=UI_FONT, size=11)
        self._lines = []
        txt = self.txt = tk.Text(body, wrap="word", font=self._font, width=self.WIDTH, height=3,
                                 relief="flat", bd=0, highlightthickness=0, bg="white", fg="#111827",
                                 selectbackground="#BFDBFE", selectforeground="#111827",
                                 padx=0, pady=2, spacing1=2, spacing3=6, cursor="arrow")
        self.bar = tk.Scrollbar(body, command=txt.yview)
        txt.configure(yscrollcommand=self.bar.set)
        txt.pack(side="left", fill="both", expand=True)
        self._set_text(text)

        bottom = self._bottom = tk.Frame(card, bg="white")
        bottom.pack(fill="x", padx=(20, 14), pady=(4, 12))
        self.status = tk.Label(bottom, text=t("正在翻译…") if streaming else note,
                               fg="#6B7280", bg="white", font=(UI_FONT, 9), anchor="w")
        self.status.pack(side="left", pady=4)
        flat_button(bottom, t("知道了") if error else t("关闭"), self.close).pack(side="right", padx=(6, 0), pady=2)
        if not error:
            flat_button(bottom, t("复制文字"), self.copy, primary=True).pack(side="right", padx=6, pady=2)
        if open_config:
            flat_button(bottom, t("打开配置文件"), lambda: app.open_file(settings.CONFIG_PATH),
                        primary=True).pack(side="right", padx=6, pady=2)
        bottom.bind("<ButtonPress-1>", self._drag_start)
        bottom.bind("<B1-Motion>", self._drag_move)
        win.bind("<Escape>", lambda e: self.close())
        win.bind("<Control-c>", self._copy_selection)
        win.bind("<Control-C>", self._copy_selection)
        win.protocol("WM_DELETE_WINDOW", self.close)
        app.place_beside(win, box)
        _round_corners(win)
        app.focus(win)

    def _drag_start(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self.win.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _copy_selection(self, event=None):
        try:
            first, last = self.txt.index("sel.first"), self.txt.index("sel.last")
        except tk.TclError:  # no selection: copy everything
            self.copy()
            return "break"
        l1, c1 = map(int, first.split("."))
        l2, c2 = map(int, last.split("."))
        parts = []
        for ln in range(l1, l2 + 1):
            text, soft = self._lines[ln - 1] if ln - 1 < len(self._lines) else ("", False)
            start = c1 if ln == l1 else 0
            end = c2 if ln == l2 else len(text)
            parts.append(text[start:end])
            if ln != l2:
                parts.append("" if soft else "\n")
        self.app.copy("".join(parts))
        return "break"

    def _set_text(self, text):
        """Show text (read-only, selectable): wrapped by pixel width, height equals the line count, scrollbar beyond 18 lines."""
        self.text = text
        width = self._font.measure("0") * self.WIDTH - 6
        self._lines = wrap_lines(text, self._font, width)
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(t for t, _ in self._lines))
        self.txt.configure(state="disabled")
        rows = len(self._lines)
        self.txt.configure(height=min(max(rows, 2), 18))
        if rows > 18 and not self.bar.winfo_ismapped():
            self.bar.pack(side="right", fill="y", before=self.txt)

    def update(self, overlay, text):
        if overlay is not None:
            self.image = overlay
        if self.alive and text != self.text:
            self._set_text(text)

    def finish(self, info=""):
        """Translation complete: copy it to the clipboard."""
        self.on_close = None
        if self.alive:
            self.status.configure(text="  ·  ".join(t for t in (self.note, info) if t))
            self.copy()
            if self.image is not None:
                flat_button(self._bottom, t("复制图片"), self._copy_image).pack(side="right", padx=6, pady=2)

    def _copy_image(self):
        if self.app.copy_image(self.image):
            self.app.toast(t("已复制译文图片"))

    def copy(self):
        self.app.copy(self.text)
        if not self._copied:
            self._copied = True
            note = self.status.cget("text")
            self.status.configure(text=(note + "  ·  " if note else "") + t("✓ 已复制到剪贴板"))

    def close(self):
        if self.alive:
            self.alive = False
            self.win.destroy()
            self.app.forget(self)
            if self.on_close:  # closed while translating: cancel the job too
                callback, self.on_close = self.on_close, None
                callback()


EFFORT_LABELS = {"": "默认", "off": "关闭思考", "on": "开启思考", "minimal": "极低", "low": "低", "medium": "中",
                 "high": "高", "xhigh": "很高", "max": "最高"}  # "" = say nothing, the model's own default


def model_label(model):
    """"claude-sonnet-5-5" -> "Claude Sonnet 5.5"; names of other companies' models are shown as they are."""
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)-(\d+)", model)
    return f"Claude {m.group(1).capitalize()} {m.group(2)}.{m.group(3)}" if m else model


class KeyDialog:
    """Small card that takes a company's API key (and, for a custom endpoint, its address).

    The key is shown as dots unless "show" is ticked, so a screenshot of the dialog does not give it away. It goes straight into config.ini; it is never logged.
    """

    def __init__(self, app, company, adding, on_save, url=""):
        self.app, self.company, self.on_save = app, company, on_save
        self.alive = True
        self._drag = None
        name = settings.company_name(company)
        win = self.win = tk.Toplevel(app.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=POPUP_BORDER)
        card = tk.Frame(win, bg="white")
        card.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(card, bg="white")
        header.pack(fill="x", padx=(20, 10), pady=(10, 0))
        title = tk.Label(header, text=t("添加 API Key · {name}", name=name) if adding else t("更换 API Key · {name}", name=name),
                         bg="white", fg="#111827", font=(UI_FONT, 10, "bold"))
        title.pack(side="left", pady=2)
        close = tk.Label(header, text="✕", bg="white", fg="#9CA3AF", font=(UI_FONT, 11), cursor="hand2", padx=8)
        close.pack(side="right")
        close.bind("<Enter>", lambda e: close.configure(fg="#111827"))
        close.bind("<Leave>", lambda e: close.configure(fg="#9CA3AF"))
        close.bind("<Button-1>", lambda e: self.close())
        for w in (header, title):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        body = tk.Frame(card, bg="white")
        body.pack(fill="both", expand=True, padx=20, pady=(8, 2))

        def entry(var, **kw):
            """A one-line input in a thin border that turns blue while it has the focus."""
            box = tk.Frame(body, bg="white", highlightthickness=1, highlightbackground="#D1D5DB")
            field = tk.Entry(box, textvariable=var, width=46, font=("Consolas", 10), relief="flat", bd=0, bg="white",
                             fg="#111827", insertbackground="#111827", highlightthickness=0, **kw)
            field.pack(fill="x", padx=8, pady=6)
            field.bind("<FocusIn>", lambda e: box.configure(highlightbackground=ACCENT))
            field.bind("<FocusOut>", lambda e: box.configure(highlightbackground="#D1D5DB"))
            return box, field

        self.url = tk.StringVar(value=url)
        self.key = tk.StringVar()
        self.show = tk.BooleanVar(value=False)
        message = tk.Label(body, text=t("把 {name} 的 API Key 粘贴到下面。\n它只保存在这台电脑的 config.ini 里。", name=name),
                           bg="white", fg="#374151", font=(UI_FONT, 10), justify="left", anchor="w")
        message.pack(fill="x", pady=(0, 8))
        first = None
        if company == "custom":
            tk.Label(body, text=t("接口地址（以 https:// 开头，一般以 /v1 结尾）"), bg="white", fg="#6B7280",
                     font=(UI_FONT, 9), anchor="w").pack(fill="x")
            box, first = entry(self.url)
            box.pack(fill="x", pady=(2, 8))
            tk.Label(body, text="API Key", bg="white", fg="#6B7280", font=(UI_FONT, 9), anchor="w").pack(fill="x")
        key_box, self.key_entry = entry(self.key, show="•")
        key_box.pack(fill="x", pady=(2, 6))
        style = ttk.Style(win)
        style.configure("Key.TCheckbutton", background="white", font=(UI_FONT, 9))
        ttk.Checkbutton(body, text=t("显示"), variable=self.show, style="Key.TCheckbutton", takefocus=False,
                        command=lambda: self.key_entry.configure(show="" if self.show.get() else "•")).pack(anchor="w")
        self.error = tk.Label(body, text="", bg="white", fg=ERROR_FG, font=(UI_FONT, 9), justify="left", anchor="w")
        self.error.pack(fill="x", pady=(4, 0))

        bottom = tk.Frame(card, bg="white")
        bottom.pack(fill="x", padx=(20, 14), pady=(4, 12))
        flat_button(bottom, t("保存"), self.save, primary=True).pack(side="right", padx=(6, 0), pady=2)
        flat_button(bottom, t("取消"), self.close).pack(side="right", padx=6, pady=2)
        bottom.bind("<ButtonPress-1>", self._drag_start)
        bottom.bind("<B1-Motion>", self._drag_move)
        win.bind("<Return>", lambda e: self.save())
        win.bind("<Escape>", lambda e: self.close())
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.update_idletasks()
        for label in (message, self.error):
            label.configure(wraplength=key_box.winfo_reqwidth())
        app.place_beside(win, None)
        _round_corners(win)
        app.focus(win)
        (first if first is not None and not url else self.key_entry).focus_set()

    def _drag_start(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self.win.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def save(self):
        key = self.key.get().strip()
        url = self.url.get().strip().rstrip("/")
        problem = None
        if self.company == "custom":
            problem = settings.url_problem(url) if url else t("还没有填写接口地址。")
        if not problem:
            if not key:
                problem = t("还没有粘贴 API Key。")
            elif any(c.isspace() or not c.isascii() for c in key):
                problem = t("API Key 里有空格或中文字符，请检查是否粘贴完整。")
        if problem:
            self.error.configure(text=problem)
            return
        self.close()
        self.on_save(key, url if self.company == "custom" else None)

    def close(self):
        if self.alive:
            self.alive = False
            self.win.destroy()


class App:
    def __init__(self):
        self.cfg = settings.load_config()
        i18n.set_language(i18n.resolve(self.cfg.ui_language))
        self.mode = self.cfg.mode
        self.events = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(settings.APP_NAME)
        self.root.report_callback_exception = self._on_tk_error
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.icon_image = make_icon(64)
        self.icon_photo = ImageTk.PhotoImage(make_icon(32), master=self.root)
        self.selection = None  # None / "pending" / SelectionWindow
        self.windows = []  # open result windows
        self.jobs = {}  # job id -> Job (cancelled or finished jobs are removed)
        self._job_seq = 0
        self._toast = None
        self._deferred_toast = None  # toasts are held back during selection and shown afterwards
        self.fx = []  # animations (spinner, highlight, ...); they fade out and close themselves
        self.labels = {}
        self.hotkeys = None
        self.tray = None
        self.models = {}  # company -> model names fetched from its API, or "loading" / "failed"
        self.key_dialog = None
        self.converting = False  # a text conversion (input box) is under way
        self._convert_run = None  # its ConvertRun: Esc cancels it
        self._convert_toast = None

    # ---------- startup / shutdown ----------
    def run(self):
        self._start_hotkeys()
        self._start_tray()
        threading.Thread(target=self._warm_up, name="warmup", daemon=True).start()
        problem = settings.api_key_problem(self.cfg)
        if problem:
            self.root.after(800, lambda: self.show_error(problem, open_config=True,
                                                         title=t("Metaphrasis · 需要设置 API Key")))
        self.root.after(30, self._poll)
        log.info("Started: version %s, model %s, mode %s, hotkeys %s", settings.VERSION, self.cfg.model, self.mode, self.labels)
        self.root.mainloop()

    def quit(self):
        log.info("Quit")
        if self.hotkeys:
            self.hotkeys.stop()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    @staticmethod
    def _warm_up():
        try:  # preload the slow modules so the first translation is faster
            import ocr_win, render, translator  # noqa: F401
        except Exception:
            log.exception("Preloading modules failed")
        # objects loaded at startup live forever: freeze them so later collections are faster and don't stall animations
        gc.collect()
        gc.freeze()

    def _start_hotkeys(self):
        bindings = {}
        for name, text, default in (("capture", self.cfg.capture_hotkey, "Alt+~"),
                                    ("toggle", self.cfg.toggle_hotkey, "Ctrl+Alt+Y"),
                                    ("convert", self.cfg.convert_hotkey, "Alt+1")):
            try:
                mods, vk = winapi.parse_hotkey(text)
            except ValueError as e:
                self.root.after(500, lambda hk=text, d=default, err=e: self.show_error(
                    t("config.ini 里的快捷键 “{t}” 无法识别：{err}\n已暂时改用默认的 {d}。", t=hk, err=err, d=d), open_config=True))
                text = default
                mods, vk = winapi.parse_hotkey(default)
            bindings[name] = (text, mods, vk)
        self.labels = {name: b[0] for name, b in bindings.items()}
        self.hotkeys = winapi.HotkeyThread(bindings, self.events)
        self.hotkeys.start()

    def _act(self, name):
        return lambda icon, it: self.events.put(("tray", name))

    def _has_key(self):
        return bool(self.cfg.api_key)

    def _company_items(self):
        """The companies; one that has no key on file yet ends in … because picking it asks for the key first."""
        import pystray
        ready = settings.saved_companies() | ({self.cfg.company} if self._has_key() else set()) | {"ollama"}
        items = [pystray.MenuItem(settings.company_name(cid) + ("" if cid in ready else " …"), self._act("company:" + cid),
                                  checked=(lambda it, c=cid: self.cfg.company == c), radio=True)
                 for cid, _, _ in settings.COMPANIES]
        return items + [pystray.Menu.SEPARATOR, pystray.MenuItem(t("更换 API Key…"), self._act("change_key"))]

    def _model_items(self):
        import pystray
        item = pystray.MenuItem

        def choices(names):
            return [item(model_label(m), self._act("model:" + m), checked=(lambda it, m=m: self.cfg.model == m), radio=True)
                    for m in names]

        cfg = self.cfg
        if cfg.provider == "claude":
            return choices(list(settings.SUPPORTED_MODELS) + ([] if cfg.model in settings.SUPPORTED_MODELS else [cfg.model]))
        state = self.models.get(cfg.company)
        if state is None and self._has_key():
            self._fetch_models()
            state = self.models.get(cfg.company)
        names = list(state) if isinstance(state, list) else []
        if cfg.model and cfg.model not in names:
            names.insert(0, cfg.model)
        items = choices(names)
        if state == "loading":
            items.append(item(t("正在获取模型列表…"), lambda icon, it: None, enabled=False))
        elif state == "failed":
            items.append(item(t("获取模型列表失败，点这里重试"), self._act("refresh_models")))
        else:
            items += [pystray.Menu.SEPARATOR, item(t("刷新模型列表"), self._act("refresh_models"))]
        return items + [item(t("手动填写模型名（打开配置文件）"), self._act("open_config"))]

    def _effort_items(self):
        import pystray
        return [pystray.MenuItem(t(EFFORT_LABELS[e]), self._act("effort:" + e), checked=(lambda it, e=e: self.cfg.effort == e), radio=True)
                for e in settings.thinking_levels(self.cfg.company)]  # every company has its own choices

    def _build_menu(self):
        import pystray
        item = pystray.MenuItem
        act = self._act

        def lang_items(kind, is_current):  # language names are written in their own language, not translated
            return [item(name, act(f"{kind}:{code}"), checked=(lambda it, c=code: is_current(c)), radio=True)
                    for code, name in i18n.LANGUAGES]

        return pystray.Menu(
            item(lambda it: t("截图翻译    {hotkey}", hotkey=self.labels['capture']), act("capture"), default=True),
            item(lambda it: t("显示模式"), pystray.Menu(
                item(lambda it: t("译文覆盖在原文上"), act("mode_overlay"), checked=lambda it: self.mode == "overlay", radio=True),
                item(lambda it: t("弹窗显示文字（自动复制）"), act("mode_popup"), checked=lambda it: self.mode == "popup", radio=True),
                pystray.Menu.SEPARATOR,
                item(lambda it: t("切换快捷键：{hotkey}", hotkey=self.labels['toggle']), lambda icon, it: None, enabled=False),
            )),
            item(lambda it: t("译文语言"), pystray.Menu(*lang_items("target", lambda c: settings.lang_code(self.cfg.target_language) == c))),
            item(lambda it: t("输入框转换成"), pystray.Menu(
                *lang_items("convert", lambda c: settings.lang_code(self.cfg.convert_language) == c),
                pystray.Menu.SEPARATOR,
                item(lambda it: t("快捷键：{hotkey}", hotkey=self.labels['convert']), lambda icon, it: None, enabled=False),
            )),
            item(lambda it: t("界面语言"), pystray.Menu(*lang_items("ui", lambda c: i18n.current() == c))),
            pystray.Menu.SEPARATOR,
            # the order one sets things up in: company first, then its key, and only then model and effort
            item(lambda it: t("接口公司"), pystray.Menu(self._company_items)),
            item(lambda it: t("模型"), pystray.Menu(self._model_items), enabled=lambda it: self._has_key()),
            item(lambda it: t("思考强度"), pystray.Menu(self._effort_items),
                 enabled=lambda it: self._has_key() and len(settings.thinking_levels(self.cfg.company)) > 1),
            pystray.Menu.SEPARATOR,
            item(lambda it: t("打开配置文件（API Key、模型）"), act("open_config")),
            item(lambda it: t("打开翻译记录"), act("open_history")),
            item(lambda it: t("打开工具所在文件夹"), act("open_folder")),
            pystray.Menu.SEPARATOR,
            item(lambda it: t("版本 {version}", version=settings.VERSION), lambda icon, it: None, enabled=False),
            item(lambda it: t("开机自动启动"), act("autostart"), checked=lambda it: autostart.is_enabled()),
            item(lambda it: t("退出"), act("quit")),
        )

    def _start_tray(self):
        import pystray
        self.tray = pystray.Icon("fanyi", self.icon_image, t("Metaphrasis · 截图翻译 {hotkey}", hotkey=self.labels['capture']), self._build_menu())
        threading.Thread(target=self.tray.run, kwargs={"setup": self._tray_ready}, name="tray", daemon=True).start()

    def _tray_ready(self, icon):
        icon.visible = True
        try:
            icon.notify(t("已在后台运行。按 {capture} 框选翻译，{toggle} 切换显示模式。",
                          capture=self.labels['capture'], toggle=self.labels['toggle']), settings.APP_NAME)
        except Exception:
            log.exception("Tray notification failed")

    # ---------- event loop ----------
    def _poll(self):
        try:
            while True:
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(event)
                except Exception:  # one failing event only gets logged; the tool must keep responding
                    log.exception("Error handling event %s", event[0])
        finally:
            self.root.after(30, self._poll)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "hotkey":
            if ev[1] == "capture":
                self.start_capture()
            elif ev[1] == "toggle":
                self.set_mode("popup" if self.mode == "overlay" else "overlay")
            elif ev[1] == "convert":
                self.start_convert()
            elif ev[1] == "esc":
                if isinstance(self.selection, SelectionWindow):
                    self.selection.close(None)
                else:
                    self.cancel_convert()
        elif kind == "hotkey_failed":
            self.show_error(t("快捷键 {key} 注册失败，可能已经被其他程序占用。\n\n请在 config.ini 的 [hotkey] 里换一个组合，"
                              "然后重启本工具（托盘图标右键 → 退出，再双击启动）。", key=ev[1]), open_config=True)
        elif kind == "tray":
            self._tray_action(ev[1])
        elif kind == "job_progress":
            self._job_progress(ev[1], ev[2])
        elif kind == "job_done":
            self._job_done(ev[1], ev[2])
        elif kind == "job_failed":
            self._job_failed(ev[1], ev[2], ev[3])
        elif kind == "job_skipped":
            self._job_skipped(ev[1], ev[2])
        elif kind == "call":
            ev[1]()

    def _tray_action(self, name):
        if name == "capture":
            self.root.after(250, self.start_capture)  # let the tray menu close before capturing
        elif name in ("mode_overlay", "mode_popup"):
            self.set_mode(name[5:])
        elif name.startswith("target:"):
            self.set_target_language(name[7:])
        elif name.startswith("convert:"):
            self.set_convert_language(name[8:])
        elif name.startswith("ui:"):
            self.set_ui_language(name[3:])
        elif name.startswith("company:"):
            self.pick_company(name[8:])
        elif name.startswith("model:"):
            self.set_api_value("model", name[6:], t("模型：{name}", name=model_label(name[6:])))
        elif name.startswith("effort:"):
            self.set_api_value("effort", name[7:], t("思考强度：{name}", name=t(EFFORT_LABELS[name[7:]])))
        elif name == "change_key":
            self.ask_key(self.cfg.company)
        elif name == "refresh_models":
            self._fetch_models(force=True)
            if self.tray:
                self.tray.update_menu()
        elif name == "open_config":
            self.open_file(settings.CONFIG_PATH)
        elif name == "open_history":
            if settings.HISTORY_PATH.exists():
                self.open_file(settings.HISTORY_PATH)
            else:
                self.toast(t("还没有翻译记录"))
        elif name == "open_folder":
            self.open_file(settings.APP_DIR)
        elif name == "autostart":
            threading.Thread(target=self._toggle_autostart, name="autostart", daemon=True).start()
        elif name == "quit":
            self.quit()

    def _toggle_autostart(self):
        try:
            if autostart.is_enabled():
                autostart.disable()
                msg = t("已关闭开机自动启动")
            else:
                autostart.enable()
                msg = t("已开启开机自动启动")
        except Exception as e:
            log.exception("Changing the startup setting failed")
            msg = t("设置开机自动启动失败：{e}", e=e)
        self.events.put(("call", lambda: (self.toast(msg), self.tray and self.tray.update_menu())))

    # ---------- API company, key, model, effort ----------
    def _reload(self):
        """Read config.ini again after a change made from the tray, and redraw the menu."""
        try:
            self.cfg = settings.load_config()
        except settings.ConfigError as e:
            self.show_error(str(e), open_config=True)
        if self.tray:
            self.tray.update_menu()

    def set_api_value(self, key, value, message):
        try:
            settings.set_api_value(key, value)
        except OSError as e:
            self.show_error(t("打不开 {path}：{e}", path=settings.CONFIG_PATH, e=e))
            return
        self._reload()
        self.toast(message)

    def pick_company(self, company):
        """A company with a key on file is switched to at once; any other first asks for its key, and the switch happens when the key is saved."""
        if company == self.cfg.company:
            if not self._has_key():  # a fresh install: the company is already Claude, only its key is missing
                self.ask_key(company)
            elif self.tray:
                self.tray.update_menu()
        elif company == "ollama" or company in settings.saved_companies():
            self._use_company(company)
        else:
            self.ask_key(company)

    def ask_key(self, company):
        if self.key_dialog and self.key_dialog.alive:
            self.key_dialog.close()
        current = company == self.cfg.company
        self.key_dialog = KeyDialog(self, company, adding=not (current and self._has_key()),
                                    on_save=lambda key, url: self._use_company(company, key, url),
                                    url=self.cfg.base_url if current and company == "custom" else "")
        if self.tray:
            self.tray.update_menu()  # the radio mark goes back to the company in use until the key is saved

    def _use_company(self, company, api_key=None, base_url=None):
        same = company == self.cfg.company  # then only its key (or address) changes
        try:
            settings.switch_company(company, api_key=api_key, base_url=base_url)
        except OSError as e:
            self.show_error(t("打不开 {path}：{e}", path=settings.CONFIG_PATH, e=e))
            return
        self.models.pop(company, None)
        self._reload()
        self.toast(t("已保存 API Key") if same else t("接口公司：{name}", name=settings.company_name(company)))
        self._fetch_models(force=True)

    def _fetch_models(self, force=False):
        """Ask the company in use which models it has (a free request), in the background. Claude's list is fixed."""
        cfg = self.cfg
        company = cfg.company
        if cfg.provider == "claude" or not cfg.api_key or (not force and self.models.get(company) is not None):
            return
        self.models[company] = "loading"

        def work():
            try:
                import translator
                names = translator.list_models(cfg)
            except Exception as e:
                log.info("Model list of %s failed: %s", company, type(e).__name__)
                names = None
            self.events.put(("call", lambda: self._models_ready(company, names)))
        threading.Thread(target=work, name="models", daemon=True).start()

    def _models_ready(self, company, names):
        self.models[company] = "failed" if names is None else names
        if company == self.cfg.company and names and not self.cfg.model:
            if len(names) == 1:  # nothing to choose
                self.set_api_value("model", names[0], t("模型：{name}", name=names[0]))
                return
            self.toast(t("请在托盘菜单的“模型”里选一个模型"))
        if self.tray:
            self.tray.update_menu()

    def set_target_language(self, code):
        """Target language picked from the tray: write it to config.ini; applies from the next translation."""
        name = i18n.NAMES[code]
        settings.set_option("target_language", name)
        self.cfg = dataclasses.replace(self.cfg, target_language=name)
        self.toast(t("译文语言：{name}", name=name))
        if self.tray:
            self.tray.update_menu()

    def set_ui_language(self, code):
        """Interface language picked from the tray: write it to config.ini and switch immediately."""
        settings.set_option("ui_language", i18n.NAMES[code])
        try:
            settings.localize_config_comments(settings.config_language(code))  # the explanations in the config file follow (Chinese / English)
        except OSError:
            log.exception("Rewriting config comments failed")
        self._apply_ui_language(code)
        self.toast(t("界面语言：{name}", name=i18n.NAMES[code]))

    def _apply_ui_language(self, code):
        if code == i18n.current():
            return
        i18n.set_language(code)
        self.root.title(settings.APP_NAME)
        if self.tray:
            self.tray.title = t("Metaphrasis · 截图翻译 {hotkey}", hotkey=self.labels['capture'])
            self.tray.update_menu()

    # ---------- capture & translate ----------
    def _checked_config(self):
        """Load the config and check the API key; on problems show an error and return None."""
        try:
            cfg = settings.load_config()
        except settings.ConfigError as e:
            self.show_error(str(e), open_config=True)
            return None
        changed = (cfg.company, cfg.model, cfg.effort, cfg.api_key) != (self.cfg.company, self.cfg.model, self.cfg.effort, self.cfg.api_key)
        self.cfg = cfg  # tray check marks follow the config file
        self._apply_ui_language(i18n.resolve(cfg.ui_language))
        if changed and self.tray:
            self.tray.update_menu()
        problem = settings.api_key_problem(cfg)
        if problem:
            self.show_error(problem, open_config=True, title=t("Metaphrasis · 需要设置 API Key"))
            return None
        problem = settings.model_problem(cfg)
        if problem:
            self.show_error(problem)
            return None
        # connect to the server while the user is still dragging the selection
        threading.Thread(target=self._prewarm, args=(cfg,), name="prewarm", daemon=True).start()
        return cfg

    def start_capture(self):
        if self.selection is not None:
            return
        cfg = self._checked_config()
        if cfg is None:
            return
        self.fx = [a for a in self.fx if a.alive]
        hidden = [w for w in self.windows if isinstance(w, ResultOverlay) and w.hide()]
        # a previous translation is still waiting: its dimming, glow and highlight go away (they belong to the latest selection only); the spinner is hidden and restored after selection
        for a in self.fx:
            if isinstance(a, spinner.PaySpinner):
                if a.hide():
                    hidden.append(a)
            else:
                a.close()
                a.hide()
        for job in self.jobs.values():
            if job.overlay is not None and job.overlay.alive:
                job.overlay.undim()
        if self._toast:
            self._toast.close()
            self._toast = None
        self.selection = "pending"
        self.root.after(150 if hidden else 0, lambda: self._begin_selection(cfg, hidden))

    def _begin_selection(self, cfg, hidden):
        try:
            vx, vy, vw, vh = winapi.virtual_screen()
            shot = ImageGrab.grab(all_screens=True).convert("RGB")
        except Exception as e:
            log.exception("Screen capture failed")
            self.selection = None
            for w in hidden:
                w.show()
            self.show_error(t("截屏失败：{e}", e=e))
            return
        if shot.size != (vw, vh):
            log.warning("Screenshot size %s differs from the virtual desktop %s", shot.size, (vw, vh))
        origin = (vx, vy)
        self.selection = SelectionWindow(
            self, shot, origin,
            lambda box, hwnd, fx: self._selection_done(cfg, shot, origin, box, hidden, hwnd, fx))
        self._sync_esc()

    def _sync_esc(self):
        """Esc is ours only while it has something to cancel: a selection being dragged, or an input-box conversion
        under way. The rest of the time other programs get it."""
        self.hotkeys.set_esc(isinstance(self.selection, SelectionWindow) or self.converting)

    def _selection_done(self, cfg, shot, origin, box, hidden, selection_hwnd, selection_fx_layer):
        """Selection finished. Returns (ready, done) so the selection window can hand over seamlessly."""
        self.selection = None
        self._sync_esc()
        for w in hidden:
            w.show()
        if self._deferred_toast:  # toasts held back during selection (e.g. a translation that just finished) are shown now
            args, self._deferred_toast = self._deferred_toast, None
            self.toast(*args)
        if box is None:
            return None
        job = self._start_job(cfg, (origin[0] + box[0], origin[1] + box[1], box[2] - box[0], box[3] - box[1]),
                              shot.crop(box), cover_hwnd=selection_hwnd, dim=selection_fx_layer)

        def ready():  # glow and highlight are drawn underneath the selection window
            return not job.glow.alive or job.glow.shown

        def done():  # selection window closed: the effects layer becomes the dimming right below the result window; glow and highlight return to the top
            job.dim.to_waiting()
            job.dim.below = job.overlay_hwnd
            job.glow.below = None
        return ready, done

    def _start_job(self, cfg, screen_box, crop, cover_hwnd=None, dim=None):
        """Start a translation.

        cover_hwnd: right after a selection, the selection window; glow and highlight hide under it and appear seamlessly when it closes.
        dim: right after a selection, its effects layer, reused as the dimming; otherwise a new dim layer is created.
        """
        self._job_seq += 1
        job = Job(self._job_seq, screen_box, crop, cfg, self.mode)
        self.jobs[job.id] = job
        seamless = cover_hwnd is not None
        # window over the selection (in overlay mode the translation replaces it in place; it also receives Esc), spinner in the centre
        job.overlay = ResultOverlay(self, screen_box, crop, on_close=lambda: self.cancel_job(job.id), dimmed=True)
        self.windows.append(job.overlay)
        job.overlay.win.update_idletasks()
        job.overlay_hwnd = int(job.overlay.win.wm_frame(), 16)
        # keep dimming outside by 40%; glow and running highlight on the border; all fade out together before the translation shows
        job.dim = dim or border_fx.DimLayer(winapi.virtual_screen(), screen_box, job.overlay_hwnd)
        job.glow = border_fx.BorderGlow(screen_box, self.scale, lambda fn: self.events.put(("call", fn)),
                                        seamless, below=cover_hwnd)
        x, y, w, h = screen_box
        size = min(76 * self.scale, max(40 * self.scale, 0.55 * min(w, h)))
        job.spinner = spinner.PaySpinner((x + w / 2, y + h / 2), size)
        # when the window is dragged after the translation shows, a check mark that hasn't faded yet follows
        job.overlay.on_move = lambda nx, ny: job.spinner.alive and job.spinner.move_center(nx + w / 2, ny + h / 2)
        self.fx += [job.spinner, job.glow, job.dim]
        threading.Thread(target=self._run_job, args=(job, cfg), name=f"job-{job.id}", daemon=True).start()
        return job

    def retranslate(self, screen_box, crop):
        """Context menu "Translate again": call the API once more for the same screenshot."""
        cfg = self._checked_config()
        if cfg is not None:
            self._start_job(cfg, screen_box, crop)

    @staticmethod
    def _prewarm(cfg):
        try:
            import translator
            translator.prewarm(cfg)
        except Exception:
            log.exception("Prewarm failed")

    def _run_job(self, job, cfg):
        """Background thread: OCR -> translate (streaming) -> render overlay -> write history."""
        try:
            import ocr_win
            import render
            import translator
        except Exception as e:
            log.exception("Loading modules failed")
            self.events.put(("job_failed", job.id, t("缺少运行组件，请双击“{bat}”。\n{e}", bat=INSTALL_BAT, e=e), False))
            return
        crop = job.crop
        try:
            t0 = time.perf_counter()
            with ThreadPoolExecutor(1) as pool:  # Chinese engine (is it Chinese or Japanese?) runs in parallel with English OCR
                zh = pool.submit(ocr_win.inspect, crop)
                try:
                    lines = ocr_win.recognize(crop)
                except Exception:
                    log.exception("OCR failed; sending the screenshot only")
                    lines = []
                try:
                    insp = zh.result()
                except Exception:
                    log.exception("Chinese/Japanese check failed; treating as English")
                    insp = None
            unreliable = False  # True: OCR text is unreliable (Japanese without the Japanese pack); only positions are used and the model reads the image
            target = settings.lang_code(cfg.target_language)
            japanese = cfg.source_language == "ja" or (cfg.source_language == "auto" and ocr_win.is_foreign(insp, lines))
            chinese = insp is not None and insp.chinese and not japanese
            if chinese and (target != "zh" or not cfg.skip_chinese):
                lines = insp.lines or lines  # Chinese source (translating to another language, or skip turned off): the English engine can't read hanzi, use the Chinese engine's lines
            if japanese:
                if ocr_win.has_language("ja"):
                    try:
                        lines = ocr_win.recognize(crop, "ja")
                    except Exception:
                        log.exception("Japanese OCR failed; using positions from the Chinese engine")
                        lines, unreliable = (insp.lines if insp else lines), True
                else:
                    lines, unreliable = (insp.lines if insp and insp.lines else lines), True
                log.info("Job %d: looks Japanese, %s", job.id, "using Japanese OCR" if not unreliable else "no Japanese OCR pack, the model reads the image")
            t1 = time.perf_counter()
            # already in the target language: skip (only decided for Chinese, Japanese and English targets; other targets always translate)
            already = (chinese and target == "zh") or (japanese and target == "ja") \
                or (target == "en" and not chinese and not japanese and ocr_win.is_english(lines))
            if cfg.skip_chinese and already:
                log.info("Job %d: already %s, no API call", job.id, cfg.target_language)
                self.events.put(("job_skipped", job.id, t("框选的内容本来就是{lang}，没有调用翻译", lang=cfg.target_language)))
                return

            memo = {}  # reuse layout analysis when the same screenshot is rendered repeatedly
            def progress(done, tail):  # popup mode shows text as it streams; overlay mode waits for the whole result
                text = "\n".join(b.translation for b in done + tail)
                self.events.put(("job_progress", job.id, Rendered(None, text)))

            result = translator.translate(cfg, crop, lines, on_progress=progress,
                                          cancelled=lambda: job.id not in self.jobs, unreliable=unreliable)
            info, label = "", cfg.model
            t2 = time.perf_counter()
            if job.id not in self.jobs:  # cancelled the moment it finished
                return
            overlay = None
            if lines and any(b.line_ids for b in result.blocks):
                overlay = render.render_translation(crop, lines, result.blocks, memo, lang=target)
            if cfg.save_history:
                try:
                    settings.append_history(label, result.source_text, result.translation_text)
                except OSError:
                    log.exception("Writing the history file failed")
            if cfg.save_images and overlay is not None:
                try:
                    settings.save_image(overlay)
                except OSError:
                    log.exception("Saving the translated image failed")
            usage = result.usage
            log.info("Job %d: %dx%d, OCR %d lines %.1fs, translated %d blocks %.1fs (first text %.1fs, %s%s)", job.id, crop.width,
                     crop.height, len(lines), t1 - t0, len(result.blocks), t2 - t1, result.first_text_seconds, label,
                     f", tokens {usage.input_tokens}/{usage.output_tokens}" if usage else "")
            self.events.put(("job_done", job.id, Rendered(overlay, result.translation_text, info)))
        except translator.Cancelled:
            log.info("Job %d cancelled; request aborted", job.id)
        except translator.TranslateError as e:
            log.warning("Job %d failed: %s", job.id, e)
            self.events.put(("job_failed", job.id, str(e), e.open_config))
        except Exception as e:
            log.exception("Job %d crashed", job.id)
            self.events.put(("job_failed", job.id, t("翻译时出错：{e}\n（详细信息见 运行日志.txt）", e=e), False))

    def _job_progress(self, job_id, part):
        """Partial translation: popup mode opens the popup on the first chunk and keeps updating it; overlay mode only stores the text until done."""
        job = self.jobs.get(job_id)
        if job is None:
            return
        if job.mode == "overlay":
            job.overlay.text = part.text
        elif job.popup is not None:
            job.popup.update(part.overlay, part.text)
        elif part.text.strip():
            job.popup = TextPopup(self, t("翻译结果"), part.text, box=job.screen_box, streaming=True,
                                  on_close=lambda: self.cancel_job(job_id))
            self.windows.append(job.popup)

    @staticmethod
    def _close_quietly(win):
        """Close a window without triggering "closed while translating = cancel"."""
        if win is not None:
            win.on_close = None
            win.close()

    def _close_job_windows(self, job):
        for fx in (job.spinner, job.glow, job.dim):
            if fx is not None:
                fx.close()
        self._close_quietly(job.overlay)
        self._close_quietly(job.popup)

    def _job_done(self, job_id, res):
        job = self.jobs.pop(job_id, None)
        if job is None:  # already cancelled
            return
        job.spinner.success()  # spinner closes into a circle with a check mark, then fades out
        job.dim.finish()  # dimming, glow and highlight fade out together over 0.3 s
        if job.mode == "overlay" and res.overlay is not None:
            job.overlay.text = res.text

            def reveal():  # reveal the translation after the highlight and glow have faded (0.3 s)
                if job.overlay.alive:
                    job.overlay.update(res.overlay, res.text)
                    job.overlay.finish(res.info)
            self._after_glow(job, reveal)
            return
        # popup mode (or no text positions): close the selection window after the highlight fades
        self._after_glow(job, lambda: self._close_quietly(job.overlay))
        popup = job.popup
        if popup is None or not popup.alive:
            note = t("没有识别到文字位置，改用弹窗显示") if job.mode == "overlay" else ""
            popup = TextPopup(self, t("翻译结果"), res.text, box=job.screen_box, note=note)
            self.windows.append(popup)
        popup.update(res.overlay, res.text)
        popup.finish(res.info)

    @staticmethod
    def _after_glow(job, then):
        """Run after the highlight fades; run immediately if it is already gone (e.g. a new selection started)."""
        if job.glow.alive and job.glow.cancel_at is None:
            job.glow.finish(then)
        else:
            then()

    def _job_skipped(self, job_id, message):
        job = self.jobs.pop(job_id, None)
        if job is None:
            return
        self._close_job_windows(job)
        # in case the guess was wrong (e.g. Japanese, Korean), "Translate anyway" skips the check
        self.toast(message, anchor=job.screen_box, seconds=6, button=(
            t("仍然翻译 (Enter)"), lambda: self._start_job(dataclasses.replace(job.cfg, skip_chinese=False),
                                                       job.screen_box, job.crop)))

    def _job_failed(self, job_id, message, open_config):
        job = self.jobs.pop(job_id, None)
        if job is None:
            return
        self._close_job_windows(job)
        self.show_error(message, open_config=open_config, box=job.screen_box)

    def cancel_job(self, job_id):
        """Esc: hide the spinner and any partial translation; the background thread aborts the request."""
        job = self.jobs.pop(job_id, None)
        if job is not None:
            self._close_job_windows(job)

    # ---------- convert the text selected in an input box ----------
    def set_convert_language(self, code):
        """Language picked from the tray: write it to config.ini; applies from the next conversion."""
        name = i18n.NAMES[code]
        settings.set_option("convert_language", name)
        self.cfg = dataclasses.replace(self.cfg, convert_language=name)
        self.toast(t("输入框转换成：{name}", name=name))
        if self.tray:
            self.tray.update_menu()

    def start_convert(self):
        """Hotkey: what is selected in an input box is replaced, in place, by the same thing said in another language."""
        if self.converting or self.selection is not None:
            return
        cfg = self._checked_config()
        if cfg is None:
            return
        self.converting = True
        run = self._convert_run = ConvertRun()
        self._sync_esc()  # Esc cancels it for as long as it runs
        key = self.hotkeys.bindings["convert"][2]
        threading.Thread(target=self._convert, args=(cfg, self.root.winfo_id(), key, run), name="convert", daemon=True).start()

    def cancel_convert(self):
        """Esc while a conversion is under way: nothing is put into the input box. Once the text has started going in
        it is too late, and Esc does nothing."""
        run = self._convert_run
        if self.converting and run is not None and run.cancel():
            self._convert_note(None)
            self.toast(t("已取消，没有转换"))

    def _convert(self, cfg, owner, key, run):
        """Background thread: is this an input box -> take its text -> the API rewords it -> put it in the text's place.

        What is taken: the selection if there is one, otherwise everything in the box (the program selects it itself, so
        the hands can stay on the keyboard). The program does the taking and the replacing; the API only sees the text
        and only rewords it. Keys are sent only where Windows says text can be typed, and the replacement is real text
        there (Ctrl+Z takes it back). run: where this conversion stands; Esc may cancel it until the text goes in."""
        def ui(function, *args, **kw):
            self.events.put(("call", lambda: function(*args, **kw)))

        name = cfg.convert_language
        session = None
        try:
            import inputbox
            import translator
            if not inputbox.wait_keys_up((key,)):
                return  # the hotkey is still held down: keys sent now would combine with it
            session = inputbox.Session()
            kind, selection, whole = session.look(keep=True)  # the box is kept: the result goes back into this one
            if kind == inputbox.PASSWORD:
                return ui(self.toast, t("密码框里的内容不会读取，没有转换"))
            if kind != inputbox.EDITABLE:
                log.info("Convert: not an input box (%s)", kind)
                return ui(self.toast, t("这里不是能输入文字的地方，没有转换"))
            # with nothing selected the whole box is taken, but only a box known to hold a message: no key goes into
            # a box that says it is empty, and a long document is never selected wholesale
            # (an empty box in a browser reports one placeholder character, U+FFFC: that is an empty box too)
            whole_fits = bool(whole and whole.replace("￼", "").strip()) and len(whole) <= inputbox.MAX_CHARS
            take_all = selection is not None and not selection.strip()  # the program says nothing is selected
            if take_all and not whole_fits:
                return ui(self.toast, self._nothing_to_convert(whole))
            place = inputbox.focus_place()
            previous = inputbox.snapshot(owner)
            text = None if take_all else inputbox.copy_selection(owner)
            if text is None and (take_all or (selection is None and whole_fits)):
                # (second case: a box that cannot say what is selected, and Ctrl+C found nothing selected)
                take_all = True
                selection = inputbox.select_all(session)
                if selection is None or selection.strip():  # go on once the box shows its text as selected (None: it cannot say)
                    text = inputbox.copy_selection(owner)
            if text is not None:
                inputbox.restore(previous, owner)  # the clipboard is the user's again while the API works
            if not text or not text.strip():
                return ui(self.toast, t("没能取到选中的文字，没有转换") if selection or take_all else self._nothing_to_convert(whole))
            if len(text) > inputbox.MAX_CHARS:
                return ui(self.toast, t("选中的文字太长（超过 {n} 字），请分成几段转换", n=inputbox.MAX_CHARS))
            if run.cancelled:
                log.info("Convert: cancelled with Esc before anything was sent")
                return
            ui(self._convert_working, run, t("正在转换成 {name}…（Esc 取消）", name=name))
            started = time.perf_counter()
            out, usage = translator.convert_text(cfg, text.strip().replace("\r\n", "\n"), settings.english_name(name),
                                                 cancelled=lambda: run.cancelled)
            if not run.commit():  # Esc came while the answer was on its way: nothing goes into the box
                log.info("Convert: cancelled with Esc; the answer was dropped")
                return
            if cfg.save_history:
                try:
                    settings.append_history(cfg.model, text, out)
                except OSError:
                    log.exception("Writing the history file failed")
            log.info("Convert: %d -> %d chars, %.1fs (%s%s)", len(text), len(out), time.perf_counter() - started, cfg.model,
                     f", tokens {usage.input_tokens}/{usage.output_tokens}" if usage else "")
            # the spaces and line breaks around the selection stay as they were
            out = out.replace("\r\n", "\n")
            if "\r\n" in text:
                out = out.replace("\n", "\r\n")
            out = text[:len(text) - len(text.lstrip())] + out + text[len(text.rstrip()):]
            # the user may have clicked elsewhere meanwhile: the result still goes into the box as long as the box holds what it held
            how = inputbox.put_in_place(session, place, selection, out, owner, whole=whole if take_all else None)
            if how:
                if how == "back":
                    log.info("Convert: the user had gone elsewhere; went back to the box, replaced, returned")
                ui(self._convert_note, None)
                time.sleep(1.0)  # the program reads the clipboard when it gets round to the paste: only then put the old content back
                inputbox.restore(previous, owner)
            else:
                log.info("Convert: the box changed or could not be reached; copied instead of replacing")
                inputbox.set_clipboard_text(out, owner)
                ui(self.toast, t("没能放回原来的位置，转换结果已复制，按 Ctrl+V 粘贴"))
        except translator.Cancelled:
            log.info("Convert: cancelled with Esc; request aborted")
        except translator.TranslateError as e:
            log.warning("Convert failed: %s", e)
            ui(self.show_error, str(e), open_config=e.open_config)
        except Exception as e:
            log.exception("Convert crashed")
            ui(self.show_error, t("转换时出错：{e}\n（详细信息见 运行日志.txt）", e=e))
        finally:
            if session:
                session.close()
            ui(self._convert_finished)

    def _nothing_to_convert(self, whole):
        """Why nothing was taken, from what the box holds (None = it does not say)."""
        if whole is None:
            return t("先选中要转换的文字，再按 {hotkey}", hotkey=self.labels["convert"])
        if not whole.replace("￼", "").strip():
            return t("输入框里还没有字，没有转换")
        import inputbox
        return t("输入框里的字太多（超过 {n} 字），请先选中要转换的那一段", n=inputbox.MAX_CHARS)

    def _convert_working(self, run, text):
        """Show the "working on it" note, unless Esc got there first."""
        if not run.cancelled:
            self._convert_note(text)

    def _convert_note(self, text):
        """The small "working on it" note; None takes it away."""
        if self._convert_toast and self._convert_toast.alive:
            self._convert_toast.close()
        self._convert_toast = None
        if text:
            self.toast(text, seconds=60)
            self._convert_toast = self._toast

    def _convert_finished(self):
        self.converting = False
        self._sync_esc()  # Esc goes back to the other programs
        if self._convert_toast and self._convert_toast is not self._toast:
            self._convert_toast = None  # another message has taken its place
        self._convert_note(None)

    # ---------- UI helpers ----------
    def set_mode(self, mode):
        self.mode = mode
        self.toast(t("显示模式：弹窗显示文字（自动复制到剪贴板）") if mode == "popup" else t("显示模式：译文覆盖在原文上"))
        if self.tray:
            self.tray.update_menu()

    def show_error(self, message, open_config=False, box=None, title=None):
        self.windows.append(TextPopup(self, title or t("Metaphrasis · 出错了"), message, box=box, error=True, open_config=open_config))

    def forget(self, window):
        if window in self.windows:
            self.windows.remove(window)

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def copy_image(self, img):
        try:
            winapi.copy_image(img, self.root.winfo_id())
            return True
        except Exception as e:
            log.exception("Copying the image failed")
            self.show_error(t("复制图片失败：{e}", e=e))
            return False

    def open_file(self, path):
        try:
            os.startfile(str(path))
        except OSError as e:
            self.show_error(t("打不开 {path}：{e}", path=path, e=e))

    def focus(self, win):
        """Bring the window to the front so keys like Esc work right away."""
        try:
            win.update_idletasks()
            winapi.force_foreground(int(win.wm_frame(), 16))
            win.focus_force()
        except (tk.TclError, ValueError):
            pass

    def toast(self, text, anchor=None, seconds=None, button=None):
        """Show a toast in the bottom-right corner (one at a time, new replaces old). Held back during selection and shown afterwards."""
        if self.selection is not None:
            self._deferred_toast = (text, anchor, seconds, button)
            return
        if self._toast:
            self._toast.close()
        self._toast = Toast(self, text, anchor=anchor, seconds=seconds, button=button)

    def place_in_corner(self, win, anchor=None):
        """Bottom-right corner of the screen that holds the selection (or the mouse when there is none)."""
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        if anchor is None:
            x, y = winapi.cursor_pos()
        else:
            x, y = anchor[0] + anchor[2] / 2, anchor[1] + anchor[3] / 2
        _, _, right, bottom = winapi.work_area(x, y)
        margin = round(16 * self.scale)
        win.geometry(f"+{right - w - margin}+{bottom - h - margin}")

    def place_beside(self, win, box, gap=12):
        """Right of the region, else left, below or above; with no region, centred on the screen under the mouse."""
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        if box is None:
            cx, cy = winapi.cursor_pos()
            left, top, right, bottom = winapi.work_area(cx, cy)
            win.geometry(f"+{(left + right - w) // 2}+{(top + bottom - h) // 3}")
            return
        bx, by, bw, bh = box
        left, top, right, bottom = winapi.work_area(bx + bw / 2, by + bh / 2)
        candidates = ((bx + bw + gap + w <= right, bx + bw + gap, by),
                      (bx - gap - w >= left, bx - gap - w, by),
                      (by + bh + gap + h <= bottom, bx, by + bh + gap),
                      (by - gap - h >= top, bx, by - gap - h))
        x, y = next(((x, y) for fits, x, y in candidates if fits), (bx, by))
        x = int(min(max(x, left), right - w))
        y = int(min(max(y, top), bottom - h))
        win.geometry(f"+{x}+{y}")

    def _on_tk_error(self, exc, value, tb):
        log.error("UI error", exc_info=(exc, value, tb))


def main():
    # the animation thread must get the GIL promptly while a worker computes layout (the default 5 ms makes animations stutter)
    sys.setswitchinterval(0.0005)
    settings.scrub_anthropic_env()
    setup_logging()
    try:  # settle the interface language early so even "already running" uses the right one
        i18n.set_language(i18n.resolve(settings.load_config().ui_language))
    except Exception:
        i18n.set_language(i18n.system_language())
    winapi.set_dpi_aware()
    mutex = winapi.single_instance("Local\\FanyiScreenTranslator.SingleInstance")
    if mutex is None:
        if "--if-not-running" in sys.argv:  # the watchdog calls this periodically: exit quietly if already running
            return
        winapi.message_box(t("Metaphrasis 已经在运行了。\n\n托盘图标在任务栏右下角（可能收在 ^ 里面）。"), settings.APP_NAME)
        return
    if "--if-not-running" in sys.argv:
        log_restart = True
    else:
        log_restart = False
    try:
        settings.ensure_files()
        try:
            refresh_icon_file(settings.ICON_PATH)
            autostart.adopt_old_link()  # a "start with Windows" shortcut made under the program's earlier name
        except OSError:
            log.exception("Updating the icon file or the startup shortcut failed")
        app = App()
    except settings.ConfigError as e:
        winapi.message_box(str(e), settings.APP_NAME)
        return
    except Exception as e:
        log.exception("Startup failed")
        winapi.message_box(t("启动失败：{e}\n\n详细信息见 运行日志.txt", e=e), settings.APP_NAME)
        return
    if log_restart:
        log.warning("Watchdog found the program not running and relaunched it (last exit was not a normal quit from the tray)")
    app.run()
    log.info("Main loop ended, exiting")


if __name__ == "__main__":
    main()
