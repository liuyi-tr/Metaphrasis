"""日志无中文；翻译记录标签跟界面语言；译文表仍完整。"""
import glob, re, sys, tempfile
from pathlib import Path
from _app import APP
import i18n, settings
from i18n import t

cjk = re.compile(r"[\u4e00-\u9fff]")
total = 0
for f in sorted(glob.glob(str(Path(APP) / "*.py"))):
    src = open(f, encoding="utf-8").read().split("\n"); i = 0
    while i < len(src):
        l = src[i]
        if re.search(r"\blog\.(info|warning|error|exception|debug|critical)\(", l):
            stmt, j = l, i
            while stmt.count("(") > stmt.count(")") and j + 1 < len(src):
                j += 1; stmt += "\n" + src[j]
            assert not cjk.search(stmt), (f, i + 1, stmt[:120])
            total += 1; i = j
        i += 1
print("1 OK", total, "log statements, none in Chinese")

PH = re.compile(r"\{(\w+)\}")
for key, tr in i18n.STRINGS.items():
    assert set(tr) == {"zh-tw", "en", "ja", "ko"} and all(set(PH.findall(x)) == set(PH.findall(key)) for x in tr.values()), key
assert "【原文】" in i18n.STRINGS and "【译文】" in i18n.STRINGS
print("2 OK 译文表", len(i18n.STRINGS), "条")

tmp = Path(tempfile.mkdtemp()); settings.HISTORY_PATH = tmp / "h.txt"
i18n.set_language("en"); settings.append_history("m", "hello", "你好")
i18n.set_language("zh"); settings.append_history("m", "hi", "嗨")
txt = (tmp / "h.txt").read_text(encoding="utf-8")
assert "[Source]\nhello\n\n[Translation]\n你好" in txt and "【原文】\nhi\n\n【译文】\n嗨" in txt, txt
print("3 OK 翻译记录标签")
print("ALL OK")
