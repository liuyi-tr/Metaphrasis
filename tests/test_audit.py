"""Checks for the audit fixes. No API calls."""
import importlib.util, io, re, sys, tempfile
from pathlib import Path
from _app import APP
import i18n, settings

# 1) plain http is refused except for this machine / the local network
tmp = Path(tempfile.mkdtemp())
def cfg_with(url):
    p = tmp / "c.ini"
    p.write_text(f"[api]\nprovider = openai\napi_key = k\nmodel = m\nbase_url = {url}\n", encoding="utf-8")
    settings.CONFIG_PATH = p
    return settings.load_config()
for ok in ("https://api.deepseek.com/v1", "http://localhost:11434/v1", "http://127.0.0.1:8000/v1", "http://192.168.1.20:11434/v1",
           "http://10.0.0.5/v1", "http://nas:11434/v1", "http://ollama.local:11434/v1", "http://[::1]:11434/v1"):
    assert cfg_with(ok).base_url == ok, ok
for bad in ("http://api.deepseek.com/v1", "http://8.8.8.8/v1", "http://example.com:11434/v1"):
    try:
        cfg_with(bad); raise AssertionError("accepted " + bad)
    except settings.ConfigError as e:
        assert "http://" in str(e)
i18n.set_language("en")
try:
    cfg_with("http://example.com/v1")
except settings.ConfigError as e:
    assert "clear text" in str(e), str(e)
i18n.set_language("zh")
print("1 OK http guard")

# 2) nothing the user selected (or the model wrote) reaches the log
src = io.open(Path(APP) / "translator.py", encoding="utf-8").read()
for stmt in re.findall(r"log\.\w+\((?:[^()]|\([^()]*\))*\)", src):
    assert "l.text" not in stmt and "texts[-1])" not in stmt.replace("len(texts[-1])", "") and "redact" not in stmt, stmt
assert "Refused content" not in src
fsrc = io.open(Path(APP) / "fanyi.py", encoding="utf-8").read()
assert 'getLogger("httpx2").setLevel(logging.WARNING)' in fsrc
print("2 OK log carries no user text; request URLs not logged")

# 3) the watchdog starts the program with the folder's own interpreter when there is one (.venv, or the portable package's python)
guard = next(p for p in (Path(APP) / "守护.pyw", Path(APP) / "guard.pyw") if p.exists())  # (its name in the author's copy, and in the repository)
code = io.open(guard, encoding="utf-8").read().replace('if __name__ == "__main__":\n    main()', "")
ns = {"__file__": str(guard), "__name__": "guard"}
exec(compile(code, "guard", "exec"), ns)
own = [p for p in (Path(APP) / ".venv" / "Scripts" / "pythonw.exe", Path(APP) / "python" / "pythonw.exe") if p.exists()]
assert Path(str(ns["PY"])).exists() and (not own or Path(str(ns["PY"])).resolve() == own[0].resolve()), ns["PY"]
print("3 OK watchdog interpreter:", "the folder's own" if own else "the one running this check")

# 4) translation table still complete
PH = re.compile(r"\{(\w+)\}")
for key, tr in i18n.STRINGS.items():
    assert set(tr) == {"zh-tw", "en", "ja", "ko"} and all(set(PH.findall(x)) == set(PH.findall(key)) for x in tr.values()), key
print("4 OK i18n table", len(i18n.STRINGS))
print("ALL OK")
