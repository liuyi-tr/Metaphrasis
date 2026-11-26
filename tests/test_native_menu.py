"""Have Windows build the real tray menu from the app's menu description and count what it made.
Throwaway config, fake key, no network. A second tray icon shows for about a second."""
import ctypes, sys, tempfile, threading, time
from pathlib import Path
from _app import APP
import settings
settings.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.ini"
settings.CONFIG_PATH.write_text(settings.default_config("zh").replace("api_key =", "api_key = sk-ant-TEST", 1).replace("ui_language = auto", "ui_language = 简体中文"), encoding="utf-8")
import fanyi, pystray

app = fanyi.App()
app.labels = {"capture": "Alt+~", "toggle": "Ctrl+Alt+Y", "convert": "Alt+1"}
user32 = ctypes.windll.user32
user32.GetSubMenu.restype = ctypes.c_void_p
user32.GetSubMenu.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetMenuItemCount.argtypes = [ctypes.c_void_p]
user32.GetMenuStringW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint]
user32.GetMenuState.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
MF_BYPOSITION, MF_GRAYED, MF_DISABLED, MF_CHECKED = 0x400, 0x1, 0x2, 0x8
result = {}


def describe(hmenu):
    out = []
    for i in range(user32.GetMenuItemCount(hmenu)):
        buf = ctypes.create_unicode_buffer(200)
        user32.GetMenuStringW(hmenu, i, buf, 200, MF_BYPOSITION)
        state = user32.GetMenuState(hmenu, i, MF_BYPOSITION)
        sub = user32.GetSubMenu(hmenu, i)
        out.append((buf.value, bool(state & (MF_GRAYED | MF_DISABLED)), bool(state & MF_CHECKED), describe(sub) if sub else None))
    return out


def setup(icon):
    try:
        icon.visible = True
        icon.update_menu()
        result["with key"] = describe(icon._menu_handle[0])
        app.cfg = fanyi.dataclasses.replace(app.cfg, api_key="")
        icon.update_menu()
        result["no key"] = describe(icon._menu_handle[0])
    except Exception as e:
        result["error"] = repr(e)
    finally:
        icon.stop()


icon = pystray.Icon("fanyi-test", app.icon_image, "test", app._build_menu())
th = threading.Thread(target=icon.run, kwargs={"setup": setup}, daemon=True)
th.start(); th.join(20)
assert "error" not in result, result.get("error")
top = {x[0]: x for x in result["with key"]}
assert not top["接口公司"][1] and not top["模型"][1] and not top["思考强度"][1], "all three enabled with a key"
companies = [x for x in top["接口公司"][3] if x[0]]
assert len(companies) == 12 and [x[0] for x in companies if x[2]] == ["Claude"], companies
models = [x for x in top["模型"][3] if x[0]]
assert len(models) == 6 and models[-1][0] == "手动填写模型名…" and [x[0] for x in models if x[2]] == ["Claude Sonnet 5.5"], models
efforts = top["思考强度"][3]
assert len(efforts) == 5 and [x[0] for x in efforts if x[2]] == ["低"], efforts
top = {x[0]: x for x in result["no key"]}
assert not top["接口公司"][1] and top["模型"][1] and top["思考强度"][1], "model and effort greyed out without a key"
print("native menu, top level:", [x[0] for x in result["with key"]])
print("OK  Windows built the three submenus: 11 companies + change key, 5 models + type a name, 5 effort levels; greyed out without a key")
app.root.destroy()
