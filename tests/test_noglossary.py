"""去掉术语表后的检查：接口签名、提示词、缓存键，再走一次跳过流程（）。"""
import dataclasses, inspect, queue, sys, tempfile, time, types, logging
from pathlib import Path
from _app import APP
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout)
logging.getLogger("httpx2").setLevel(logging.WARNING)
from PIL import Image
import settings, translator as T, ocr_win, fanyi

HERE = Path(__file__).parent
assert not hasattr(settings, "GLOSSARY_PATH") and not hasattr(settings, "load_glossary") and not hasattr(T, "relevant_glossary")
assert list(inspect.signature(T.translate).parameters) == ["cfg", "image", "lines", "on_progress", "cancelled", "unreliable"]
for t in ("中文", "English", "日本語"):
    p = T.system_prompt(t); assert "术语表" not in p and "4. OCR 结果" in p and "5. 已经是" in p and "6." not in p, t
assert "术语表" not in T._user_text((10, 10), [ocr_win.OcrLine(0, "hi there", (0, 0, 5, 5))])
print("1 OK 代码里没有术语表了")

settings.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.ini"  # a fresh settings file: nothing is sent on this path, so no key is needed
base = settings.load_config()
en_img = Image.open(HERE / "data" / "en_sample.png"); en_img.load()  # 先读进内存：两个 OCR 线程同时碰一个懒加载的 PNG 会出错（程序里的截图本来就在内存里）

def run(cfg, img, name):
    fake = types.SimpleNamespace(events=queue.Queue(), jobs={})
    job = types.SimpleNamespace(id=1, crop=img, cfg=cfg); fake.jobs[1] = job
    t0 = time.time(); fanyi.App._run_job(fake, job, cfg)
    ev = None
    while not fake.events.empty():
        e = fake.events.get_nowait()
        if e[0] != "job_progress":
            ev = e
    print(f"   {name}: {ev[0]} {time.time()-t0:.1f}s")
    return ev

ev = run(dataclasses.replace(base, target_language="English"), en_img, "英→英")
assert ev[0] == "job_skipped", ev
print("2 OK 真实流程")
print("ALL OK")
