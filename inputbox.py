"""Work on the text selected in another program's input box: tell whether the place that has the keyboard can be
typed into at all (asking Windows through UI Automation), take the selection, and put other text in its place.

Keys are only ever sent to a place Windows says is an input box. The selection travels through the clipboard
(Ctrl+C / Ctrl+V), which is the one way every program supports; what was on the clipboard before (text, a picture,
copied files) is put back afterwards.
"""
import ctypes
import queue
import threading
import time
from collections import namedtuple
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")
oleaut32 = ctypes.WinDLL("oleaut32")

EDITABLE, READ_ONLY, PASSWORD, UNKNOWN = "editable", "readonly", "password", "unknown"
MAX_CHARS = 5000  # longer selections are not taken: this is for messages, not documents

# ---------- can the focused control be typed into? ----------
VT_I4, VT_BSTR, VT_BOOL = 3, 8, 11
# UI Automation ids: properties, the text pattern, control types, a text attribute
_CONTROL_TYPE, _IS_PASSWORD, _HAS_VALUE, _VALUE, _VALUE_READ_ONLY = 30003, 30019, 30043, 30045, 30046
_TEXT_PATTERN, _EDIT, _COMBO_BOX, _IS_READ_ONLY_ATTRIBUTE = 10014, 50004, 50003, 40015
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
    {"type", "password", "has_value", "value_read_only", "read_only" (None = not said),
     "selection" and "whole": the selected text and all the text of the box (None = cannot be read)}."""
    held = []  # COM objects to let go of

    def take(pointer):
        held.append(pointer)
        return pointer

    try:
        found = {"type": _property(element, _CONTROL_TYPE), "password": bool(_property(element, _IS_PASSWORD)),
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


def classify(found):
    """EDITABLE / READ_ONLY / PASSWORD / UNKNOWN from what _describe() found (None = Windows told us nothing)."""
    if not found:
        return UNKNOWN
    if found["password"]:
        return PASSWORD
    if found["has_value"] and found["value_read_only"]:
        return READ_ONLY
    if found["read_only"] is not None:  # the program says outright whether the text at the caret can be changed
        return READ_ONLY if found["read_only"] else EDITABLE
    if found["has_value"] or found["type"] in (_EDIT, _COMBO_BOX):
        return EDITABLE
    return UNKNOWN  # a button, a picture, a page, or a program that tells Windows nothing: not known to be a place to type


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
                except Exception:  # a misbehaving program must not take the conversion down with it
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
        return bool(self._ask(lambda: bool(self._box) and _method(self._box, _SET_FOCUS)() >= 0))

    def focus_away(self):
        return bool(self._ask(lambda: bool(self._away) and _method(self._away, _SET_FOCUS)() >= 0))

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
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None


def focus_place():
    """(foreground window, focused child window): compared before and after, to notice that the user went somewhere else."""
    window = user32.GetForegroundWindow()
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    user32.GetGUIThreadInfo(user32.GetWindowThreadProcessId(window, None), ctypes.byref(info))
    return window, info.hwndFocus


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


def snapshot(owner):
    """What is on the clipboard, to put back later: {format: bytes}. {} when it is empty, or holds nothing we can keep."""
    if not _open(owner):
        return {}
    try:
        wanted = set(_KEPT) | {user32.RegisterClipboardFormatW(name) for name in _KEPT_NAMES}
        saved, total, fmt = {}, 0, 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                return saved
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
                    return {}
                saved[fmt] = ctypes.string_at(pointer, size)
            finally:
                kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def restore(saved, owner):
    """Put back what snapshot() took. Nothing was kept -> the clipboard is left as it is."""
    if not saved or not _open(owner):
        return
    try:
        user32.EmptyClipboard()
        for fmt, data in saved.items():
            try:
                _put(fmt, data)
            except OSError:
                pass
        _put(user32.RegisterClipboardFormatW(_NOT_FOR_HISTORY), b"\x00\x00\x00\x00")
    except OSError:
        pass
    finally:
        user32.CloseClipboard()


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


def select_all(session, timeout=0.8):
    """Have the focused program select everything in the box (Ctrl+A). Returns the selection it reports afterwards
    (waiting for it to show one), or None when it is a program that cannot say what is selected."""
    _chord(0x41)  # A
    end = time.monotonic() + timeout
    while True:
        time.sleep(0.08)
        selection = session.look(tries=1).selection
        if selection is None or selection.strip() or time.monotonic() > end:
            return selection


def copy_selection(owner, timeout=1.0):
    """Have the focused program copy its selection and return it; None when nothing was selected."""
    before = user32.GetClipboardSequenceNumber()
    _chord(0x43)  # C
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if user32.GetClipboardSequenceNumber() != before:
            time.sleep(0.05)  # some programs write the clipboard in two steps
            return clipboard_text(owner) or None
        time.sleep(0.02)
    return None


def paste(text, owner):
    """Replace the focused program's selection with text."""
    set_clipboard_text(text, owner, passing=True)
    _chord(0x56)  # V


def _hands_off(quiet=0.25, timeout=4.0):
    """Wait for a moment in which no key and no mouse button is down (the user is not in the middle of typing or
    dragging): keys pressed while the keyboard is briefly taken elsewhere would land in the wrong place."""
    keys = [k for k in range(1, 255) if k not in (0x14, 0x90, 0x91)]  # every key and button, bar the lock keys
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


def put_in_place(session, place, selection, text, owner, whole=None):
    """Replace the box's selection with text. Returns "direct" or "back" (the box had to be gone back to) when it was
    done, "" when it was not.

    place / selection: where the keyboard was and what the box said was selected when the text was taken (None: it is
    a box that cannot say). whole: for a conversion of everything in the box, all its text at that time.

    The user may have clicked somewhere else since. That alone does not cancel the replacement: the box is given the
    keyboard back for a moment, and if it still has the same text selected, the text goes in and the keyboard returns
    to where the user was. With everything-in-the-box conversions an unchanged box is simply selected again. Only when
    the box cannot be reached, or its content has changed, is nothing typed."""
    away = None
    if focus_place() != place or not session.box_focused():
        if selection is None or not _hands_off():
            return False
        away = _front()
        session.mark_away()
        _foreground(place[0])
        session.focus_box()
        end = time.monotonic() + 1.0
        while not (_front() == place[0] and session.box_focused()):
            if time.monotonic() > end:
                break
            time.sleep(0.03)
    done = _front() == place[0] and (away is None or session.box_focused())
    if done and selection is not None:
        now = session.look(tries=1)
        if now.selection != selection:
            # the selection is gone (a click into the text): with everything-in-the-box conversions, an unchanged box is selected again
            done = whole is not None and now.whole == whole and select_all(session) == selection
    if done and wait_keys_up(timeout=1.0):
        paste(text, owner)
        if away is not None:
            time.sleep(0.35)  # the box takes the paste before the keyboard leaves it again
    else:
        done = False
    if away is not None:  # back to where the user was
        if away != place[0]:
            _foreground(away)
        else:
            session.focus_away()
    return ("direct" if away is None else "back") if done else ""
