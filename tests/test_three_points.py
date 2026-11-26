"""Three small points the pre-release review named and that were first left as they were:
  1  an effort line left over from before Grok and Hunyuan had entries of their own is not sent after the upgrade
  2  the note above a saved company's key switches language with the other comments
  3  the hotkey for converting what was typed can be switched off (convert = off), and says how when it cannot be had
  4  the wording of the comments changed for that: the wording published before is still recognised
Config files of earlier versions are made by those versions' own code (kept next to this file). No API, no cost.
usage: test_three_points.py [app dir]"""
import importlib.util, os, sys, tempfile, threading, time, types
from pathlib import Path
HERE = Path(__file__).resolve().parent
from _app import APP
import i18n, settings

tmp = Path(tempfile.mkdtemp())
settings.CONFIG_PATH = tmp / "config.ini"
i18n.set_language("zh")


def earlier(name):
    """The settings module of an earlier version, to write config files exactly as that version did."""
    spec = importlib.util.spec_from_file_location(name, HERE / "data" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v103, before = earlier("settings_103"), earlier("settings_before")   # 1.0.3 as published; 1.1.0 as first pushed
assert v103.VERSION == "1.0.3" and "convert" not in v103.default_config("zh") and "grok" not in [c[0] for c in v103.COMPANIES]


def write(text, newline="\n", bom=False):
    settings.CONFIG_PATH.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.replace("\n", newline).encode("utf-8"))


def file():
    return settings.CONFIG_PATH.read_bytes().decode("utf-8-sig")


def by_hand(text, url, model, key="sk-TEST0000"):
    """A 1.0.x config edited by hand for another endpoint, as the README of the time said to: provider, address, model
    and key are changed, the effort line of the template (low, meant for Claude) is left standing."""
    for old, new in (("provider = claude", "provider = openai"), ("api_key =", "api_key = " + key),
                     ("model = claude-sonnet-5-5", "model = " + model), ("base_url =", "base_url = " + url)):
        assert text.count(old + "\n") == 1, old
        text = text.replace(old + "\n", new + "\n")
    assert "\neffort = low\n" in text
    return text


# ---------- 1) the effort line left over in a config that names Grok's or Hunyuan's address ----------
for lang in ("zh", "en"):
    for url, company, model in (("https://api.x.ai/v1", "grok", "grok-4"), ("https://tokenhub.tencentmaas.com/v1", "hunyuan", "hy4")):
        old = by_hand(v103.default_config(lang), url, model)
        assert v103.company_of("openai", url) == "custom"                        # what 1.0.3 took it for: no thinking setting was sent
        for newline, bom in (("\n", False), ("\r\n", True)):
            write(old, newline, bom)
            cfg = settings.load_config()
            assert cfg.company == company and cfg.effort == "" and cfg.model == model and cfg.api_key == "sk-TEST0000", (lang, url, cfg.effort)
            assert settings.thinking_body(cfg.company, cfg.effort) == {}                    # nothing is sent, as before
            now = file()
            assert now.replace("\r\n", "\n").startswith(old.replace("\neffort = low\n", "\neffort =\n")), (lang, url)   # that one line, nothing else
            assert now.count("convert_language = English") == 1 and ("\r\n" in now) == (newline == "\r\n")
            assert settings.CONFIG_PATH.read_bytes().startswith(b"\xef\xbb\xbf") == bom
            raw = settings.CONFIG_PATH.read_bytes()
            assert settings.load_config().effort == "" and settings.CONFIG_PATH.read_bytes() == raw   # done once
            settings.set_api_value("effort", "low")                              # the user's own choice, from the tray menu
            assert settings.load_config().effort == "low" and settings.load_config().effort == "low"   # ... stays
            assert settings.thinking_body(company, "low")
# everything else keeps its effort line through the upgrade: companies 1.0.3 knew by name, Claude, any other address
for url, company, model, effort in (("https://api.deepseek.com/v1", "deepseek", "deepseek-chat", "low"),
                                    ("https://example.com/v1", "custom", "some-model", "")):
    old = by_hand(v103.default_config("zh"), url, model)
    write(old)
    cfg = settings.load_config()
    assert cfg.company == company and cfg.effort == effort and "\neffort = low\n" in file(), (url, cfg.effort)
write(v103.default_config("zh").replace("api_key =\n", "api_key = sk-ant-TEST0000\n"))
assert settings.load_config().effort == "low" and "\neffort = low\n" in file()
# a config of this version (it has the new options already) is never touched: there the line is the user's choice
write(by_hand(settings.default_config("zh"), "https://api.x.ai/v1", "grok-4"))
raw = settings.CONFIG_PATH.read_bytes()
assert settings.load_config().effort == "low" and settings.CONFIG_PATH.read_bytes() == raw
print("1 OK upgrade: an effort line that 1.0.x ignored for Grok's and Hunyuan's addresses is emptied once, so nothing new is sent; a level chosen afterwards stays")

# ---------- 2) the note above a saved company's settings follows the comment language ----------
ZH_NOTE, EN_NOTE = settings._SAVED_NOTE["zh"], settings._SAVED_NOTE["en"]
write(settings.default_config("zh").replace("api_key =\n", "api_key = sk-ant-TEST0000\n", 1))
settings.switch_company("deepseek", api_key="sk-TESTdeepseek")
settings.switch_company("kimi", api_key="sk-TESTkimi")
text = file()
assert text.count(ZH_NOTE) == 2 and EN_NOTE not in text and "[saved.claude]" in text and "[saved.deepseek]" in text
changed = settings.localize_config_comments("en")
text = file()
assert text.count(EN_NOTE) == 2 and ZH_NOTE not in text and changed, text[:200]
english = {l.rstrip() for l in settings.default_config("en").splitlines() if l.startswith(";")} | {EN_NOTE}
assert not [l for l in text.splitlines() if l.startswith(";") and l.rstrip() not in english], "a comment that is not the English one is left"
cfg = settings.load_config()
assert cfg.company == "kimi" and cfg.api_key == "sk-TESTkimi" and settings.saved_companies() == {"claude", "deepseek"}
settings.localize_config_comments("zh")
text = file()
assert text.count(ZH_NOTE) == 2 and EN_NOTE not in text
settings.switch_company("claude")                                         # and switching back still finds what was put aside
assert settings.load_config().api_key == "sk-ant-TEST0000"
write(file().replace(ZH_NOTE, ZH_NOTE + "（我自己加的话）"))                 # a note the user has reworded is theirs: left alone
settings.localize_config_comments("en")
assert "（我自己加的话）" in file()
print("2 OK comments: the line above a saved company's key is switched with the rest, both ways; one the user reworded is left alone")

# ---------- 3) convert = off ----------
def hotkey(line):
    text = settings.default_config("zh").replace("api_key =\n", "api_key = sk-ant-TEST0000\n", 1).replace("ui_language = auto", "ui_language = 简体中文")
    assert text.count("convert = Alt+1\n") == 1
    write(text.replace("convert = Alt+1\n", line))
    return settings.load_config().convert_hotkey


for value in ("off", "OFF", "Off", "none", "关", "关闭", "關閉", "no", "false", "0"):
    assert hotkey(f"convert = {value}\n") == "", value                   # "" = switched off
assert hotkey("") == "Alt+1" and hotkey("convert =\n") == "Alt+1" and hotkey("convert = Ctrl+Shift+F9\n") == "Ctrl+Shift+F9"
assert hotkey("convert = offer\n") == "offer"                             # (not a word for "off": reported as a key that is not known)
import fanyi, winapi

started = []


class FakeHotkeys:
    def __init__(self, bindings, events):
        self.bindings = bindings
        started.append(self)

    def start(self):
        pass

    def set_esc(self, enabled):
        pass


winapi.HotkeyThread = FakeHotkeys
errors = []


def app_with(line):
    hotkey(line)
    app = fanyi.App()
    app.show_error = lambda message, *a, **k: errors.append(message)
    app._start_hotkeys()
    app.root.update()
    return app


app = app_with("convert = off\n")
assert set(app.hotkeys.bindings) == {"capture", "toggle"} and app.labels == {"capture": "Alt+~", "toggle": "Ctrl+Alt+Y", "convert": ""}, (app.hotkeys.bindings, app.labels)
for code, want in (("zh", "快捷键：已关闭"), ("en", "Hotkey: off")):
    i18n.set_language(code)
    top = {it.text: it for it in app._build_menu().items if str(it) != "- - - -"}
    assert [it.text for it in top[i18n.t("输入框转换成")].submenu.items][-1] == want
i18n.set_language("zh")
app._handle(("hotkey", "convert"))                                        # (cannot come, but if it did: nothing)
assert not app.converting and not errors
app.root.destroy()
app = app_with("convert = Alt+1\n")
assert set(app.hotkeys.bindings) == {"capture", "toggle", "convert"} and app.labels["convert"] == "Alt+1"
top = {it.text: it for it in app._build_menu().items if str(it) != "- - - -"}
assert [it.text for it in top["输入框转换成"].submenu.items][-1] == "快捷键：Alt+1"
# another program has the key: the message says what to write, and that it can be switched off
app._handle(("hotkey_failed", "Alt+1", "convert"))
assert "convert = " in errors[-1] and "off" in errors[-1] and "Alt+1" in errors[-1] and "[hotkey]" in errors[-1], errors[-1]
app._handle(("hotkey_failed", "Alt+~", "capture"))
assert "Alt+~" in errors[-1] and "convert" not in errors[-1]
app._handle(("hotkey_failed", "Alt+~"))                                    # (the event as it used to be)
assert "Alt+~" in errors[-1] and "convert" not in errors[-1] and len(errors) == 3
for code in i18n.CODES:                                                    # the new texts exist in all five languages
    i18n.set_language(code)
    app._handle(("hotkey_failed", "Alt+1", "convert"))
    assert "convert = off" in errors[-1], (code, errors[-1])
    assert code == "zh" or errors[-1] != errors[3], code
i18n.set_language("zh")
app.root.destroy()
# the real thread says which hotkey it could not have: two entries with the same keys, the second cannot be registered
import importlib, queue
importlib.reload(winapi)
events = queue.Queue()
mods, vk = winapi.parse_hotkey("Ctrl+Alt+Shift+Win+F23")                   # a combination nobody uses
thread = winapi.HotkeyThread({"toggle": ("Ctrl+Alt+Shift+Win+F23", mods, vk), "convert": ("Ctrl+Alt+Shift+Win+F23", mods, vk)}, events)
thread.start()
try:
    assert events.get(timeout=5) == ("hotkey_failed", "Ctrl+Alt+Shift+Win+F23", "convert")
finally:
    thread.stop()
    thread.join(3)
# the whole of it with the real thread: nothing is registered for the conversion, and nothing fails
u = winapi.user32


def can_be_had(text):
    """Is this key combination free for a program to register right now?"""
    m, k = winapi.parse_hotkey(text)
    if u.RegisterHotKey(None, 91, m | 0x4000, k):
        u.UnregisterHotKey(None, 91)
        return True
    return False


for line, taken in (("convert = off\n", False), ("convert = Ctrl+Alt+Shift+Win+F22\n", True)):
    hotkey(line)
    write(file().replace("capture = Alt+~", "capture = Ctrl+Alt+Shift+Win+F20").replace("toggle_mode = Ctrl+Alt+Y", "toggle_mode = Ctrl+Alt+Shift+Win+F21"))
    app = fanyi.App()
    del errors[:]
    app.show_error = lambda message, *a, **k: errors.append(message)
    assert can_be_had("Ctrl+Alt+Shift+Win+F22")
    app._start_hotkeys()
    try:
        # until the thread has registered its keys (not found out by trying the keys ourselves meanwhile: a try
        # that falls on the very moment the thread registers one takes that key away from it)
        assert app.hotkeys._ready.wait(5)
        time.sleep(0.2)
        while not app.events.empty():
            app._handle(app.events.get_nowait())
        assert not can_be_had("Ctrl+Alt+Shift+Win+F20") and not can_be_had("Ctrl+Alt+Shift+Win+F21"), "the other two hotkeys are not registered"
        assert can_be_had("Ctrl+Alt+Shift+Win+F22") != taken and not errors, (line, errors)
        assert ("convert" in app.hotkeys.bindings) == taken and app.labels["convert"] == ("Ctrl+Alt+Shift+Win+F22" if taken else "")
    finally:
        app.hotkeys.stop()
        app.hotkeys.join(3)
    assert can_be_had("Ctrl+Alt+Shift+Win+F20") and can_be_had("Ctrl+Alt+Shift+Win+F22")   # all given back
    app.root.destroy()
print("3 OK hotkey: convert = off leaves Alt+1 to the other programs and the tray says so; when the key cannot be had, the message says what to write")

# ---------- 4) the comments reworded for this are still recognised in their earlier wording ----------
def comment_lines(text):
    return [l.rstrip() for l in text.splitlines() if l.lstrip().startswith(";")]


for old, label in ((before, "1.1.0 as first pushed"), (v103, "1.0.3")):
    for src, dst in (("zh", "en"), ("en", "zh")):
        write(old.default_config(src))
        assert settings.comment_language(file()) == src, (label, src)
        settings.localize_config_comments(dst)
        got, want = settings._comment_blocks(file()), settings._comment_blocks(settings.default_config(dst))
        for key, block in got.items():
            assert block == want[key], (label, src, dst, key, block)
        write(old.default_config(src))                                    # and a file in the earlier wording is loaded as it is
        cfg = settings.load_config()
        assert cfg.convert_hotkey == "Alt+1" and settings.comment_language(file()) == src
        if old is before:
            assert file() == old.default_config(src), (label, src)        # nothing to add, nothing changed
for lang, word in (("zh", "convert = off"), ("en", "convert = off")):
    text = settings.default_config(lang)
    assert text.count(word) == 2, (lang, text.count(word))                # said at the hotkey, and where upgraders get to read it
    assert word in dict(settings._option_blocks(lang))["convert_language"]
print("4 OK comments: files in the wording of 1.0.3 and of the first 1.1.0 push switch language completely, and load unchanged")
print("ALL OK")
sys.stdout.flush()
os._exit(0)
