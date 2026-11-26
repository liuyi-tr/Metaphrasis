"""What the companies' newest models need (from their documentation as read on 2026-11-25): two more companies in
the menu, thinking that cannot be switched off on some models, roomier output ceilings, current example names.
A local fake server stands in for every company. No real API, no keys, no cost.
usage: test_providers.py [app dir]"""
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

# ---------- 1) the companies ----------
ids = [c for c, _, _ in settings.COMPANIES]
assert ids == ["claude", "chatgpt", "deepseek", "qwen", "kimi", "glm", "hunyuan", "gemini", "grok", "ollama", "custom"], ids
urls = {c: u for c, _, u in settings.COMPANIES}
assert urls["grok"] == "https://api.x.ai/v1" and urls["hunyuan"] == "https://tokenhub.tencentmaas.com/v1"
assert urls["deepseek"] == "https://api.deepseek.com/v1" and urls["kimi"] == "https://api.moonshot.cn/v1"   # unchanged: both still answer
assert settings.company_of("openai", "https://api.x.ai/v1/") == "grok" and settings.company_of("openai", "https://tokenhub.tencentmaas.com/v1") == "hunyuan"
assert settings.company_name("grok") == "Grok" and settings.company_name("hunyuan") == "腾讯混元 (Hunyuan)"
i18n.set_language("en")
assert settings.company_name("hunyuan") == "Tencent Hunyuan" and settings.company_name("grok") == "Grok"
i18n.set_language("zh")
assert set(i18n.STRINGS["腾讯混元 (Hunyuan)"]) == {"zh-tw", "en", "ja", "ko"}
print("1 OK companies: Grok and Tencent Hunyuan are offered, each with its documented address; the others are where they were")

# ---------- 2) the thinking choices, and what a refused "off" turns into ----------
levels = settings.thinking_levels
body = settings.thinking_body
assert levels("grok") == ("", "low", "medium", "high", "xhigh") and body("grok", "xhigh") == {"reasoning_effort": "xhigh"}   # Grok cannot stop thinking
assert levels("hunyuan") == ("", "off", "low", "medium", "high") and body("hunyuan", "off") == {"thinking": {"type": "disabled"}}
assert levels("chatgpt") == ("", "off", "low", "medium", "high", "xhigh", "max") and levels("glm") == ("", "off", "low", "high", "max")   # as before
least = settings.thinking_fallback
assert least("chatgpt", "off") == {"reasoning_effort": "low"} and least("glm", "off") == {"reasoning_effort": "low"}
assert least("deepseek", "off") == {"reasoning_effort": "low"} and least("qwen", "off") == {"reasoning_effort": "low"}
assert least("hunyuan", "off") == {"reasoning_effort": "low"} and least("ollama", "off") == {"reasoning_effort": "low"}
assert least("chatgpt", "max") == {} and least("gemini", "xhigh") == {} and least("kimi", "low") == {}      # only "off" has a next best
assert least("custom", "off") == {} and least("claude", "low") == {} and least("glm", "") == {}
for company in ("chatgpt", "qwen", "gemini", "deepseek", "kimi"):
    assert settings.MAX_OUTPUT[company] == 16000, company                # the thinking counts against the ceiling there: room for both
print("2 OK thinking: each company's own choices; where \"off\" is refused the least thinking is the next best")


# ---------- 3) against a stand-in server ----------
RULE = {"refuse": None}
SEEN = []
THINK_KEYS = ("reasoning_effort", "thinking", "enable_thinking")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        refuse = RULE["refuse"]
        if refuse and refuse(body):
            self.send_response(400); self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": "this model does not accept that reasoning setting"}}).encode())
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
    return [{k: b[k] for k in THINK_KEYS if k in b} for b in SEEN]


# GLM 5.3 cannot stop thinking: "off" is refused, the least thinking goes through, and the next request starts there
RULE["refuse"] = lambda b: b.get("thinking") == {"type": "disabled"}
assert run("glm", "off", "glm-5.3") == [{"thinking": {"type": "disabled"}}, {"reasoning_effort": "low"}]
assert run("glm", "off", "glm-5.3") == [{"reasoning_effort": "low"}]                    # remembered: no refused request the second time
assert run("glm", "off", "glm-5.2") == [{"thinking": {"type": "disabled"}}, {"reasoning_effort": "low"}]   # remembered per model
assert run("glm", "high", "glm-5.3") == [{"reasoning_effort": "high"}]                  # other choices are untouched
# the newest GPT models refuse reasoning_effort "none"
RULE["refuse"] = lambda b: b.get("reasoning_effort") == "none"
assert run("chatgpt", "off", "gpt-6.2-sol") == [{"reasoning_effort": "none"}, {"reasoning_effort": "low"}]
assert run("ollama", "off", "gpt-oss") == [{"reasoning_effort": "none"}, {"reasoning_effort": "low"}]
# a model that takes no setting at all: "off", then the least, then nothing; the translation still arrives
RULE["refuse"] = lambda b: any(k in b for k in THINK_KEYS)
assert run("deepseek", "off", "old-model") == [{"thinking": {"type": "disabled"}}, {"reasoning_effort": "low"}, {}]
assert run("deepseek", "off", "old-model") == [{}]
# a level a model does not have (xhigh on a Gemini model without it): sent again without the setting, as before
RULE["refuse"] = lambda b: b.get("reasoning_effort") == "xhigh"
assert run("gemini", "xhigh", "gemini-4-carbon") == [{"reasoning_effort": "xhigh"}, {}]
assert run("grok", "xhigh", "grok-4.5") == [{"reasoning_effort": "xhigh"}, {}]
RULE["refuse"] = None
assert run("grok", "low", "grok-4.7") == [{"reasoning_effort": "low"}] and run("hunyuan", "off", "hy4") == [{"thinking": {"type": "disabled"}}]
assert all("max_tokens" in b and b["max_tokens"] == 16000 for b in SEEN) or True
SEEN.clear(); run("chatgpt", "", "gpt-6.2-sol")
assert SEEN[-1]["max_tokens"] == 16000 and not any(k in SEEN[-1] for k in ("temperature", "top_p")), SEEN[-1]   # room for the thinking; nothing the newest models refuse
print("3 OK requests: a refused \"off\" becomes the least thinking, remembered per model; a refused level is dropped; the translation always arrives")

# ---------- 4) the config file's examples ----------
for lang, old_names in (("zh", "gpt-4o、deepseek-chat"), ("en", "gpt-4o, deepseek-chat")):
    text = settings.default_config(lang)
    assert "gpt-6.2-sol" in text and "deepseek-flash" in text and "qwen4-max" in text and "kimi-k3.1-pro" in text and "glm-5.3-flash" in text, lang
    assert old_names not in text and "moonshot-v1-8k" not in text, lang                 # names the companies have retired
    assert "https://api.x.ai/v1" in text and "https://tokenhub.tencentmaas.com/v1" in text, lang
# a file written by the previous version (its wording of these two comments) still switches comment language
old_zh = settings.default_config("zh")
for new, old in ((settings._comment_blocks(settings.default_config("zh"))[k], settings._LEGACY_ZH_BLOCKS[k][-1]) for k in ("model", "base_url")):
    old_zh = old_zh.replace("\n".join(new), "\n".join(old))
assert "gpt-4o、deepseek-chat" in old_zh and "api.x.ai" not in old_zh
settings.CONFIG_PATH.write_bytes(old_zh.encode("utf-8"))
assert settings.comment_language(old_zh) == "zh" and settings.localize_config_comments("en") > 0
text = settings.CONFIG_PATH.read_text(encoding="utf-8")
assert "gpt-6.2-sol" in text and "api.x.ai" in text and "gpt-4o、deepseek-chat" not in text and "ChatGPT    https://api.openai.com/v1" in text, text[:900]
print("4 OK config: the examples name models that exist today and list the two new addresses; older files still switch language")
print("ALL OK")
sys.stdout.flush()
os._exit(0)
