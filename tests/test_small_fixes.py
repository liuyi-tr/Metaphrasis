"""Three things a second reader of the code pointed out (fixed for 1.1.2):
1. the settings file is saved whole or not at all: a program ended in the middle of a save must not leave half a
   config.ini behind, with every company's key gone
2. the model a refused request is tried again on may one day be gone, or not allowed for a key: then the translation
   goes on without that step instead of failing, and an error about a model names the model that was asked for
3. small things: usage figures of the other providers (never a made-up 0), a note that named Claude, in Chinese,
   whatever the provider and the language, and the punctuation round pieces that are put back
A local fake server stands in for the other providers and a function for Claude. No real API, no keys, no cost.
usage: test_small_fixes.py [app dir]"""
import json, os, sys, tempfile, threading, types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from _app import APP
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
import anthropic, httpx2
from PIL import Image
import i18n, settings

tmp = Path(tempfile.mkdtemp())
P = settings.CONFIG_PATH = tmp / "config.ini"
SPARE = P.with_name(P.name + ".tmp")
i18n.set_language("zh")
import ocr_win, translator as T

# ---------- 1) the settings file: whole or not at all ----------
KEY = "sk-ant-TEST0000"
FULL = settings.default_config("zh").replace("api_key =", "api_key = " + KEY, 1).encode("utf-8")
OLD = ("[api]\napi_key = " + KEY + "\n\n[options]\ntarget_language = 中文\n").encode("utf-8")  # from a version that knew fewer options


class Stopped(Exception):
    """Stands for the program being ended at this very moment."""


def stopped(*a, **k):
    raise Stopped()


def held(*a, **k):
    raise PermissionError(13, "the file is held open by another program")


real_replace = os.replace
for what, before, save in (("a choice from the tray menu", FULL, lambda: settings.set_option("save_history", "true")),
                           ("another model", FULL, lambda: settings.set_api_value("model", "claude-haiku-5-5")),
                           ("the comments in another language", FULL, lambda: settings.localize_config_comments("en")),
                           ("options a newer version adds", OLD, settings.load_config)):
    P.write_bytes(before)
    os.replace = stopped
    try:
        save()
        ended = False
    except Stopped:
        ended = True
    finally:
        os.replace = real_replace
    assert ended, what + ": the save does not go through a spare file that takes the place of the real one in one step"
    assert P.read_bytes() == before, what + ": ended in the middle of the save, the settings file is no longer what it was"
P.unlink()
os.replace = stopped
try:
    settings.ensure_files()
except Stopped:
    pass
finally:
    os.replace = real_replace
assert not P.exists(), "a first settings file that was not finished must not be there under its real name"
print("1a OK ended in the middle of a save, the settings file is exactly what it was (five kinds of save)")

for leftover in (SPARE,):
    if leftover.exists():
        leftover.unlink()
P.write_bytes(FULL)
settings.set_option("save_history", "true")
text = P.read_text(encoding="utf-8")
assert "save_history = true" in text and KEY in text and sorted(p.name for p in tmp.iterdir()) == ["config.ini"], sorted(p.name for p in tmp.iterdir())
settings.set_api_value("model", "claude-haiku-5-5")
assert settings.load_config().model == "claude-haiku-5-5" and settings.load_config().api_key == KEY
P.write_bytes(OLD)
cfg = settings.load_config()
assert cfg.api_key == KEY and "convert_language" in P.read_text(encoding="utf-8") and P.read_bytes().startswith(OLD.rstrip(b"\n"))
assert sorted(p.name for p in tmp.iterdir()) == ["config.ini"]
print("1b OK an ordinary save changes what it should and leaves no spare file")

P.write_bytes(FULL)  # another program holds the file open, so it cannot be swapped: the save is made into the file itself, as before
os.replace = held
try:
    settings.set_option("save_history", "true")
finally:
    os.replace = real_replace
assert "save_history = true" in P.read_text(encoding="utf-8") and KEY in P.read_text(encoding="utf-8")
assert sorted(p.name for p in tmp.iterdir()) == ["config.ini"], "no spare copy of the keys is left lying about"
SPARE.write_bytes(b"[api]\napi_key = from-a-save-that-never-finished\n")  # left by a program that was ended mid-save
assert settings.load_config().api_key == KEY and not SPARE.exists(), "a spare file left over is cleared away, not read and not kept"
print("1c OK a file that cannot be swapped is still saved; a spare file left over from a broken save is removed")

# ---------- 2) the model a refused request is tried again on ----------
calls, plan = [], []
GOOD = json.dumps({"blocks": [{"line_ids": [0], "translation": "你好世界"}]}, ensure_ascii=False)


def fake_stream(client, params, on_text, alive=None):
    calls.append(params["model"])
    step = plan.pop(0)
    if isinstance(step, Exception):
        raise step
    said, stop = step
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=said)] if said is not None else [],
                                 stop_reason=stop, usage=types.SimpleNamespace(input_tokens=100, output_tokens=10))


def status_error(kind, code, model):
    request = httpx2.Request("POST", "https://example.invalid/v1/messages")
    return kind("model: " + model, response=httpx2.Response(code, request=request), body=None)


T._stream = fake_stream
T._client = lambda cfg: None
MINE = "claude-fable-5-2"
claude = types.SimpleNamespace(provider="claude", model=MINE, effort="low", api_key="k", proxy="", company="claude", vision="auto", base_url="")
img = Image.new("RGB", (200, 60), "white")
lines = [ocr_win.OcrLine(0, "hello world", (0, 0, 100, 20))]
assert T.RETRY_MODEL != MINE

for kind, code in ((anthropic.NotFoundError, 404), (anthropic.PermissionDeniedError, 403)):
    T._unavailable.clear()
    del calls[:]
    plan[:] = [(None, "refusal"), status_error(kind, code, T.RETRY_MODEL), (GOOD, "end_turn")]
    assert T.translate(claude, img, lines).translation_text == "你好世界"
    assert calls == [MINE, T.RETRY_MODEL, MINE], calls           # ... and on with the next step, on the model that was chosen
    del calls[:]
    plan[:] = [(None, "refusal"), (GOOD, "end_turn")]
    assert T.translate(claude, img, lines).translation_text == "你好世界" and calls == [MINE, MINE], calls   # not asked for again in this run
print("2a OK a refused screenshot: when the other model is gone, or not allowed for the key, the translation goes on without it")

T._unavailable.clear()
del calls[:]
plan[:] = [(None, "refusal"), status_error(anthropic.NotFoundError, 404, T.RETRY_MODEL)]
try:
    T.convert_text(claude, "明天见", "English")
    raise AssertionError("a refused conversion must be reported")
except T.TranslateError as e:
    assert "安全过滤" in str(e) and "找不到模型" not in str(e) and T.RETRY_MODEL not in str(e), str(e)
assert calls == [MINE, T.RETRY_MODEL]
print("2b OK a refused conversion is reported as refused, not as a model the user never chose being missing")

T._unavailable.clear()
plan[:] = [status_error(anthropic.NotFoundError, 404, MINE)]
try:
    T.translate(claude, img, lines)
    raise AssertionError("a chosen model that does not exist must be reported")
except T.TranslateError as e:
    assert MINE in str(e) and e.open_config, str(e)
for kind, code in ((anthropic.NotFoundError, 404), (anthropic.PermissionDeniedError, 403)):
    plan[:] = [status_error(kind, code, "claude-other-9")]
    try:
        T._call(claude, {"model": "claude-other-9", "max_tokens": 10, "messages": []}, lambda _: None)
        raise AssertionError("not reported")
    except T.TranslateError as e:
        assert "claude-other-9" in str(e) and MINE not in str(e), str(e)   # the model that was asked for, whichever it is
print("2c OK an error about a model names the model that was asked for")


# ---------- 3) small things ----------
RULE = {"usage": None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        said = body["messages"][-1]["content"][-1]["text"]
        ids = [int(x.split(" | ")[0]) for x in said.splitlines() if x[:1].isdigit() and " | " in x]
        answer = json.dumps({"blocks": [{"line_ids": ids, "translation": "你好世界"}]}, ensure_ascii=False)
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()

        def send(chunk):
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        for i in range(0, len(answer), 9):
            send({"choices": [{"delta": {"content": answer[i:i + 9]}, "finish_reason": None}]})
        used = {"prompt_tokens": 321, "completion_tokens": 45, "total_tokens": 366}
        last = {"delta": {}, "finish_reason": "stop"}
        if RULE["usage"] == "with the last piece":      # as Kimi sends it
            last["usage"] = used
        send({"choices": [last]})
        if RULE["usage"] == "in a piece of its own":    # as OpenAI and the others send it when they do
            send({"choices": [], "usage": used})
        if RULE["usage"] == "empty":
            send({"choices": [], "usage": None})
        self.wfile.write(b"data: [DONE]\n\n")


server = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=server.serve_forever, daemon=True).start()
other = types.SimpleNamespace(provider="openai", base_url="http://127.0.0.1:%d/v1" % server.server_address[1], api_key="test", model="m-1",
                              vision="auto", proxy="", effort="", company="custom")
for how, expected in ((None, None), ("empty", None), ("with the last piece", (321, 45)), ("in a piece of its own", (321, 45))):
    RULE["usage"] = how
    result = T.translate(other, img, lines)
    got = result.usage and (result.usage.input_tokens, result.usage.output_tokens)
    assert result.translation_text == "你好世界" and got == expected, (how, got)
server.shutdown()
print("3a OK usage of other providers: what the provider reports, and nothing (not 0) when it reports none")

note = T.source_text([], [])
assert note == "（OCR 没有识别出原文，译文由模型直接从截图读取）", note
assert set(i18n.STRINGS[note]) == {"zh-tw", "en", "ja", "ko"}
for code in ("en", "ja", "ko", "zh-tw"):
    i18n.set_language(code)
    said = T.source_text([], [])
    assert said != note and "Claude" not in said, (code, said)
i18n.set_language("zh")
print("3b OK the note for a screenshot the OCR read nothing from follows the interface language and names no provider")

table = {"⟦1⟧": "API_KEY_A", "⟦2⟧": "C:\\data\\b.txt"}
masked = [ocr_win.OcrLine(0, "set ⟦1⟧ and open ⟦2⟧", (0, 0, 9, 9))]


def put_back(translation):
    return T._restore_blocks([T.Block([0], translation)], table, masked)[0].translation


assert put_back("设置 ⟦1⟧ 然后打开 ⟦2⟧") == "设置 API_KEY_A 然后打开 C:\\data\\b.txt"          # nothing dropped: nothing added
assert put_back("设置然后打开") == "设置然后打开（API_KEY_A、C:\\data\\b.txt）"
assert put_back("設定して開く") == "設定して開く（API_KEY_A、C:\\data\\b.txt）"
assert put_back("Set it and open the file") == "Set it and open the file (API_KEY_A, C:\\data\\b.txt)"
assert put_back("Set ⟦1⟧ and open the file") == "Set API_KEY_A and open the file (C:\\data\\b.txt)"
assert put_back("설정하고 파일을 엽니다") == "설정하고 파일을 엽니다 (API_KEY_A, C:\\data\\b.txt)"
assert put_back("Установите и откройте") == "Установите и откройте (API_KEY_A, C:\\data\\b.txt)"
print("3c OK pieces the model dropped go back in the punctuation of the translation: （、） after Chinese or Japanese, (, ) otherwise")
print("ALL OK")
