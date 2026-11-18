"""Config file and translation history."""
import codecs
import configparser
import ipaddress
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import i18n
import redact
from i18n import t

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.ini"
HISTORY_PATH = APP_DIR / "翻译记录.txt"
LOG_PATH = APP_DIR / "运行日志.txt"
ICON_PATH = APP_DIR / "icon.ico"
IMAGES_DIR = APP_DIR / "保存的译文"  # with save_images on, rendered images go here, one folder per day

SUPPORTED_MODELS = ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5", "claude-fable-5-2", "claude-fable-5-1")
DEFAULT_MODEL = "claude-sonnet-5-5"
# How hard to think, per company: the choices the tray menu offers, in order, and what each one adds to the request.
# Claude takes EFFORTS (output_config.effort). For every other company the first choice is "" = say nothing and let the
# model use its own default; the entries here come from each company's API documentation (looked up 2026-11-18).
def _effort(level):
    return {"reasoning_effort": level}


THINKING = {
    # developers.openai.com/api/docs/guides/reasoning : which of these a model takes depends on its generation
    "chatgpt": {"off": _effort("none"), "low": _effort("low"), "medium": _effort("medium"), "high": _effort("high"),
                "xhigh": _effort("xhigh"), "max": _effort("max")},
    # api-docs.deepseek.com/guides/thinking_mode : thinking is on by default, at "high"
    "deepseek": {"off": {"thinking": {"type": "disabled"}}, "low": _effort("low"), "high": _effort("high"), "max": _effort("max")},
    # help.aliyun.com/zh/model-studio/deep-thinking : the newest models take levels, older ones only on / off
    "qwen": {"off": {"enable_thinking": False}, "on": {"enable_thinking": True}, "low": _effort("low"), "high": _effort("high"),
             "xhigh": _effort("xhigh")},
    # platform.kimi.com/docs/guide/use-reasoning-effort : K3 models always think, at "max" by default
    "kimi": {"low": _effort("low"), "high": _effort("high"), "max": _effort("max")},
    # docs.bigmodel.cn/cn/guide/capabilities/thinking : on by default at "max"; the levels need GLM-5.2 or later
    "glm": {"off": {"thinking": {"type": "disabled"}}, "low": _effort("low"), "high": _effort("high"), "max": _effort("max")},
    # aistudio.google.com/docs/openai : current models cannot stop thinking; "minimal" is the least
    "gemini": {"minimal": _effort("minimal"), "low": _effort("low"), "medium": _effort("medium"), "high": _effort("high"),
               "xhigh": _effort("xhigh")},
    # docs.ollama.com/api/openai-compatibility : each model maps these names onto what it supports
    "ollama": {"off": _effort("none"), "low": _effort("low"), "medium": _effort("medium"), "high": _effort("high")},
}
# Ceiling on output tokens for OpenAI-compatible endpoints. Where the company's documentation says the thinking counts
# against it, the ceiling has to be roomier or a long think leaves no room for the answer.
MAX_OUTPUT = {"deepseek": 16000, "kimi": 16000}


def thinking_levels(company):
    """The effort choices for a company, in menu order."""
    return EFFORTS if company == "claude" else ("",) + tuple(THINKING.get(company, {}))


def default_effort(company):
    return "low" if company == "claude" else ""


def thinking_body(company, effort):
    """What an effort choice adds to an OpenAI-compatible request body ({} = nothing)."""
    return THINKING.get(company, {}).get(effort, {})


# The companies the tray menu offers: (id, name, endpoint). Everything but Claude speaks the OpenAI-compatible protocol;
# "custom" is any other such endpoint, with the address typed in by the user.
COMPANIES = (
    ("claude", "Claude", ""),
    ("chatgpt", "ChatGPT", "https://api.openai.com/v1"),
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1"),
    ("qwen", "通义千问 (Qwen)", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("kimi", "Kimi", "https://api.moonshot.cn/v1"),
    ("glm", "智谱 (GLM)", "https://open.bigmodel.cn/api/paas/v4"),
    ("gemini", "Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("ollama", "Ollama", "http://localhost:11434/v1"),
    ("custom", "", ""),
)
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODES = {"overlay": "overlay", "覆盖": "overlay", "popup": "popup", "弹窗": "popup"}

DEFAULT_CONFIG = """\
; ================== 截图翻译 配置文件 ==================
; 改完保存即可，下一次截图翻译时自动生效。
; （只有 [hotkey] 里的快捷键改了以后需要重启本工具）

[api]
; 用哪家的接口：
;   claude = Claude 官方接口（功能最全：能看截图、被误拒时自动换模型重试）
;   openai = 任何“OpenAI 兼容”接口：ChatGPT、DeepSeek、通义千问、Kimi、智谱、Gemini、本地 Ollama 等
provider = claude

; API Key，直接粘贴在等号后面，不要加引号
api_key =

; 模型名。provider = claude 时可选：
;   claude-sonnet-5-5（默认，均衡）  claude-haiku-5-5（最快最便宜）  claude-opus-5-5（更好）  claude-fable-5-2（最强）  claude-fable-5-1（上一代最强）
; provider = openai 时填该平台的模型名，例如 gpt-4o、deepseek-chat、qwen-vl-max、moonshot-v1-8k、glm-4v
model = claude-sonnet-5-5

; provider = openai 时的接口地址（claude 不用填）。常见：
;   ChatGPT    https://api.openai.com/v1
;   DeepSeek   https://api.deepseek.com/v1
;   通义千问   https://dashscope.aliyuncs.com/compatible-mode/v1
;   Kimi       https://api.moonshot.cn/v1
;   智谱       https://open.bigmodel.cn/api/paas/v4
;   Gemini     https://generativelanguage.googleapis.com/v1beta/openai
;   Ollama     http://localhost:11434/v1        （本地模型，api_key 随便填）
base_url =

; 模型能不能看截图：auto = 自动探测（推荐），true / false = 强制。不能看图的模型只靠 OCR 文字翻译
vision = auto

; 思考强度：在托盘菜单里选最方便。Claude 可填 low（最快，推荐）/ medium / high / xhigh / max；其它公司各有各的档位，留空 = 用模型自己的默认
effort = low

; 网络代理，一般留空（会自动使用系统代理）。需要时填写，例如 http://127.0.0.1:7890
proxy =

[hotkey]
; 框选截图并翻译（~ 指 Esc 下面、数字 1 左边那个键，不用按 Shift）
capture = Alt+~
; 切换显示模式：覆盖在原文上 <-> 弹窗显示文字（并复制到剪贴板）
toggle_mode = Ctrl+Alt+Y

[display]
; 启动时的默认显示模式：
;   overlay = 译文直接覆盖在截图原文的位置上
;   popup   = 弹窗显示纯文字译文，并自动复制到剪贴板
mode = overlay
""" + "\n" + """\
[options]
""" + "%OPTION_BLOCKS%"

# one block per [options] key: whatever an older config file lacks gets appended (never changing what the user has)
OPTION_BLOCKS = [
    ("target_language", """\
; 译文语言：默认中文。想翻成别的语言就直接写，例如 English、日本語、한국어、Français、繁體中文
; 原文是什么语言都行（英文、中文、日文……）。目标语言不是中文时，框到的中文也会翻
target_language = 中文
"""),
    ("ui_language", """\
; 界面语言：auto = 跟随 Windows 的显示语言（不支持的语言用英文）。也可以写 简体中文 / 繁體中文 / English / 日本語 / 한국어
; 托盘菜单里也能切换。切换后这个文件里的注释也会跟着换成中文或英文
ui_language = auto
"""),
    ("skip_chinese", """\
; 框选的内容本来就是译文语言时（例如目标是中文，框到的也是中文），不调用 API（省钱）。如果判断错了，改成 false
skip_chinese = true
"""),
    ("source_language", """\
; 原文语言：auto = 自动判断（英文 / 日文），ja = 总是按日文处理，en = 总是按英文处理
; 日文不需要额外安装什么：AI 直接从截图读日文。装了 Windows 的日语 OCR 语言包只是让译文的位置更准
; （设置 → 时间和语言 → 语言和区域 → 添加语言 → 日本語，勾选“光学字符识别”），可装可不装。
source_language = auto
"""),
    ("save_history", """\
; 翻译记录：true = 把每次的原文和译文追加到本文件夹的 翻译记录.txt（密钥会自动打码）
; 默认 false：不记录任何内容。想回看历史再打开。
save_history = false
"""),
    ("save_images", """\
; 保存译文图片：true = 每次翻译后把贴好译文的截图存到本文件夹的 保存的译文\\ 里（按日期分文件夹）
; 默认 false：不保存。注意打开后文件会越积越多，记得清理。
save_images = false
"""),
]
OPTIONS_SECTION = "[options]\n" + "".join(block for _, block in OPTION_BLOCKS)
DEFAULT_CONFIG = DEFAULT_CONFIG.replace("[options]\n%OPTION_BLOCKS%", OPTIONS_SECTION)

# ---------- English template: same keys and order as the Chinese one, only the comments differ ----------
DEFAULT_CONFIG_EN = """\
; ================== Metaphrasis configuration ==================
; Save the file and the next translation picks it up.
; (Only the hotkeys under [hotkey] need a restart.)

[api]
; Which API to use:
;   claude = the official Claude API (full feature set: sees the screenshot, switches model automatically after a refusal)
;   openai = any "OpenAI-compatible" API: ChatGPT, DeepSeek, Qwen, Kimi, GLM, Gemini, local Ollama, ...
provider = claude

; API key: paste it right after the equals sign, no quotes
api_key =

; Model name. With provider = claude, one of:
;   claude-sonnet-5-5 (default, balanced)  claude-haiku-5-5 (fastest, cheapest)  claude-opus-5-5 (better)  claude-fable-5-2 (best)  claude-fable-5-1 (previous best)
; With provider = openai, the platform's model name, e.g. gpt-4o, deepseek-chat, qwen-vl-max, moonshot-v1-8k, glm-4v
model = claude-sonnet-5-5

; Endpoint for provider = openai (leave empty for claude). Common ones:
;   ChatGPT    https://api.openai.com/v1
;   DeepSeek   https://api.deepseek.com/v1
;   Qwen       https://dashscope.aliyuncs.com/compatible-mode/v1
;   Kimi       https://api.moonshot.cn/v1
;   GLM        https://open.bigmodel.cn/api/paas/v4
;   Gemini     https://generativelanguage.googleapis.com/v1beta/openai
;   Ollama     http://localhost:11434/v1        (local model; any api_key will do)
base_url =

; Can the model see the screenshot: auto = detect (recommended), true / false = force. Models without vision translate from OCR text only
vision = auto

; Thinking effort: easiest to pick from the tray menu. Claude takes low (fastest, recommended) / medium / high / xhigh / max; other providers have their own choices, empty = the model's own default
effort = low

; Network proxy. Usually empty (the system proxy is used). Set it if needed, e.g. http://127.0.0.1:7890
proxy =

[hotkey]
; Select a screen region and translate (~ is the key below Esc, left of 1; no Shift needed)
capture = Alt+~
; Switch display mode: overlay on the original <-> popup text (also copied to the clipboard)
toggle_mode = Ctrl+Alt+Y

[display]
; Default display mode at startup:
;   overlay = the translation is drawn over the original text in the screenshot
;   popup   = the translation is shown as plain text in a popup and copied to the clipboard
mode = overlay

[options]
""" + "%OPTION_BLOCKS%"

OPTION_BLOCKS_EN = [
    ("target_language", """\
; Target language: Chinese by default. Write any other language, e.g. English, 日本語, 한국어, Français, 繁體中文
; The source can be any language (English, Chinese, Japanese, ...). When the target is not Chinese, Chinese selections are translated too
target_language = English
"""),
    ("ui_language", """\
; Interface language: auto = follow the Windows display language (English for unsupported languages). Or write 简体中文 / 繁體中文 / English / 日本語 / 한국어
; You can also switch it from the tray menu. The comments in this file switch between Chinese and English with it
ui_language = auto
"""),
    ("skip_chinese", """\
; When the selection is already in the target language (e.g. target Chinese and the selection is Chinese), skip the API call (saves money). Set false if it misjudges
skip_chinese = true
"""),
    ("source_language", """\
; Source language: auto = detect (English / Japanese), ja = always treat as Japanese, en = always treat as English
; Nothing extra is needed for Japanese: the model reads it from the screenshot. The Windows Japanese OCR pack only makes placement more accurate
; (Settings → Time & language → Language & region → Add a language → 日本語, tick "Optical character recognition"); optional.
source_language = auto
"""),
    ("save_history", """\
; History: true = append every source and translation to 翻译记录.txt in this folder (keys are masked)
; Default false: nothing is recorded. Turn it on if you want to look back.
save_history = false
"""),
    ("save_images", """\
; Save translated images: true = after each translation save the rendered screenshot under 保存的译文\\ in this folder (one subfolder per day)
; Default false: nothing is saved. Note the folder keeps growing; clean it out now and then.
save_images = false
"""),
]
OPTIONS_SECTION_EN = "[options]\n" + "".join(block for _, block in OPTION_BLOCKS_EN)
DEFAULT_CONFIG_EN = DEFAULT_CONFIG_EN.replace("[options]\n%OPTION_BLOCKS%", OPTIONS_SECTION_EN)
assert [k for k, _ in OPTION_BLOCKS] == [k for k, _ in OPTION_BLOCKS_EN]

# Chinese comments written by older versions (different from the current template): recognized when switching to English
_LEGACY_ZH_BLOCKS = {
    "model": [["; 模型名。provider = claude 时可选：",
               ";   claude-sonnet-5-5（默认，均衡）  claude-haiku-5-5（最快最便宜）  claude-opus-5-5（更好）  claude-fable-5-1（最强最贵）",
               "; provider = openai 时填该平台的模型名，例如 gpt-4o、deepseek-chat、qwen-vl-max、moonshot-v1-8k、glm-4v"]],
    "ui_language": [["; 界面语言：auto = 跟随 Windows 的显示语言（不支持的语言用英文）。也可以写 简体中文 / 繁體中文 / English / 日本語 / 한국어",
                     "; 托盘菜单里也能切换。配置文件里的这些注释始终是中文"]],
    "skip_chinese": [["; 框选的内容本来就是中文时，不调用 API（省钱）。如果判断错了，改成 false"]],
    "source_language": [["; 原文语言：auto = 自动判断（英文 / 日文），ja = 总是按日文处理，en = 总是按英文处理",
                         "; 日文不需要额外安装什么：Claude 直接从截图读日文。装了 Windows 的日语 OCR 语言包只是让译文的位置更准",
                         "; （设置 → 时间和语言 → 语言和区域 → 添加语言 → 日本語，勾选“光学字符识别”），可装可不装。"]],
    "save_history": [["; 翻译记录：true = 把每次的原文和译文追加到本文件夹的 翻译记录.txt（密钥会自动打码）",
                      "; （你之前的版本一直在记录，升级后保持开启；不想要就改成 false）"]],
}
_LEGACY_EN_BLOCKS = {
    "model": [["; Model name. With provider = claude, one of:",
               ";   claude-sonnet-5-5 (default, balanced)  claude-haiku-5-5 (fastest, cheapest)  claude-opus-5-5 (better)  claude-fable-5-1 (best, most expensive)",
               "; With provider = openai, the platform's model name, e.g. gpt-4o, deepseek-chat, qwen-vl-max, moonshot-v1-8k, glm-4v"]],
    "save_history": [["; History: true = append every source and translation to 翻译记录.txt in this folder (keys are masked)",
                      "; (your previous version always recorded; kept on after the upgrade, set false if you don't want it)"]],
}
_UPGRADE_NOTE = {"zh": ("; 默认 false：不记录任何内容。想回看历史再打开。", "; （你之前的版本一直在记录，升级后保持开启；不想要就改成 false）"),
                 "en": ("; Default false: nothing is recorded. Turn it on if you want to look back.", "; (your previous version always recorded; kept on after the upgrade, set false if you don't want it)")}


def config_language(code=None):
    """Which language the config comments use: Chinese for a Chinese interface (Simplified or Traditional), English otherwise. Without code, the current interface language."""
    code = code or i18n.current()
    return "zh" if code in ("zh", "zh-tw") else "en"


def comment_language(text):
    """Are the built-in comments in a config.ini Chinese or English?
    First look for lines identical to the Chinese / English templates; else compare character counts; with no comments at all, use the interface language.
    """
    lines = {l.rstrip() for l in text.splitlines() if l.lstrip().startswith(";")}
    if not lines:
        return config_language()
    zh_lines = {x for block in _comment_blocks(DEFAULT_CONFIG).values() for x in block}
    zh_lines |= {x for blocks in _LEGACY_ZH_BLOCKS.values() for block in blocks for x in block}
    en_lines = {x for block in _comment_blocks(DEFAULT_CONFIG_EN).values() for x in block}
    en_lines |= {x for blocks in _LEGACY_EN_BLOCKS.values() for block in blocks for x in block}
    zh, en = len(lines & zh_lines), len(lines & en_lines)
    if zh or en:
        return "zh" if zh >= en else "en"
    joined = "\n".join(lines)
    cjk, latin = len(re.findall(r"[\u4e00-\u9fff]", joined)), len(re.findall(r"[A-Za-z]", joined))
    return "zh" if cjk >= 0.3 * latin else "en"


def _option_blocks(lang):
    return OPTION_BLOCKS if lang == "zh" else OPTION_BLOCKS_EN


def default_config(lang, target=None):
    """Contents of a fresh config file in the given language. target: default target language (the English template says English; can be 日本語 etc.)."""
    text = DEFAULT_CONFIG if lang == "zh" else DEFAULT_CONFIG_EN
    if lang != "zh" and target:
        text = text.replace("target_language = English", f"target_language = {target}", 1)
    return text


_ANCHOR = re.compile(r"^\s*(?:\[(?P<section>[^\]]+)\]|(?P<key>[A-Za-z_]\w*)\s*=)")


def _comment_blocks(text):
    """Comment lines directly above each key (and at the top of the file) in a template or user file: {"__top__" | key: [lines]}."""
    lines = text.splitlines()
    blocks, top = {}, []
    for l in lines:
        if l.lstrip().startswith(";"):
            top.append(l.rstrip())
        else:
            break
    blocks["__top__"] = top
    for i, l in enumerate(lines):
        m = _ANCHOR.match(l)
        if not m or not m.group("key"):
            continue
        j = i
        while j > 0 and lines[j - 1].lstrip().startswith(";"):
            j -= 1
        blocks[m.group("key")] = [x.rstrip() for x in lines[j:i]]
    return blocks


# wording of earlier versions, kept so that a comment-language switch still recognises it
for _legacy, _template, _old in ((_LEGACY_ZH_BLOCKS, DEFAULT_CONFIG, {';   DeepSeek   https://api.deepseek.com/v1': ';   DeepSeek   https://api.deepseek.com/v1      （不支持传图，只能靠 OCR 文字翻译）', '; 思考强度：在托盘菜单里选最方便。Claude 可填 low（最快，推荐）/ medium / high / xhigh / max；其它公司各有各的档位，留空 = 用模型自己的默认': '; 思考深度（只对 Claude 有效）：low（最快，推荐）/ medium / high'}),
                                 (_LEGACY_EN_BLOCKS, DEFAULT_CONFIG_EN, {';   DeepSeek   https://api.deepseek.com/v1': ';   DeepSeek   https://api.deepseek.com/v1      (no image input; translates from OCR text only)', "; Thinking effort: easiest to pick from the tray menu. Claude takes low (fastest, recommended) / medium / high / xhigh / max; other providers have their own choices, empty = the model's own default": '; Thinking depth (Claude only): low (fastest, recommended) / medium / high'})):
    for _key in ("base_url", "effort"):
        _legacy.setdefault(_key, []).append([_old.get(line, line) for line in _comment_blocks(_template)[_key]])


def localize_config_comments(lang):
    """Switch the built-in comments in config.ini to Chinese or English (lang = "zh" / "en").
    Only blocks identical to the template (or an older template) are replaced; user comments, all values, order, encoding and line endings stay. Returns the number of blocks changed.
    """
    if not CONFIG_PATH.exists():
        return 0
    raw = CONFIG_PATH.read_bytes()
    bom = raw.startswith(codecs.BOM_UTF8)
    text = read_text(CONFIG_PATH)
    src_lang = "en" if lang == "zh" else "zh"
    src = _comment_blocks(default_config(src_lang))
    dst = _comment_blocks(default_config(lang))
    legacy = _LEGACY_ZH_BLOCKS if src_lang == "zh" else _LEGACY_EN_BLOCKS
    # split each line into content + line ending, keeping the ending as is (\n, \r\n, even \r\r\n)
    parts = text.split("\n")
    ends = ["\r\n" if p.endswith("\r") else "\n" for p in parts]
    parts = [p.rstrip("\r") for p in parts]
    if text.endswith("\n"):
        parts.pop(); ends.pop()
    else:
        ends[-1] = ""
    nl = "\r\n" if "\r\n" in text else "\n"

    def is_comment(x):
        return x.lstrip().startswith(";")

    changed = 0
    k = 0  # banner at the top of the file
    while k < len(parts) and is_comment(parts[k]):
        k += 1
    if k and [x.rstrip() for x in parts[:k]] == src["__top__"] and dst.get("__top__"):
        e = ends[0] or nl
        parts[:k] = list(dst["__top__"]); ends[:k] = [e] * len(dst["__top__"])
        changed += 1
    i = 0  # comment block directly above each key
    while i < len(parts):
        m = _ANCHOR.match(parts[i])
        key = m.group("key") if m else None
        if key and key in dst:
            j = i
            while j > 0 and is_comment(parts[j - 1]):
                j -= 1
            block = [x.rstrip() for x in parts[j:i]]
            candidates = ([src[key]] if key in src else []) + legacy.get(key, [])
            if block and block in candidates:
                e = ends[j] or nl
                parts[j:i] = list(dst[key]); ends[j:i] = [e] * len(dst[key])
                i = j + len(dst[key])
                changed += 1
        i += 1
    if changed:
        out = "".join(c + e for c, e in zip(parts, ends))
        CONFIG_PATH.write_bytes((codecs.BOM_UTF8 if bom else b"") + out.encode("utf-8"))
    return changed


class ConfigError(Exception):
    pass


_LANG_ALIASES = {
    "zh": ("zh", "zh-cn", "zh-hans", "cn", "中文", "简体中文", "简体", "汉语", "中国语", "chinese", "simplified chinese", "mandarin"),
    "zh-tw": ("zh-tw", "zh-hk", "zh-hant", "繁体中文", "繁體中文", "繁体", "繁體", "traditional chinese"),
    "en": ("en", "en-us", "en-gb", "english", "英文", "英语", "英語"),
    "ja": ("ja", "jp", "ja-jp", "japanese", "日文", "日语", "日本語", "日本语"),
    "ko": ("ko", "kr", "ko-kr", "korean", "韩文", "韩语", "韓文", "한국어", "朝鲜语"),
}
_LANG_LABELS = {"zh": "简体中文", "zh-tw": "繁體中文", "en": "英文（English）", "ja": "日文（日本語）", "ko": "韩文（한국어）"}


def lang_code(name):
    """Normalize a target language the user typed to a code: zh / zh-tw / en / ja / ko; anything else is "other" (translated by the name as written)."""
    key = (name or "").strip().lower()
    for code, names in _LANG_ALIASES.items():
        if key in names:
            return code
    return "other"


def lang_label(name):
    """Language name for the prompt: a fixed form for known languages, otherwise as the user wrote it."""
    return _LANG_LABELS.get(lang_code(name), (name or "").strip() or "简体中文")


@dataclass
class Config:
    api_key: str
    model: str
    effort: str
    proxy: str
    capture_hotkey: str
    toggle_hotkey: str
    mode: str
    skip_chinese: bool = True
    source_language: str = "auto"  # auto / en / ja
    target_language: str = "中文"
    ui_language: str = "auto"
    save_history: bool = False  # nothing is kept by default; the user opts in
    save_images: bool = False
    provider: str = "claude"       # the protocol: claude / openai
    base_url: str = ""
    vision: str = "auto"           # auto / true / false
    company: str = "claude"        # which entry of COMPANIES this is (worked out from provider and base_url)


def scrub_anthropic_env():
    """This tool only uses config.ini: remove every ANTHROPIC_* variable from this process.

    Affects only this process; system environment variables and Claude Code are untouched.
    """
    for key in list(os.environ):
        if key.upper().startswith("ANTHROPIC_"):
            os.environ.pop(key, None)


def read_text(path):
    """Read a user-edited text file: UTF-8 (with or without BOM), UTF-16 or GBK."""
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def ensure_files():
    if not CONFIG_PATH.exists():  # first run: comments in the system language (Chinese / English), target language defaults to the system language
        sys_lang = i18n.system_language()
        CONFIG_PATH.write_text(default_config(config_language(sys_lang), i18n.NAMES.get(sys_lang)), encoding="utf-8")


def load_config():
    ensure_files()
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";", "#"),
                                       inline_comment_prefixes=None, strict=False)
    text = read_text(CONFIG_PATH)
    addition = ""
    lang = comment_language(text)  # appended explanations match the language of the comments already in the file
    blocks = _option_blocks(lang)
    if "[options]" not in text:  # old config without [options]: append the whole section, leave the rest alone
        addition = "\n[options]\n" + "".join(block for _, block in blocks)
    else:  # [options] exists but some keys are missing: append just those
        for key, block in blocks:
            if key not in text:
                if key == "save_history":  # upgrading users were recording all along: keep it on, they can turn it off
                    block = block.replace("save_history = false", "save_history = true").replace(*_UPGRADE_NOTE[lang])
                addition += block
    if addition:
        try:
            with open(CONFIG_PATH, "a", encoding="utf-8") as f:
                f.write(("" if text.endswith("\n") else "\n") + addition)
            text = read_text(CONFIG_PATH)
        except OSError:
            pass
    try:
        parser.read_string(text)
    except configparser.Error as e:
        raise ConfigError(t("config.ini 格式有误，请检查：\n{e}", e=e)) from e

    def get(section, key, default=""):
        return parser.get(section, key, fallback=default).strip().strip('"').strip("'").strip()

    sec = "api" if parser.has_section("api") else "claude"  # old config files used [claude]
    provider = get(sec, "provider", "claude").lower()
    if provider not in ("claude", "openai"):
        raise ConfigError(t("config.ini 里的 provider 填写有误：{provider}\n可选：claude、openai", provider=provider))
    model = get(sec, "model")
    base_url = get(sec, "base_url")
    if provider == "claude":
        model = model or DEFAULT_MODEL
        if not model.startswith("claude-"):
            raise ConfigError(t("config.ini 里的 model 填写有误：{model}\n可选：{models}", model=model, models="、".join(SUPPORTED_MODELS)))
    else:
        if not base_url:
            base_url = "https://api.openai.com/v1"
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError(t("config.ini 里的 base_url 要以 http:// 或 https:// 开头：{url}", url=base_url))
        if base_url.startswith("http://") and not _is_local_address(base_url):
            raise ConfigError(t("config.ini 里的 base_url 用的是不加密的 http://，API Key 和截图会明文传出去。"
                                "请改成 https://（只有本机和局域网地址才允许 http）：{url}", url=base_url))
    vision = get(sec, "vision", "auto").lower()
    if vision not in ("auto", "true", "false"):
        vision = "auto"
    company = company_of(provider, base_url)
    effort = get(sec, "effort").lower()
    if effort not in thinking_levels(company):  # each company has its own choices
        effort = default_effort(company)
    mode = MODES.get(get("display", "mode", "overlay").lower(), "overlay")

    def flag(key, default="true"):
        return get("options", key, default).lower() not in ("false", "no", "off", "0", "否", "关")

    return Config(
        api_key=get(sec, "api_key"),
        model=model,
        effort=effort,
        proxy=get(sec, "proxy"),
        provider=provider,
        base_url=base_url,
        vision=vision,
        company=company,
        capture_hotkey=get("hotkey", "capture", "Alt+~") or "Alt+~",
        toggle_hotkey=get("hotkey", "toggle_mode", "Ctrl+Alt+Y") or "Ctrl+Alt+Y",
        mode=mode,
        skip_chinese=flag("skip_chinese"),
        source_language=(get("options", "source_language", "auto").lower() if get("options", "source_language", "auto").lower() in ("auto", "en", "ja") else "auto"),
        target_language=(get("options", "target_language", "中文") or "中文"),
        ui_language=(get("options", "ui_language", "auto") or "auto"),
        save_history=flag("save_history", "false"),
        save_images=flag("save_images", "false"),
    )


def company_of(provider, base_url):
    """Which entry of COMPANIES a provider / endpoint pair is."""
    if provider == "claude":
        return "claude"
    url = (base_url or "https://api.openai.com/v1").strip().rstrip("/").lower()
    for cid, _, preset in COMPANIES:
        if preset and preset.rstrip("/").lower() == url:
            return cid
    return "custom"


def company_name(cid):
    return next((t(name) for c, name, _ in COMPANIES if c == cid and name), t("自定义接口"))  # the two Chinese names have translations


def model_problem(cfg):
    """A message if no model is chosen yet (an OpenAI-compatible provider has no default), else None."""
    if not cfg.model:
        return t("还没有选择模型。\n\n请右键托盘图标，在“模型”里选一个；列表里没有的，可以在 config.ini 的 model 后面直接填写。")
    return None


def url_problem(url):
    """A message if an endpoint address typed by the user cannot be used, else None."""
    if not url.startswith(("http://", "https://")):
        return t("config.ini 里的 base_url 要以 http:// 或 https:// 开头：{url}", url=url)
    if url.startswith("http://") and not _is_local_address(url):
        return t("config.ini 里的 base_url 用的是不加密的 http://，API Key 和截图会明文传出去。"
                 "请改成 https://（只有本机和局域网地址才允许 http）：{url}", url=url)
    return None


# ---------- editing config.ini in place (tray menu) ----------
_HEADER = re.compile(r"^[ \t]*\[([^\]\r\n]+)\][ \t]*\r*$", re.M)  # \r*: files that went through a careless editor can end lines in \r\r\n
_SAVED_NOTE = {"zh": "; 由程序保存：这家公司的 Key 和模型（现在没在用）。在托盘菜单“接口公司”里可以切换回来",
               "en": "; Kept by the program: this provider's key and model while another one is in use. Switch back from the tray menu"}


def _read_config():
    raw = CONFIG_PATH.read_bytes() if CONFIG_PATH.exists() else b""
    text = read_text(CONFIG_PATH) if raw else default_config(config_language())
    return text, raw.startswith(codecs.BOM_UTF8), "\r\n" if "\r\n" in text else "\n"


def _write_config(text, bom):
    CONFIG_PATH.write_bytes((codecs.BOM_UTF8 if bom else b"") + text.encode("utf-8"))


def _find(text, name):
    """(start of the header line, start of the body, end) of a section, or None."""
    found = list(_HEADER.finditer(text))
    for i, m in enumerate(found):
        if m.group(1).strip().lower() == name.lower():
            body = m.end() + (1 if text[m.end():m.end() + 1] == "\n" else 0)
            return m.start(), body, found[i + 1].start() if i + 1 < len(found) else len(text)
    return None


def _get_in(text, section, key):
    """Value of a key inside one section (None when the section or the key is missing)."""
    span = _find(text, section)
    if not span:
        return None
    m = re.search(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*([^\r\n]*)", text[span[1]:span[2]], re.M)
    return m.group(1).strip().strip('"').strip("'").strip() if m else None


def _set_in(text, section, key, value, nl):
    """Set a key inside one section: that line changes (or is added at the end of the section), nothing else does. A missing section is added at the end of the file."""
    line = f"{key} = {value}".rstrip()
    span = _find(text, section)
    if not span:
        return (text.rstrip("\r\n") + nl + nl if text.strip() else "") + f"[{section}]" + nl + line + nl
    head, body = text[:span[1]], text[span[1]:span[2]]
    if not head.endswith("\n"):
        head += nl
    m = re.search(rf"^[ \t]*{re.escape(key)}[ \t]*=[^\r\n]*", body, re.M)
    if m:
        body = body[:m.start()] + line + body[m.end():]
    else:
        kept = body.rstrip("\r\n \t")
        body = kept + (nl if kept else "") + line + (body[len(kept):] or nl)
    return head + body + text[span[2]:]


def _remove_section(text, name):
    span = _find(text, name)
    return text if not span else text[:span[0]] + text[span[2]:]


def _add_section_after(text, anchor, name, lines, nl):
    """Insert a new section right after another one (so [options] stays the last section: missing options are appended at the end of the file)."""
    span = _find(text, anchor)
    pos = span[2] if span else len(text)
    head = text[:pos]
    if head.strip() and not head.endswith("\n"):
        head += nl
    if head.strip() and not head.endswith(nl + nl):
        head += nl
    return head + f"[{name}]" + nl + "".join(l + nl for l in lines) + nl + text[pos:]


def _active_section(text):
    return "api" if _find(text, "api") else "claude" if _find(text, "claude") else None


def set_api_value(key, value):
    """Change one key of the API settings in use (api_key, model, effort): only that line changes."""
    text, bom, nl = _read_config()
    _write_config(_set_in(text, _active_section(text) or "api", key, value, nl), bom)


def saved_companies():
    """The companies, other than the one in use, that have an API key on file."""
    text, _, _ = _read_config()
    return {cid for cid, _, _ in COMPANIES if _get_in(text, "saved." + cid, "api_key")}


def switch_company(target, api_key=None, base_url=None):
    """Make another company the one in use (tray menu).

    The outgoing company's key and model are put aside in a [saved.<company>] section and the incoming one's are taken out of its section again, so [api] always holds exactly the settings in use and no key is ever sent to a company it does not belong to. api_key / base_url: values the user just typed in for the target.
    """
    text, bom, nl = _read_config()
    section = _active_section(text)
    if section == "claude":  # layout of the first versions: the API settings lived in [claude]
        span = _find(text, "claude")
        text = text[:span[0]] + re.sub(r"\[[^\]]+\]", "[api]", text[span[0]:span[1]], count=1) + text[span[1]:]
    elif section is None:
        text = _set_in(text, "api", "provider", "claude", nl)
    provider = (_get_in(text, "api", "provider") or "claude").lower()
    current = company_of(provider, _get_in(text, "api", "base_url") or "")
    if current != target:
        keep = {"api_key": _get_in(text, "api", "api_key") or "", "model": _get_in(text, "api", "model") or ""}
        effort = (_get_in(text, "api", "effort") or "").lower()
        if effort in thinking_levels(current) and effort != default_effort(current):
            keep["effort"] = effort  # the choices differ from company to company, so each keeps its own
        if current == "custom":
            keep["base_url"] = _get_in(text, "api", "base_url") or ""
        if any(keep.values()):  # put the outgoing company's settings aside
            text = _remove_section(text, "saved." + current)
            lines = [_SAVED_NOTE[comment_language(text)]] + [f"{k} = {v}".rstrip() for k, v in keep.items()]
            text = _add_section_after(text, "api", "saved." + current, lines, nl)
        saved = "saved." + target
        new_key = api_key if api_key is not None else (_get_in(text, saved, "api_key") or "")
        new_model = _get_in(text, saved, "model") or ""
        new_effort = (_get_in(text, saved, "effort") or "").lower()
        if new_effort not in thinking_levels(target):
            new_effort = default_effort(target)
        new_url = base_url if base_url is not None else (_get_in(text, saved, "base_url") or "")
        text = _remove_section(text, saved)
        if target == "claude":
            new_model = new_model or DEFAULT_MODEL
        elif target != "custom":
            new_url = next(url for cid, _, url in COMPANIES if cid == target)
        if target == "ollama" and not new_key:
            new_key = "ollama"  # a local Ollama takes any key
        for key, value in (("provider", "claude" if target == "claude" else "openai"), ("api_key", new_key),
                           ("model", new_model), ("base_url", "" if target == "claude" else new_url), ("effort", new_effort)):
            text = _set_in(text, "api", key, value, nl)
    else:
        if api_key is not None:
            text = _set_in(text, "api", "api_key", api_key, nl)
        if base_url is not None and target == "custom":
            text = _set_in(text, "api", "base_url", base_url, nl)
    _write_config(text, bom)


def set_option(key, value):
    """Change one key under [options] in config.ini (used by the tray menu). Only that line changes; everything else, comments, encoding and line endings stay."""
    raw = CONFIG_PATH.read_bytes() if CONFIG_PATH.exists() else b""
    bom = raw.startswith(codecs.BOM_UTF8)
    text = read_text(CONFIG_PATH) if raw else DEFAULT_CONFIG
    nl = "\r\n" if "\r\n" in text else "\n"
    line = f"{key} = {value}"
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=[^\r\n]*", re.M)
    if pattern.search(text):
        text = pattern.sub(lambda m: line, text, count=1)
    elif "[options]" in text:
        text = text.replace("[options]", "[options]" + nl + line, 1)
    else:
        text = text.rstrip("\r\n") + nl + nl + "[options]" + nl + line + nl
    CONFIG_PATH.write_bytes((codecs.BOM_UTF8 if bom else b"") + text.encode("utf-8"))


def _is_local_address(url):
    """Is the host this machine or on the local network? Plain http is only acceptable there."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local", ".lan")) or ("." not in host and ":" not in host):
        return True  # single-label names are LAN names
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def api_key_problem(cfg):
    """A message if the API key is obviously wrong, else None."""
    key = cfg.api_key
    if not key:
        return t("还没有填写 API Key。\n\n请打开本工具文件夹里的 config.ini，把 API Key 粘贴到 “api_key =” 后面，保存即可，不需要重启。")
    if not key.isascii() or any(c.isspace() for c in key):
        return t("config.ini 里的 api_key 含有空格或中文字符，请检查是否粘贴完整、正确。")
    return None


def save_image(img):
    """Save the rendered screenshot as 保存的译文/YYYY-MM-DD/HHMMSS.png; returns the path."""
    now = datetime.now()
    folder = IMAGES_DIR / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / now.strftime("%H%M%S.png")
    n = 1
    while path.exists():  # two translations within the same second
        n += 1
        path = folder / f"{now.strftime('%H%M%S')}_{n}.png"
    img.save(path, format="PNG")
    return path


def append_history(label, source, translation):
    """Append one history entry; API keys and similar secrets are masked to *** first."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(f"==================== {stamp}  ({label}) ====================\n")
        f.write(f"{t('【原文】')}\n{redact.redact(source).strip()}\n\n{t('【译文】')}\n{redact.redact(translation).strip()}\n\n\n")
