"""Switching API companies from the tray menu: what ends up in config.ini. No API calls, no real keys.
usage: test_company.py [app dir]"""
import codecs, sys, tempfile
from pathlib import Path
from _app import APP
import i18n, settings

i18n.set_language("zh")
tmp = Path(tempfile.mkdtemp())
settings.CONFIG_PATH = tmp / "config.ini"
P = settings.CONFIG_PATH
CLAUDE, DEEP, KIMI = "sk-ant-TESTclaude0000000000", "sk-TESTdeepseek00000000", "sk-TESTkimi000000000000"


def sections(text):
    return [m.group(1) for m in settings._HEADER.finditer(text)]


def keys_in(text):
    return {k for k in (CLAUDE, DEEP, KIMI) if k in text}


# A ---- current layout, CRLF + BOM: Claude -> DeepSeek -> Claude
fresh = settings.default_config("zh").replace("api_key =", "api_key = " + CLAUDE, 1).replace("model = claude-sonnet-5-5", "model = claude-fable-5-1", 1)
P.write_bytes(codecs.BOM_UTF8 + fresh.replace("\n", "\r\n").encode("utf-8"))
cfg = settings.load_config()
assert (cfg.company, cfg.api_key, cfg.model) == ("claude", CLAUDE, "claude-fable-5-1")
before = P.read_bytes()
settings.switch_company("deepseek", api_key=DEEP)
cfg = settings.load_config()
assert (cfg.provider, cfg.company, cfg.api_key, cfg.model, cfg.base_url) == ("openai", "deepseek", DEEP, "", "https://api.deepseek.com/v1"), cfg
assert settings.model_problem(cfg) and not settings.api_key_problem(cfg)
text = settings.read_text(P)
assert sections(text) == ["api", "saved.claude", "hotkey", "display", "options"], sections(text)
assert settings._get_in(text, "saved.claude", "api_key") == CLAUDE and settings._get_in(text, "saved.claude", "model") == "claude-fable-5-1"
assert settings._get_in(text, "api", "api_key") == DEEP and text.count(CLAUDE) == 1 and text.count(DEEP) == 1
assert settings.saved_companies() == {"claude"}
settings.set_api_value("model", "deepseek-chat")
cfg = settings.load_config()
assert cfg.model == "deepseek-chat" and settings.model_problem(cfg) is None
settings.switch_company("claude")
cfg = settings.load_config()
assert (cfg.provider, cfg.company, cfg.api_key, cfg.model, cfg.base_url) == ("claude", "claude", CLAUDE, "claude-fable-5-1", ""), cfg
text = settings.read_text(P)
assert sections(text) == ["api", "saved.deepseek", "hotkey", "display", "options"], sections(text)
assert settings._get_in(text, "saved.deepseek", "model") == "deepseek-chat" and text.count(CLAUDE) == 1 and text.count(DEEP) == 1
raw = P.read_bytes()
assert raw.startswith(codecs.BOM_UTF8) and b"\n" not in raw.replace(b"\r\n", b""), "BOM / CRLF must survive"
# apart from the saved section the file is byte-identical to what it was
span = settings._find(text, "saved.deepseek")
assert (codecs.BOM_UTF8 + (text[:span[0]] + text[span[2]:]).encode("utf-8")) == before, "round trip changed something else"
print("A OK  Claude -> DeepSeek -> Claude: keys never mixed, everything else untouched")

# B ---- layout of the first versions: only [claude]
legacy = "; my notes\n[claude]\n; API Key\napi_key = %s\nmodel = claude-fable-5-1\neffort = medium\nproxy = http://127.0.0.1:7890\n\n[hotkey]\ncapture = Alt+~\n\n[options]\ntarget_language = 中文\nui_language = auto\nskip_chinese = true\nsource_language = auto\nsave_history = true\nsave_images = false\n" % CLAUDE
P.write_text(legacy, encoding="utf-8")
cfg = settings.load_config()
assert (cfg.company, cfg.api_key, cfg.effort, cfg.proxy) == ("claude", CLAUDE, "medium", "http://127.0.0.1:7890")
settings.set_api_value("effort", "high"); settings.set_api_value("model", "claude-fable-5-2")
text = settings.read_text(P)
assert sections(text) == ["claude", "hotkey", "options"] and settings.load_config().effort == "high" and settings.load_config().model == "claude-fable-5-2"
settings.switch_company("kimi", api_key=KIMI)
cfg = settings.load_config()
assert (cfg.company, cfg.api_key, cfg.base_url, cfg.effort, cfg.proxy) == ("kimi", KIMI, "https://api.moonshot.cn/v1", "", "http://127.0.0.1:7890"), cfg  # effort is per company: Claude's "high" is put aside with its key
text = settings.read_text(P)
assert sections(text) == ["api", "saved.claude", "hotkey", "options"], sections(text)
assert text.replace("\r\n", "\n").startswith("; my notes\n[api]\n; API Key\n"), repr(text[:60])
settings.switch_company("claude")
cfg = settings.load_config()
assert (cfg.company, cfg.api_key, cfg.model, cfg.effort) == ("claude", CLAUDE, "claude-fable-5-2", "high"), cfg
assert sections(settings.read_text(P)) == ["api", "saved.kimi", "hotkey", "options"]
print("B OK  old [claude] layout: edits in place, renamed to [api] on the first switch, effort and proxy kept")

# C ---- three companies, switching among saved ones needs no key again
settings.switch_company("deepseek", api_key=DEEP)
settings.switch_company("kimi")
cfg = settings.load_config()
assert (cfg.company, cfg.api_key) == ("kimi", KIMI) and settings.saved_companies() == {"claude", "deepseek"}
text = settings.read_text(P)
assert all(text.count(k) == 1 for k in (CLAUDE, DEEP, KIMI)), "each key is stored exactly once"
settings.switch_company("deepseek")
assert settings.load_config().api_key == DEEP
print("C OK  three companies: each key stored once, switching restores the right one")

# D ---- a company with no key on file gets none (never the previous company's)
settings.switch_company("glm")
cfg = settings.load_config()
assert (cfg.company, cfg.api_key) == ("glm", "") and settings.api_key_problem(cfg)
settings.switch_company("ollama")
cfg = settings.load_config()
assert (cfg.company, cfg.api_key, cfg.base_url) == ("ollama", "ollama", "http://localhost:11434/v1")
print("D OK  no key on file -> empty (Ollama: placeholder), never another company's key")

# E ---- custom endpoint
settings.switch_company("custom", api_key="sk-TESTcustom", base_url="https://llm.example.com/v1")
cfg = settings.load_config()
assert (cfg.company, cfg.api_key, cfg.base_url) == ("custom", "sk-TESTcustom", "https://llm.example.com/v1")
settings.switch_company("claude"); settings.switch_company("custom")
cfg = settings.load_config()
assert (cfg.company, cfg.api_key, cfg.base_url) == ("custom", "sk-TESTcustom", "https://llm.example.com/v1"), cfg
assert settings.url_problem("http://example.com/v1") and settings.url_problem("ftp://x") and not settings.url_problem("http://localhost:1234/v1") and not settings.url_problem("https://a.b/v1")
settings.switch_company("custom", api_key="sk-TESTcustom2")  # same company: just the key changes
assert settings.load_config().api_key == "sk-TESTcustom2" and settings.load_config().base_url == "https://llm.example.com/v1"
print("E OK  custom endpoint: address saved with the key; plain http only for local addresses")

# F ---- [options] stays last, so the upgrade that appends missing options still lands in [options]
settings.switch_company("claude")
text = settings.read_text(P)
assert sections(text)[-1] == "options"
assert "save_images = false\r\n" in text
P.write_bytes(text.replace("save_images = false\r\n", "").encode("utf-8"))
cfg = settings.load_config()
text = settings.read_text(P)
assert sections(text)[-1] == "options" and settings._get_in(text, "options", "save_images") == "false" and cfg.save_images is False
settings.set_option("target_language", "English")
assert settings.load_config().target_language == "English" and settings.load_config().api_key == CLAUDE
# a file whose lines end in \r\r\n (double-converted by some editor) is still understood
P.write_bytes(settings.read_text(P).replace("\r\n", "\r\r\n").encode("utf-8"))
settings.switch_company("deepseek")
assert settings.load_config().api_key == DEEP and sections(settings.read_text(P))[-1] == "options"
settings.switch_company("claude")
assert settings.load_config().api_key == CLAUDE
print("F OK  [options] is still the last section; appended options and set_option work; odd line endings survive")

# G ---- comment language switch translates the template blocks, and the note above a saved company's settings with them; the values stay
P.write_text(settings.default_config("zh").replace("api_key =", "api_key = " + CLAUDE, 1), encoding="utf-8")
settings.switch_company("deepseek", api_key=DEEP)
n = settings.localize_config_comments("en")
text = settings.read_text(P)
assert n > 5 and "; API key: paste it right after the equals sign" in text and settings._SAVED_NOTE["en"] in text and settings._SAVED_NOTE["zh"] not in text
assert settings.saved_companies() == {"claude"} and settings._get_in(text, "saved.claude", "api_key") == CLAUDE
assert settings.load_config().api_key == DEEP
# an old Chinese model comment (before Fable 5.2 was listed) is still recognised
old = settings.default_config("zh").replace("claude-fable-5-2（最强）  claude-fable-5-1（上一代最强）", "claude-fable-5-1（最强最贵）").replace("gpt-6.2-sol、deepseek-flash、qwen4-max、kimi-k3.1-pro、glm-5.3-flash", "gpt-4o、deepseek-chat、qwen-vl-max、moonshot-v1-8k、glm-4v")
assert old != settings.default_config("zh")
P.write_text(old, encoding="utf-8")
settings.localize_config_comments("en")
assert "claude-fable-5-2 (best)" in settings.read_text(P)
# the wording of 1.0.x for the endpoint list and the effort line is recognised as well
old = settings.default_config("zh").replace(";   DeepSeek   https://api.deepseek.com/v1\n", ";   DeepSeek   https://api.deepseek.com/v1      （不支持传图，只能靠 OCR 文字翻译）\n")
old = old.replace(";   腾讯混元   https://tokenhub.tencentmaas.com/v1\n", "").replace(";   Grok       https://api.x.ai/v1\n", "")  # 1.0.x did not list these two
old = "\n".join("; 思考深度（只对 Claude 有效）：low（最快，推荐）/ medium / high" if l.startswith("; 思考强度：") else l for l in old.split("\n"))
assert old != settings.default_config("zh") and "思考深度" in old and "不支持传图" in old
P.write_text(old, encoding="utf-8")
settings.localize_config_comments("en")
text = settings.read_text(P)
assert "; Thinking effort: easiest to pick from the tray menu" in text and "不支持传图" not in text and "思考深度" not in text, "old wording not recognised"
settings.localize_config_comments("zh")
assert settings.read_text(P).replace("\r\n", "\n") == settings.default_config("zh"), "en -> zh must give the current Chinese template"
print("G OK  comment language switch: template blocks translated (old wordings too), saved sections untouched")

# H ---- an OpenAI-compatible provider has no default model; Claude does
P.write_text("[api]\nprovider = openai\napi_key = k\nbase_url = https://api.deepseek.com/v1\n", encoding="utf-8")
assert settings.load_config().model == "" and settings.load_config().company == "deepseek"
P.write_text("[api]\nprovider = claude\napi_key = k\n", encoding="utf-8")
assert settings.load_config().model == settings.DEFAULT_MODEL
assert "claude-fable-5-2" in settings.SUPPORTED_MODELS
print("H OK  defaults")
print("ALL OK")
