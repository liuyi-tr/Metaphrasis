"""Per-pixel transparent topmost window plus an animation thread synced to the screen refresh (shared by the spinner and the border highlight).

- Fully transparent background (Windows layered window); clicks pass through, never takes focus.
- The animation runs on its own thread, one frame per screen refresh (60 on a 60 Hz screen, 75 on 75 Hz); no dropped frames while the main thread is busy.
- Only changed regions are written each frame (e.g. the patch the highlight crossed), so large selections stay cheap.
"""
import ctypes
import gc
import threading
import time
from ctypes import wintypes

from PIL import Image

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
winmm = ctypes.WinDLL("winmm")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00080000, 0x00000020
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = 0x00000080, 0x08000000, 0x00000008
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x2, 0x0, 0x1
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
PM_REMOVE = 1
MONITOR_DEFAULTTONEAREST = 2


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class UPDATELAYEREDWINDOWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hdcDst", wintypes.HDC), ("pptDst", ctypes.POINTER(wintypes.POINT)),
                ("psize", ctypes.POINTER(wintypes.SIZE)), ("hdcSrc", wintypes.HDC),
                ("pptSrc", ctypes.POINTER(wintypes.POINT)), ("crKey", wintypes.COLORREF),
                ("pblend", ctypes.POINTER(BLENDFUNCTION)), ("dwFlags", wintypes.DWORD),
                ("prcDirty", ctypes.POINTER(wintypes.RECT))]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", ctypes.c_wchar * 32)]


class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", ctypes.c_wchar * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD), ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", ctypes.c_wchar * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.UpdateLayeredWindowIndirect.argtypes = [wintypes.HWND, ctypes.POINTER(UPDATELAYEREDWINDOWINFO)]
user32.UpdateLayeredWindowIndirect.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFOEXW)]
user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
dwmapi.DwmFlush.restype = ctypes.c_long

_CLASS_NAME = "FanyiLayeredFx"
_class_lock = threading.Lock()
_class_registered = False
_wndproc = WNDPROC(lambda hwnd, msg, wparam, lparam: user32.DefWindowProcW(hwnd, msg, wparam, lparam))


def _register_class():
    global _class_registered
    with _class_lock:
        if not _class_registered:
            wc = WNDCLASSW(lpfnWndProc=_wndproc, hInstance=kernel32.GetModuleHandleW(None), lpszClassName=_CLASS_NAME)
            user32.RegisterClassW(ctypes.byref(wc))
            _class_registered = True


def refresh_rate(x, y):
    """Refresh rate (Hz) of the monitor at these coordinates; 60 if unknown."""
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(MONITORINFOEXW)
    mode = DEVMODEW()
    mode.dmSize = ctypes.sizeof(DEVMODEW)
    monitor = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
    if (monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info))
            and user32.EnumDisplaySettingsW(info.szDevice, -1, ctypes.byref(mode)) and mode.dmDisplayFrequency > 1):
        return mode.dmDisplayFrequency
    return 60


_gc_lock = threading.Lock()
_animating = 0
_gc_was_enabled = False


def _pause_gc(on):
    """Pause Python's garbage collector during an animation: a full collection stalls every thread for ~30 ms and makes the animation hitch. Resumed as soon as the animation ends."""
    global _animating, _gc_was_enabled
    with _gc_lock:
        if on:
            if _animating == 0:
                _gc_was_enabled = gc.isenabled()
                gc.disable()
            _animating += 1
        else:
            _animating -= 1
            if _animating == 0 and _gc_was_enabled:
                gc.enable()


def premultiplied_bgra(img):
    """RGBA image -> premultiplied-alpha BGRA bytes as Windows wants them."""
    r, g, b, a = img.convert("RGBa").split()
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def white_bgra(alpha):
    """Pure white layer with alpha from an L image -> premultiplied BGRA bytes (for white, all four channels equal alpha)."""
    return Image.merge("RGBA", (alpha, alpha, alpha, alpha)).tobytes()


class LayeredAnimation:
    """Animation base class. Subclasses implement prepare() (optional) and draw(now), and set their own fields before calling super().__init__."""

    def __init__(self, x, y, width, height):
        self.x, self.y = int(x), int(y)
        self.width, self.height = max(1, int(width)), max(1, int(height))
        self.hz = refresh_rate(self.x + self.width / 2, self.y + self.height / 2)
        self.start = time.perf_counter()
        self.frames = 0
        self.alive = True
        self.hwnd = None  # created on the animation thread
        self.shown = False
        self.below = getattr(self, "below", None)  # subclasses may set: stay right below this window (otherwise topmost)
        self._stop = False
        self._hidden = False
        _pause_gc(True)
        self._thread = threading.Thread(target=self._run, name=type(self).__name__, daemon=True)
        self._thread.start()

    # -- subclass hooks (called on the animation thread) --
    def prepare(self):
        """Called once before the first frame to prepare assets."""

    def draw(self, now):
        """Return (overall alpha 0-1, [(x0, y0, x1, y1, premultiplied BGRA bytes), ...]); None ends the animation."""
        raise NotImplementedError

    # -- main thread --
    def hide(self):
        self._hidden = True
        return self.alive

    def show(self):
        self._hidden = False

    def close_now(self):
        self._stop = True

    def move_to(self, x, y):
        """Move the window (takes effect on the next frame)."""
        self.x, self.y = int(x), int(y)

    # -- animation thread --
    def _run(self):
        try:
            self._animate()
        finally:  # however the animation ends (including errors), resume garbage collection
            self.alive = False
            _pause_gc(False)

    def _animate(self):
        _register_class()
        w, h = self.width, self.height
        hwnd = user32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
                                      | WS_EX_TOPMOST, _CLASS_NAME, None, WS_POPUP, self.x, self.y, w, h,
                                      None, None, kernel32.GetModuleHandleW(None), None)
        self.hwnd = hwnd
        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(mem_dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(mem_dc, bitmap)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        position, size, origin = wintypes.POINT(self.x, self.y), wintypes.SIZE(w, h), wintypes.POINT()
        dirty = wintypes.RECT()
        info = UPDATELAYEREDWINDOWINFO(ctypes.sizeof(UPDATELAYEREDWINDOWINFO), screen_dc, ctypes.pointer(position),
                                       ctypes.pointer(size), mem_dc, ctypes.pointer(origin), 0,
                                       ctypes.pointer(blend), ULW_ALPHA, None)
        msg = wintypes.MSG()
        period = 1 / self.hz
        visible = False
        fallback_timer = False
        last_alpha = None
        stride = w * 4
        try:
            self.prepare()
            last = time.perf_counter()
            while not self._stop:
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                # wait for the next screen refresh so frames align with it
                if dwmapi.DwmFlush() != 0 or time.perf_counter() - last < period * 0.5:
                    if not fallback_timer:  # desktop composition unavailable: fall back to a timer at the refresh period
                        winmm.timeBeginPeriod(1)
                        fallback_timer = True
                    time.sleep(max(0.0, last + period - time.perf_counter()))
                now = last = time.perf_counter()
                result = self.draw(now + period)  # this frame appears on the next refresh
                if result is None:
                    break
                alpha, updates = result
                box = None
                for x0, y0, x1, y1, data in updates:
                    row = (x1 - x0) * 4
                    if row <= 0 or y1 <= y0:
                        continue
                    if x0 == 0 and x1 == w:
                        ctypes.memmove(bits.value + y0 * stride, data, len(data))
                    else:
                        buf = (ctypes.c_char * len(data)).from_buffer_copy(data)  # must stay referenced until the copy is done
                        src = ctypes.addressof(buf)
                        for r in range(y1 - y0):
                            ctypes.memmove(bits.value + (y0 + r) * stride + x0 * 4, src + r * row, row)
                        del buf
                    box = (x0, y0, x1, y1) if box is None else (min(box[0], x0), min(box[1], y0),
                                                                max(box[2], x1), max(box[3], y1))
                blend.SourceConstantAlpha = max(0, min(255, round(alpha * 255)))
                moved = (position.x, position.y) != (self.x, self.y)
                if moved:
                    position.x, position.y = self.x, self.y
                if blend.SourceConstantAlpha != last_alpha or moved:  # alpha changed or moved: resubmit the whole window
                    info.prcDirty = None
                    user32.UpdateLayeredWindowIndirect(hwnd, ctypes.byref(info))
                    last_alpha = blend.SourceConstantAlpha
                elif box is not None:  # submit only the changed region
                    dirty.left, dirty.top, dirty.right, dirty.bottom = box
                    info.prcDirty = ctypes.pointer(dirty)
                    user32.UpdateLayeredWindowIndirect(hwnd, ctypes.byref(info))
                # z-order: topmost by default (other topmost windows may cover it after being clicked), or right below the given window
                # must be ordered before showing, or it flashes on top for one frame
                user32.SetWindowPos(hwnd, self.below or HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                if self._hidden == visible:
                    visible = not self._hidden
                    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE if visible else SW_HIDE)
                    self.shown = self.shown or visible
                self.frames += 1
        finally:
            if fallback_timer:
                winmm.timeEndPeriod(1)
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
            user32.DestroyWindow(hwnd)
