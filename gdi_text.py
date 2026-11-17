"""Measure and draw text through Windows GDI: hinted and smoothed the way every other Windows app does it, far crisper
at UI sizes than FreeType's unhinted greyscale. render.py uses it for all text and falls back to PIL if it fails."""
import ctypes
import ctypes.wintypes as wt
import threading
from functools import lru_cache

from PIL import Image

gdi32 = ctypes.WinDLL("gdi32")
user32 = ctypes.WinDLL("user32")

FACES = {"zh": "Microsoft YaHei UI", "zh-tw": "Microsoft JhengHei UI", "ja": "Yu Gothic UI", "ko": "Malgun Gothic",
         "latin": "Segoe UI"}
TRANSPARENT, TA_BASELINE, DEFAULT_CHARSET = 1, 24, 1
ANTIALIASED_QUALITY, CLEARTYPE_QUALITY = 4, 5
SPI_GETFONTSMOOTHING, SPI_GETFONTSMOOTHINGTYPE, FE_FONTSMOOTHINGCLEARTYPE = 0x004A, 0x200A, 2


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wt.LONG), ("cy", wt.LONG)]


class TEXTMETRICW(ctypes.Structure):
    _fields_ = [("tmHeight", wt.LONG), ("tmAscent", wt.LONG), ("tmDescent", wt.LONG), ("tmInternalLeading", wt.LONG),
                ("tmExternalLeading", wt.LONG), ("tmAveCharWidth", wt.LONG), ("tmMaxCharWidth", wt.LONG),
                ("tmWeight", wt.LONG), ("tmOverhang", wt.LONG), ("tmDigitizedAspectX", wt.LONG),
                ("tmDigitizedAspectY", wt.LONG), ("tmFirstChar", wt.WCHAR), ("tmLastChar", wt.WCHAR),
                ("tmDefaultChar", wt.WCHAR), ("tmBreakChar", wt.WCHAR), ("tmItalic", wt.BYTE), ("tmUnderlined", wt.BYTE),
                ("tmStruckOut", wt.BYTE), ("tmPitchAndFamily", wt.BYTE), ("tmCharSet", wt.BYTE)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


_int = ctypes.c_int
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateFontW.restype = wt.HFONT
gdi32.CreateFontW.argtypes = [_int] * 5 + [wt.DWORD] * 8 + [wt.LPCWSTR]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.GetTextExtentPoint32W.restype = wt.BOOL
gdi32.GetTextExtentPoint32W.argtypes = [wt.HDC, wt.LPCWSTR, _int, ctypes.POINTER(SIZE)]
gdi32.GetTextMetricsW.restype = wt.BOOL
gdi32.GetTextMetricsW.argtypes = [wt.HDC, ctypes.POINTER(TEXTMETRICW)]
gdi32.SetBkMode.argtypes = [wt.HDC, _int]
gdi32.SetTextAlign.argtypes = [wt.HDC, wt.UINT]
gdi32.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
gdi32.TextOutW.restype = wt.BOOL
gdi32.TextOutW.argtypes = [wt.HDC, _int, _int, wt.LPCWSTR, _int]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFOHEADER), wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wt.HANDLE, wt.DWORD]
user32.SystemParametersInfoW.restype = wt.BOOL
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]

_lock = threading.RLock()  # one GDI device context is shared for measuring; keep its use serialized
_measure_dc = None


def _dc():
    global _measure_dc
    if _measure_dc is None:
        _measure_dc = gdi32.CreateCompatibleDC(None)
        if not _measure_dc:
            raise OSError("CreateCompatibleDC failed")
    return _measure_dc


@lru_cache(maxsize=1)
def _quality():
    """ClearType when the system uses it (the common case on LCDs), plain antialiasing otherwise."""
    on, kind = wt.BOOL(0), wt.UINT(0)
    user32.SystemParametersInfoW(SPI_GETFONTSMOOTHING, 0, ctypes.byref(on), 0)
    user32.SystemParametersInfoW(SPI_GETFONTSMOOTHINGTYPE, 0, ctypes.byref(kind), 0)
    return CLEARTYPE_QUALITY if on.value and kind.value == FE_FONTSMOOTHINGCLEARTYPE else ANTIALIASED_QUALITY


@lru_cache(maxsize=None)  # never evicted: a font handle that falls out of the cache would leak
def _hfont(size, bold, face):
    handle = gdi32.CreateFontW(-int(size), 0, 0, 0, 700 if bold else 400, 0, 0, 0, DEFAULT_CHARSET, 0, 0, _quality(), 0, face)
    if not handle:
        raise OSError("CreateFontW failed")
    return handle


def _units(text):
    return len(text.encode("utf-16-le")) // 2  # GDI counts UTF-16 code units, not code points


@lru_cache(maxsize=50000)
def _width(text, size, bold, face):
    if not text:
        return 0
    with _lock:
        dc = _dc()
        gdi32.SelectObject(dc, _hfont(size, bold, face))
        sz = SIZE()
        if not gdi32.GetTextExtentPoint32W(dc, text, _units(text), ctypes.byref(sz)):
            raise OSError("GetTextExtentPoint32W failed")
        return sz.cx


class Font:
    """A GDI font at a pixel size. getlength() mirrors PIL's ImageFont API so the wrapping code works with either."""
    __slots__ = ("size", "bold", "face", "handle", "ascent", "descent")

    def __init__(self, size, bold, lang):
        self.size, self.bold = int(size), bool(bold)
        self.face = FACES.get(lang, FACES["latin"])
        self.handle = _hfont(self.size, self.bold, self.face)
        with _lock:
            dc = _dc()
            gdi32.SelectObject(dc, self.handle)
            tm = TEXTMETRICW()
            gdi32.GetTextMetricsW(dc, ctypes.byref(tm))
        self.ascent, self.descent = tm.tmAscent, tm.tmDescent

    def getlength(self, text):
        return _width(text, self.size, self.bold, self.face)


@lru_cache(maxsize=None)
def font(size, bold, lang):
    return Font(size, bold, lang)


def draw(img, items):
    """Draw text onto a copy of an RGB image and return it. items: (x, baseline_y, text, Font, (r, g, b)).

    The whole image goes into a DIB first so the smoothing blends against the pixels that are really there."""
    img = img.convert("RGB")
    w, h = img.size
    with _lock:
        dc = gdi32.CreateCompatibleDC(None)
        if not dc:
            raise OSError("CreateCompatibleDC failed")
        try:
            bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            bits = ctypes.c_void_p()
            bmp = gdi32.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if not bmp or not bits:
                raise OSError("CreateDIBSection failed")
            try:
                old = gdi32.SelectObject(dc, bmp)
                ctypes.memmove(bits, img.tobytes("raw", "BGRX"), w * h * 4)
                gdi32.SetBkMode(dc, TRANSPARENT)
                gdi32.SetTextAlign(dc, TA_BASELINE)
                for x, y, text, fnt, color in items:
                    if not text:
                        continue
                    gdi32.SelectObject(dc, fnt.handle)
                    gdi32.SetTextColor(dc, color[0] | (color[1] << 8) | (color[2] << 16))
                    gdi32.TextOutW(dc, int(round(x)), int(round(y)), text, _units(text))
                gdi32.GdiFlush()
                out = Image.frombuffer("RGB", (w, h), ctypes.string_at(bits, w * h * 4), "raw", "BGRX", 0, 1).copy()
                gdi32.SelectObject(dc, old)
            finally:
                gdi32.DeleteObject(bmp)
        finally:
            gdi32.DeleteDC(dc)
    return out
