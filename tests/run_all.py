"""Run every check suite in this folder, one after the other, and say which passed.

    python tests\\run_all.py            checks the program in the folder above this one
    python tests\\run_all.py <folder>   checks a copy of the program somewhere else

Windows only, like the program. Needs the program's own requirements installed (install.bat) and nothing else.
No API key is needed and nothing is sent anywhere: the providers are local stand-ins. Settings, history and logs are
written to temporary folders, never to the program's folder. Several suites open windows for a moment or register
global hotkeys; they give everything back when they end.

These are the working checks the program was developed against, kept as they were written: one script per round of
work, plain asserts, a line printed for every group that holds. A suite passes when it ends with exit code 0."""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
suites = sorted(HERE.glob("test_*.py"))
env = dict(os.environ, PYTHONIOENCODING="utf-8")
failed = []
started = time.monotonic()
for suite in suites:
    done = subprocess.run([sys.executable, str(suite), *sys.argv[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    ok = done.returncode == 0
    print(("ok    " if ok else "FAILED") + " " + suite.name, flush=True)
    if not ok:
        failed.append(suite.name)
        print("\n".join("       " + line for line in (done.stdout + done.stderr).strip().splitlines()[-12:]), flush=True)
print("%d of %d suites passed in %.0f s" % (len(suites) - len(failed), len(suites), time.monotonic() - started))
sys.exit(1 if failed else 0)
