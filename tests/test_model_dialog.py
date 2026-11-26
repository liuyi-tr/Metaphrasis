"""Typing a model's name by hand: the entry at the bottom of the Model menu opens a small box instead of the config
file. Driven in-process on a throwaway config: no tray icon, no network, no real keys; windows stay off-screen.
usage: test_model_dialog.py [app dir]"""
import os, sys, tempfile
from pathlib import Path
from _app import APP
import i18n, settings

tmp = Path(tempfile.mkdtemp())
settings.CONFIG_PATH = tmp / "config.ini"
CLAUDE, DEEP = "sk-ant-TESTclaude0000000000", "sk-TESTdeepseek00000000"
settings.CONFIG_PATH.write_text(settings.default_config("zh").replace("api_key =", "api_key = " + CLAUDE, 1).replace("ui_language = auto", "ui_language = 简体中文"), encoding="utf-8")
i18n.set_language("zh")

# ---------- 1) is a typed name usable? ----------
ok = settings.model_name_problem
assert ok("deepseek", "deepseek-chat") is None and ok("ollama", "qwen2.5:14b") is None and ok("custom", "org/model-name_v2.1") is None
assert ok("claude", "claude-fable-5-2") is None
assert "还没有填写" in ok("deepseek", "") and "还没有填写" in ok("claude", "   ")
assert "空格" in ok("deepseek", "deepseek chat") and "空格" in ok("qwen", "通义千问")           # a pasted description instead of a name
assert "claude-" in ok("claude", "sonnet-5-5") and "claude-" in ok("claude", "gpt-6")           # Claude's own names all start with claude-
print("1 OK names: empty, with spaces or Chinese characters, or not a Claude name for Claude, are caught before saving")

import fanyi, translator
app = fanyi.App()
app.labels = {"capture": "Alt+~", "toggle": "Ctrl+Alt+Y", "convert": "Alt+1"}
toasts, errors = [], []
app.toast = lambda text, *a, **k: toasts.append(text)
app.show_error = lambda message, *a, **k: errors.append(message)
app.focus = lambda win: None
app.place_beside = lambda win, box, gap=12: win.geometry("+-4000+-4000")
translator.list_models = lambda cfg, limit=40: ["deepseek-chat", "deepseek-reasoner"]


def pump():
    import threading, time
    for _ in range(100):
        while not app.events.empty():
            app._handle(app.events.get_nowait())
        if not any(t.name == "models" for t in threading.enumerate()) and app.events.empty():
            return
        time.sleep(0.02)


def model_menu():
    top = {it.text: it for it in app._build_menu().items if str(it) != "- - - -"}
    return [it.text for it in top["模型"].submenu.items if str(it) != "- - - -"]


# ---------- 2) the menu entry ----------
assert model_menu()[-1] == "手动填写模型名…" and len(model_menu()) == 6, model_menu()            # Claude: its five, then the entry
app._use_company("deepseek", DEEP); pump()
assert model_menu() == ["deepseek-chat", "deepseek-reasoner", "刷新模型列表", "手动填写模型名…"], model_menu()
app.models["deepseek"] = "failed"                                                              # the list could not be fetched
assert model_menu() == ["获取模型列表失败，点这里重试", "手动填写模型名…"], model_menu()
for key in ("手动填写模型名…", "手动填写模型名 · {name}", "还没有填写模型名。", "模型名里有空格或中文字符，请照文档里的写法填。",
            "Claude 的模型名都以 claude- 开头，例如 claude-sonnet-5-5。", "模型名的写法要和这家公司文档里的完全一样，\n大小写和横线都不能错。"):
    assert set(i18n.STRINGS.get(key, {})) == {"zh-tw", "en", "ja", "ko"}, key
assert "手动填写模型名（打开配置文件）" not in i18n.STRINGS
print("2 OK menu: the Model menu of every company ends with the entry, also when the list could not be fetched")

# ---------- 3) the box ----------
toasts.clear()
app._tray_action("type_model")
d = app.model_dialog
assert d.alive and d.model.get() == "" and "DeepSeek" in d.title.cget("text"), (d.model.get(), d.title.cget("text"))   # no model chosen yet: empty
d.save()
assert d.alive and "还没有填写" in d.error.cget("text") and not toasts                           # nothing saved, the box says why
d.model.set("deepseek chat v3")
d.save()
assert d.alive and "空格" in d.error.cget("text")
d.model.set("  deepseek-v4-pro  ")
d.save()
assert not d.alive and app.cfg.model == "deepseek-v4-pro" and settings.load_config().model == "deepseek-v4-pro"
assert toasts == ["模型：deepseek-v4-pro"] and not errors, (toasts, errors)
assert model_menu()[0] == "deepseek-v4-pro"                                                    # the typed name is in the menu now (the failed list aside)
app._tray_action("type_model")
first = app.model_dialog
assert first.model.get() == "deepseek-v4-pro"                                                  # opens with the name in use
app._tray_action("type_model")
assert not first.alive and app.model_dialog.alive and app.model_dialog is not first            # one box at a time
app.model_dialog.close()
assert settings.load_config().model == "deepseek-v4-pro"                                       # closing changes nothing
# Claude: its own check
app._use_company("claude"); pump()
toasts.clear()
app._tray_action("type_model")
d = app.model_dialog
assert d.model.get() == "claude-sonnet-5-5" and "Claude" in d.title.cget("text")
d.model.set("gpt-6")
d.save()
assert d.alive and "claude-" in d.error.cget("text") and app.cfg.model == "claude-sonnet-5-5"
d.model.set("claude-opus-6")
d.save()
assert not d.alive and app.cfg.model == "claude-opus-6" and toasts == ["模型：Claude Opus 6"] or toasts == ["模型：claude-opus-6"], toasts
assert model_menu()[-2] in ("Claude Opus 6", "claude-opus-6") and len(model_menu()) == 7, model_menu()   # listed after the five, ticked
print("3 OK box: opens with the model in use, refuses a bad name and says why, saves a good one to config.ini; one box at a time")
print("ALL OK")
sys.stdout.flush()
os._exit(0)
