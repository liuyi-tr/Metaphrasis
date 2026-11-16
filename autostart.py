"""Start with Windows: a shortcut in the current user's Startup folder (no admin rights needed)."""
import os
import subprocess
import sys
from pathlib import Path

from settings import APP_DIR, ICON_PATH

STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
LINK_PATH = STARTUP_DIR / "截图翻译.lnk"
CREATE_NO_WINDOW = 0x08000000

_PS_SCRIPT = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:FANYI_LNK);"
    "$s.TargetPath = $env:FANYI_TARGET;"
    "$s.Arguments = '\"' + $env:FANYI_SCRIPT + '\"';"
    "$s.WorkingDirectory = $env:FANYI_DIR;"
    "if (Test-Path $env:FANYI_ICON) { $s.IconLocation = $env:FANYI_ICON };"
    "$s.Description = $env:FANYI_DESC;"
    "$s.Save()"
)


def is_enabled():
    return LINK_PATH.exists()


def enable():
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    env = dict(os.environ,
               FANYI_LNK=str(LINK_PATH), FANYI_TARGET=str(pythonw),
               FANYI_SCRIPT=str(APP_DIR / "guard.pyw"), FANYI_DIR=str(APP_DIR),  # launched through the watchdog so it comes back if killed
               FANYI_ICON=str(ICON_PATH), FANYI_DESC="截图翻译")
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-Command", _PS_SCRIPT],
                   env=env, check=True, timeout=30, creationflags=CREATE_NO_WINDOW,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def disable():
    LINK_PATH.unlink(missing_ok=True)
