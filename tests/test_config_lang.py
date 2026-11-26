"""config.ini 注释中英切换的测试（不花钱）。"""
import configparser, sys, tempfile
from pathlib import Path
from _app import APP
import i18n, settings

def parse(text):
    p = configparser.ConfigParser(interpolation=None, comment_prefixes=(";", "#"), inline_comment_prefixes=None, strict=False)
    p.read_string(text); return p

# 1) 两份模板：键、顺序、默认值一致（译文语言默认值除外）
zh, en = parse(settings.DEFAULT_CONFIG), parse(settings.DEFAULT_CONFIG_EN)
assert zh.sections() == en.sections()
for sec in zh.sections():
    assert list(zh[sec]) == list(en[sec]), sec
    for k in zh[sec]:
        if k != "target_language": assert zh[sec][k] == en[sec][k], (sec, k)
assert en["options"]["target_language"] == "English" and zh["options"]["target_language"] == "中文"
bz, be = settings._comment_blocks(settings.DEFAULT_CONFIG), settings._comment_blocks(settings.DEFAULT_CONFIG_EN)
assert set(bz) == set(be) and all(bz[k] and be[k] for k in bz), set(bz) ^ set(be)
assert settings.comment_language(settings.DEFAULT_CONFIG) == "zh" and settings.comment_language(settings.DEFAULT_CONFIG_EN) == "en"
print("1 OK 两份模板结构一致，", len(bz), "个注释块")

# 2) 新文件按系统语言生成
tmp = Path(tempfile.mkdtemp())
orig = i18n.system_language
for sys_lang, want_lang, want_target in (("en", "en", "English"), ("ja", "en", "日本語"), ("ko", "en", "한국어"), ("zh", "zh", "中文"), ("zh-tw", "zh", "中文")):
    i18n.system_language = lambda l=sys_lang: l
    settings.CONFIG_PATH = tmp / f"new_{sys_lang}.ini"; c = settings.load_config()
    text = settings.CONFIG_PATH.read_text(encoding="utf-8")
    assert settings.comment_language(text) == want_lang and c.target_language == want_target, (sys_lang, c.target_language)
    assert ("Metaphrasis configuration" in text) == (want_lang == "en")
i18n.system_language = orig
print("2 OK 新文件按系统语言生成")

# 3) 中→英→中：值不动、自定义注释不动、BOM/CRLF 不动，回来后和原文一模一样
body = settings.DEFAULT_CONFIG.replace("api_key =", "api_key = sk-test-123").replace("model = claude-sonnet-5-5", "model = claude-fable-5-1") \
    .replace("save_history = false", "save_history = true").replace("proxy =\n", "proxy =\n\n; 我自己加的备注，别动\n")
body = "\ufeff" + body.replace("\n", "\r\n")
(tmp / "user.ini").write_bytes(body.encode("utf-8"))
settings.CONFIG_PATH = tmp / "user.ini"
n = settings.localize_config_comments("en")
raw = settings.CONFIG_PATH.read_bytes(); txt = raw.decode("utf-8-sig")
assert n == len(bz), n
assert raw.startswith(b"\xef\xbb\xbf") and "\r\n" in txt and "\r\r" not in txt
assert "; 我自己加的备注，别动" in txt and "api_key = sk-test-123" in txt and "model = claude-fable-5-1" in txt and "save_history = true" in txt
assert settings.comment_language(txt) == "en" and "Metaphrasis configuration" in txt and "; 用哪家的接口" not in txt
c = settings.load_config(); assert c.api_key == "sk-test-123" and c.model == "claude-fable-5-1" and c.save_history is True
assert settings.localize_config_comments("en") == 0  # 已经是英文：什么都不改
n2 = settings.localize_config_comments("zh"); assert n2 == len(bz), n2
back = settings.CONFIG_PATH.read_bytes().decode("utf-8-sig")
assert back == body.lstrip("\ufeff"), [l for l in back.splitlines() if l not in body.splitlines()][:3]
print("3 OK 中英来回切换，值和自定义注释不动")

# 4) 老版本的中文注释也认得出来
old = ("[api]\napi_key = x\n[options]\n"
       "; 框选的内容本来就是中文时，不调用 API（省钱）。如果判断错了，改成 false\nskip_chinese = true\n"
       "; 原文语言：auto = 自动判断（英文 / 日文），ja = 总是按日文处理，en = 总是按英文处理\n"
       "; 日文不需要额外安装什么：Claude 直接从截图读日文。装了 Windows 的日语 OCR 语言包只是让译文的位置更准\n"
       "; （设置 → 时间和语言 → 语言和区域 → 添加语言 → 日本語，勾选“光学字符识别”），可装可不装。\nsource_language = auto\n"
       "; 翻译记录：true = 把每次的原文和译文追加到本文件夹的 翻译记录.txt（密钥会自动打码）\n"
       "; （你之前的版本一直在记录，升级后保持开启；不想要就改成 false）\nsave_history = true\n"
       "; 界面语言：auto = 跟随 Windows 的显示语言（不支持的语言用英文）。也可以写 简体中文 / 繁體中文 / English / 日本語 / 한국어\n"
       "; 托盘菜单里也能切换。配置文件里的这些注释始终是中文\nui_language = auto\n")
(tmp / "old.ini").write_text(old, encoding="utf-8"); settings.CONFIG_PATH = tmp / "old.ini"
n = settings.localize_config_comments("en"); txt = settings.CONFIG_PATH.read_text(encoding="utf-8")
assert n == 4 and "skip the API call" in txt and "Source language:" in txt and "History:" in txt and "Interface language:" in txt, (n, txt)
assert "save_history = true" in txt and "api_key = x" in txt
print("4 OK 老版本注释也能换")

# 5) 升级补块跟着文件的注释语言
(tmp / "up_en.ini").write_text("[api]\napi_key = x\n[options]\n; Target language: whatever\ntarget_language = English\n", encoding="utf-8")
settings.CONFIG_PATH = tmp / "up_en.ini"; settings.load_config(); txt = settings.CONFIG_PATH.read_text(encoding="utf-8")
assert "; Interface language:" in txt and "; History:" in txt and "kept on after the upgrade" in txt and "; 界面语言" not in txt
(tmp / "up_zh.ini").write_text("[claude]\napi_key = x\n[options]\n; 译文语言：默认中文。想翻成别的语言就直接写\ntarget_language = 中文\n", encoding="utf-8")
settings.CONFIG_PATH = tmp / "up_zh.ini"; settings.load_config(); txt = settings.CONFIG_PATH.read_text(encoding="utf-8")
assert "; 界面语言" in txt and "升级后保持开启" in txt and "; Interface language:" not in txt
print("5 OK 升级补块按语言")
print("ALL OK")
