"""Watchdog: starts fanyi.py and, if it exits other than via the tray's Quit (e.g. killed by Task Manager or a cleaner), restarts it after 3 s.

start.bat, the startup entry and the desktop shortcut point here. No admin rights, no scheduled task.
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _pythonw():
    """Interpreter for the app: the local .venv, else the bundled python, else whatever runs this watchdog."""
    for cand in (HERE / ".venv" / "Scripts" / "pythonw.exe", HERE / "python" / "pythonw.exe"):
        if cand.exists():
            return cand
    return Path(sys.executable).with_name("pythonw.exe")


PY = _pythonw()
NO_WINDOW = 0x08000000


def main():
    first = True
    restarts = []  # restart timestamps within the last hour, to avoid endless restart loops
    while True:
        args = [str(PY), str(HERE / "fanyi.py")] + ([] if first else ["--if-not-running"])
        try:
            p = subprocess.Popen(args, cwd=str(HERE), creationflags=NO_WINDOW)
            rc = p.wait()
        except OSError:
            return
        if rc == 0:  # normal exit: tray Quit, already running, or a config error at startup (shown in a dialog)
            return
        now = time.time()
        restarts = [t for t in restarts if now - t < 3600] + [now]
        if len(restarts) > 20:  # crashed 20 times within an hour: give up
            return
        first = False
        time.sleep(3)


if __name__ == "__main__":
    main()
