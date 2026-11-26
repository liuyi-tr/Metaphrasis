"""Per-company thinking settings: what goes into the request, and what happens when a model turns it down.
A local fake server stands in for every company. No real API, no keys, no cost.
usage: test_thinking.py [app dir]"""
import json, os, sys, tempfile, threading, types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from _app import APP
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
from PIL import Image
import i18n, ocr_win, settings, translator as T

i18n.set_language("zh")
MODE = {"m": "ok"}
SEEN = []
THINK_KEYS = ("reasoning_effort", "thinking", "enable_thinking")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def fail(self, message):
        self.send_response(400); self.end_headers()
        self.wfile.write(json.dumps({"error": {"message": message}}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        m = MODE["m"]
        if m == "bad_key":
            return self.fail("Please pass a valid API key")
        if m == "no_think" and any(k in body for k in THINK_KEYS):
            return self.fail('"fake-model" does not support thinking')
        if m == "only_low" and body.get("reasoning_effort") not in (None, "low"):
            return self.fail("invalid reasoning value")
        if m == "renamed" and "max_tokens" in body:
            return self.fail("Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.")
        if m == "json_think" and "response_format" in body and body.get("enable_thinking"):
            return self.fail("Json mode response is not supported when enable_thinking is true")
        text = body["messages"][-1]["content"][-1]["text"]
        ids = [int(x.split(" | ")[0]) for x in text.splitlines() if x[:1].isdigit() and " | " in x]
        answer = json.dumps({"blocks": [{"line_ids": ids, "translation": "你好世界"}]}, ensure_ascii=False)
        if m == "think_tag":
            answer = "<think>\nthe user wants {\"blocks\": ...} hmm\n</think>\n" + answer
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()

        def send(delta, finish=None):
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}) + "\n\n").encode()); self.wfile.flush()
        if m == "reasoning_field":
            for piece in ("Let me think {about} this. ", "The lines are short."):
                send({"reasoning_content": piece, "content": ""})
                send({"reasoning": piece})
        for i in range(0, len(answer), 9):
            send({"content": answer[i:i + 9]})
        send({}, "stop")
        self.wfile.write(b"data: [DONE]\n\n")


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
lines = [ocr_win.OcrLine(i, f"line {i}", (0, i * 20, 200, i * 20 + 18)) for i in range(3)]
img = Image.new("RGB", (220, 80), "white")


def run(company, effort, model="fake-model"):
    SEEN.clear()
    cfg = types.SimpleNamespace(provider="openai", base_url=base, api_key="test", model=model, vision="auto", proxy="", effort=effort, company=company)
    res = T.translate(cfg, img, lines)
    assert res.translation_text == "你好世界", res.translation_text
    return list(SEEN)


def sent(body):
    return {k: body[k] for k in THINK_KEYS if k in body}


# 1 ---- every company, every choice: exactly that company's parameters, and nothing at all for "default"
EXPECT = {
    "chatgpt": {"off": {"reasoning_effort": "none"}, "low": {"reasoning_effort": "low"}, "medium": {"reasoning_effort": "medium"},
                "high": {"reasoning_effort": "high"}, "xhigh": {"reasoning_effort": "xhigh"}, "max": {"reasoning_effort": "max"}},
    "deepseek": {"off": {"thinking": {"type": "disabled"}}, "low": {"reasoning_effort": "low"}, "high": {"reasoning_effort": "high"}, "max": {"reasoning_effort": "max"}},
    "qwen": {"off": {"enable_thinking": False}, "on": {"enable_thinking": True}, "low": {"reasoning_effort": "low"}, "high": {"reasoning_effort": "high"}, "xhigh": {"reasoning_effort": "xhigh"}},
    "kimi": {"low": {"reasoning_effort": "low"}, "high": {"reasoning_effort": "high"}, "max": {"reasoning_effort": "max"}},
    "glm": {"off": {"thinking": {"type": "disabled"}}, "low": {"reasoning_effort": "low"}, "high": {"reasoning_effort": "high"}, "max": {"reasoning_effort": "max"}},
    "gemini": {"minimal": {"reasoning_effort": "minimal"}, "low": {"reasoning_effort": "low"}, "medium": {"reasoning_effort": "medium"}, "high": {"reasoning_effort": "high"}, "xhigh": {"reasoning_effort": "xhigh"}},
    "ollama": {"off": {"reasoning_effort": "none"}, "low": {"reasoning_effort": "low"}, "medium": {"reasoning_effort": "medium"}, "high": {"reasoning_effort": "high"}},
}
n = 0
for company, choices in EXPECT.items():
    assert settings.thinking_levels(company) == ("",) + tuple(choices), (company, settings.thinking_levels(company))
    bodies = run(company, "", model=f"m-{company}")
    assert len(bodies) == 1 and sent(bodies[0]) == {}, (company, "default must send nothing", bodies[0])
    for effort, want in choices.items():
        bodies = run(company, effort, model=f"m-{company}")
        assert len(bodies) == 1 and sent(bodies[0]) == want, (company, effort, sent(bodies[0]))
        n += 1
assert settings.thinking_levels("custom") == ("",) and sent(run("custom", "high")[0]) == {}, "an unknown endpoint is never sent a thinking setting"
assert settings.thinking_levels("claude") == settings.EFFORTS and settings.thinking_body("claude", "high") == {}
print(f"1 OK {n} company/level combinations send exactly the documented parameters; default and custom send none")

# 2 ---- a model that turns the setting down: the request goes again without it, and that is remembered
MODE["m"] = "no_think"
bodies = run("glm", "high", model="old-model")
assert [sent(b) for b in bodies] == [{"reasoning_effort": "high"}, {}], [sent(b) for b in bodies]
bodies = run("glm", "high", model="old-model")
assert [sent(b) for b in bodies] == [{}], "second time it is not even tried"
MODE["m"] = "only_low"
bodies = run("kimi", "max", model="k")
assert [sent(b) for b in bodies] == [{"reasoning_effort": "max"}, {}]
bodies = run("kimi", "low", model="k")
assert [sent(b) for b in bodies] == [{"reasoning_effort": "low"}], "another level of the same model is still tried"
# a 400 that has nothing to do with thinking (Gemini answers 400 to a wrong key): reported as it is, and nothing is remembered
MODE["m"] = "bad_key"
try:
    run("gemini", "high", model="g")
    raise AssertionError("should have failed")
except T.TranslateError as e:
    assert "valid API key" in str(e), str(e)
assert len(SEEN) == 2 and sent(SEEN[0]) == {"reasoning_effort": "high"} and sent(SEEN[1]) == {}
MODE["m"] = "ok"
assert [sent(b) for b in run("gemini", "high", model="g")] == [{"reasoning_effort": "high"}], "once the key is right the setting is sent again"
print("2 OK a rejected setting is dropped and the translation still succeeds; remembered per model and level; an unrelated 400 is not blamed on it")

# 3 ---- output limit: roomier where thinking counts against it; renamed parameter is followed
MODE["m"] = "ok"
assert run("deepseek", "")[0]["max_tokens"] == 16000 and run("kimi", "")[0]["max_tokens"] == 16000
assert run("chatgpt", "")[0]["max_tokens"] == 16000 and run("custom", "")[0]["max_tokens"] == 8192 and run("grok", "")[0]["max_tokens"] == 8192
MODE["m"] = "renamed"
bodies = run("chatgpt", "medium", model="new-reasoner")
assert len(bodies) == 2 and "max_tokens" in bodies[0] and "max_tokens" not in bodies[1] and bodies[1]["max_completion_tokens"] == 16000, bodies
assert sent(bodies[1]) == {"reasoning_effort": "medium"}, "the thinking setting survives the rename"
bodies = run("chatgpt", "medium", model="new-reasoner")
assert len(bodies) == 1 and "max_completion_tokens" in bodies[0]
print("3 OK output limit: 16000 for DeepSeek and Kimi, 8192 elsewhere; max_completion_tokens is picked up when asked for")

# 4 ---- Qwen: JSON mode refused while thinking -> JSON mode goes, the thinking choice stays
MODE["m"] = "json_think"
T._rf_known.clear()
bodies = run("qwen", "on", model="qwen-plus")
assert len(bodies) == 2 and "response_format" in bodies[0] and "response_format" not in bodies[1] and bodies[1]["enable_thinking"] is True, bodies
T._rf_known.clear()
print("4 OK JSON mode refused while thinking: retried without JSON mode, thinking setting kept")

# 5 ---- thinking text never ends up in the translation
MODE["m"] = "think_tag"
run("ollama", "high", model="tagger")
MODE["m"] = "reasoning_field"
run("deepseek", "high", model="reasoner")
print("5 OK thinking in <think> tags or in a separate field is ignored")

# 6 ---- config: each company's choice is its own, checked against its own list, kept across switches
settings.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.ini"
settings.CONFIG_PATH.write_text(settings.default_config("zh").replace("api_key =", "api_key = sk-ant-TEST", 1).replace("effort = low", "effort = high"), encoding="utf-8")
assert settings.load_config().effort == "high"
settings.switch_company("deepseek", api_key="sk-TESTdeep")
assert settings.load_config().effort == "", "a new company starts at its default"
settings.set_api_value("effort", "max")
assert settings.load_config().effort == "max"
settings.set_api_value("effort", "medium")  # not a DeepSeek level
assert settings.load_config().effort == ""
settings.set_api_value("effort", "max")
settings.switch_company("claude")
assert settings.load_config().effort == "high"
settings.switch_company("deepseek")
assert settings.load_config().effort == "max"
settings.switch_company("kimi", api_key="sk-TESTkimi")
assert settings.load_config().effort == ""
print("6 OK each company keeps its own thinking choice; a value from another company's list falls back to default")
print("ALL OK")
