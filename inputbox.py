"""Work on the text selected in another program's input box: tell whether the place that has the keyboard can be
typed into at all (asking Windows through UI Automation), take the selection, and put other text in its place.

Keys are only ever sent to a place Windows says is an input box (a terminal is not one: the same keys mean other
things there). The selection travels through the clipboard (Ctrl+C / Ctrl+V), which is the one way every program
supports; what was on the clipboard before (text, a picture, copied files) is put back afterwards.
"""
import ctypes
import logging
import queue
import threading
import time
from collections import namedtuple
from ctypes import wintypes

log = logging.getLogger("fanyi")  # only what happened is logged, never a word of the text or of the clipboard
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")
oleaut32 = ctypes.WinDLL("oleaut32")

EDITABLE, READ_ONLY, PASSWORD, UNKNOWN = "editable", "readonly", "password", "unknown"
MAX_CHARS = 5000  # longer selections are not taken: this is for messages, not documents
# characters an empty box may hold all the same: the placeholder a browser reports for one, and zero-width marks
_INVISIBLE = dict.fromkeys((0xFFFC, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))


def blank(text):
    """Is there nothing to read in this text? (None, nothing but spaces, or only what an empty box holds.)"""
    return not text or not text.translate(_INVISIBLE).strip()


# ---------- can the focused control be typed into? ----------
VT_I4, VT_BSTR, VT_BOOL = 3, 8, 11
# UI Automation ids: properties, the text pattern, control types, a text attribute
_CONTROL_TYPE, _CLASS_NAME, _IS_PASSWORD, _HAS_VALUE, _VALUE, _VALUE_READ_ONLY = 30003, 30012, 30019, 30043, 30045, 30046
_TEXT_PATTERN, _EDIT, _TEXT, _DOCUMENT, _IS_READ_ONLY_ATTRIBUTE = 10014, 50004, 50020, 50030, 40015
# positions in the COM method tables (UIAutomationClient.h)
_RELEASE = 2                                               # IUnknown
_COMPARE_ELEMENTS, _GET_FOCUSED_ELEMENT = 3, 8             # IUIAutomation
_SET_FOCUS, _GET_CURRENT_PROPERTY_VALUE, _GET_CURRENT_PATTERN_AS = 3, 10, 14  # IUIAutomationElement
_GET_SELECTION, _DOCUMENT_RANGE = 5, 7                     # IUIAutomationTextPattern
_ARRAY_LENGTH, _ARRAY_ELEMENT = 3, 4                       # IUIAutomationTextRangeArray
_RANGE_ATTRIBUTE, _RANGE_TEXT = 9, 12                      # IUIAutomationTextRange


class GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(self))


class _Value(ctypes.Union):
    _fields_ = [("i4", ctypes.c_long), ("boolean", ctypes.c_short), ("pointer", ctypes.c_void_p), ("pair", ctypes.c_void_p * 2)]


class VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort), ("value", _Value)]


ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CoUninitialize.restype = None
oleaut32.VariantClear.argtypes = [ctypes.POINTER(VARIANT)]
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
oleaut32.SysStringLen.argtypes = [ctypes.c_void_p]
oleaut32.SysStringLen.restype = ctypes.c_uint
_OUT = ctypes.POINTER(ctypes.c_void_p)


def _method(obj, index, *argtypes):
    """A method of a COM object by its position in the method table, already bound to the object."""
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])
    return lambda *args: function(obj, *args)


def _release(obj):
    if obj:
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
            ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[_RELEASE])(obj)


def _property(element, pid):
    """One UI Automation property of an element: int, bool, text, or None when it has none."""
    v = VARIANT()
    try:
        if _method(element, _GET_CURRENT_PROPERTY_VALUE, ctypes.c_int, ctypes.POINTER(VARIANT))(pid, ctypes.byref(v)) < 0:
            return None
        if v.vt == VT_BSTR:
            return ctypes.wstring_at(v.value.pointer, oleaut32.SysStringLen(v.value.pointer)) if v.value.pointer else ""
        return v.value.i4 if v.vt == VT_I4 else bool(v.value.boolean) if v.vt == VT_BOOL else None
    finally:
        oleaut32.VariantClear(ctypes.byref(v))


def _range_text(text_range, limit):
    text = ctypes.c_void_p()
    if _method(text_range, _RANGE_TEXT, ctypes.c_int, _OUT)(limit, ctypes.byref(text)) < 0 or not text:
        return ""
    try:
        return ctypes.wstring_at(text, oleaut32.SysStringLen(text))
    finally:
        oleaut32.SysFreeString(text)


def _range_read_only(text_range):
    """Is the text of this range read-only? None when the program does not say (or it is mixed)."""
    v = VARIANT()
    try:
        if _method(text_range, _RANGE_ATTRIBUTE, ctypes.c_int, ctypes.POINTER(VARIANT))(_IS_READ_ONLY_ATTRIBUTE, ctypes.byref(v)) < 0:
            return None
        return bool(v.value.boolean) if v.vt == VT_BOOL else None
    finally:
        oleaut32.VariantClear(ctypes.byref(v))


def _describe(element):
    """What Windows knows about an element:
    {"type", "class" (the program's own name for this kind of control), "password", "has_value", "value_read_only",
     "read_only" (None = not said),
     "selection" and "whole": the selected text and all the text of the box (None = cannot be read)}."""
    held = []  # COM objects to let go of

    def take(pointer):
        held.append(pointer)
        return pointer

    try:
        found = {"type": _property(element, _CONTROL_TYPE), "class": _property(element, _CLASS_NAME), "password": bool(_property(element, _IS_PASSWORD)),
                 "has_value": bool(_property(element, _HAS_VALUE)), "value_read_only": None, "selection": None, "whole": None, "read_only": None}
        if found["has_value"]:
            found["value_read_only"] = bool(_property(element, _VALUE_READ_ONLY))
        if found["password"]:
            return found  # nothing in a password box is read
        pattern = ctypes.c_void_p()
        if _method(element, _GET_CURRENT_PATTERN_AS, ctypes.c_int, ctypes.POINTER(GUID), _OUT)(
                _TEXT_PATTERN, ctypes.byref(GUID("{32eba289-3583-42c9-9c59-3b6d9a1e9b6a}")), ctypes.byref(pattern)) >= 0 and pattern:
            take(pattern)
            ranges, count = ctypes.c_void_p(), ctypes.c_int()
            if _method(pattern, _GET_SELECTION, _OUT)(ctypes.byref(ranges)) >= 0 and ranges:
                take(ranges)
                found["selection"] = ""
                _method(ranges, _ARRAY_LENGTH, ctypes.POINTER(ctypes.c_int))(ctypes.byref(count))
                if count.value > 0:
                    first = ctypes.c_void_p()
                    if _method(ranges, _ARRAY_ELEMENT, ctypes.c_int, _OUT)(0, ctypes.byref(first)) >= 0 and first:
                        take(first)
                        found["selection"] = _range_text(first, MAX_CHARS + 1)
                        found["read_only"] = _range_read_only(first)
            whole = ctypes.c_void_p()
            if _method(pattern, _DOCUMENT_RANGE, _OUT)(ctypes.byref(whole)) >= 0 and whole:
                take(whole)
                found["whole"] = _range_text(whole, MAX_CHARS + 1)
                if found["read_only"] is None:  # no caret and no selection: go by the text as a whole
                    found["read_only"] = _range_read_only(whole)
        elif found["has_value"]:  # a plain text box: it has no selection to ask about, but it gives its content
            value = _property(element, _VALUE)
            found["whole"] = value[:MAX_CHARS + 1] if isinstance(value, str) else None
        return found
    finally:
        for pointer in reversed(held):
            _release(pointer)


# Terminals. They can be typed into, and say so, but they are no place for this: Ctrl+C there stops the program that
# is running, and lines pasted there are run as commands. Told by the window they are in (the Windows console, Windows
# Terminal, ConEmu, mintty, PuTTY, KiTTY) or by the control itself (Windows Terminal's, and the hidden text box of
# xterm.js, which is the terminal inside VS Code and many other apps)
_TERMINAL_WINDOWS = ("consolewindowclass", "cascadia_hosting_window_class", "virtualconsoleclass", "mintty", "putty", "kitty")
_TERMINAL_CONTROLS = ("termcontrol", "xterm")


def _is_terminal(found):
    return ((found.get("window") or "").lower() in _TERMINAL_WINDOWS
            or any(word in (found.get("class") or "").lower() for word in _TERMINAL_CONTROLS))


def classify(found):
    """EDITABLE / READ_ONLY / PASSWORD / UNKNOWN from what _describe() found (None = Windows told us nothing).

    EDITABLE is a place where text is typed. A checkbox, a slider or a drop-down list also holds a value that can be
    changed, and browsers report that, but none of them is a text box: without word from the program that the text
    at the caret can be changed, only a control of the "edit" kind counts."""
    if not found:
        return UNKNOWN
    if found["password"]:
        return PASSWORD
    if _is_terminal(found):
        return UNKNOWN
    if found["has_value"] and found["value_read_only"]:
        return READ_ONLY
    if found["read_only"] is not None and found["type"] != _TEXT:  # the program says outright whether the text at the caret can be changed
        return READ_ONLY if found["read_only"] else EDITABLE
    if found["type"] == _EDIT or (found["type"] == _DOCUMENT and found["has_value"]):
        return EDITABLE
    return UNKNOWN  # a button, a list, a picture, a page, or a program that tells Windows nothing: not known to be a place to type


def _facts(found):
    """What _describe() found, for the log: the kind of place and the sizes of its text, never a word of the text."""
    def size(text):
        return None if text is None else len(text)
    return ("type %s, class %r, window %r, value %s (read-only %s), text read-only %s, %s chars selected of %s" % (
        found["type"], (found["class"] or "")[:60], found.get("window"), found["has_value"], found["value_read_only"],
        found["read_only"], size(found["selection"]), size(found["whole"])))


Focus = namedtuple("Focus", "kind selection whole")  # selection / whole: None when the program does not let them be read
_NOTHING = Focus(UNKNOWN, None, None)


def _focus(found):
    return Focus(classify(found), found.get("selection"), found.get("whole")) if found else _NOTHING


class Session:
    """One conversion's dealings with UI Automation.

    Everything is asked on one thread of the session's own: the answers come from the program that owns the place, so
    each question has a time limit, and a program that stops answering is not asked again. The input box that had the
    keyboard at the start is kept, so that later (the user may have clicked somewhere else while the API worked) it can
    be asked whether it still has the same text selected, and be given the keyboard back."""

    def __init__(self):
        self._tasks = queue.Queue()
        self._silent = False  # the program did not answer in time
        self.seen = None  # what the last look found, for the log (no text in it)
        self._automation = self._box = self._away = None  # COM objects; touched on the session thread only
        threading.Thread(target=self._run, name="uia", daemon=True).start()

    def _run(self):
        ole32.CoInitializeEx(None, 0)  # multithreaded
        try:
            while True:
                task = self._tasks.get()
                if task is None:
                    return
                function, result, done = task
                try:
                    result.append(function())
                except Exception as e:  # a misbehaving program must not take the conversion down with it
                    log.warning("Asking Windows about the place that has the keyboard failed (%s)", type(e).__name__)
                    result.append(None)
                done.set()
        finally:
            for pointer in (self._box, self._away, self._automation):
                _release(pointer)
            ole32.CoUninitialize()

    def _ask(self, function, timeout=1.5):
        if self._silent:
            return None
        result, done = [], threading.Event()
        self._tasks.put((function, result, done))
        if not done.wait(timeout):
            self._silent = True
            return None
        return result[0]

    # --- on the session thread ---
    def _focused(self):
        if not self._automation:
            automation = ctypes.c_void_p()
            if ole32.CoCreateInstance(ctypes.byref(GUID("{ff48dba4-60ef-4201-aa87-54103eef594e}")), None, 1,
                                      ctypes.byref(GUID("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")), ctypes.byref(automation)) < 0:
                return None
            self._automation = automation
        element = ctypes.c_void_p()
        if _method(self._automation, _GET_FOCUSED_ELEMENT, _OUT)(ctypes.byref(element)) < 0 or not element:
            return None
        return element

    def _look(self, keep):
        element = self._focused()
        if not element:
            return None
        found = _describe(element)
        found["window"] = _window_class(user32.GetForegroundWindow())
        self.seen = _facts(found)
        if keep and classify(found) == EDITABLE:
            _release(self._box)
            self._box = element
        else:
            _release(element)
        return found

    def _box_focused(self):
        element = self._focused()
        if not element or not self._box:
            _release(element)
            return False
        same = wintypes.BOOL()
        ok = _method(self._automation, _COMPARE_ELEMENTS, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL))(
            self._box, element, ctypes.byref(same)) >= 0
        _release(element)
        return ok and bool(same.value)

    def _mark(self):
        _release(self._away)
        self._away = self._focused()

    # --- for the caller ---
    def look(self, tries=3, pause=0.35, keep=False):
        """Focus(kind, selected text, all the text of the box) for the place that has the keyboard now.

        Browsers and apps built on them only describe their content once somebody has asked, so the first answer can
        be an empty one: ask again a moment later. keep: if it is an input box, hold on to it as "the box"."""
        settle_native_focus()  # (left on a page's hidden window, the answer would be about that page only)
        focus = _NOTHING
        for attempt in range(tries):
            if attempt:
                time.sleep(pause)
            focus = _focus(self._ask(lambda: self._look(keep)))
            if focus.kind in (EDITABLE, PASSWORD) or self._silent:
                break
        return focus

    def box(self):
        """The kept box as it is now, whether or not it still has the keyboard."""
        return _focus(self._ask(lambda: _describe(self._box) if self._box else None))

    def box_focused(self):
        """Does the kept box have the keyboard?"""
        return bool(self._ask(self._box_focused))

    def mark_away(self):
        """Remember the place that has the keyboard now (where the user went), to hand the keyboard back to it."""
        self._ask(self._mark)

    def focus_box(self):
        """Give the kept box the keyboard."""
        done = bool(self._ask(lambda: bool(self._box) and _method(self._box, _SET_FOCUS)() >= 0))
        settle_native_focus()  # (UI Automation's SetFocus leaves it on the page's hidden window)
        return done

    def focus_away(self):
        """Give the keyboard back to the place remembered with mark_away()."""
        done = bool(self._ask(lambda: bool(self._away) and _method(self._away, _SET_FOCUS)() >= 0))
        settle_native_focus()
        return done

    def close(self):
        self._tasks.put(None)


def look(tries=3, pause=0.35):
    """Focus(kind, selected text, all the text of the box) for the place that has the keyboard, asked once and done."""
    session = Session()
    try:
        return session.look(tries=tries, pause=pause)
    finally:
        session.close()


# ---------- where the keyboard is ----------
class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND), ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None


def _window_class(window):
    """The kind of window, as its program registered it ("" when there is none)."""
    name = ctypes.create_unicode_buffer(256)
    return name.value if window and user32.GetClassNameW(window, name, 256) else ""


def _keyboard_window(window):
    """The window that has the keyboard in the program `window` belongs to, as Windows itself records it."""
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    user32.GetGUIThreadInfo(user32.GetWindowThreadProcessId(window, None), ctypes.byref(info))
    return info.hwndFocus


def focus_place():
    """(foreground window, focused child window): compared before and after, to notice that the user went somewhere else."""
    window = user32.GetForegroundWindow()
    return window, _keyboard_window(window)


user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
_PAGE_WINDOW = "Chrome_RenderWidgetHostHWND"  # the hidden window each page has in a program built on Chromium


def _parent_window(window):
    return user32.GetAncestor(window, 1)  # GA_PARENT


def _give_keyboard(window):
    """Make `window` the one that has the keyboard in its own program. Windows lets a thread do that for its own
    windows only, so ours joins theirs for the moment."""
    theirs, mine = user32.GetWindowThreadProcessId(window, None), kernel32.GetCurrentThreadId()
    joined = bool(theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True))
    try:
        user32.SetFocus(window)
    finally:
        if joined:
            user32.AttachThreadInput(mine, theirs, False)
    return _keyboard_window(window) == window


def settle_native_focus(window=None):
    """Take the keyboard, as Windows itself records it, off a page's hidden window and give it back to the window the
    page is in (window: the program's window; the one in front when not given). True when that was needed.

    In programs built on Chromium (browsers, Electron apps) the main window has the keyboard and hands the keys on to
    whichever of its pages is the active one; each page also has a hidden window of its own, which never has it.
    UI Automation's SetFocus(), which is how the box is given the keyboard back, leaves the keyboard on the hidden
    window of that page. Typing goes on working, but from then on UI Automation answers "what has the keyboard?" from
    that one page's point of view, whichever page the user clicks into afterwards: an input box in another page of the
    same window (the chat box next to a browser pane, say) is then taken for a place that cannot be typed into, and
    the other way round. So it is put right before asking, and after each SetFocus() of our own."""
    window = window or user32.GetForegroundWindow()
    held = _keyboard_window(window) if window else None
    if not held or _window_class(held) != _PAGE_WINDOW:
        return False
    parent = _parent_window(held)
    if not parent:
        return False
    if not _give_keyboard(parent):
        log.info("The keyboard was on a page's hidden window and could not be given back to the window it is in")
    return True


def wait_keys_up(extra=(), timeout=2.0):
    """Wait until Ctrl, Alt, Shift, Win and the given keys are released: a key the user still holds would join the ones we send."""
    keys = (0x10, 0x11, 0x12, 0x5B, 0x5C) + tuple(extra)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(user32.GetAsyncKeyState(k) & 0x8000 for k in keys):
            return True
        time.sleep(0.02)
    return False


def _chord(key):
    """Press Ctrl + key."""
    for vk, up in ((0x11, 0), (key, 0), (key, 2), (0x11, 2)):
        user32.keybd_event(vk, 0, up, 0)


# ---------- clipboard ----------
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HANDLE
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalFree.argtypes = [wintypes.HANDLE]


def _open(owner):
    for _ in range(20):  # another program may be holding the clipboard for a moment
        if user32.OpenClipboard(owner):
            return True
        time.sleep(0.025)
    return False


def _put(fmt, data):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("clipboard write failed")
    ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        raise OSError("clipboard write failed")


user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.EnumClipboardFormats.restype = wintypes.UINT
kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t
# what the user had on the clipboard is put back afterwards: text, a picture (in its usual forms), copied files
_KEPT = (CF_UNICODETEXT, 8, 17, 15)  # text, CF_DIB, CF_DIBV5, CF_HDROP
_KEPT_NAMES = ("PNG", "HTML Format", "Rich Text Format", "Preferred DropEffect")
MAX_KEPT = 256 * 1024 * 1024
_NOT_FOR_HISTORY = "ExcludeClipboardContentFromMonitorProcessing"  # asks clipboard-history tools to leave an entry out


def sequence():
    """A number that goes up each time anything is put on the clipboard: compared before and after, it tells whether
    somebody (the user, another program) has copied something in between."""
    return user32.GetClipboardSequenceNumber()


def snapshot(owner):
    """What is on the clipboard, to put back later: {format: bytes}; {} when the clipboard is empty.
    None when it cannot be kept: another program has the clipboard open, it holds more than MAX_KEPT, or nothing it
    holds is of a kind that is kept."""
    if not _open(owner):
        return None
    try:
        wanted = set(_KEPT) | {user32.RegisterClipboardFormatW(name) for name in _KEPT_NAMES}
        saved, total, fmt, kinds = {}, 0, 0, 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                return saved if saved or not kinds else None
            kinds += 1
            if fmt not in wanted:
                continue
            handle = user32.GetClipboardData(fmt)
            size = kernel32.GlobalSize(handle) if handle else 0
            pointer = kernel32.GlobalLock(handle) if size else None
            if not pointer:
                continue
            try:
                total += size
                if total > MAX_KEPT:
                    return None
                saved[fmt] = ctypes.string_at(pointer, size)
            finally:
                kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def restore(saved, owner):
    """Put back what snapshot() took ({}: the clipboard was empty, and is emptied again). None: nothing was kept, and
    the clipboard is left as it is. True when all of it went back."""
    if saved is None:
        return False
    if not _open(owner):
        log.warning("The clipboard was in use by another program; what it held before could not be put back")
        return False
    lost = 0
    try:
        user32.EmptyClipboard()
        for fmt, data in saved.items():
            try:
                _put(fmt, data)
            except OSError:
                lost += 1
        if saved:
            _put(user32.RegisterClipboardFormatW(_NOT_FOR_HISTORY), b"\x00\x00\x00\x00")
    except OSError:
        lost += 1
    finally:
        user32.CloseClipboard()
    if lost:
        log.warning("Putting back what the clipboard held: %d of its %d kinds of content could not be written", lost, len(saved))
    return not lost


def clipboard_text(owner):
    """Text on the clipboard, or None when there is none (or the clipboard holds something else)."""
    if not _open(owner):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer) if pointer else None
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text, owner, passing=False):
    """Put text on the clipboard. owner must be a window of this process.

    passing: the text is only on its way through (about to be pasted): ask clipboard-history tools to leave it out."""
    if not _open(owner):
        raise OSError("clipboard busy")
    try:
        user32.EmptyClipboard()
        _put(CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00")
        if passing:
            _put(user32.RegisterClipboardFormatW(_NOT_FOR_HISTORY), b"\x00\x00\x00\x00")
    finally:
        user32.CloseClipboard()


# ---------- the keys ----------
def select_all(session, timeout=0.8):
    """Have the focused program select everything in the box (Ctrl+A). Returns the selection it reports afterwards
    (waiting for it to show one), or None when it is a program that cannot say what is selected. "" as well when the
    key was not sent: the user is holding Ctrl, Alt, Shift or Win, which would join it."""
    if not wait_keys_up(timeout=1.0):
        return ""
    _chord(0x41)  # A
    end = time.monotonic() + timeout
    while True:
        time.sleep(0.08)
        selection = session.look(tries=1).selection
        if selection is None or not blank(selection) or time.monotonic() > end:
            return selection


def copy_selection(owner, timeout=1.0):
    """Have the focused program copy its selection and return it; None when it gave no text (nothing was selected,
    or the key was not sent: the user is holding Ctrl, Alt, Shift or Win). Whether the clipboard was written to at
    all is for the caller to tell from sequence(): a program may copy something that is not text."""
    if not wait_keys_up(timeout=1.0):
        return None
    before = sequence()
    _chord(0x43)  # C
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if sequence() != before:
            time.sleep(0.05)  # some programs write the clipboard in two steps
            return clipboard_text(owner) or None
        time.sleep(0.02)
    return None


def _tap(key):
    """Press one key on its own."""
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, 2, 0)


def unselect(session, place, selected):
    """Take away a selection the program made itself (Ctrl+A) when nothing is going to replace it after all: left as
    it is, the next key the user typed would wipe the whole message. The caret goes to the end of the text (the Right
    arrow key). Only when the box still has the keyboard and still shows that very selection; a box that cannot say
    what is selected is left alone. True when the key was sent."""
    if not selected or focus_place() != place or not session.box_focused():
        return False
    if session.look(tries=1).selection != selected or not wait_keys_up(timeout=0.3) or focus_place() != place:
        return False
    _tap(0x27)  # Right
    return True


# keys that are no sign of anybody typing: the lock keys, and the mode keys of a Japanese keyboard, one of which
# always reads as held down
_NOT_PRESSED = frozenset((0x14, 0x90, 0x91)) | frozenset(range(0xF0, 0xF6))


def _hands_off(quiet=0.6, timeout=4.0):
    """Wait until the user's hands are off the keyboard and the mouse buttons: no key and no button down for `quiet`
    seconds on end. Keys pressed while the keyboard is briefly taken elsewhere would land in the wrong place, and the
    gap between two keys of somebody who is typing is shorter than that. False when no such moment comes in time."""
    keys = [k for k in range(1, 255) if k not in _NOT_PRESSED]
    end = time.monotonic() + timeout
    calm = time.monotonic()
    while time.monotonic() < end:
        if any(user32.GetAsyncKeyState(k) & 0x8000 for k in keys):
            calm = time.monotonic()
        elif time.monotonic() - calm >= quiet:
            return True
        time.sleep(0.01)
    return False


def _front():
    return user32.GetForegroundWindow()


def _foreground(window):
    import winapi
    winapi.force_foreground(window)


def _landed(session, before, same, timeout=1.0):
    """Has the box taken the paste? True: its text is no longer what it was. False: it still is, `timeout` later.
    None: there is no telling (the box does not let its text be read, or what went in is letter for letter what was
    there); it is then given the moment a program needs to take a paste.

    before: the box as it was just before the paste."""
    readable = before.whole is not None and len(before.whole) <= MAX_CHARS
    if same or not (readable or before.selection is not None):
        time.sleep(0.35)
        return None
    end = time.monotonic() + timeout
    while True:
        time.sleep(0.04)
        now = session.box()
        if (now.whole != before.whole) if readable else (now.selection != before.selection):
            return True
        if time.monotonic() > end:
            return False


def put_in_place(session, place, selection, text, owner, whole=None, everything=False, same=False, pasted=None):
    """Replace the box's selection with text. Returns "direct", or "back" (the box had to be gone back to), when the
    text went in; "" when nothing was typed, or the box did not take it. After "" the caller still has the text to
    hand over some other way, and the clipboard may or may not hold it.

    place / selection / whole: where the keyboard was, what the box said was selected, and all the text it held, when
    the text was taken (None: it is a box that cannot say). everything: the conversion is of all the text in the box
    (the program selected it itself). same: what goes in is letter for letter what is selected (the box will look no
    different afterwards). pasted(): called as soon as the text is in.

    The user may have clicked somewhere else since. That alone does not cancel the replacement: once their hands are
    off the keyboard, the box is given the keyboard back for a moment, and if it still has the same text selected,
    the text goes in and the keyboard returns to where the user was. With everything-in-the-box conversions an
    unchanged box is simply selected again. Only when the box cannot be reached, or its content has changed, is
    nothing typed.

    The clipboard carries the text into the box. What it holds at that moment (the user may have copied something
    while the API worked) is taken first, and put back once the box has taken the paste, unless something else has
    been copied in the meantime."""
    away = kept = mine = landed = None
    done = False
    try:
        if focus_place() != place or not session.box_focused():
            if selection is None or not _hands_off():
                return ""
            away = _front()
            session.mark_away()
            _foreground(place[0])
            session.focus_box()
            end = time.monotonic() + 0.6
            while not (_front() == place[0] and session.box_focused()) and time.monotonic() < end:
                time.sleep(0.03)
        ok = _front() == place[0] and session.box_focused()
        before = session.look(tries=1) if ok else None
        if ok and selection is not None and before.selection != selection:
            # the selection is gone (a click into the text): with everything-in-the-box conversions, an unchanged box is selected again
            ok = everything and before.whole == whole and select_all(session) == selection
            before = before._replace(selection=selection)
        elif ok and selection is None and whole is not None:
            # a box that cannot say what is selected: its text at least must be what it was. With everything-in-the-box
            # conversions all of it is selected once more (a click into it would have left only a caret)
            ok = before.whole == whole
            if ok and everything:
                select_all(session)
        if ok:
            kept = snapshot(owner)
            set_clipboard_text(text, owner, passing=True)
            mine = sequence()
            # the last look before the key goes: nothing held down that would join it, and the keyboard still in the box
            if wait_keys_up(timeout=1.0) and _front() == place[0] and session.box_focused():
                _chord(0x56)  # V
                # (waited for less long when the keyboard was taken from somewhere else: it is due back there)
                landed = _landed(session, before, same, timeout=2.0 if away is None else 1.0)
                done = landed is not False
    except OSError:
        log.warning("The clipboard was in use by another program; the text could not be put on it")
    finally:
        if away is not None:  # back to where the user was
            if away != place[0]:
                _foreground(away)
            else:
                session.focus_away()
    if not done:
        return ""
    if pasted:
        pasted()
    if landed is None:
        time.sleep(0.65)  # a box that cannot be read: a program takes a paste well within a second of the key
    if sequence() == mine:  # (if not, something else has been copied meanwhile: that stays)
        restore(kept, owner)
    return "direct" if away is None else "back"
