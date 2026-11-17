"""Build the portable zip: the app + an embedded Python + Tk + all dependencies. Unzip, double-click start.bat, done.

Run on Windows with a full CPython install of the same minor version as PYVER (its Tk files are copied), e.g.
    python tools\\build_portable.py
Output: ..\\dist\\Metaphrasis-<version>-win64\\ next to the repository, and the zip of it.

Nothing from the build machine ends up in the zip: packages are installed without compiling, the smoke test runs with
bytecode writing off, and any __pycache__ is removed before zipping (.pyc files embed absolute build paths).
"""
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "1.0.1"
PYVER = "3.14.7"
EMBED_NAME = f"python-{PYVER}-embed-amd64.zip"
EMBED_SHA256 = "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"  # of the file served by python.org
EMBED_URLS = (f"https://www.python.org/ftp/python/{PYVER}/{EMBED_NAME}",
              f"https://registry.npmmirror.com/-/binary/python/{PYVER}/{EMBED_NAME}")  # mirror; verified by the hash above

REPO = Path(__file__).resolve().parent.parent
DIST = REPO.parent / "dist"
OUT = DIST / f"Metaphrasis-{VERSION}-win64"
PY = OUT / "python"
APP_FILES = sorted(p.name for p in REPO.glob("*.py")) + ["guard.pyw", "LICENSE", "README.md", "README.zh-CN.md", "requirements.txt"]
START_BAT = "@echo off\r\nchcp 65001 >nul\r\ncd /d \"%~dp0\"\r\nstart \"\" \"%~dp0python\\pythonw.exe\" \"%~dp0guard.pyw\"\r\n"


def log(*a):
    print("*", *a, flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_embed(dest):
    """Download the embeddable Python and refuse anything that does not match the pinned hash."""
    if dest.exists() and sha256(dest) == EMBED_SHA256:
        return
    for url in EMBED_URLS:
        try:
            log("downloading", url)
            tmp = dest.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=180) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            if sha256(tmp) == EMBED_SHA256:
                tmp.replace(dest)
                return
            log("hash mismatch, discarded")
            tmp.unlink()
        except Exception as e:  # try the next source
            log("failed:", e)
    sys.exit("could not get a verified copy of " + EMBED_NAME)


def main():
    major_minor = ".".join(sys.version.split(".")[:2])
    if not PYVER.startswith(major_minor + "."):
        sys.exit(f"run this with Python {PYVER.rsplit('.', 1)[0]}.x (its Tk files are copied); this is {sys.version.split()[0]}")
    base = Path(sys.base_prefix)
    DIST.mkdir(exist_ok=True)
    if OUT.exists():
        shutil.rmtree(OUT)
    PY.mkdir(parents=True)

    # 1. embedded Python (hash-checked)
    cache = DIST / EMBED_NAME
    fetch_embed(cache)
    with zipfile.ZipFile(cache) as z:
        z.extractall(PY)
    pth = next(PY.glob("python3*._pth"))
    zip_name = pth.name.replace("._pth", ".zip")
    pth.write_text(f"{zip_name}\n.\nLib\nLib\\site-packages\n..\n", encoding="ascii")  # .. = the app folder
    log("embedded python ready")

    # 2. Tk from the full install (the embeddable package does not include tkinter)
    dlls = base / "DLLs"
    for name in ("_tkinter.pyd", "tcl90.dll", "tcl9tk90.dll", "tcl86t.dll", "tk86t.dll", "zlib1.dll"):
        if (dlls / name).exists():
            shutil.copy2(dlls / name, PY / name)
    shutil.copytree(base / "Lib" / "tkinter", PY / "Lib" / "tkinter", ignore=shutil.ignore_patterns("__pycache__", "test"))
    shutil.copytree(base / "tcl", PY / "tcl", ignore=shutil.ignore_patterns("*.lib", "*.sh", "nmake", "*.h"))
    log("tk copied")

    # 3. dependencies, exact versions from requirements.txt, not compiled
    site = PY / "Lib" / "site-packages"
    subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(site), "-r", str(REPO / "requirements.txt"),
                    "--no-compile", "--disable-pip-version-check", "--no-warn-script-location", "-q"], check=True)
    for junk in ("bin", "Scripts"):
        shutil.rmtree(site / junk, ignore_errors=True)
    log("dependencies installed")

    # 4. the app, plus the images the README shows
    for name in APP_FILES:
        shutil.copy2(REPO / name, OUT / name)
    shutil.copytree(REPO / "docs", OUT / "docs")
    (OUT / "start.bat").write_bytes(START_BAT.encode("utf-8"))
    log("app files copied")

    # 5. smoke test with the embedded interpreter, without leaving bytecode behind
    check = ("import tkinter, fanyi, translator, ocr_win, render, settings, i18n; "
             "r = tkinter.Tk(); r.withdraw(); r.destroy(); "
             "from winrt.windows.media.ocr import OcrEngine; import anthropic, PIL, pystray; print('embedded python ok', tkinter.TkVersion)")
    subprocess.run([str(PY / "python.exe"), "-B", "-c", check], cwd=str(OUT), check=True,
                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    for cache_dir in list(OUT.rglob("__pycache__")):
        shutil.rmtree(cache_dir, ignore_errors=True)
    leftovers = [p for p in OUT.rglob("*") if p.name in ("config.ini", "icon.ico") or p.suffix in (".pyc", ".log")]
    assert not leftovers, leftovers

    # 6. zip
    zip_path = DIST / f"{OUT.name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(DIST / OUT.name), "zip", DIST, OUT.name)
    log("done:", zip_path, f"{zip_path.stat().st_size / 1e6:.1f} MB, sha256 {sha256(zip_path)}")


if __name__ == "__main__":
    main()
