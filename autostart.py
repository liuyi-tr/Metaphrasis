"""Start with Windows: a shortcut in the current user's Startup folder (no admin rights needed)."""
import os
import subprocess
import sys
from pathlib import Path

from settings import APP_DIR, ICON_PATH

STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
LINK_PATH = STARTUP_DIR / "Metaphrasis.lnk"
OLD_LINK_PATH = STARTUP_DIR / "截图翻译.lnk"  # the shortcut's name before the program was called Metaphrasis
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
    return LINK_PATH.exists() or OLD_LINK_PATH.exists()


def adopt_old_link():
    """A shortcut made under the program's earlier name is replaced by one under the new name, so the program is not
    started twice. The new one is made afresh and starts this copy: the old one may point at a folder the program has
    since moved out of (a newer version unpacked somewhere else). The old one only goes once the new one is there.
    True when there was one to take over."""
    if not OLD_LINK_PATH.exists():
        return False
    if not LINK_PATH.exists():
        enable()
    if LINK_PATH.exists():
        OLD_LINK_PATH.unlink()
    return True


def enable():
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    env = dict(os.environ,
               FANYI_LNK=str(LINK_PATH), FANYI_TARGET=str(pythonw),
               FANYI_SCRIPT=str(APP_DIR / "guard.pyw"), FANYI_DIR=str(APP_DIR),  # launched through the watchdog so it comes back if killed
               FANYI_ICON=str(ICON_PATH), FANYI_DESC="Metaphrasis")
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-Command", _PS_SCRIPT],
                   env=env, check=True, timeout=30, creationflags=CREATE_NO_WINDOW,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def disable():
    LINK_PATH.unlink(missing_ok=True)
    OLD_LINK_PATH.unlink(missing_ok=True)
