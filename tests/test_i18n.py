"""界面语言 + 托盘语言菜单的测试，不花钱：译文表完整性、配置读写、菜单构建、弹窗/提示在英文下能开、跳过提示用对语言。"""
import dataclasses, queue, re, sys, tempfile, time, tkinter as tk, types
from pathlib import Path
from _app import APP
import i18n, settings
from i18n import t

HERE = Path(__file__).parent
PH = re.compile(r"\{(\w+)\}")

# 1) 译文表：每个键 4 种语言都有，占位符一致，英文里没有汉字
for key, tr in i18n.STRINGS.items():
    assert set(tr) == {"zh-tw", "en", "ja", "ko"}, key
    for code, text in tr.items():
        assert set(PH.findall(text)) == set(PH.findall(key)), (key, code)
        assert text.strip(), (key, code)
    assert not re.search(r"[\u4e00-\u9fff]", tr["en"].replace("运行日志.txt", "")), key
assert len(i18n.STRINGS) >= 85
for code in i18n.CODES:
    i18n.set_language(code)
    assert t("退出") and t("已清空翻译缓存（{n} 条）", n=3).count("3") == 1
    assert "Alt+~" in t("截图翻译    {hotkey}", hotkey="Alt+~")
i18n.set_language("zh"); assert t("退出") == "退出"
i18n.set_language("en"); assert t("退出") == "Quit" and t("没有这个键") == "没有这个键"
assert i18n.system_language() in i18n.CODES and i18n.resolve("auto") in i18n.CODES
assert i18n.resolve("English") == "en" and i18n.resolve("日本語") == "ja" and i18n.resolve("繁體中文") == "zh-tw"
print("1 OK 译文表", len(i18n.STRINGS), "条")

# 2) 配置：新文件 ui_language = auto；set_option 只改那一行，保留注释、BOM、CRLF；缺项时补在 [options] 下
tmp = Path(tempfile.mkdtemp())
settings.CONFIG_PATH = tmp / "new.ini"; c = settings.load_config()
assert c.ui_language == "auto" and "ui_language = auto" in (tmp / "new.ini").read_text(encoding="utf-8")
body = "\ufeff[api]\r\napi_key = x\r\n; 注释\r\n[options]\r\n; 译文语言\r\ntarget_language = 中文\r\nuse_cache = true\r\n"
(tmp / "crlf.ini").write_text(body, encoding="utf-8")
settings.CONFIG_PATH = tmp / "crlf.ini"
settings.set_option("target_language", "日本語")
raw = (tmp / "crlf.ini").read_bytes(); txt = raw.decode("utf-8-sig")
assert raw.startswith(b"\xef\xbb\xbf") and "\r\n" in txt and "; 注释" in txt and "; 译文语言" in txt
assert txt.count("target_language = 日本語") == 1 and "target_language = 中文" not in txt and "use_cache = true" in txt
settings.set_option("ui_language", "English")  # 原来没有这一项：补在 [options] 下面
txt = (tmp / "crlf.ini").read_bytes().decode("utf-8-sig")
assert txt.count("ui_language = English") == 1 and txt.index("[options]") < txt.index("ui_language = English") < txt.index("target_language")
c = settings.load_config(); assert c.target_language == "日本語" and c.ui_language == "English"
settings.set_option("ui_language", "한국어"); assert settings.load_config().ui_language == "한국어"
print("2 OK 配置读写")

# 3) 托盘菜单：在 5 种界面语言下都能构建，两个语言子菜单各 5 项，勾选状态对
import fanyi
fake = types.SimpleNamespace(labels={"capture": "Alt+~", "toggle": "Ctrl+Alt+Y", "convert": "Alt+1"}, mode="overlay",
                             cfg=types.SimpleNamespace(target_language="日本語", api_key="k", provider="claude", company="claude",
                                                       model="claude-sonnet-5-5", effort="low"),
                             events=queue.Queue(), tray=None, models={})
for name in ("_act", "_has_key", "_company_items", "_model_items", "_effort_items"):  # the stub borrows the real menu builders
    setattr(fake, name, types.MethodType(getattr(fanyi.App, name), fake))
def texts(menu):
    out = []
    for it in menu.items:
        if str(it) == "- - - -" or getattr(it, "_text", None) is None and not it.text:
            continue
        out.append((it.text, it.checked, it.submenu))
    return out
for code in i18n.CODES:
    i18n.set_language(code)
    menu = fanyi.App._build_menu(fake)
    items = texts(menu)
    labels = [x[0] for x in items]
    assert labels[0].startswith(t("截图翻译    {hotkey}", hotkey="Alt+~")[:4]) and t("退出") in labels, labels
    subs = {x[0]: x[2] for x in items if x[2] is not None}
    assert t("译文语言") in subs and t("界面语言") in subs and t("显示模式") in subs, list(subs)
    tgt = [(it.text, it.checked) for it in subs[t("译文语言")].items]
    ui = [(it.text, it.checked) for it in subs[t("界面语言")].items]
    assert [x[0] for x in tgt] == [n for _, n in i18n.LANGUAGES] and [x[1] for x in tgt] == [n == "日本語" for _, n in i18n.LANGUAGES], tgt
    assert [x[1] for x in ui] == [c_ == code for c_, _ in i18n.LANGUAGES], (code, ui)
    if code == "en":
        assert "Target language" in labels and "Interface language" in labels and "Quit" in labels, labels
print("3 OK 托盘菜单")

# 4) 英文界面下开一次弹窗、错误弹窗、提示，不报错
i18n.set_language("en")
root = tk.Tk(); root.withdraw()
from PIL import Image, ImageTk
app = types.SimpleNamespace(root=root, icon_photo=ImageTk.PhotoImage(fanyi.make_icon(32)), windows=[], scale=root.winfo_fpixels("1i") / 96,
                            copy=lambda text: None, copy_image=lambda img: True, open_file=lambda p: None, focus=lambda w: None,
                            forget=lambda w: None, toast=lambda *a, **k: None, selection=None, _toast=None)
app.place_beside = lambda win, box, gap=12: (win.update_idletasks(), win.geometry("+100+100"))
app.place_in_corner = lambda win, anchor=None: (win.update_idletasks(), win.geometry("+100+400"))
p = fanyi.TextPopup(app, t("翻译结果"), "Hello world", note=""); p.finish("note-x")
assert "Copied to clipboard" in p.status.cget("text") and "note-x" in p.status.cget("text"); p.close()
e = fanyi.TextPopup(app, t("截图翻译 · 出错了"), t("截图里没有找到需要翻译的文字。"), error=True, open_config=True); e.close()
tst = fanyi.Toast(app, t("还没有翻译记录"), seconds=1); tst.close()
for _ in range(3): root.update()
root.destroy()
print("4 OK 英文界面弹窗")

# 5) 跳过提示跟着界面语言（英→英，不调 API）
settings.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.ini"  # a fresh settings file: nothing is sent on this path, so no key is needed
base = settings.load_config()
img = Image.open(HERE / "data" / "en_sample.png"); img.load()
for code, expect in (("en", "The selection is already in English"), ("ja", "すでにEnglish"), ("zh", "框选的内容本来就是English")):
    i18n.set_language(code)
    f = types.SimpleNamespace(events=queue.Queue(), jobs={})
    job = types.SimpleNamespace(id=1, crop=img, cfg=dataclasses.replace(base, target_language="English")); f.jobs[1] = job
    fanyi.App._run_job(f, job, job.cfg)
    ev = f.events.get_nowait(); assert ev[0] == "job_skipped" and expect in ev[2], ev
print("5 OK 跳过提示多语言")
print("ALL OK")
