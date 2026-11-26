"""The program's name (Metaphrasis everywhere it names itself) and its icon (a pen that has written >_, black on
white, drawn in code). Nothing outside this process is touched: a temporary config, a temporary Startup folder.
usage: test_brand.py [app dir]"""
import os, sys, tempfile
from pathlib import Path
from _app import APP
import i18n, settings

tmp = Path(tempfile.mkdtemp())
settings.CONFIG_PATH = tmp / "config.ini"
settings.HISTORY_PATH = tmp / "history.txt"
i18n.set_language("zh")

# ---------- 1) the icon ----------
import appicon
from PIL import Image

icon = appicon.draw(256)
assert icon.mode == "RGBA" and icon.size == (256, 256)
px = icon.load()
assert px[0, 0][3] == 0 and px[255, 255][3] == 0 and px[255, 0][3] == 0                       # round corners: nothing there
assert px[128, 20][:3] == (255, 255, 255) and px[128, 20][3] == 255                             # a white ground
solid = [px[x, y] for x in range(256) for y in range(256) if px[x, y][3] == 255]
dark = [p for p in solid if sum(p[:3]) < 150]
light = [p for p in solid if sum(p[:3]) > 700]
assert 0.08 < len(dark) / len(solid) < 0.30 and len(light) / len(solid) > 0.60, (len(dark) / len(solid), len(light) / len(solid))  # black drawing, mostly white
assert all(abs(p[0] - p[2]) < 30 for p in dark[::50])                                           # black, not blue
left = sum(1 for x in range(30, 100) for y in range(130, 230) if sum(px[x, y][:3]) < 150)       # the > it has written, bottom left
top_right = sum(1 for x in range(150, 240) for y in range(60, 150) if sum(px[x, y][:3]) < 150)  # the pen, top right
assert left > 300 and top_right > 1500, (left, top_right)
for size in (16, 24, 32, 48, 64):
    small = appicon.draw(size)
    assert small.size == (size, size) and small.mode == "RGBA"
    assert min(sum(p[:3]) for p in small.getdata() if p[3] == 255) < 200, size                  # still something dark to see at every size
assert appicon.draw(64).tobytes() == appicon.draw(64).tobytes()                                 # the same picture every time
print("1 OK icon: black on white with round corners; the pen top right, what it wrote bottom left; visible down to 16 pixels")

import fanyi
assert fanyi.make_icon(32).tobytes() == appicon.draw(32).tobytes()
path = tmp / "icon.ico"
assert fanyi.refresh_icon_file(path) is True and path.exists()                                  # written when there is none
first = path.read_bytes()
assert fanyi.refresh_icon_file(path) is False and path.read_bytes() == first                    # left alone when it is already this icon
path.write_bytes(b"an icon file from an earlier version")
assert fanyi.refresh_icon_file(path) is True and path.read_bytes() == first                     # replaced when the design has changed
sizes = Image.open(path).info.get("sizes") or set()
assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(sizes), sizes
print("2 OK icon file: written once, left alone while unchanged, replaced when an older icon is found")

# ---------- 3) the name ----------
assert settings.APP_NAME == "Metaphrasis"
names = {"Metaphrasis · 截图翻译 {hotkey}", "Metaphrasis · 需要设置 API Key", "Metaphrasis · 出错了",
         "Metaphrasis 已经在运行了。\n\n托盘图标在任务栏右下角（可能收在 ^ 里面）。"}
for key in names:
    assert set(i18n.STRINGS.get(key, {})) == {"zh-tw", "en", "ja", "ko"}, key
    assert all("Metaphrasis" in text for text in i18n.STRINGS[key].values()), key
for old in ("截图翻译", "截图翻译（{hotkey}）", "截图翻译 · 需要设置 API Key", "截图翻译 · 出错了"):
    assert old not in i18n.STRINGS, old                                                         # the old name is gone as a name
assert "截图翻译    {hotkey}" in i18n.STRINGS                                                    # ... and stays as what the first menu entry does
source = Path(fanyi.__file__).read_text(encoding="utf-8")
assert 't("截图翻译")' not in source and "截图翻译（" not in source and "截图翻译 · " not in source
settings.CONFIG_PATH.write_bytes(settings.default_config("zh").replace("api_key =", "api_key = sk-ant-TEST0000", 1)
                                 .replace("ui_language = auto", "ui_language = 简体中文").encode("utf-8"))
app = fanyi.App()
assert app.root.title() == "Metaphrasis"
for code, want in (("zh", "Metaphrasis · 截图翻译 Alt+~"), ("en", "Metaphrasis · Translate selection Alt+~")):
    i18n.set_language(code)
    assert i18n.t("Metaphrasis · 截图翻译 {hotkey}", hotkey="Alt+~") == want, i18n.t("Metaphrasis · 截图翻译 {hotkey}", hotkey="Alt+~")
i18n.set_language("zh")
print("3 OK name: Metaphrasis in the window title, the tray tooltip, error and notice titles, in every interface language")

# ---------- 4) the config file's first line, and files written by earlier versions ----------
assert settings.default_config("zh").startswith("; ================== Metaphrasis 配置文件 ==================")
assert settings.default_config("en").startswith("; ================== Metaphrasis configuration ==================")
old = settings.default_config("zh").replace("Metaphrasis 配置文件", "截图翻译 配置文件", 1)        # a file from before the name changed
assert settings.comment_language(old) == "zh"
settings.CONFIG_PATH.write_bytes(old.encode("utf-8"))
assert settings.localize_config_comments("en") > 0
text = settings.CONFIG_PATH.read_text(encoding="utf-8")
assert text.startswith("; ================== Metaphrasis configuration") and "截图翻译 配置文件" not in text   # its first line follows the language too
assert settings.localize_config_comments("zh") > 0
assert settings.CONFIG_PATH.read_text(encoding="utf-8").startswith("; ================== Metaphrasis 配置文件")
print("4 OK config: new files are headed Metaphrasis; a file with the old heading still switches comment language")

# ---------- 5) start with Windows: the shortcut carries the new name, and one made under the old name is taken over ----------
import autostart
startup = tmp / "Startup"; startup.mkdir()
autostart.STARTUP_DIR = startup
autostart.LINK_PATH = startup / autostart.LINK_PATH.name
autostart.OLD_LINK_PATH = startup / autostart.OLD_LINK_PATH.name
assert autostart.LINK_PATH.name == "Metaphrasis.lnk" and autostart.OLD_LINK_PATH.name == "截图翻译.lnk"
assert not autostart.is_enabled()
autostart.OLD_LINK_PATH.write_bytes(b"shortcut made by an earlier version")
assert autostart.is_enabled()                                                                   # still counts as switched on
autostart.enable = lambda: autostart.LINK_PATH.write_bytes(b"shortcut that starts this copy")   # (the real one asks PowerShell)
assert autostart.adopt_old_link() is True
assert autostart.LINK_PATH.read_bytes() == b"shortcut that starts this copy" and not autostart.OLD_LINK_PATH.exists()
assert autostart.adopt_old_link() is False and autostart.is_enabled()                           # nothing more to take over
autostart.OLD_LINK_PATH.write_bytes(b"x")                                                       # both there: the new one is kept as it is, the old one goes
autostart.enable = lambda: 1 / 0
assert autostart.adopt_old_link() is True and not autostart.OLD_LINK_PATH.exists()
assert autostart.LINK_PATH.read_bytes() == b"shortcut that starts this copy"
autostart.OLD_LINK_PATH.write_bytes(b"x")                                                       # switching off removes both
autostart.disable()
assert not autostart.LINK_PATH.exists() and not autostart.OLD_LINK_PATH.exists() and not autostart.is_enabled()
print("5 OK start with Windows: the shortcut is called Metaphrasis; one made under the old name is replaced by one that starts this copy, not doubled")
print("ALL OK")
sys.stdout.flush()
os._exit(0)
