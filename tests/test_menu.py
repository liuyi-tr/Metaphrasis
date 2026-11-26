"""The tray submenus for company / model / effort and the key dialog, driven in-process on a throwaway config.
No tray icon, no hotkeys, no network (the model-list request is replaced by a fake), no real keys. Windows it opens
are kept off-screen and never take the keyboard focus.
usage: test_menu.py [app dir] [--shot out.png]"""
import sys, tempfile
from pathlib import Path
from _app import APP
import i18n, settings

tmp = Path(tempfile.mkdtemp())
settings.CONFIG_PATH = tmp / "config.ini"
CLAUDE, DEEP = "sk-ant-TESTclaude0000000000", "sk-TESTdeepseek00000000"
settings.CONFIG_PATH.write_text(settings.default_config("zh").replace("api_key =", "api_key = " + CLAUDE, 1).replace("ui_language = auto", "ui_language = 简体中文"), encoding="utf-8")
import fanyi, translator

app = fanyi.App()
app.labels = {"capture": "Alt+~", "toggle": "Ctrl+Alt+Y", "convert": "Alt+1"}  # the hotkey thread is not started here
toasts, errors = [], []
app.toast = lambda text, *a, **k: toasts.append(text)
app.show_error = lambda message, *a, **k: errors.append(message)
app.focus = lambda win: None                                    # never take the user's keyboard
app.place_beside = lambda win, box, gap=12: win.geometry("+-4000+-4000")  # and stay out of sight
calls = []
translator.list_models = lambda cfg, limit=40: (calls.append((cfg.company, cfg.api_key)), ["deepseek-chat", "deepseek-reasoner"])[1]


def pump():
    """run what the worker threads queued for the main loop"""
    import time
    for _ in range(100):
        while not app.events.empty():
            app._handle(app.events.get_nowait())
        if not any(t.name == "models" for t in __import__("threading").enumerate()):
            if app.events.empty():
                return
        time.sleep(0.02)


def menu():
    out = {}
    for it in app._build_menu().items:
        out[it.text] = it
    return out


def sub(it):
    return [(x.text, bool(x.checked), x.enabled) for x in it.submenu.items if x.text != "- - - -"]


def cfg():
    return settings.load_config()


# 1 ---- Claude with a key: everything is there and enabled
m = menu()
assert ["接口公司", "模型", "思考强度"] == [k for k in m if k in ("接口公司", "模型", "思考强度")], list(m)
assert m["模型"].enabled and m["思考强度"].enabled
companies = sub(m["接口公司"])
assert [c[0] for c in companies] == ["Claude", "ChatGPT …", "DeepSeek …", "通义千问 (Qwen) …", "Kimi …", "智谱 (GLM) …", "腾讯混元 (Hunyuan) …", "Gemini …", "Grok …", "Ollama", "自定义接口 …", "更换 API Key…"], companies
assert [c[0] for c in companies if c[1]] == ["Claude"]
models = sub(m["模型"])
assert [x[0] for x in models] == ["Claude Sonnet 5.5", "Claude Haiku 5.5", "Claude Opus 5.5", "Claude Fable 5.2", "Claude Fable 5.1", "手动填写模型名…"], models
assert [x[0] for x in models if x[1]] == ["Claude Sonnet 5.5"]
efforts = sub(m["思考强度"])
assert [x[0] for x in efforts] == ["低", "中", "高", "很高", "最高"] and [x[0] for x in efforts if x[1]] == ["低"]
print("1 OK menu with a Claude key: 9 companies, 5 models, 5 effort levels")

# 2 ---- model and effort picked from the menu land in config.ini
app._tray_action("model:claude-fable-5-2"); app._tray_action("effort:medium")
assert (cfg().model, cfg().effort) == ("claude-fable-5-2", "medium") and toasts[-2:] == ["模型：Claude Fable 5.2", "思考强度：中"], toasts
m = menu()
assert [x[0] for x in sub(m["模型"]) if x[1]] == ["Claude Fable 5.2"] and [x[0] for x in sub(m["思考强度"]) if x[1]] == ["中"]
print("2 OK picking a model and an effort level writes config.ini and moves the marks")

# 3 ---- a company without a key: the dialog comes first, nothing changes until a key is saved
app._tray_action("company:deepseek")
d = app.key_dialog
assert d and d.alive and cfg().company == "claude", "must not switch before the key is saved"
assert d.key_entry.cget("show") == "•", "the key must be masked by default"
d.save()
assert d.alive and d.error.cget("text") == "还没有粘贴 API Key。"
d.key.set("sk bad key"); d.save()
assert d.alive and "空格" in d.error.cget("text")
d.close()
assert cfg().company == "claude" and cfg().api_key == CLAUDE, "cancelling leaves everything as it was"
app._tray_action("company:deepseek")
d = app.key_dialog
if "--shot" in sys.argv:
    shot_dialog = d
d.key.set("  " + DEEP + "\n"); d.show.set(True)
d.save()
assert not d.alive
pump()
c = cfg()
assert (c.company, c.api_key, c.model, c.base_url) == ("deepseek", DEEP, "", "https://api.deepseek.com/v1"), c
assert calls == [("deepseek", DEEP)], calls  # the model list was asked of DeepSeek with DeepSeek's key, nobody else's
assert "接口公司：DeepSeek" in toasts and toasts[-1] == "请在托盘菜单的“模型”里选一个模型", toasts[-3:]
m = menu()
assert m["模型"].enabled and m["思考强度"].enabled
assert sub(m["思考强度"]) == [("默认", True, True), ("关闭思考", False, True), ("低", False, True), ("高", False, True), ("最高", False, True)], sub(m["思考强度"])
app._tray_action("effort:max")
assert cfg().effort == "max" and toasts[-1] == "思考强度：最高" and [x[0] for x in sub(menu()["思考强度"]) if x[1]] == ["最高"]
assert [x[0] for x in sub(m["模型"])] == ["deepseek-chat", "deepseek-reasoner", "刷新模型列表", "手动填写模型名…"], sub(m["模型"])
assert [c[0] for c in sub(m["接口公司"]) if c[1]] == ["DeepSeek"] and "Claude" in [c[0] for c in sub(m["接口公司"])]
print("3 OK no key -> dialog (masked, validated); cancel changes nothing; saving switches and loads that company's models")

# 4 ---- translating before a model is chosen is refused with a hint
assert app._checked_config() is None and "还没有选择模型" in errors[-1], errors
app._tray_action("model:deepseek-chat")
assert cfg().model == "deepseek-chat"
print("4 OK no model chosen yet -> clear message; choosing one fixes it")

# 5 ---- back to Claude and to DeepSeek again: no dialog, each company gets its own key and model back
app.key_dialog = None
app._tray_action("company:claude")
c = cfg()
assert (c.company, c.api_key, c.model, c.effort) == ("claude", CLAUDE, "claude-fable-5-2", "medium") and app.key_dialog is None, c
app._tray_action("company:deepseek"); pump()
c = cfg()
assert (c.company, c.api_key, c.model) == ("deepseek", DEEP, "deepseek-chat") and app.key_dialog is None, c
text = settings.read_text(settings.CONFIG_PATH)
assert text.count(CLAUDE) == 1 and text.count(DEEP) == 1
print("5 OK switching between companies with keys on file needs no dialog and never mixes keys")

# 6 ---- custom endpoint: address + key; change key of the company in use
app._tray_action("company:custom")
d = app.key_dialog
d.key.set("sk-TESTcustom"); d.url.set("http://example.com/v1"); d.save()
assert d.alive and "http://" in d.error.cget("text")
d.url.set(""); d.save()
assert d.alive and d.error.cget("text") == "还没有填写接口地址。"
d.url.set("https://llm.example.com/v1/"); d.save(); pump()
c = cfg()
assert (c.company, c.api_key, c.base_url) == ("custom", "sk-TESTcustom", "https://llm.example.com/v1"), c
app._tray_action("change_key")
d = app.key_dialog
assert d.url.get() == "https://llm.example.com/v1"
d.key.set("sk-TESTcustom2"); d.save(); pump()
assert not menu()["思考强度"].enabled, "an endpoint we know nothing about gets no thinking setting"
assert cfg().api_key == "sk-TESTcustom2" and toasts[-2] == "已保存 API Key" or "已保存 API Key" in toasts[-3:], toasts[-3:]
print("6 OK custom endpoint asks for the address too; plain http to the internet is refused; changing a key works")

# 7 ---- no key at all: model and effort are greyed out
app._tray_action("company:claude")
settings.set_api_value("api_key", "")
app._reload()
m = menu()
assert not m["模型"].enabled and not m["思考强度"].enabled and m["接口公司"].enabled
assert sub(m["接口公司"])[0][0] == "Claude …"
# a fresh install: Claude is already selected and only the key is missing -> picking it asks for the key
app.key_dialog = None
app._tray_action("company:claude")
d = app.key_dialog
assert d and d.alive, "picking the current company without a key must ask for the key"
d.key.set(CLAUDE); d.save(); pump()
assert cfg().api_key == CLAUDE and menu()["模型"].enabled and toasts[-1] == "已保存 API Key", toasts[-2:]
settings.set_api_value("api_key", ""); app._reload()
print("7 OK without a key the model and effort menus are greyed out; picking the current company asks for its key")

# 8 ---- other interface languages
for code, names in (("en", ("API provider", "Model", "Thinking effort")), ("ja", ("API プロバイダー", "モデル", "思考の深さ")),
                    ("ko", ("API 제공업체", "모델", "사고 강도")), ("zh-tw", ("介面公司", "模型", "思考強度"))):
    i18n.set_language(code)
    m = menu()
    assert all(n in m for n in names), (code, list(m))
i18n.set_language("en")
settings.set_api_value("api_key", CLAUDE); app._reload()
m = menu()
assert sub(m["Model"])[0][0] == "Claude Sonnet 5.5" and sub(m["Thinking effort"])[0][0] == "Low"
assert sub(m["API provider"])[-2][0] == "Custom endpoint" and sub(m["API provider"])[-1][0] == "Change API key…"
i18n.set_language("zh")
print("8 OK menu texts in all five languages")

if "--shot" in sys.argv:
    # picture of the dialog for a visual check, drawn by the window itself while it stays off-screen
    import ctypes
    from PIL import Image
    out = sys.argv[sys.argv.index("--shot") + 1]
    app._tray_action("company:kimi")
    d = app.key_dialog
    d.key.set("sk-TESTkimi0000000000000000")
    d.error.configure(text="")
    d.win.update()
    hwnd = int(d.win.wm_frame(), 16)
    w, h = d.win.winfo_width(), d.win.winfo_height()
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hdc = user32.GetWindowDC(hwnd); mem = gdi32.CreateCompatibleDC(hdc); bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                    ("a", ctypes.c_int32), ("b", ctypes.c_int32), ("c", ctypes.c_uint32), ("d", ctypes.c_uint32)]
    bmi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).save(out)
    d.close()
    print("dialog picture:", out, (w, h))
app.root.destroy()
print("ALL OK")
