"""What the pre-release review found, each as a check that fails on the code as reviewed.
A local fake server stands in for every company; a throwaway config; windows off-screen. No real API, no cost.
usage: test_review_fixes.py [app dir]"""
import json, os, sys, tempfile, threading, types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from _app import APP
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
from PIL import Image
import i18n, settings

tmp = Path(tempfile.mkdtemp())
settings.CONFIG_PATH = tmp / "config.ini"
i18n.set_language("zh")
import ocr_win, translator as T

RULE = {"answer": None}
SEEN = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        refusal = RULE["answer"](body) if RULE["answer"] else None
        if refusal:   # text: the message of an error in the usual shape; a dict: the whole error body; bytes: sent as they are
            self.send_response(400); self.end_headers()
            self.wfile.write(refusal if isinstance(refusal, bytes) else
                             json.dumps(refusal if isinstance(refusal, dict) else {"error": {"message": refusal}}).encode())
            return
        text = body["messages"][-1]["content"][-1]["text"]
        ids = [int(x.split(" | ")[0]) for x in text.splitlines() if x[:1].isdigit() and " | " in x]
        answer = json.dumps({"blocks": [{"line_ids": ids, "translation": "你好世界"}]}, ensure_ascii=False)
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for i in range(0, len(answer), 9):
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": answer[i:i + 9]}, "finish_reason": None}]}) + "\n\n").encode())
        self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n").encode())


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
lines = [ocr_win.OcrLine(i, f"line {i}", (0, i * 20, 200, i * 20 + 18)) for i in range(3)]
img = Image.new("RGB", (220, 80), "white")


def run(company, effort, model):
    SEEN.clear()
    cfg = types.SimpleNamespace(provider="openai", base_url=base, api_key="test", model=model, vision="auto", proxy="", effort=effort, company=company)
    assert T.translate(cfg, img, lines).translation_text == "你好世界"
    return list(SEEN)


def ceiling(body):
    return body.get("max_tokens", body.get("max_completion_tokens"))


def has_image(body):
    return any(part.get("type") == "image_url" for part in body["messages"][-1]["content"])


# ---------- 1) a model whose own output ceiling is lower than what is asked for ----------
RULE["answer"] = lambda b: "Range of max_tokens should be [1, 8192]" if ceiling(b) > 8192 else None            # Qwen's wording
assert [ceiling(b) for b in run("qwen", "", "qwen-old-0428")] == [16000, 8192]
assert [ceiling(b) for b in run("qwen", "", "qwen-old-0428")] == [8192]                                         # remembered: no refused request the next time
RULE["answer"] = lambda b: f"max_tokens is too large: {ceiling(b)}. This model supports at most 4096 completion tokens" if ceiling(b) > 4096 else None   # OpenAI's
assert [ceiling(b) for b in run("chatgpt", "", "gpt-small")] == [16000, 4096]
RULE["answer"] = lambda b: "invalid value for max_tokens" if ceiling(b) > 5000 else None                        # no figure given: halve until it fits
assert [ceiling(b) for b in run("gemini", "", "g-small")] == [16000, 8000, 4000]
# together with the renamed parameter and a refused thinking setting
RULE["answer"] = lambda b: ("Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead." if "max_tokens" in b else
                            "max_completion_tokens must be less than or equal to 8192" if ceiling(b) > 8192 else
                            "reasoning_effort 'none' is not supported" if b.get("reasoning_effort") == "none" else None)
bodies = run("chatgpt", "off", "gpt-mixed")
assert [(ceiling(b), b.get("reasoning_effort")) for b in bodies][-1] == (8192, "low") and "max_completion_tokens" in bodies[-1], bodies[-1]
assert len(run("chatgpt", "off", "gpt-mixed")) == 1                                                             # all three lessons kept


def refused(company, effort, model):
    """The requests made before the program gave up, and what it told the user."""
    try:
        run(company, effort, model)
    except T.TranslateError as e:
        return list(SEEN), str(e)
    raise AssertionError("the request went through")


# the figure is taken from what the error says, not from the ids, dates and codes that come with it
RULE["answer"] = lambda b: ({"error": {"message": "Range of max_tokens should be [1, 8192]", "code": 400},
                             "request_id": "9f8b2024-4096-4a7c-b1d2-000012345678", "created": 1795000000} if ceiling(b) > 8192 else None)
assert [ceiling(b) for b in run("qwen", "", "qwen-with-ids")] == [16000, 8192]
RULE["answer"] = lambda b: (b"Bad Request: max_tokens (16000) exceeds 4096; request 2026-11-25T10:00:00 id 7777-8888 v1.2048"
                            if ceiling(b) > 4096 else None)                                                     # not JSON at all
assert [ceiling(b) for b in run("deepseek", "", "ds-plain-text")] == [16000, 4096]
RULE["answer"] = lambda b: [{"error": {"code": 400, "message": "maxOutputTokens 16000 is outside the supported range 1 to 8193 (exclusive)", "status": "INVALID_ARGUMENT"}}] if ceiling(b) > 8192 else None
assert [ceiling(b) for b in run("gemini", "", "g-listed")][-1] <= 8192                                          # Gemini answers with a list
# a refusal that asks for MORE output, or that no smaller figure cures, lowers nothing and leaves nothing behind
RULE["answer"] = lambda b: "max_tokens must be at least 20000 for this model"
bodies, message = refused("chatgpt", "", "gpt-wants-more")
assert [ceiling(b) for b in bodies] == [16000] and "20000" in message and (base, "gpt-wants-more") not in T._limit_known
RULE["answer"] = lambda b: "invalid value for max_tokens"
bodies, message = refused("qwen", "", "qwen-never")
assert [ceiling(b) for b in bodies] == [16000, 8000, 4000, 2000] and (base, "qwen-never") not in T._limit_known, [ceiling(b) for b in bodies]
RULE["answer"] = lambda b: "max_tokens must be greater than thinking budget" if "reasoning_effort" in b else None   # about thinking: the ceiling stays
assert [(ceiling(b), "reasoning_effort" in b) for b in run("chatgpt", "high", "gpt-budget")] == [(16000, True), (16000, False)]
print("1 OK ceiling: a model that allows less output than asked for is asked again for what it allows, and that is remembered")

# ---------- 2) a refused thinking setting is not mistaken for "this model cannot see pictures" ----------
RULE["answer"] = lambda b: '"llama3.2-vision" does not support thinking' if "reasoning_effort" in b else None
bodies = run("ollama", "low", "llama3.2-vision")
assert has_image(bodies[-1]) and "reasoning_effort" not in bodies[-1] and len(bodies) == 2, [(has_image(b), b.get("reasoning_effort")) for b in bodies]
assert has_image(run("ollama", "low", "llama3.2-vision")[-1])                                                   # and the picture goes on being sent
RULE["answer"] = lambda b: "multimodal-pro-image does not accept reasoning_effort" if "reasoning_effort" in b else None
assert has_image(run("custom", "", "multimodal-pro-image")[-1])
RULE["answer"] = lambda b: "This model does not support image input" if has_image(b) else None                  # a real "no pictures" is still understood
bodies = run("deepseek", "", "text-only-model")
assert [has_image(b) for b in bodies] == [True, False]
RULE["answer"] = lambda b: ("thinking is not available together with image input" if "reasoning_effort" in b else
                            "image input is not supported" if has_image(b) else None)                           # one after the other
bodies = run("ollama", "low", "plain-llm")
assert [("reasoning_effort" in b, has_image(b)) for b in bodies] == [(True, True), (False, True), (False, False)], bodies
print("2 OK pictures: a model named ...vision that refuses a thinking setting keeps getting the screenshot; a real refusal of pictures is still followed")

# ---------- 3) the "type a model name" box and a change of company while it is open ----------
CLAUDE, DEEP = "sk-ant-TESTclaude0000000000", "sk-TESTdeepseek00000000"
settings.CONFIG_PATH.write_text(settings.default_config("zh").replace("api_key =", "api_key = " + CLAUDE, 1).replace("ui_language = auto", "ui_language = 简体中文"), encoding="utf-8")
import fanyi
app = fanyi.App()
app.labels = {"capture": "Alt+~", "toggle": "Ctrl+Alt+Y", "convert": "Alt+1"}
toasts, errors = [], []
app.toast = lambda text, *a, **k: toasts.append(text)
app.show_error = lambda message, *a, **k: errors.append(message)
app.focus = lambda win: None
app.place_beside = lambda win, box, gap=12: win.geometry("+-4000+-4000")
T.list_models = lambda cfg, limit=40: ["deepseek-flash"]


def pump():
    import time
    for _ in range(100):
        while not app.events.empty():
            app._handle(app.events.get_nowait())
        if not any(t.name == "models" for t in threading.enumerate()) and app.events.empty():
            return
        time.sleep(0.02)


app._use_company("deepseek", DEEP); pump()
app._tray_action("type_model")
box = app.model_dialog
box.model.set("deepseek-v4-pro")
app._use_company("claude"); pump()                                # the company is changed from the tray while the box is open
assert not box.alive, "the box for the other company is still open"
box.on_save("deepseek-v4-pro")                                    # even if its Save were still pressed
cfg = settings.load_config()                                      # must not raise: a Claude config with another company's model would
assert cfg.company == "claude" and cfg.model.startswith("claude-"), cfg.model
assert not errors
print("3 OK model box: changing company closes it, and a name typed for one company can never be saved under another")

# ---------- 4) "start with Windows": the shortcut taken over from the old name starts THIS copy ----------
import autostart
startup = tmp / "Startup"; startup.mkdir()
autostart.STARTUP_DIR = startup
autostart.LINK_PATH = startup / autostart.LINK_PATH.name
autostart.OLD_LINK_PATH = startup / autostart.OLD_LINK_PATH.name
made = []
autostart.enable = lambda: (made.append(1), autostart.LINK_PATH.write_bytes(b"points at this folder"))[1]
autostart.OLD_LINK_PATH.write_bytes(b"points at the old folder")
assert autostart.adopt_old_link() is True and made == [1]
assert autostart.LINK_PATH.read_bytes() == b"points at this folder" and not autostart.OLD_LINK_PATH.exists()
assert autostart.adopt_old_link() is False and made == [1]         # done once
autostart.LINK_PATH.unlink()
autostart.OLD_LINK_PATH.write_bytes(b"points at the old folder")


def fails():
    raise RuntimeError("PowerShell could not write the shortcut")


autostart.enable = fails
try:
    autostart.adopt_old_link()
except RuntimeError:
    pass
assert autostart.OLD_LINK_PATH.exists() and autostart.is_enabled()  # the old one stays until a new one has been made
print("4 OK start with Windows: the old shortcut is replaced by one that starts this copy; it is only removed once that exists")

# ---------- 5) a config file that is not UTF-8 gets the new option added in its own encoding ----------
old = settings.default_config("zh").replace("convert = Alt+1\n", "")
head, _, tail = old.partition("; 输入框转换成哪种语言")
old = (head + tail[tail.index("; 框选的内容本来就是"):]).replace("api_key =", "api_key = sk-ant-TEST0000", 1)
assert "convert_language" not in old
for encoding, newline in (("utf-16", "\r\n"), ("utf-16-be", "\n"), ("gbk", "\r\n"), ("utf-8", "\n"), ("utf-8-sig", "\r\n")):
    # (a file saved as GBK cannot hold the Korean in one of the comments: an editor would have put "?" there)
    fixture = (b"\xfe\xff" if encoding == "utf-16-be" else b"") + old.replace("\n", newline).encode(encoding, errors="replace")
    settings.CONFIG_PATH.write_bytes(fixture)
    cfg = settings.load_config()
    assert cfg.convert_language == "English" and cfg.api_key == "sk-ant-TEST0000", encoding
    raw = settings.CONFIG_PATH.read_bytes()
    assert raw.startswith(fixture), encoding                       # what was there is untouched: the option is added at the end
    added = raw[len(fixture):].decode("utf-16-le" if encoding == "utf-16" else encoding.replace("-sig", ""))   # in the file's own encoding
    assert added.count("convert_language = English") == 1 and "输入框转换成哪种语言" in added, encoding
    assert "\n" in added and ("\r" in added) == (newline == "\r\n") and added.count("\r") in (0, added.count("\n")), (encoding, "line endings of the added part")
    assert settings.read_text(settings.CONFIG_PATH).count("convert_language = English") == 1, encoding
    settings.load_config()
    assert settings.CONFIG_PATH.read_bytes() == raw, encoding        # added once
print("5 OK config: a file saved as UTF-16 or GBK gets the new option in that encoding, with the file's own line endings, once")
print("ALL OK")
sys.stdout.flush()
os._exit(0)
