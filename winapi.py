"""Low-level Windows: DPI awareness, global hotkeys, single instance, foreground window, screen info."""
import ctypes
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
WM_APP = 0x8000
WM_APP_ESC_ON = WM_APP + 1
WM_APP_ESC_OFF = WM_APP + 2
VK_ESCAPE = 0x1B
PM_NOREMOVE = 0
ERROR_ALREADY_EXISTS = 183
MONITOR_DEFAULTTONEAREST = 2


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
user32.MessageBoxW.restype = ctypes.c_int
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p

CF_DIB = 8
GMEM_MOVEABLE = 0x0002
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE


def set_dpi_aware():
    """Use real pixels for all coordinates; otherwise captures are misplaced on 125%/150% scaled screens."""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def single_instance(name):
    """Returns the mutex handle, or None if another instance is running."""
    handle = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def message_box(text, title):
    user32.MessageBoxW(None, text, title, 0x40 | 0x40000)  # MB_ICONINFORMATION | MB_TOPMOST


def virtual_screen():
    """Virtual desktop spanning all monitors: (left, top, width, height)."""
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


def cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def work_area(x, y):
    """Usable area (without the taskbar) of the monitor containing (x, y): (left, top, right, bottom)."""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    monitor = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    vx, vy, vw, vh = virtual_screen()
    return vx, vy, vx + vw, vy + vh


def force_foreground(hwnd):
    """Bring a window to the front with keyboard focus (so Esc works)."""
    if not hwnd:
        return
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_tid and fg_tid != cur_tid and user32.AttachThreadInput(fg_tid, cur_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(fg_tid, cur_tid, False)


_MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
              "shift": MOD_SHIFT, "win": MOD_WIN}
_KEY_NAMES = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "insert": 0x2D, "delete": 0x2E,
              "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
              "printscreen": 0x2C, "pause": 0x13, "-": 0xBD, "=": 0xBB,
              "`": 0xC0, "~": 0xC0, "～": 0xC0, "·": 0xC0,  # the key left of 1
              "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF}


def parse_hotkey(text):
    """Parse text like "Ctrl+Alt+T" or "Alt+~" into (modifiers, virtual key code)."""
    parts = [p.strip().lower() for p in text.replace("＋", "+").split("+") if p.strip()]
    if not parts:
        raise ValueError("快捷键为空")
    mods = 0
    for p in parts[:-1]:
        if p not in _MOD_NAMES:
            raise ValueError(f"不认识的修饰键：{p}（可用 Ctrl / Alt / Shift / Win）")
        mods |= _MOD_NAMES[p]
    key = parts[-1]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key in _KEY_NAMES:
        vk = _KEY_NAMES[key]
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"不认识的按键：{key}")
    if mods == 0:
        raise ValueError("快捷键至少要包含 Ctrl / Alt / Shift / Win 中的一个")
    return mods, vk


def _global_copy(data):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise MemoryError("GlobalAlloc 失败")
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def copy_image(img, owner_hwnd):
    """Put an image on the clipboard as both DIB and PNG, so chat apps, Office, Paint and browsers can all paste it.

    owner_hwnd must be a window of this process: Windows refuses writes with an empty clipboard owner.
    """
    import io
    bmp = io.BytesIO()
    img.convert("RGB").save(bmp, "BMP")
    png = io.BytesIO()
    img.save(png, "PNG")
    formats = [(CF_DIB, bmp.getvalue()[14:]),  # a BMP without its 14-byte file header is a DIB
               (user32.RegisterClipboardFormatW("PNG"), png.getvalue())]
    for _ in range(20):  # the clipboard may be held by another program; wait and retry
        if user32.OpenClipboard(owner_hwnd):
            break
        time.sleep(0.025)
    else:
        raise OSError("剪贴板正被其他程序占用，请稍后再试")
    try:
        user32.EmptyClipboard()
        for fmt, data in formats:
            handle = _global_copy(data)
            if not user32.SetClipboardData(fmt, handle):
                kernel32.GlobalFree(handle)
                raise OSError("写入剪贴板失败")
    finally:
        user32.CloseClipboard()


def _mask_menu_key():
    """Tap an unused virtual key (0xE8) so releasing Alt / Win doesn't activate the menu bar or Start menu."""
    user32.keybd_event(0xE8, 0, 0, 0)
    user32.keybd_event(0xE8, 0, 2, 0)  # KEYEVENTF_KEYUP


class HotkeyThread(threading.Thread):
    """Registers global hotkeys on its own thread and puts ("hotkey", name) on the events queue when pressed.

    Esc is registered only while a selection is in progress and released right after, so other programs are unaffected.
    """

    _ESC_ID = 100

    def __init__(self, bindings, events):
        super().__init__(name="hotkeys", daemon=True)
        self.bindings = bindings  # {name: (label, modifiers, virtual key)}
        self.events = events
        self.tid = None
        self._ready = threading.Event()

    def run(self):
        msg = wintypes.MSG()
        self.tid = kernel32.GetCurrentThreadId()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)  # create the message queue
        ids = {}
        for i, (name, (label, mods, vk)) in enumerate(self.bindings.items(), start=1):
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                ids[i] = (name, mods)
            else:
                self.events.put(("hotkey_failed", label, name))
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == self._ESC_ID:
                    self.events.put(("hotkey", "esc"))
                elif msg.wParam in ids:
                    name, mods = ids[msg.wParam]
                    if mods & (MOD_ALT | MOD_WIN) and not mods & MOD_CONTROL:
                        _mask_menu_key()
                    self.events.put(("hotkey", name))
            elif msg.message == WM_APP_ESC_ON:
                user32.RegisterHotKey(None, self._ESC_ID, MOD_NOREPEAT, VK_ESCAPE)
            elif msg.message == WM_APP_ESC_OFF:
                user32.UnregisterHotKey(None, self._ESC_ID)
        for i in list(ids) + [self._ESC_ID]:
            user32.UnregisterHotKey(None, i)

    def _post(self, message):
        if self._ready.wait(2) and self.tid:
            user32.PostThreadMessageW(self.tid, message, 0, 0)

    def set_esc(self, enabled):
        self._post(WM_APP_ESC_ON if enabled else WM_APP_ESC_OFF)

    def stop(self):
        self._post(WM_QUIT)
