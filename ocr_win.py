"""Text and positions from the built-in Windows OCR (Windows.Media.Ocr)."""
import asyncio
import math
import re
from dataclasses import dataclass

from PIL import Image
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter


@dataclass
class OcrLine:
    id: int
    text: str
    box: tuple  # (left, top, right, bottom) in screenshot pixels


def _engine_for(prefix):
    for lang in OcrEngine.available_recognizer_languages:
        if lang.language_tag.lower().startswith(prefix):
            return OcrEngine.try_create_from_language(Language(lang.language_tag))
    return None


def _engine(lang="en"):
    """Engine for the given language; falls back to English, then to the user's profile languages."""
    engine = _engine_for(lang) or _engine_for("en") or OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("这台电脑没有可用的 Windows OCR 语言包")
    return engine


def has_language(prefix):
    """Is the OCR pack for this language installed (e.g. "ja", "zh")?"""
    return _engine_for(prefix) is not None


def _to_bitmap(img):
    r, g, b, a = img.convert("RGBA").split()
    writer = DataWriter()
    writer.write_bytes(Image.merge("RGBA", (b, g, r, a)).tobytes())
    return SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                  img.width, img.height)


async def _recognize(engine, bitmap):
    return await engine.recognize_async(bitmap)


def _run(engine, img):
    """Upscale small text, then recognize; returns (result, scale factor)."""
    if max(img.size) < 400:
        scale = 3
    elif img.width * img.height <= 1_500_000:
        scale = 2
    else:
        scale = 1
    max_dim = OcrEngine.max_image_dimension
    while scale > 1 and max(img.size) * scale > max_dim:
        scale -= 1
    work = img.resize((img.width * scale, img.height * scale), Image.LANCZOS) if scale > 1 else img
    if max(work.size) > max_dim:
        work = work.copy()
        work.thumbnail((max_dim, max_dim))
    return asyncio.run(_recognize(engine, _to_bitmap(work))), work.width / img.width


def recognize(img, lang="en"):
    """OcrLine list in the reading order OCR gives. lang: recognition language; falls back to English when the pack is missing."""
    result, factor = _run(_engine(lang), img)
    return _lines_from(result, factor, img)


def _lines_from(result, factor, img):
    lines = []
    for line in result.lines:
        rects = [w.bounding_rect for w in line.words]
        text = line.text.strip()
        if not rects or not text:
            continue
        x0 = max(0, math.floor(min(r.x for r in rects) / factor))
        y0 = max(0, math.floor(min(r.y for r in rects) / factor))
        x1 = min(img.width, math.ceil(max(r.x + r.width for r in rects) / factor))
        y1 = min(img.height, math.ceil(max(r.y + r.height for r in rects) / factor))
        if x1 > x0 and y1 > y0:
            lines.append(OcrLine(len(lines), text, (x0, y0, x1, y1)))
    return lines


# the Chinese engine misreads kana as these radicals (which almost never stand alone in real Chinese)
_KANA_GHOSTS = set("丿冫亻兯丶乚亅卩阝彡冖廴宀巛灬丷亠刂氵扌忄犭纟讠钅饣礻衤〈〉℃〖〗圭匕廿弋彐祀吨乜")


def _script_stats(text):
    hanzi = len(re.findall(r"[一-鿿]", text))
    words = len(re.findall(r"[A-Za-z]{2,}", text))
    kana = len(re.findall(r"[぀-ヿ]", text))
    ghosts = sum(c in _KANA_GHOSTS for c in text) + len(re.findall(r"(?<![A-Za-z0-9])[A-Za-z](?![A-Za-z0-9])", text))
    return hanzi, words, kana, ghosts


def is_chinese_text(text):
    """From the Chinese engine's output: the content is Chinese (almost no foreign text).

    Conservative: mixed Chinese/English, anything Japanese-looking (kana or kana misread as radicals), or too few characters all return False.
    """
    hanzi, words, kana, ghosts = _script_stats(text)
    if kana or ghosts >= max(2, hanzi * 0.1):
        return False
    return hanzi >= 4 and hanzi >= 3 * words


def is_japanese_text(text):
    """From the Chinese engine's output: the content looks Japanese.

    The Chinese engine reads some kana as kana and misreads the rest as radicals ("ghosts"); Japanese is kana-heavy, so a clear excess of kana + ghosts means Japanese. Kanji-only Japanese (e.g. 「設定」 in a menu) is indistinguishable and treated as Chinese.
    """
    hanzi, words, kana, ghosts = _script_stats(text)
    if kana >= 2:
        return True
    if words >= 3 and words > hanzi:  # mostly English: isolated single letters (a, I) are not misread kana
        return False
    odd = kana + ghosts
    return odd >= 3 and odd >= 0.15 * max(1, hanzi + odd)


@dataclass
class Inspection:
    chinese: bool
    japanese: bool
    lines: list  # lines from the Chinese engine (positions reliable; text unreliable for Japanese)


def inspect(img):
    """Look at the selection with the Chinese engine: Chinese or Japanese? Also returns line positions."""
    engine = _engine_for("zh")
    if engine is None:
        return Inspection(False, False, [])
    result, factor = _run(engine, img)
    text = " ".join(line.text for line in result.lines)
    return Inspection(is_chinese_text(text), is_japanese_text(text), _lines_from(result, factor, img))


def looks_chinese(img):
    """Is the selection already Chinese (Chinese engine)?"""
    return inspect(img).chinese


def is_foreign(insp, en_lines):
    """Neither English nor Chinese (likely Japanese, Korean, ...): the English engine finds almost nothing, the Chinese engine finds a lot that doesn't look Chinese.
    The OCR text is unreliable then; only positions are used and the model reads the screenshot.
    """
    if insp is None or insp.chinese:
        return False
    if insp.japanese:
        return True
    en_chars = sum(len(l.text.replace(" ", "")) for l in en_lines)
    zh_chars = sum(len(l.text.replace(" ", "")) for l in insp.lines)
    return zh_chars >= 8 and en_chars < 0.4 * zh_chars


def is_english(lines):
    """The English engine's output looks like plain English: at least two words and almost all letters ASCII (French or German accents make it return False)."""
    text = " ".join(l.text for l in lines)
    letters = [c for c in text if c.isalpha()]
    return len(re.findall(r"[A-Za-z]{2,}", text)) >= 2 and sum(c.isascii() for c in letters) >= 0.97 * max(1, len(letters))
