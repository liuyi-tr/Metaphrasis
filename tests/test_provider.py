"""OpenAI 兼容后端测试：本地起一个假的 /chat/completions（SSE），验证传图、自动探测不支持传图、被过滤、代码块包裹、错误映射；
以及配置读取（新 [api] 段 + 旧 [claude] 段兼容）。不花钱。"""
import json, os, sys, threading, types, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from _app import APP
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
from PIL import Image
import translator as T, ocr_win
MODE = {"m": "ok"}
SEEN = []

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"data":[]}')
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        has_img = any(c.get("type") == "image_url" for c in body["messages"][-1]["content"])
        m = MODE["m"]
        if m == "no_vision" and has_img:
            self.send_response(400); self.end_headers()
            self.wfile.write(b'{"error":{"message":"This model does not support image input"}}'); return
        if m == "no_rf" and "response_format" in body:
            self.send_response(400); self.end_headers()
            self.wfile.write(b'{"error":{"message":"response_format is not supported"}}'); return
        if m == "auth":
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":{"message":"Invalid API key"}}'); return
        text = body["messages"][-1]["content"][-1]["text"]
        ids = [int(x.split(" | ")[0]) for x in text.splitlines() if x[:1].isdigit() and " | " in x]
        answer = json.dumps({"blocks": [{"line_ids": ids, "translation": "你好世界" + ("（带图）" if has_img else "（无图）")}]}, ensure_ascii=False)
        if m == "fenced":
            answer = "```json\n" + answer + "\n```"
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        finish = "content_filter" if m == "filter" else "stop"
        pieces = [answer[i:i + 7] for i in range(0, len(answer), 7)]
        for i, p in enumerate(pieces):
            if m == "filter" and i > 1:
                break
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": p}, "finish_reason": None}]}) + "\n\n").encode()); self.wfile.flush()
        self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")

srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
cfg = types.SimpleNamespace(provider="openai", base_url=base, api_key="test", model="fake-model", vision="auto", proxy="", effort="low")
lines = [ocr_win.OcrLine(i, f"line {i} with ANTHROPIC_API_KEY", (0, i * 20, 200, i * 20 + 18)) for i in range(3)]
img = Image.new("RGB", (220, 80), "white")

# 1) 正常：带图、JSON 直出
MODE["m"] = "ok"; SEEN.clear()
res = T.translate(cfg, img, lines)
assert res.translation_text.startswith("你好世界（带图）"), res.translation_text
assert "response_format" in SEEN[0] and SEEN[0]["stream"] is True and SEEN[0]["model"] == "fake-model"
assert "ANTHROPIC_API_KEY" not in json.dumps(SEEN[0], ensure_ascii=False) and "⟦1⟧" in SEEN[0]["messages"][-1]["content"][-1]["text"]
assert "JSON" in SEEN[0]["messages"][0]["content"]
print("1 OK 正常请求：带图、遮盖、JSON 说明都在")

# 2) 模型不支持传图：自动探测后只发文字，并记住
MODE["m"] = "no_vision"; SEEN.clear()
res = T.translate(cfg, img, lines)
assert res.translation_text.startswith("你好世界（无图）") and len(SEEN) == 2
assert T._vision_known[(base, "fake-model")] is False
SEEN.clear(); T.translate(cfg, img, lines); assert len(SEEN) == 1  # 记住了，不再带图
print("2 OK 不支持传图自动降级并记住")
T._vision_known.clear()

# 3) 接口不接受 response_format：自动去掉
MODE["m"] = "no_rf"; SEEN.clear()
res = T.translate(cfg, img, lines); assert len(SEEN) == 2 and "response_format" not in SEEN[1]
print("3 OK response_format 自动去掉"); T._rf_known.clear()

# 4) 代码块包裹的 JSON 也能解析
MODE["m"] = "fenced"; res = T.translate(cfg, img, lines); assert "你好世界" in res.translation_text
print("4 OK ```json 包裹可解析")

# 5) content_filter → 走重试阶梯（不换模型：去图 → 分段），最终被拦的段有标注
MODE["m"] = "filter"; SEEN.clear()
res = T.translate(cfg, img, lines)
assert T.BLOCKED_NOTE in res.translation_text and len(SEEN) >= 3 and all(m["model"] == "fake-model" for m in SEEN)
print("5 OK 过滤 → 去图 → 分段，最终标注被拦，调用", len(SEEN), "次")

# 6) 401 → 清楚的提示
MODE["m"] = "auth"
try:
    T.translate(cfg, img, lines); raise SystemExit("应报错")
except T.TranslateError as e:
    assert "API Key" in str(e) and e.open_config; print("6 OK 401 →", str(e)[:30])

# 7) 缓存键区分接口

# 8) 配置读取：新 [api] 与旧 [claude]
import settings
tmp = Path(tempfile.mkdtemp())
for name, text, expect in (
    ("new.ini", "[api]\nprovider = openai\napi_key = k\nmodel = deepseek-chat\nbase_url = https://api.deepseek.com/v1\n", ("openai", "deepseek-chat", "https://api.deepseek.com/v1")),
    ("old.ini", "[claude]\napi_key = sk-ant-x\nmodel = claude-haiku-5-5\n", ("claude", "claude-haiku-5-5", "")),
    ("openai_default.ini", "[api]\nprovider = openai\napi_key = k\nmodel = gpt-4o\n", ("openai", "gpt-4o", "https://api.openai.com/v1")),
):
    p = tmp / name; p.write_text(text, encoding="utf-8")
    settings.CONFIG_PATH = p
    c = settings.load_config()
    assert (c.provider, c.model, c.base_url) == expect, (name, c.provider, c.model, c.base_url)
    assert "source_language" in p.read_text(encoding="utf-8")  # 自动补上了 [options]
print("8 OK 配置：新 [api] / 旧 [claude] / 默认地址")
try:
    (tmp / "bad.ini").write_text("[api]\nprovider = openai\napi_key = k\nmodel = x\nbase_url = api.deepseek.com\n", encoding="utf-8")
    settings.CONFIG_PATH = tmp / "bad.ini"; settings.load_config(); raise SystemExit("应报错")
except settings.ConfigError as e:
    print("9 OK base_url 校验：", str(e)[:40])
srv.shutdown()
print("ALL OK")
