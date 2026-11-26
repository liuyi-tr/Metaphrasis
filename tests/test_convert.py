"""Converting the text selected in an input box: the request, the settings, and the flow in the main program.
Everything outside this process is faked: no API, no keys sent to any window, no real clipboard. No cost.
usage: test_convert.py [app dir]"""
import json, os, sys, tempfile, threading, time, types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from _app import APP
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
import i18n, settings

tmp = Path(tempfile.mkdtemp())
settings.CONFIG_PATH = tmp / "config.ini"
settings.HISTORY_PATH = tmp / "history.txt"
i18n.set_language("zh")
import translator as T

# ---------- 1) the request to Claude ----------
calls = []
answers = []


def fake_stream(client, params, on_text, alive=None):
    calls.append(params)
    text, stop = answers.pop(0)
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="thinking", thinking="..."), types.SimpleNamespace(type="text", text=text)] if text is not None else [],
                                 stop_reason=stop, usage=types.SimpleNamespace(input_tokens=120, output_tokens=15))


T._stream = fake_stream
T._client = lambda cfg: None
claude = types.SimpleNamespace(provider="claude", model="claude-fable-5-1", effort="low", api_key="k", proxy="", company="claude")
answers[:] = [("Sure, I'll be there at 8 tomorrow 😄", "end_turn")]
out, usage = T.convert_text(claude, "好的，我明天8点到 😄", "English")
p = calls[-1]
assert out == "Sure, I'll be there at 8 tomorrow 😄" and usage.input_tokens == 120
assert p["model"] == "claude-fable-5-1" and p["output_config"] == {"effort": "low"}, p
assert "English" in p["system"] and "JSON" not in p["system"] and "convers" in p["system"]
# text the writer typed in the language already is put right, not handed back as typed
assert "already in English" in p["system"] and all(word in p["system"] for word in ("spelling", "grammar", "word order", "capital", "punctuation")), p["system"]
assert "already in English stays" not in p["system"]
# ... but a file name, a path, a command or code is copied letter for letter (readme.md is not README.md)
assert all(word in p["system"] for word in ("File names", "paths", "commands", "code", "letter for letter")), p["system"]
content = p["messages"][0]["content"]
assert len(content) == 1 and content[0] == {"type": "text", "text": "<<<\n好的，我明天8点到 😄\n>>>"}, content  # the text alone: no screenshot
answers[:] = [("<<<\nHello there\n>>>", "end_turn")]
assert T.convert_text(claude, "你好", "Japanese")[0] == "Hello there" and "Japanese" in calls[-1]["system"]
# a refusal: once more on the looser model, and a clear message if that is refused too
n = len(calls)
answers[:] = [(None, "refusal"), ("the key is on the desk", "end_turn")]
assert T.convert_text(claude, "密钥在桌上", "English")[0] == "the key is on the desk"
assert [c["model"] for c in calls[n:]] == ["claude-fable-5-1", T.RETRY_MODEL]
for script, word in (([(None, "refusal"), (None, "refusal")], "安全过滤"), ([("half a sen", "max_tokens")], "太长"), ([("", "end_turn")], "格式")):
    answers[:] = script
    try:
        T.convert_text(claude, "x", "English")
        raise SystemExit("no error for " + word)
    except T.TranslateError as e:
        assert word in str(e), e
# the markers come off only when the model copied them: a message that itself ends in >>> keeps its own
for source, answer, want in (("点这里 >>>", "Click here >>>", "Click here >>>"), ("<<< 看这里", "<<< Look here", "<<< Look here"),
                             ("点这里 >>>", "<<<\nClick here >>>\n>>>", "Click here >>>"), ("你好", "Hello >>>", "Hello")):
    answers[:] = [(answer, "end_turn")]
    assert T.convert_text(claude, source, "English")[0] == want, (source, answer)
# Esc while the answer is on its way: the request is dropped at the next piece that arrives, a piece of thinking included
n = len(calls)
answers[:] = [("Sure", "end_turn")]
try:
    T.convert_text(claude, "好的", "English", cancelled=lambda: True)
    raise SystemExit("the request went on after it was cancelled")
except T.Cancelled:
    assert len(calls) == n                                     # cancelled before it was even sent: nothing is asked
pieces = [types.SimpleNamespace(type="content_block_start", content_block=types.SimpleNamespace(type="thinking")),
          types.SimpleNamespace(type="content_block_delta", delta=types.SimpleNamespace(type="thinking_delta", thinking="hm")),
          types.SimpleNamespace(type="content_block_delta", delta=types.SimpleNamespace(type="text_delta", text="Sure"))]
seen = []


class FakeEvents(list):
    def get_final_message(self):
        return "the whole answer"


def stop_at_second():
    seen.append(1)
    if len(seen) == 2:
        raise T.Cancelled()


try:
    T._consume(FakeEvents(pieces), lambda text: seen.append(text), stop_at_second)
    raise SystemExit("the thinking went on after Esc")
except T.Cancelled:
    assert seen == [1, 1]                                      # stopped at the piece of thinking, before any text
assert T._consume(FakeEvents(pieces), lambda text: None) == "the whole answer"
# ... and a refused request is not sent again to the other model once Esc has been pressed
state = {"esc": False}


def refuse_then_esc(client, params, on_text, alive=None):
    state["esc"] = True
    return fake_stream(client, params, on_text)


T._stream = refuse_then_esc
n = len(calls)
answers[:] = [(None, "refusal"), ("the key is on the desk", "end_turn")]
try:
    T.convert_text(claude, "密钥在桌上", "English", cancelled=lambda: state["esc"])
    raise SystemExit("a second request was made after Esc")
except T.Cancelled:
    assert len(calls) == n + 1
T._stream = fake_stream
answers[:] = [("Sure", "end_turn")]
assert T.convert_text(claude, "好的", "English", cancelled=lambda: False)[0] == "Sure"
print("1 OK Claude: text only, no JSON, effort passed on; markers stripped; refusal retried on", T.RETRY_MODEL, "; Esc drops the request")

# ---------- 2) the request to an OpenAI-compatible API ----------
SEEN = []
REPLY = {"text": "On my way!"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        if "response_format" in body:
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"error": "response_format not wanted here"}')
            return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        answer = REPLY["text"]
        for _ in range(REPLY.get("thinking", 0)):  # a model that thinks first: pieces with no text in them
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "hm"}, "finish_reason": None}]}) + "\n\n").encode())
            self.wfile.flush()
        for i in range(0, len(answer), 5):
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": answer[i:i + 5]}, "finish_reason": None}]}) + "\n\n").encode())
        self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n").encode())


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
deep = types.SimpleNamespace(provider="openai", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", api_key="test", model="deepseek-flash",
                             vision="auto", proxy="", effort="off", company="deepseek")
assert T.convert_text(deep, "我在路上了！", "English")[0] == "On my way!"
body = SEEN[-1]
assert len(SEEN) == 1 and "response_format" not in body and body["thinking"] == {"type": "disabled"}, body
assert "输出格式" not in body["messages"][0]["content"] and "English" in body["messages"][0]["content"]
assert body["messages"][1]["content"] == [{"type": "text", "text": "<<<\n我在路上了！\n>>>"}]
REPLY["text"] = "<think>\nthey are on the way {so}\n</think>\n\nBe right {there}!"
assert T.convert_text(deep, "马上到", "English")[0] == "Be right {there}!"
REPLY.update(text="On my way!", thinking=3)
n = len(SEEN)
try:
    T.convert_text(deep, "我在路上了！", "English", cancelled=lambda: len(SEEN) > n)   # Esc as soon as the request is out
    raise SystemExit("the request went on after Esc")
except T.Cancelled:
    pass
assert T.convert_text(deep, "我在路上了！", "English", cancelled=lambda: False)[0] == "On my way!"
REPLY.pop("thinking")
print("2 OK other companies: plain text asked for (no JSON mode), thinking setting sent, <think> dropped, braces kept")

# ---------- 3) settings ----------
old = settings.default_config("zh").replace("; 不想用这个功能，就写 convert = off\nconvert = Alt+1\n", "")
head, _, tail = old.partition("; 输入框转换成哪种语言")
old = head + tail[tail.index("; 框选的内容本来就是"):]
assert "convert" not in old
settings.CONFIG_PATH.write_bytes(old.replace("api_key =", "api_key = sk-ant-TEST0000", 1).encode("utf-8"))
cfg = settings.load_config()
assert cfg.convert_hotkey == "Alt+1" and cfg.convert_language == "English"
text = settings.CONFIG_PATH.read_text(encoding="utf-8")
assert text.count("convert_language = English") == 1 and "输入框转换成哪种语言" in text and text.rstrip().endswith("convert_language = English")
settings.load_config()
assert settings.CONFIG_PATH.read_text(encoding="utf-8") == text  # added once, not again
settings.set_option("convert_language", "日本語")
assert settings.load_config().convert_language == "日本語" and settings.english_name("日本語") == "Japanese"
assert settings.english_name("简体中文") == "Simplified Chinese" and settings.english_name("Français") == "Français" and settings.english_name("") == "English"
settings.CONFIG_PATH.write_bytes(settings.default_config("en").replace("convert = Alt+1", "convert = Ctrl+Shift+F9").encode("utf-8"))
assert settings.load_config().convert_hotkey == "Ctrl+Shift+F9"
assert settings.localize_config_comments("zh") and "输入框转换成哪种语言" in settings.CONFIG_PATH.read_text(encoding="utf-8")
print("3 OK settings: an older config gets the new option once; hotkey and language can be changed; comments switch language")

# ---------- 4) what Windows says about the focused control -> may we type there? ----------
import inputbox as ib


def found(type=50033, password=False, has_value=False, value_read_only=None, selection=None, read_only=None, whole=None, **more):
    return dict(type=type, password=password, has_value=has_value, value_read_only=value_read_only, selection=selection, read_only=read_only, whole=whole, **more)


table = [(None, ib.UNKNOWN),
         (found(type=ib._EDIT, password=True, has_value=True, value_read_only=False), ib.PASSWORD),
         (found(type=ib._EDIT, has_value=True, value_read_only=False, selection="你好", read_only=False), ib.EDITABLE),   # a chat box
         (found(type=ib._EDIT, has_value=True, value_read_only=True, selection="你好", read_only=True), ib.READ_ONLY),    # a locked field
         (found(type=50030, has_value=True, value_read_only=True, selection="网页上的字", read_only=True), ib.READ_ONLY),  # a web page
         (found(type=50030, selection="文档", read_only=False), ib.EDITABLE),                                            # a document being edited
         (found(type=50030, selection="文档", read_only=True), ib.READ_ONLY),                                             # a document being read
         (found(type=ib._EDIT), ib.EDITABLE),
         (found(type=50000), ib.UNKNOWN), (found(type=50006), ib.UNKNOWN), (found(type=50033), ib.UNKNOWN),              # button, picture, bare pane
         (found(type=50033, selection="some text"), ib.UNKNOWN),
         # as a browser reports them (measured in Edge): things that hold a value which can be changed, but are no text boxes
         (found(type=50002, has_value=True, value_read_only=False, whole=""), ib.UNKNOWN),                              # a checkbox
         (found(type=50013, has_value=True, value_read_only=False, whole=""), ib.UNKNOWN),                              # a radio button
         (found(type=50015, has_value=True, value_read_only=False, whole="50"), ib.UNKNOWN),                            # a slider
         (found(type=50003, has_value=True, value_read_only=False, whole="one"), ib.UNKNOWN),                           # a drop-down list
         (found(type=50008, has_value=True, value_read_only=False, whole=""), ib.UNKNOWN),                              # a list
         (found(type=50000, has_value=True, value_read_only=False, whole=""), ib.UNKNOWN),                              # a switch
         (found(type=50016, has_value=True, value_read_only=False, whole="0"), ib.UNKNOWN),                             # a date field
         (found(type=50033, has_value=True, value_read_only=False, whole="x"), ib.UNKNOWN),                             # some pane with a value
         # ... and the places where text is typed
         (found(type=50026, selection="", read_only=False, whole="今晚一起吃饭吗"), ib.EDITABLE),                          # a chat box made of a plain editable block
         (found(type=50003, has_value=True, value_read_only=False, selection="", read_only=False, whole="搜索"), ib.EDITABLE),  # a search box with suggestions
         (found(type=50016, has_value=True, value_read_only=False, selection="", read_only=False, whole="5"), ib.EDITABLE),   # a number field
         (found(type=50030, has_value=True, value_read_only=False), ib.EDITABLE),                                        # a rich text box that says no more
         # terminals say they can be typed into, but Ctrl+C and Ctrl+V mean other things there
         (found(type=50030, selection="", read_only=False, whole="C:\\>", window="ConsoleWindowClass"), ib.UNKNOWN),       # the Windows console
         (found(type=50020, selection="", read_only=False, whole="PS>", **{"class": "TermControl"}, window="CASCADIA_HOSTING_WINDOW_CLASS"), ib.UNKNOWN),  # Windows Terminal
         (found(type=50020, selection="", read_only=False, whole="PS>"), ib.UNKNOWN),                                    # anything that calls itself plain text
         (found(type=ib._EDIT, has_value=True, value_read_only=False, selection="", read_only=False, whole="￼", **{"class": "xterm-helper-textarea"}), ib.UNKNOWN),  # the terminal in VS Code
         (found(type=ib._EDIT, has_value=True, value_read_only=False, selection="", read_only=False, whole="hi", **{"class": "chat-input"}, window="Chrome_WidgetWin_1"), ib.EDITABLE)]
for info, want in table:
    assert ib.classify(info) == want, (info, want, ib.classify(info))
assert all(ib.blank(x) for x in (None, "", "  \n", "￼", "\u200b", "\ufeff\u200b ")) and not ib.blank(" a ")
print("4 OK input boxes are told apart from pages, pictures, buttons, checkboxes, lists, sliders, terminals, locked fields and password boxes")

# ---------- 5) the flow in the main program, with the outside world faked ----------
import fanyi

settings.CONFIG_PATH.write_bytes(settings.default_config("zh").replace("api_key =", "api_key = sk-ant-TEST0000", 1)
                                 .replace("ui_language = auto", "ui_language = 简体中文").encode("utf-8"))
app = fanyi.App()
app.labels = {"capture": "Alt+~", "toggle": "Ctrl+Alt+Y", "convert": "Alt+1"}
esc_key = []  # every time the program takes hold of the Esc key (True) or lets go of it (False)
app.hotkeys = types.SimpleNamespace(bindings={"convert": ("Alt+1", 1, 0x31)}, set_esc=esc_key.append)
app._prewarm = staticmethod(lambda cfg: None)
toasts, errors, shown, notes = [], [], [], []
app.toast = lambda text, *a, **k: notes.append(text) if text.startswith("正在转换") else toasts.append(text)  # the "working on it" note is not a result
app.show_error = lambda message, *a, **k: errors.append(message)
app._show_converted = shown.append
world = {}
keys = []   # what would have been sent to the other program
waits = []  # how long each Ctrl+C was given to show up on the clipboard
asked = []  # what would have been sent to the API
WHOLE = "前面一句。我明天再回你"
BOX, ELSEWHERE = 11, 99  # the window the box is in, and another program's window


def reset(kind=ib.EDITABLE, selection="我明天再回你", copied="我明天再回你", clipboard="旧的剪贴板内容", move=None, answer="I'll get back to you tomorrow",
          whole=WHOLE, select_works=True, reachable=True, typing=False, early=None, keepable=True, deaf=False, busy=False):
    """A pretend input box in a pretend window. selection / whole: what the box reports (None = it cannot say).
    copied: what Ctrl+C yields for the user's own selection. move: what the user does while the API works.
    early: what the user does right after the hotkey, before any key has been sent. keepable: can what is on the
    clipboard be put back? deaf: the box ignores Ctrl+V. busy: another program is holding the clipboard."""
    world.clear()
    world.update(kind=kind, selection=selection, copied=copied, clipboard=clipboard, move=move, answer=answer, pasted=None, whole=whole,
                 select_works=select_works, all_selected=False, reachable=reachable, typing=typing, early=early, keepable=keepable,
                 deaf=deaf, busy=busy, holding=False, seq=1, hang=None,
                 window=BOX, on="box", hidden=None, marked=None, returns=0)
    del keys[:], asked[:], toasts[:], errors[:], shown[:], notes[:], waits[:]


class FakeSession:
    seen = "a place made up for the test"  # what the real one notes down for the log

    def look(self, tries=3, pause=0.35, keep=False):  # the place that has the keyboard now
        now = ib.Focus(world["kind"], world["selection"], world["whole"]) if world["window"] == BOX and world["on"] == "box" else ib.Focus(ib.UNKNOWN, None, None)
        if keep and world["early"] == "window":      # ... and right after the hotkey the user clicks into another program
            world["window"], world["on"] = ELSEWHERE, "theirs"
        elif keep and world["early"] == "esc":       # ... or presses Esc
            run = app._convert_run
            app.events.put(("hotkey", "esc"))
            end = time.monotonic() + 3
            while not run.cancelled and time.monotonic() < end:
                real_sleep(0.005)
        return now

    def box(self):
        return ib.Focus(world["kind"], world["selection"], world["whole"])

    def box_focused(self):
        return world["window"] == BOX and world["on"] == "box"

    def mark_away(self):
        world["marked"] = world["on"]

    def focus_box(self):
        if world["reachable"] and world["window"] == BOX:
            world["on"] = "box"
            if world["hidden"] is not None:  # a box shows its selection again when it gets the keyboard back
                world["selection"], world["hidden"] = world["hidden"], None
        return world["reachable"]

    def focus_away(self):
        world["on"] = world["marked"]
        world["returns"] += 1
        return True

    def close(self):
        pass


def fake_foreground(window):
    if window == BOX and not world["reachable"]:
        return
    if window != world["window"]:
        world["returns"] += window != BOX
        world["window"] = window
        world["on"] = "box" if window == BOX else "theirs"  # a window gives the keyboard to whatever had it last


def fake_chord(key):
    name = {0x41: "Ctrl+A", 0x43: "Ctrl+C", 0x56: "Ctrl+V", 0x27: "Right"}[key]
    keys.append(name)
    assert world["window"] == BOX and world["on"] == "box", "a key was sent while the box did not have the keyboard"
    assert not world["holding"], "a key was sent while the user was holding Ctrl"
    if key == 0x41 and world["select_works"]:
        world["all_selected"] = True
        if world["selection"] is not None:
            world["selection"] = world["whole"]
    elif key == 0x27:                               # the selection collapses, the caret goes to its end
        world["all_selected"] = False
        if world["selection"] is not None:
            world["selection"] = ""
    elif key == 0x56:
        if world["move"] == "esc, too late":        # the key comes when the text is already going in
            app.events.put(("hotkey", "esc"))
        if not world["deaf"]:                       # the box puts what is on the clipboard in the selection's place
            text, old = world["clipboard"], world["whole"]
            world["pasted"] = text
            if old is not None:
                chosen = old if world["all_selected"] else (world["selection"] or world["copied"] or "")
                world["whole"] = old.replace(chosen, text, 1) if chosen else old + text
            if world["selection"] is not None:
                world["selection"] = ""
            world["all_selected"] = False
        if world["move"] == "copies right after":   # ... and the user copies something else straight away
            world["clipboard"], world["seq"] = "粘贴之后复制的", world["seq"] + 1


def fake_copy(owner, timeout=1.0):
    waits.append(timeout)
    fake_chord(0x43)
    copied = world["real_whole"] if world["all_selected"] else world["copied"]
    if copied == "<a picture>":                     # the program copied something that is not text
        world["clipboard"], world["seq"] = copied, world["seq"] + 1
        return None
    if copied is None:
        return None
    world["clipboard"], world["seq"] = copied, world["seq"] + 1
    return copied


def fake_set(text, owner, passing=False):
    if world["busy"]:
        raise OSError("clipboard busy")
    world["clipboard"], world["seq"] = text, world["seq"] + 1
    if world["move"] == "holds ctrl at the end":    # on the way to a shortcut of their own
        world["holding"] = True
    elif world["move"] == "leaves at the end":
        world["on"] = "other field"


def fake_restore(saved, owner):
    if saved is not None:
        world["clipboard"], world["seq"] = saved[13], world["seq"] + 1


def fake_convert(cfg, text, language, cancelled=None):
    asked.append((text, language))
    move = world["move"]
    if move in ("esc", "esc, answer arrives anyway", "esc, request hangs"):  # the user presses Esc while the API works
        hang = world["hang"]
        app.events.put(("hotkey", "esc"))
        end = time.monotonic() + 3
        while not cancelled() and time.monotonic() < end:
            real_sleep(0.005)
        assert cancelled(), "Esc did not reach the conversion"
        if move == "esc":
            raise T.Cancelled()  # the request is dropped at the next piece of the answer
        if move == "esc, request hangs":  # nothing comes from the API for a long while, and then an error
            hang.wait(10)
            raise T.TranslateError("额度不足")
        # (otherwise: the whole answer had already arrived; it is handed back as usual)
    if move == "copies something":   # the user copies something of their own while the API works
        world["clipboard"], world["seq"] = "用户刚复制的", world["seq"] + 1
    if move == "window":            # clicks into another program; the selection stays in the box
        world["window"], world["on"] = ELSEWHERE, "theirs"
    elif move == "field":           # clicks into another field of the same window; the box hides its selection meanwhile
        world["on"], world["hidden"], world["selection"] = "other field", world["selection"], ""
    elif move == "field, selection lost":
        world["on"], world["selection"], world["all_selected"] = "other field", "", False
    elif move == "click in the box":  # the caret moves, nothing is selected any more, the text is the same
        world["selection"], world["all_selected"] = "", False
    elif move == "edits the box":
        world["selection"], world["all_selected"], world["whole"] = "", False, world["whole"] + "，再加一句"
    elif move == "selects something else":
        world["selection"], world["all_selected"] = "别的字", False
    if isinstance(world["answer"], Exception):
        raise world["answer"]
    return world["answer"], types.SimpleNamespace(input_tokens=100, output_tokens=10)


ib.Session = FakeSession
ib._chord, ib._tap, ib.copy_selection, ib.set_clipboard_text = fake_chord, fake_chord, fake_copy, fake_set
ib.snapshot = lambda owner: {13: world["clipboard"]} if world["keepable"] else None
ib.restore = fake_restore
ib.sequence = lambda: world["seq"]
ib.focus_place = lambda: (world["window"], 22)
ib._front = lambda: world["window"]
ib._foreground = fake_foreground
ib._hands_off = lambda quiet=0.6, timeout=4.0: not world["typing"]
ib.wait_keys_up = lambda extra=(), timeout=2.0: not world["holding"]
T.convert_text = fake_convert
real_sleep = time.sleep
fanyi.time.sleep = lambda s: real_sleep(min(s, 0.01))
ib.time = types.SimpleNamespace(sleep=lambda s: real_sleep(min(s, 0.005)), monotonic=time.monotonic)


def go(real_whole=WHOLE):
    world["real_whole"] = real_whole
    app._handle(("hotkey", "convert"))
    for _ in range(600):
        while not app.events.empty():
            app._handle(app.events.get_nowait())
        if not app.converting and not any(t.name in ("convert", "unselect") for t in threading.enumerate()) and app.events.empty():
            return
        real_sleep(0.01)
    raise SystemExit("the conversion did not finish")


ENGLISH = "I'll get back to you tomorrow"
reset()
go()
assert keys == ["Ctrl+C", "Ctrl+V"] and world["pasted"] == ENGLISH and world["whole"] == "前面一句。" + ENGLISH, (keys, world)
assert waits == [3.0]  # the box has shown what is selected: the copy is certain to come, and is waited for
assert asked == [("我明天再回你", "English")] and world["clipboard"] == "旧的剪贴板内容" and not errors and not toasts, (asked, world, errors, toasts)
assert not settings.HISTORY_PATH.exists()  # nothing is recorded unless the user turned history on
# what surrounds the selection stays: leading / trailing spaces and line breaks, Windows line endings
reset(selection="  第一行\r\n第二行 \r\n", copied="  第一行\r\n第二行 \r\n", answer="Line one\nLine two")
go()
assert asked[0][0] == "第一行\n第二行" and world["pasted"] == "  Line one\r\nLine two \r\n", (asked, world["pasted"])
# no keys are ever sent where typing is not possible
for kind, word in ((ib.READ_ONLY, "不是能输入文字的地方"), (ib.UNKNOWN, "不是能输入文字的地方"), (ib.PASSWORD, "密码框")):
    reset(kind=kind)
    go()
    assert keys == [] and asked == [] and word in toasts[-1] and world["clipboard"] == "旧的剪贴板内容", (kind, keys, toasts)
# nothing selected: the program selects everything in the box itself (hands stay on the keyboard) and replaces all of it
reset(selection="", copied=None)
go()
assert keys == ["Ctrl+A", "Ctrl+C", "Ctrl+V"] and asked == [(WHOLE, "English")] and world["pasted"] == ENGLISH, (keys, asked, world)
assert world["clipboard"] == "旧的剪贴板内容" and not toasts, (world, toasts)
# a box that cannot say what is selected (a plain Windows text box): Ctrl+C first, and only if that finds nothing, everything
reset(selection=None, copied=None)
go()  # (before the paste all of it is selected once more: a click into such a box would have left only a caret, and it cannot say)
assert keys == ["Ctrl+C", "Ctrl+A", "Ctrl+C", "Ctrl+A", "Ctrl+V"] and asked == [(WHOLE, "English")] and world["whole"] == ENGLISH, (keys, asked, world)
assert waits == [1.0, 1.0]  # (a box that cannot say: there may be nothing to copy, so no long wait)
reset(selection=None)  # ... and with something selected there, only that
go()
assert keys == ["Ctrl+C", "Ctrl+V"] and asked == [("我明天再回你", "English")], (keys, asked)
# no key is sent into a box that says it is empty, and a long document is never selected wholesale
# (an empty box in a browser reports one placeholder character, U+FFFC: that is an empty box too)
for whole, word in (("", "还没有字"), ("   ", "还没有字"), ("￼", "还没有字"), ("字" * (ib.MAX_CHARS + 1), "字太多")):
    reset(selection="", copied=None, whole=whole)
    go()
    assert keys == [] and asked == [] and word in toasts[-1], (whole[:5], keys, toasts)
    reset(selection=None, copied=None, whole=whole)  # a box that cannot say what is selected: Ctrl+C only if it holds something
    go()
    assert keys == (["Ctrl+C"] if len(whole) > 9 else []) and asked == [] and word in toasts[-1] and world["clipboard"] == "旧的剪贴板内容", (whole[:5], keys, toasts)
reset(selection=None, copied=None, whole=None)  # a box that says nothing about its content: nothing is selected for the user
go()
assert keys == ["Ctrl+C"] and asked == [] and "先选中" in toasts[-1], (keys, toasts)
reset(selection="", copied=None, select_works=False)  # Ctrl+A did not take: no Ctrl+C is sent after it
go()
assert keys == ["Ctrl+A"] and asked == [] and "没能取到" in toasts[-1], (keys, toasts)
reset(copied=None)  # something is selected but the other program gave nothing (it runs as administrator, say)
go()
assert keys == ["Ctrl+C"] and asked == [] and "没能取到" in toasts[-1], (keys, toasts)
reset(selection=None, copied="字" * (ib.MAX_CHARS + 1))
go()
assert asked == [] and "太长" in toasts[-1] and world["clipboard"] == "旧的剪贴板内容", toasts

# --- the user clicks somewhere else while the API works ---
# into another program, the selection still in the box: the box gets the keyboard back, the text goes in, the user is returned
for selection, copied, first in (("我明天再回你", "我明天再回你", ["Ctrl+C"]), ("", None, ["Ctrl+A", "Ctrl+C"])):
    reset(selection=selection, copied=copied, move="window")
    go()
    assert keys == first + ["Ctrl+V"] and world["pasted"] == ENGLISH and not toasts, (keys, world, toasts)
    assert world["window"] == ELSEWHERE and world["on"] == "theirs" and world["clipboard"] == "旧的剪贴板内容", world
    # into another field of the same window: the same, and the keyboard goes back to that field
    reset(selection=selection, copied=copied, move="field")
    go()
    assert keys == first + ["Ctrl+V"] and world["pasted"] == ENGLISH and world["on"] == "other field" and not toasts, (keys, world, toasts)
# the selection is gone but the box holds the same text: a whole-box conversion selects it again; a part chosen by the user cannot be found again
reset(selection="", copied=None, move="click in the box")
go()
assert keys == ["Ctrl+A", "Ctrl+C", "Ctrl+A", "Ctrl+V"] and world["pasted"] == ENGLISH and not toasts, (keys, toasts)
reset(selection="", copied=None, move="field, selection lost")
go()
assert keys == ["Ctrl+A", "Ctrl+C", "Ctrl+A", "Ctrl+V"] and world["pasted"] == ENGLISH and world["on"] == "other field", (keys, world)
reset(move="click in the box")
go()
assert keys == ["Ctrl+C"] and world["pasted"] is None and world["clipboard"] == ENGLISH and "没能放回" in toasts[-1], (keys, world, toasts)
# the text in the box was changed meanwhile, or something else is selected: nothing is typed over it
for move in ("edits the box", "selects something else"):
    for selection, copied in (("我明天再回你", "我明天再回你"), ("", None)):
        if move == "selects something else" and selection == "":
            continue  # (with the same text in the box, a whole-box conversion would rightly select it all again)
        reset(selection=selection, copied=copied, move=move)
        go()
        assert "Ctrl+V" not in keys and world["pasted"] is None and world["clipboard"] == ENGLISH and "没能放回" in toasts[-1], (move, keys, toasts)
# the box cannot be reached any more (its window is gone), or the user is in the middle of typing elsewhere: nothing is typed, the user is not disturbed
reset(move="window", reachable=False)
go()
assert keys == ["Ctrl+C"] and world["pasted"] is None and world["window"] == ELSEWHERE and "没能放回" in toasts[-1], (keys, world, toasts)
reset(move="window", typing=True)
go()
assert keys == ["Ctrl+C"] and world["pasted"] is None and world["window"] == ELSEWHERE and world["returns"] == 0 and "没能放回" in toasts[-1], (keys, world, toasts)
reset(selection=None, move="window")  # a box that cannot say what is selected: no way to know it is unchanged
go()
assert keys == ["Ctrl+C"] and world["pasted"] is None and world["window"] == ELSEWHERE and "没能放回" in toasts[-1], (keys, world, toasts)

# errors are shown, nothing is typed, the clipboard is the user's own again
reset(answer=T.TranslateError("额度不足"))
go()
assert keys == ["Ctrl+C"] and errors == ["额度不足"] and world["clipboard"] == "旧的剪贴板内容" and not app.converting
reset(answer=RuntimeError("boom"))
go()
assert "boom" in errors[0] and not app.converting
reset(selection="", copied=None, answer=T.TranslateError("额度不足"))  # ... and a selection the program made itself is taken away again
go()
assert keys == ["Ctrl+A", "Ctrl+C", "Right"] and errors == ["额度不足"] and world["selection"] == "" and world["whole"] == WHOLE, (keys, errors, world)
# --- Esc while it is under way: nothing is replaced, and the user is told ---
for move in ("esc", "esc, answer arrives anyway"):
    for selection, copied, first in (("我明天再回你", "我明天再回你", ["Ctrl+C"]), ("", None, ["Ctrl+A", "Ctrl+C"])):
        reset(selection=selection, copied=copied, move=move)
        del esc_key[:]
        go()
        # no Ctrl+V; the clipboard is the user's own; a selection the program made itself is taken away again (the caret goes to the end)
        assert keys == first + (["Right"] if "Ctrl+A" in first else []) and world["pasted"] is None and world["clipboard"] == "旧的剪贴板内容", (move, keys, world)
        assert world["whole"] == WHOLE and (world["selection"] == "") == ("Ctrl+A" in first), (move, world)
        assert toasts == ["已取消，没有转换"] and not errors and not app.converting, (move, toasts, errors)
        assert esc_key == [True, False], esc_key                           # Esc is the program's only while a conversion runs
reset(move="esc, too late")  # the text is already going in: it goes in, and nothing says "cancelled"
go()
assert keys == ["Ctrl+C", "Ctrl+V"] and world["pasted"] == ENGLISH and not toasts and not errors, (keys, world, toasts)
reset()                      # Esc with no conversion under way: nothing happens
app._handle(("hotkey", "esc"))
assert keys == [] and not toasts and not errors and app.events.empty()
reset()                      # a cancelled conversion leaves the next one untouched
go()
assert keys == ["Ctrl+C", "Ctrl+V"] and world["pasted"] == ENGLISH and not toasts
# a second press while one is under way does nothing
reset()
app.converting = True
app._handle(("hotkey", "convert"))
assert keys == [] and app.events.empty()
app.converting = False

# --- what the review found ---
# the user is elsewhere before the first key could go (a click right after the hotkey), or has pressed Esc: no key is sent at all
for selection, copied in (("我明天再回你", "我明天再回你"), ("", None), (None, None)):
    reset(selection=selection, copied=copied, early="window")
    go()
    assert keys == [] and asked == [] and len(toasts) == 1 and world["clipboard"] == "旧的剪贴板内容", (selection, keys, toasts)
    reset(selection=selection, copied=copied, early="esc")
    go()
    assert keys == [] and asked == [] and toasts == ["已取消，没有转换"] and not errors, (selection, keys, toasts)
# the clipboard holds something that could not be put back afterwards: it is not overwritten, so nothing is converted
reset(keepable=False)
go()
assert keys == [] and asked == [] and "剪贴板" in toasts[-1] and world["clipboard"] == "旧的剪贴板内容", (keys, toasts)
# the program copied something that is not text: the clipboard is the user's own again all the same
reset(copied="<a picture>")
go()
assert keys == ["Ctrl+C"] and asked == [] and "没能取到" in toasts[-1] and world["clipboard"] == "旧的剪贴板内容", (keys, toasts, world)
# something the user copied while the API worked, or right after the text went in, is not wiped by putting the old content back
reset(move="copies something")
go()
assert world["pasted"] == ENGLISH and world["clipboard"] == "用户刚复制的" and not toasts, world
reset(move="copies right after")
go()
assert world["pasted"] == ENGLISH and world["clipboard"] == "粘贴之后复制的" and not toasts, world
# the keyboard leaves the box, or Ctrl goes down, in the last moment before the paste: no Ctrl+V; the result is handed over on the clipboard
for move in ("leaves at the end", "holds ctrl at the end"):
    reset(move=move)
    go()
    assert keys == ["Ctrl+C"] and world["pasted"] is None and world["clipboard"] == ENGLISH and "没能放回" in toasts[-1], (move, keys, world, toasts)
# the box does not take the paste: the result stays on the clipboard and the user is told, instead of being told nothing
reset(deaf=True)
go()
assert keys == ["Ctrl+C", "Ctrl+V"] and world["whole"] == WHOLE and world["clipboard"] == ENGLISH and "没能放回" in toasts[-1], (keys, world, toasts)
reset(selection="", copied=None, deaf=True)   # (the selection stays: a Ctrl+V by hand then replaces it)
go()
assert keys == ["Ctrl+A", "Ctrl+C", "Ctrl+V"] and world["selection"] == WHOLE and world["clipboard"] == ENGLISH and "没能放回" in toasts[-1], (keys, world, toasts)
# the answer is word for word what was there: it counts as put in place (the box looks no different afterwards)
reset(answer="我明天再回你")
go()
assert keys == ["Ctrl+C", "Ctrl+V"] and not toasts and world["clipboard"] == "旧的剪贴板内容", (keys, toasts, world)
# a box that cannot say what is selected, changed while the API worked: nothing is typed over it
reset(selection=None, move="edits the box")
go()
assert keys == ["Ctrl+C"] and world["pasted"] is None and world["clipboard"] == ENGLISH and "没能放回" in toasts[-1], (keys, world, toasts)
# another program is holding the clipboard when the result is ready: it is shown in a window, not lost
reset(busy=True)
world["busy"] = False


def busy_later(text, owner, passing=False, real=fake_set):
    if text == ENGLISH:
        raise OSError("clipboard busy")
    real(text, owner, passing)


ib.set_clipboard_text = busy_later
go()
ib.set_clipboard_text = fake_set
assert "Ctrl+V" not in keys and shown == [ENGLISH] and not errors and not toasts, (keys, shown, errors, toasts)
# Esc, and the request does not come back for a long time: the conversion is over for the user at once. Esc is free
# again, the next conversion runs, and the error that arrives at last for the cancelled one is shown to nobody
reset(move="esc, request hangs")
world["hang"] = threading.Event()
del esc_key[:]
world["real_whole"] = WHOLE
app._handle(("hotkey", "convert"))
for _ in range(300):
    while not app.events.empty():
        app._handle(app.events.get_nowait())
    if toasts and not app.converting:
        break
    real_sleep(0.01)
assert toasts == ["已取消，没有转换"] and not app.converting and esc_key == [True, False], (toasts, app.converting, esc_key)
assert any(t.name == "convert" for t in threading.enumerate())            # (the first request is still out)
hang, world["move"] = world["hang"], None
app._handle(("hotkey", "convert"))                                         # the next conversion, while the first still hangs
for _ in range(300):
    while not app.events.empty():
        app._handle(app.events.get_nowait())
    if world["pasted"] and not app.converting:
        break
    real_sleep(0.01)
assert world["pasted"] == ENGLISH and not app.converting and esc_key == [True, False, True, False], (world, esc_key)
app._handle(("hotkey", "convert"))                                         # and a third: the late error of the first must not end this one
hang.set()
for _ in range(600):
    while not app.events.empty():
        app._handle(app.events.get_nowait())
    if not app.converting and not any(t.name in ("convert", "unselect") for t in threading.enumerate()) and app.events.empty():
        break
    real_sleep(0.01)
assert toasts == ["已取消，没有转换"] and not errors and not app.converting and keys.count("Ctrl+V") == 2, (toasts, errors, keys)
assert esc_key == [True, False, True, False, True, False], esc_key
# the "working on it" note: not shown, and not kept for later, while a screenshot is being selected
reset()
app.selection = "pending"
app._convert_note("正在转换成 English…")
assert not notes and app._convert_toast is None and app._deferred_toast is None
app.selection = None
print("5 OK flow: selection or, with none, the whole box replaced in place; still replaced after the user clicked elsewhere, as long as the box holds the same text; never types into pages, locked fields, password boxes, empty boxes, long documents or a changed box; clipboard put back, and what the user copied meanwhile left alone; safe when the API fails; Esc ends it at once")

# ---------- 6) history only when turned on; tray entry ----------
settings.set_option("save_history", "true")
reset()
go()
record = settings.HISTORY_PATH.read_text(encoding="utf-8")
assert "我明天再回你" in record and "I'll get back to you tomorrow" in record
app._tray_action("convert:ja")
assert settings.load_config().convert_language == "日本語" and app.cfg.convert_language == "日本語"
reset()
go()
assert asked == [("我明天再回你", "Japanese")], asked
for code in i18n.CODES:
    i18n.set_language(code)
    top = {it.text: it for it in app._build_menu().items if str(it) != "- - - -"}
    sub = top[i18n.t("输入框转换成")].submenu.items
    names = [it.text for it in sub if str(it) != "- - - -"]
    assert names[:5] == [n for _, n in i18n.LANGUAGES] and names[5] == i18n.t("快捷键：{hotkey}", hotkey="Alt+1") and not sub[-1].enabled, names
    assert [it.checked for it in sub[:5]] == [False, False, False, True, False]
print("6 OK history follows the switch; the tray entry lists the five languages and shows the hotkey")
print("ALL OK")
sys.stdout.flush()
os._exit(0)
