"""保留选项测试：新配置默认关闭；旧配置升级时 save_history 保持开启、save_images 关闭；save_image 存到日期文件夹。"""
import sys, tempfile, re
from pathlib import Path
from _app import APP
import settings
from PIL import Image

tmp = Path(tempfile.mkdtemp())
assert "save_history = false" in settings.DEFAULT_CONFIG and "save_images = false" in settings.DEFAULT_CONFIG
assert settings.OPTIONS_SECTION.startswith("[options]\n; 译文语言")
assert "%OPTION_BLOCKS%" not in settings.DEFAULT_CONFIG

# 1) 全新配置：默认全关
settings.CONFIG_PATH = tmp / "new.ini"
c = settings.load_config()
assert c.save_history is False and c.save_images is False and c.source_language == "auto"
txt = (tmp / "new.ini").read_text(encoding="utf-8")
assert txt.count("save_history") == 1 and txt.count("save_images") == 1
print("1 OK 新配置默认不保留")

# 2) 旧配置（有 [options] 但缺新项）：补齐，save_history 保持 true
old = "[claude]\napi_key = x\nmodel = claude-haiku-5-5\n[options]\nuse_cache = true\nskip_chinese = true\n"
(tmp / "old.ini").write_text(old, encoding="utf-8")
settings.CONFIG_PATH = tmp / "old.ini"
c = settings.load_config()
txt = (tmp / "old.ini").read_text(encoding="utf-8")
assert c.save_history is True and c.save_images is False and c.source_language == "auto", (c.save_history, c.save_images)
assert "升级后保持开启" in txt and txt.count("save_history = true") == 1 and "save_images = false" in txt and "source_language = auto" in txt
c2 = settings.load_config(); assert (tmp / "old.ini").read_text(encoding="utf-8") == txt  # 第二次不再追加
print("2 OK 旧配置升级：记录保持开启，其余补齐，不重复")

# 3) 没有 [options] 的更老配置：整段补上，默认 false
(tmp / "older.ini").write_text("[claude]\napi_key = x\n", encoding="utf-8")
settings.CONFIG_PATH = tmp / "older.ini"
c = settings.load_config(); assert c.save_history is False and c.save_images is False
print("3 OK 更老的配置整段补齐，默认关闭")

# 4) 用户打开后能读到
(tmp / "on.ini").write_text("[api]\napi_key = x\n[options]\nsave_history = true\nsave_images = yes\n", encoding="utf-8")
settings.CONFIG_PATH = tmp / "on.ini"
c = settings.load_config(); assert c.save_history is True and c.save_images is True
print("4 OK 打开后生效")

# 5) save_image
settings.IMAGES_DIR = tmp / "保存的译文"
p1 = settings.save_image(Image.new("RGB", (20, 20), "red")); p2 = settings.save_image(Image.new("RGB", (20, 20), "blue"))
assert p1.exists() and p2.exists() and p1 != p2 and re.match(r"\d{4}-\d{2}-\d{2}", p1.parent.name) and p1.suffix == ".png"
print("5 OK 图片存到", p1.parent.name, p1.name, p2.name)

# 6) 主程序里的调用受开关控制（静态检查）
src = (Path(APP) / "fanyi.py").read_text(encoding="utf-8")
assert "if cfg.save_history:" in src and "if cfg.save_images and overlay is not None:" in src
print("6 OK 主程序按开关保存")
print("ALL OK")
