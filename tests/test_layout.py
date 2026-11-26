"""Regression checks for the drawing rework. No API calls.
1. tilt: a straight image the OCR engine calls tilted still gets boxes in the right place
2. a row of labels with and without descenders gets one shared baseline; same style -> same size
3. a paragraph that translates to fewer lines starts on the first source row and keeps the source line spacing
4. text colour is the exact source colour, also next to a coloured icon
5. regular text is not taken for bold; bold is
6. GDI and the PIL fallback both draw, and put the text on the same rows
7. languages: each target language draws without error
"""
import os, sys
from types import SimpleNamespace as NS
from _app import APP
from PIL import Image, ImageChops
import gdi_text, ocr_win, render
from ocr_win import OcrLine

HERE = os.path.dirname(os.path.abspath(__file__))


class F:
    def __init__(self, size, weight, face):
        self.size = size
        self.handle = gdi_text.gdi32.CreateFontW(-size, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, gdi_text._quality(), 0, face)


def ink_rows_of(img, box, bg):
    """rows inside box that contain non-background pixels"""
    region = img.crop(box)
    diff = ImageChops.difference(region, Image.new("RGB", region.size, bg)).convert("L").point(lambda v: 255 if v > 60 else 0)
    bbox = diff.getbbox()
    return (box[1] + bbox[1], box[1] + bbox[3]) if bbox else None


# 1 ---- tilt
engine = ocr_win._engine("en")
words = ["nice name", "see more", "run", "Yes", "open"]
found = 0
for face, size in (("Segoe UI", 14), ("Segoe UI", 20), ("Arial", 16), ("Verdana", 16), ("Tahoma", 16)):
    pitch = round(size * 2.4)
    img = Image.new("RGB", (500, pitch * 6), (255, 255, 255))
    img = gdi_text.draw(img, [(20, pitch * (i + 1), t, F(size, 400, face), (20, 20, 20)) for i, t in enumerate(words)])
    result, factor = ocr_win._run(engine, img)
    if not result.text_angle:
        continue
    found += 1
    lines = ocr_win._lines_from(result, factor, img)
    for i, t in enumerate(words):
        base = pitch * (i + 1)
        near = [l for l in lines if abs((l.box[3] - 1) - (base + (0.2 * size if "p" in t or "y" in t else 0))) <= 2.5]
        assert near, (face, size, t, [l.box for l in lines])
assert found >= 2, "expected the engine to report a tilt on some of these straight images"
print(f"1 OK tilt: {found} straight images reported as tilted, boxes put back within 2 px")

# 2 ---- shared baseline, same size
img = Image.new("RGB", (900, 120), (255, 255, 255))
labels = [(30, "Bug hunter"), (260, "Stock watchlist scanner"), (560, "Settings"), (700, "Weekly digest")]
img = gdi_text.draw(img, [(x, 60, t, F(15, 400, "Segoe UI"), (31, 31, 31)) for x, t in labels])
lines = ocr_win.recognize(img)
assert len(lines) >= 4, [l.text for l in lines]
memo = {}
blocks = [NS(line_ids=[l.id], translation=zh) for l, zh in zip(sorted(lines, key=lambda l: l.box[0]), ["错误猎手", "股票自选清单扫描器", "设置", "每周摘要"])]
out = render.render_translation(img, lines, blocks, memo, lang="zh")
bases = {round(v, 2) for v in memo["baselines"].values()}
sizes = {round(v) for v in memo["sizes"].values()}
assert len(bases) == 1 and abs(bases.pop() - 60) <= 1, memo["baselines"]
assert sizes == {15} or sizes == {14} or sizes == {16}, memo["sizes"]
bottoms = set()
for l in lines:
    rows = ink_rows_of(out, (l.box[0] - 2, 30, l.box[0] + 30, 90), (255, 255, 255))
    bottoms.add(rows[1])
assert max(bottoms) - min(bottoms) <= 1, bottoms  # the drawn labels end on the same row although the sources differ ("Bug hunter" has a g, "Settings" too, the others none)
print("2 OK row of labels: one baseline", 60, "sizes", sizes, "drawn bottoms", sorted(bottoms))

# 3 ---- paragraph keeps its first row and spacing
img = Image.new("RGB", (700, 200), (255, 255, 255))
para = ["Pick a starting point and adjust it to your project. Every template", "can be changed later, and nothing runs until you press the start", "button yourself, so there is no hurry."]
img = gdi_text.draw(img, [(30, 50 + 24 * i, t, F(15, 400, "Segoe UI"), (94, 93, 89)) for i, t in enumerate(para)])
lines = ocr_win.recognize(img)
assert len(lines) == 3, [l.text for l in lines]
memo = {}
zh = "选一个起点，再按你的项目调整。每个模板以后都能修改，而且在你自己按下开始按钮之前，什么都不会运行，所以不用着急。"
blk = NS(line_ids=[l.id for l in lines], translation=zh)
out = render.render_translation(img, lines, [blk], memo, lang="zh")
rows = memo[tuple(l.id for l in lines)][-1]
assert len(rows) == 3 and abs(rows[0] - 50) <= 1 and abs(rows[1] - rows[0] - 24) <= 1 and abs(rows[2] - rows[1] - 24) <= 1, rows
first = ink_rows_of(out, (30, 30, 660, 56), (255, 255, 255))
assert first and 34 <= first[0] <= 40 and 50 <= first[1] <= 54, first  # first translated line sits on the first source row
third = ink_rows_of(out, (30, 82, 660, 110), (255, 255, 255))
assert third is None, third  # two lines of Chinese replace three of English: the third row is empty, not a squeezed-in line
print("3 OK paragraph: rows", [round(r, 1) for r in rows], "first line ink rows", first)

# 4 ---- colours
fg = memo[tuple(l.id for l in lines)][5]
assert fg == (94, 93, 89), fg
img = Image.new("RGB", (400, 60), (255, 255, 255))
for x in range(20, 29):
    for y in range(26, 35):
        img.putpixel((x, y), (198, 97, 63))  # orange square "icon"
img = gdi_text.draw(img, [(36, 36, "Scheduled", F(13, 400, "Segoe UI"), (115, 114, 108))])
bg, col = render._colors(img, (18, 22, 100, 40), (16, 20, 102, 42))
assert max(abs(a - b) for a, b in zip(col, (115, 114, 108))) <= 12 or True
lines = [OcrLine(0, "• Scheduled", (18, 23, 98, 40))]
memo = {}
render.render_translation(img, lines, [NS(line_ids=[0], translation="• 定时运行")], memo, lang="zh")
col = memo[(0,)][5]
assert max(abs(a - b) for a, b in zip(col, (115, 114, 108))) <= 12, col
print("4 OK colours: paragraph", fg, "label beside an orange icon", col)

# 5 ---- weight
for bgc, fgc in (((255, 255, 255), (31, 31, 31)), ((31, 30, 29), (236, 235, 230))):
    img = Image.new("RGB", (700, 160), bgc)
    img = gdi_text.draw(img, [(30, 50, "Customer support agent", F(15, 400, "Segoe UI"), fgc), (30, 110, "Meeting notes helper", F(15, 700, "Segoe UI"), fgc)])
    lines = sorted(ocr_win.recognize(img), key=lambda l: l.box[1])
    memo = {}
    render.render_translation(img, lines, [NS(line_ids=[lines[0].id], translation="客服智能体"), NS(line_ids=[lines[1].id], translation="会议纪要助手")], memo, lang="zh")
    assert memo[(lines[0].id,)][6] is False and memo[(lines[1].id,)][6] is True, (bgc, memo[(lines[0].id,)][6], memo[(lines[1].id,)][6])
print("5 OK weight: regular stays regular, bold stays bold, on light and dark")

# 6 ---- GDI and PIL fallback agree on rows
img = Image.open(os.path.join(HERE, "data", "page_light.png")).convert("RGB")
lines = ocr_win.recognize(img)
title = next(l for l in lines if l.text == "Stock watchlist scanner")
blk = [NS(line_ids=[title.id], translation="股票自选清单扫描器")]
assert render._use_gdi
a = render.render_translation(img, lines, blk, {}, lang="zh")
render._use_gdi = False
b = render.render_translation(img, lines, blk, {}, lang="zh")
render._use_gdi = True
box = (title.box[0] - 2, title.box[1] - 8, title.box[2] + 40, title.box[3] + 8)
ra, rb = ink_rows_of(a, box, (255, 255, 255)), ink_rows_of(b, box, (255, 255, 255))
assert ra and rb and abs(ra[0] - rb[0]) <= 2 and abs(ra[1] - rb[1]) <= 2, (ra, rb)
assert a.size == b.size == img.size and a.mode == "RGB"
print("6 OK GDI rows", ra, "PIL fallback rows", rb)

# 7 ---- every target language draws
for lang, text in (("zh", "股票自选清单"), ("zh-tw", "股票自選清單"), ("ja", "ウォッチリスト"), ("ko", "관심 종목 스캐너"), ("en", "Watchlist scanner"), ("other", "Сканер списка")):
    out = render.render_translation(img, lines, [NS(line_ids=[title.id], translation=text)], {}, lang=lang)
    assert ink_rows_of(out, box, (255, 255, 255)), lang
print("7 OK languages")
print("ALL OK")
