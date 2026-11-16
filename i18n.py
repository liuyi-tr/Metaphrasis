"""Interface language: every user-facing string goes through t(). Keys are the Simplified Chinese originals; returned unchanged when the current language is Chinese or no translation exists.

Supported: 简体中文, 繁體中文, English, 日本語, 한국어. Logs are English; config comments come in Chinese and English (settings.py).
"""
import ctypes

LANGUAGES = [("zh", "简体中文"), ("zh-tw", "繁體中文"), ("en", "English"), ("ja", "日本語"), ("ko", "한국어")]
CODES = [code for code, _ in LANGUAGES]
NAMES = dict(LANGUAGES)
_current = "zh"


def system_language():
    """Code for the Windows display language; unsupported languages (German, French, ...) get English."""
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except Exception:
        return "en"
    primary, sub = langid & 0x3FF, langid >> 10
    if primary == 0x04:  # Chinese: 0x0404 Taiwan, 0x0C04 Hong Kong, 0x1404 Macau -> Traditional; 0x0804 mainland, 0x1004 Singapore -> Simplified
        return "zh-tw" if sub in (0x01, 0x03, 0x05) else "zh"
    return {0x09: "en", 0x11: "ja", 0x12: "ko"}.get(primary, "en")


def resolve(name):
    """Interface language as written in config.ini (auto or a language name) -> code."""
    import settings
    name = (name or "").strip()
    if not name or name.lower() == "auto":
        return system_language()
    code = settings.lang_code(name)
    return code if code in CODES else system_language()


def set_language(code):
    global _current
    _current = code if code in CODES else "zh"


def current():
    return _current


def t(text, **kw):
    """Text in the current language. {placeholders} are filled from keyword arguments."""
    s = text if _current == "zh" else STRINGS.get(text, {}).get(_current, text)
    return s.format(**kw) if kw else s


STRINGS = {
    # ---------- app name, tray ----------
    "截图翻译": {"zh-tw": "截圖翻譯", "en": "Screen Translation", "ja": "画面翻訳", "ko": "화면 번역"},
    "截图翻译    {hotkey}": {"zh-tw": "截圖翻譯    {hotkey}", "en": "Translate selection    {hotkey}", "ja": "範囲を選んで翻訳    {hotkey}", "ko": "영역 선택 번역    {hotkey}"},
    "截图翻译（{hotkey}）": {"zh-tw": "截圖翻譯（{hotkey}）", "en": "Screen Translation ({hotkey})", "ja": "画面翻訳（{hotkey}）", "ko": "화면 번역({hotkey})"},
    "已在后台运行。按 {capture} 框选翻译，{toggle} 切换显示模式。": {
        "zh-tw": "已在背景執行。按 {capture} 框選翻譯，{toggle} 切換顯示模式。",
        "en": "Running in the background. Press {capture} to select and translate, {toggle} to switch display mode.",
        "ja": "バックグラウンドで実行中です。{capture} で範囲を選んで翻訳、{toggle} で表示モードを切り替えます。",
        "ko": "백그라운드에서 실행 중입니다. {capture}로 영역을 선택해 번역하고, {toggle}로 표시 모드를 전환합니다."},
    "显示模式": {"zh-tw": "顯示模式", "en": "Display mode", "ja": "表示モード", "ko": "표시 모드"},
    "译文覆盖在原文上": {"zh-tw": "譯文覆蓋在原文上", "en": "Overlay on the original", "ja": "原文の上に重ねて表示", "ko": "원문 위에 덮어쓰기"},
    "弹窗显示文字（自动复制）": {"zh-tw": "彈窗顯示文字（自動複製）", "en": "Popup text (auto-copied)", "ja": "ポップアップで表示（自動コピー）", "ko": "팝업으로 표시(자동 복사)"},
    "切换快捷键：{hotkey}": {"zh-tw": "切換快捷鍵：{hotkey}", "en": "Toggle hotkey: {hotkey}", "ja": "切り替えキー：{hotkey}", "ko": "전환 단축키: {hotkey}"},
    "译文语言": {"zh-tw": "譯文語言", "en": "Target language", "ja": "翻訳先の言語", "ko": "번역 언어"},
    "界面语言": {"zh-tw": "介面語言", "en": "Interface language", "ja": "表示言語", "ko": "인터페이스 언어"},
    "译文语言：{name}": {"zh-tw": "譯文語言：{name}", "en": "Target language: {name}", "ja": "翻訳先の言語：{name}", "ko": "번역 언어: {name}"},
    "界面语言：{name}": {"zh-tw": "介面語言：{name}", "en": "Interface language: {name}", "ja": "表示言語：{name}", "ko": "인터페이스 언어: {name}"},
    "打开配置文件（API Key、模型）": {"zh-tw": "開啟設定檔（API Key、模型）", "en": "Open config file (API key, model)", "ja": "設定ファイルを開く（API キー・モデル）", "ko": "설정 파일 열기(API 키, 모델)"},
    "打开翻译记录": {"zh-tw": "開啟翻譯記錄", "en": "Open translation history", "ja": "翻訳履歴を開く", "ko": "번역 기록 열기"},
    "打开工具所在文件夹": {"zh-tw": "開啟工具所在資料夾", "en": "Open program folder", "ja": "プログラムのフォルダーを開く", "ko": "프로그램 폴더 열기"},
    "开机自动启动": {"zh-tw": "開機自動啟動", "en": "Start with Windows", "ja": "Windows 起動時に実行", "ko": "Windows 시작 시 실행"},
    "退出": {"zh-tw": "結束", "en": "Quit", "ja": "終了", "ko": "종료"},
    "还没有翻译记录": {"zh-tw": "還沒有翻譯記錄", "en": "No translation history yet", "ja": "翻訳履歴はまだありません", "ko": "아직 번역 기록이 없습니다"},
    "已关闭开机自动启动": {"zh-tw": "已關閉開機自動啟動", "en": "Start with Windows turned off", "ja": "Windows 起動時の実行をオフにしました", "ko": "Windows 시작 시 실행을 껐습니다"},
    "已开启开机自动启动": {"zh-tw": "已開啟開機自動啟動", "en": "Start with Windows turned on", "ja": "Windows 起動時の実行をオンにしました", "ko": "Windows 시작 시 실행을 켰습니다"},
    "设置开机自动启动失败：{e}": {"zh-tw": "設定開機自動啟動失敗：{e}", "en": "Could not change the startup setting: {e}", "ja": "Windows 起動時の実行を設定できませんでした：{e}", "ko": "시작 프로그램 설정에 실패했습니다: {e}"},
    "显示模式：弹窗显示文字（自动复制到剪贴板）": {"zh-tw": "顯示模式：彈窗顯示文字（自動複製到剪貼簿）", "en": "Display mode: popup text (auto-copied to clipboard)", "ja": "表示モード：ポップアップで表示（クリップボードに自動コピー）", "ko": "표시 모드: 팝업 텍스트(클립보드에 자동 복사)"},
    "显示模式：译文覆盖在原文上": {"zh-tw": "顯示模式：譯文覆蓋在原文上", "en": "Display mode: overlay on the original", "ja": "表示モード：原文の上に重ねて表示", "ko": "표시 모드: 원문 위에 덮어쓰기"},
    # ---------- result window context menu, toasts ----------
    "复制图片": {"zh-tw": "複製圖片", "en": "Copy image", "ja": "画像をコピー", "ko": "이미지 복사"},
    "复制文字": {"zh-tw": "複製文字", "en": "Copy text", "ja": "テキストをコピー", "ko": "텍스트 복사"},
    "保存图片…": {"zh-tw": "儲存圖片…", "en": "Save image…", "ja": "画像を保存…", "ko": "이미지 저장…"},
    "重新翻译": {"zh-tw": "重新翻譯", "en": "Translate again", "ja": "再翻訳", "ko": "다시 번역"},
    "关闭": {"zh-tw": "關閉", "en": "Close", "ja": "閉じる", "ko": "닫기"},
    "Esc 关闭 · 空格看原文 · Ctrl+C 复制图片 · 右键更多": {
        "zh-tw": "Esc 關閉 · 空白鍵看原文 · Ctrl+C 複製圖片 · 右鍵更多",
        "en": "Esc close · Space shows the original · Ctrl+C copy image · right-click for more",
        "ja": "Esc 閉じる · Space 原文を表示 · Ctrl+C 画像をコピー · 右クリックでその他",
        "ko": "Esc 닫기 · Space 원문 보기 · Ctrl+C 이미지 복사 · 우클릭 더 보기"},
    "还在翻译，请稍等": {"zh-tw": "還在翻譯，請稍等", "en": "Still translating, one moment", "ja": "翻訳中です。少々お待ちください", "ko": "아직 번역 중입니다. 잠시만 기다려 주세요"},
    "这次还没翻译完，请稍等": {"zh-tw": "這次還沒翻譯完，請稍等", "en": "This one isn't finished yet, one moment", "ja": "まだ翻訳が終わっていません。少々お待ちください", "ko": "아직 번역이 끝나지 않았습니다. 잠시만 기다려 주세요"},
    "已复制译文图片，可以直接粘贴到微信、QQ、Word 等": {"zh-tw": "已複製譯文圖片，可以直接貼到 LINE、Word 等", "en": "Translated image copied. Paste it into chat apps, Word, etc.", "ja": "翻訳画像をコピーしました。チャットや Word などにそのまま貼り付けられます", "ko": "번역 이미지를 복사했습니다. 메신저나 Word 등에 바로 붙여넣을 수 있습니다"},
    "已复制译文文字": {"zh-tw": "已複製譯文文字", "en": "Translated text copied", "ja": "翻訳テキストをコピーしました", "ko": "번역 텍스트를 복사했습니다"},
    "已复制译文图片": {"zh-tw": "已複製譯文圖片", "en": "Translated image copied", "ja": "翻訳画像をコピーしました", "ko": "번역 이미지를 복사했습니다"},
    "保存翻译后的图片": {"zh-tw": "儲存翻譯後的圖片", "en": "Save translated image", "ja": "翻訳後の画像を保存", "ko": "번역된 이미지 저장"},
    "翻译": {"zh-tw": "翻譯", "en": "translation", "ja": "翻訳", "ko": "번역"},
    "PNG 图片": {"zh-tw": "PNG 圖片", "en": "PNG image", "ja": "PNG 画像", "ko": "PNG 이미지"},
    "JPG 图片": {"zh-tw": "JPG 圖片", "en": "JPG image", "ja": "JPG 画像", "ko": "JPG 이미지"},
    "图片已保存": {"zh-tw": "圖片已儲存", "en": "Image saved", "ja": "画像を保存しました", "ko": "이미지를 저장했습니다"},
    "保存失败：{err}": {"zh-tw": "儲存失敗：{err}", "en": "Save failed: {err}", "ja": "保存に失敗しました：{err}", "ko": "저장 실패: {err}"},
    # ---------- popup ----------
    "正在翻译…": {"zh-tw": "正在翻譯…", "en": "Translating…", "ja": "翻訳中…", "ko": "번역 중…"},
    "知道了": {"zh-tw": "知道了", "en": "OK", "ja": "OK", "ko": "확인"},
    "打开配置文件": {"zh-tw": "開啟設定檔", "en": "Open config file", "ja": "設定ファイルを開く", "ko": "설정 파일 열기"},
    "✓ 已复制到剪贴板": {"zh-tw": "✓ 已複製到剪貼簿", "en": "✓ Copied to clipboard", "ja": "✓ クリップボードにコピーしました", "ko": "✓ 클립보드에 복사했습니다"},
    "翻译结果": {"zh-tw": "翻譯結果", "en": "Translation", "ja": "翻訳結果", "ko": "번역 결과"},
    "没有识别到文字位置，改用弹窗显示": {"zh-tw": "沒有辨識到文字位置，改用彈窗顯示", "en": "No text positions detected, showing as popup", "ja": "文字の位置を検出できなかったため、ポップアップで表示します", "ko": "텍스트 위치를 인식하지 못해 팝업으로 표시합니다"},
    # ---------- errors, status ----------
    "截图翻译 · 需要设置 API Key": {"zh-tw": "截圖翻譯 · 需要設定 API Key", "en": "Screen Translation · API key needed", "ja": "画面翻訳 · API キーの設定が必要です", "ko": "화면 번역 · API 키 설정 필요"},
    "截图翻译 · 出错了": {"zh-tw": "截圖翻譯 · 出錯了", "en": "Screen Translation · Error", "ja": "画面翻訳 · エラー", "ko": "화면 번역 · 오류"},
    "config.ini 里的快捷键 “{t}” 无法识别：{err}\n已暂时改用默认的 {d}。": {
        "zh-tw": "config.ini 裡的快捷鍵 “{t}” 無法辨識：{err}\n已暫時改用預設的 {d}。",
        "en": "The hotkey “{t}” in config.ini could not be parsed: {err}\nUsing the default {d} for now.",
        "ja": "config.ini のショートカット “{t}” を認識できません：{err}\n一時的に既定の {d} を使います。",
        "ko": "config.ini의 단축키 “{t}”을(를) 인식할 수 없습니다: {err}\n임시로 기본값 {d}을(를) 사용합니다."},
    "快捷键 {key} 注册失败，可能已经被其他程序占用。\n\n请在 config.ini 的 [hotkey] 里换一个组合，然后重启本工具（托盘图标右键 → 退出，再双击启动）。": {
        "zh-tw": "快捷鍵 {key} 註冊失敗，可能已經被其他程式佔用。\n\n請在 config.ini 的 [hotkey] 裡換一個組合，然後重新啟動本工具（系統匣圖示右鍵 → 結束，再按兩下啟動）。",
        "en": "Could not register the hotkey {key}; another program probably uses it.\n\nChange it under [hotkey] in config.ini, then restart the tool (right-click the tray icon → Quit, then start it again).",
        "ja": "ショートカット {key} を登録できませんでした。他のプログラムが使用している可能性があります。\n\nconfig.ini の [hotkey] で別の組み合わせに変更し、ツールを再起動してください（トレイアイコンを右クリック → 終了、その後もう一度起動）。",
        "ko": "단축키 {key}을(를) 등록하지 못했습니다. 다른 프로그램이 사용 중일 수 있습니다.\n\nconfig.ini의 [hotkey]에서 다른 조합으로 바꾼 뒤 도구를 다시 시작하세요(트레이 아이콘 우클릭 → 종료 후 다시 실행)."},
    "截屏失败：{e}": {"zh-tw": "截圖失敗：{e}", "en": "Screen capture failed: {e}", "ja": "画面のキャプチャに失敗しました：{e}", "ko": "화면 캡처에 실패했습니다: {e}"},
    "缺少运行组件，请双击“{bat}”。\n{e}": {"zh-tw": "缺少執行元件，請按兩下“{bat}”。\n{e}", "en": "A required component is missing. Double-click “{bat}”.\n{e}", "ja": "必要なコンポーネントがありません。“{bat}” をダブルクリックしてください。\n{e}", "ko": "필요한 구성 요소가 없습니다. “{bat}”을(를) 더블클릭하세요.\n{e}"},
    "框选的内容本来就是{lang}，没有调用翻译": {"zh-tw": "框選的內容本來就是{lang}，沒有呼叫翻譯", "en": "The selection is already in {lang}; nothing was translated", "ja": "選択した内容はすでに{lang}のため、翻訳しませんでした", "ko": "선택한 내용이 이미 {lang}이므로 번역하지 않았습니다"},
    "仍然翻译 (Enter)": {"zh-tw": "仍然翻譯 (Enter)", "en": "Translate anyway (Enter)", "ja": "それでも翻訳 (Enter)", "ko": "그래도 번역 (Enter)"},
    "翻译时出错：{e}\n（详细信息见 运行日志.txt）": {"zh-tw": "翻譯時出錯：{e}\n（詳細資訊見 运行日志.txt）", "en": "Translation failed: {e}\n(details in 运行日志.txt)", "ja": "翻訳中にエラーが発生しました：{e}\n（詳細は 运行日志.txt を参照）", "ko": "번역 중 오류가 발생했습니다: {e}\n(자세한 내용은 运行日志.txt 참조)"},
    "复制图片失败：{e}": {"zh-tw": "複製圖片失敗：{e}", "en": "Could not copy the image: {e}", "ja": "画像をコピーできませんでした：{e}", "ko": "이미지를 복사하지 못했습니다: {e}"},
    "打不开 {path}：{e}": {"zh-tw": "打不開 {path}：{e}", "en": "Could not open {path}: {e}", "ja": "{path} を開けません：{e}", "ko": "{path}을(를) 열 수 없습니다: {e}"},
    "截图翻译已经在运行了。\n\n托盘图标在任务栏右下角（可能收在 ^ 里面）。": {
        "zh-tw": "截圖翻譯已經在執行了。\n\n系統匣圖示在工作列右下角（可能收在 ^ 裡面）。",
        "en": "Screen Translation is already running.\n\nThe tray icon is at the bottom right of the taskbar (it may be hidden under ^).",
        "ja": "画面翻訳はすでに実行中です。\n\nトレイアイコンはタスクバーの右下にあります（^ の中に隠れていることがあります）。",
        "ko": "화면 번역이 이미 실행 중입니다.\n\n트레이 아이콘은 작업 표시줄 오른쪽 아래에 있습니다(^ 안에 숨어 있을 수 있습니다)."},
    "启动失败：{e}\n\n详细信息见 运行日志.txt": {"zh-tw": "啟動失敗：{e}\n\n詳細資訊見 运行日志.txt", "en": "Failed to start: {e}\n\nDetails in 运行日志.txt", "ja": "起動に失敗しました：{e}\n\n詳細は 运行日志.txt を参照", "ko": "시작하지 못했습니다: {e}\n\n자세한 내용은 运行日志.txt 참조"},
    # ---------- API errors (translator.py) ----------
    "API Key 无效或没有权限（{status}），请检查 config.ini 里的 api_key。\n{short}": {
        "zh-tw": "API Key 無效或沒有權限（{status}），請檢查 config.ini 裡的 api_key。\n{short}",
        "en": "API key invalid or lacks permission ({status}). Check api_key in config.ini.\n{short}",
        "ja": "API キーが無効か権限がありません（{status}）。config.ini の api_key を確認してください。\n{short}",
        "ko": "API 키가 잘못되었거나 권한이 없습니다({status}). config.ini의 api_key를 확인하세요.\n{short}"},
    "找不到模型或接口地址不对（404）：请检查 config.ini 里的 base_url 和 model。\n{short}": {
        "zh-tw": "找不到模型或介面位址不對（404）：請檢查 config.ini 裡的 base_url 和 model。\n{short}",
        "en": "Model not found or wrong endpoint (404): check base_url and model in config.ini.\n{short}",
        "ja": "モデルが見つからないか、エンドポイントが間違っています（404）。config.ini の base_url と model を確認してください。\n{short}",
        "ko": "모델을 찾을 수 없거나 주소가 잘못되었습니다(404). config.ini의 base_url과 model을 확인하세요.\n{short}"},
    "额度不足或请求太频繁（{status}），请到该平台查看用量。\n{short}": {
        "zh-tw": "額度不足或請求太頻繁（{status}），請到該平台查看用量。\n{short}",
        "en": "Out of quota or too many requests ({status}). Check your usage on that platform.\n{short}",
        "ja": "残高不足またはリクエストが多すぎます（{status}）。プラットフォームで使用量を確認してください。\n{short}",
        "ko": "한도가 부족하거나 요청이 너무 많습니다({status}). 해당 플랫폼에서 사용량을 확인하세요.\n{short}"},
    "接口暂时出错（{status}），请稍后再试。": {"zh-tw": "介面暫時出錯（{status}），請稍後再試。", "en": "The service returned an error ({status}). Try again later.", "ja": "サービス側で一時的なエラーです（{status}）。しばらくしてからもう一度お試しください。", "ko": "서비스에 일시적인 오류가 있습니다({status}). 잠시 후 다시 시도하세요."},
    "请求被拒绝（{status}）：{short}": {"zh-tw": "請求被拒絕（{status}）：{short}", "en": "Request rejected ({status}): {short}", "ja": "リクエストが拒否されました（{status}）：{short}", "ko": "요청이 거부되었습니다({status}): {short}"},
    "连接接口超时，请检查网络（必要时在 config.ini 里设置 proxy）。": {"zh-tw": "連線介面逾時，請檢查網路（必要時在 config.ini 裡設定 proxy）。", "en": "The connection timed out. Check your network (set proxy in config.ini if needed).", "ja": "接続がタイムアウトしました。ネットワークを確認してください（必要なら config.ini で proxy を設定）。", "ko": "연결 시간이 초과되었습니다. 네트워크를 확인하세요(필요하면 config.ini에서 proxy 설정)."},
    "连不上接口 {url}：{e}": {"zh-tw": "連不上介面 {url}：{e}", "en": "Cannot reach {url}: {e}", "ja": "{url} に接続できません：{e}", "ko": "{url}에 연결할 수 없습니다: {e}"},
    "接口反复拒绝请求，请检查 config.ini 里的 base_url 和 model。": {"zh-tw": "介面反覆拒絕請求，請檢查 config.ini 裡的 base_url 和 model。", "en": "The endpoint keeps rejecting requests. Check base_url and model in config.ini.", "ja": "エンドポイントがリクエストを拒否し続けています。config.ini の base_url と model を確認してください。", "ko": "엔드포인트가 요청을 계속 거부합니다. config.ini의 base_url과 model을 확인하세요."},
    "API Key 无效或已被停用，请检查 config.ini 里的 api_key。": {"zh-tw": "API Key 無效或已被停用，請檢查 config.ini 裡的 api_key。", "en": "API key invalid or disabled. Check api_key in config.ini.", "ja": "API キーが無効か停止されています。config.ini の api_key を確認してください。", "ko": "API 키가 잘못되었거나 비활성화되었습니다. config.ini의 api_key를 확인하세요."},
    "这个 API Key 没有权限使用模型 {model}：{msg}": {"zh-tw": "這個 API Key 沒有權限使用模型 {model}：{msg}", "en": "This API key has no access to the model {model}: {msg}", "ja": "この API キーにはモデル {model} を使う権限がありません：{msg}", "ko": "이 API 키는 모델 {model}을(를) 사용할 권한이 없습니다: {msg}"},
    "找不到模型 {model}，请检查 config.ini 里的 model。": {"zh-tw": "找不到模型 {model}，請檢查 config.ini 裡的 model。", "en": "Model {model} not found. Check model in config.ini.", "ja": "モデル {model} が見つかりません。config.ini の model を確認してください。", "ko": "모델 {model}을(를) 찾을 수 없습니다. config.ini의 model을 확인하세요."},
    "请求太频繁或账户额度不足，请稍后再试，或到 Claude 控制台查看用量。": {"zh-tw": "請求太頻繁或帳戶額度不足，請稍後再試，或到 Claude 控制台查看用量。", "en": "Too many requests or out of credit. Try again later, or check your usage in the Claude Console.", "ja": "リクエストが多すぎるか残高が不足しています。しばらくしてから再試行するか、Claude Console で使用量を確認してください。", "ko": "요청이 너무 많거나 잔액이 부족합니다. 잠시 후 다시 시도하거나 Claude Console에서 사용량을 확인하세요."},
    "API 账户余额不足，请到 Claude 控制台充值。": {"zh-tw": "API 帳戶餘額不足，請到 Claude 控制台儲值。", "en": "Your API balance is empty. Add credit in the Claude Console.", "ja": "API の残高がありません。Claude Console でチャージしてください。", "ko": "API 잔액이 부족합니다. Claude Console에서 충전하세요."},
    "请求被拒绝：{msg}": {"zh-tw": "請求被拒絕：{msg}", "en": "Request rejected: {msg}", "ja": "リクエストが拒否されました：{msg}", "ko": "요청이 거부되었습니다: {msg}"},
    "连接 Claude 超时，请检查网络（必要时在 config.ini 里设置 proxy）。": {"zh-tw": "連線 Claude 逾時，請檢查網路（必要時在 config.ini 裡設定 proxy）。", "en": "The connection to Claude timed out. Check your network (set proxy in config.ini if needed).", "ja": "Claude への接続がタイムアウトしました。ネットワークを確認してください（必要なら config.ini で proxy を設定）。", "ko": "Claude 연결 시간이 초과되었습니다. 네트워크를 확인하세요(필요하면 config.ini에서 proxy 설정)."},
    "连不上 Claude 服务器，请检查网络；如果需要代理，请在 config.ini 里填写 proxy。": {"zh-tw": "連不上 Claude 伺服器，請檢查網路；如果需要代理，請在 config.ini 裡填寫 proxy。", "en": "Cannot reach the Claude servers. Check your network; if you need a proxy, set proxy in config.ini.", "ja": "Claude のサーバーに接続できません。ネットワークを確認し、プロキシが必要なら config.ini の proxy に設定してください。", "ko": "Claude 서버에 연결할 수 없습니다. 네트워크를 확인하고, 프록시가 필요하면 config.ini의 proxy를 설정하세요."},
    "Claude 服务器暂时出错（{status}），请稍后再试。": {"zh-tw": "Claude 伺服器暫時出錯（{status}），請稍後再試。", "en": "Claude's servers returned an error ({status}). Try again later.", "ja": "Claude のサーバーで一時的なエラーです（{status}）。しばらくしてからもう一度お試しください。", "ko": "Claude 서버에 일시적인 오류가 있습니다({status}). 잠시 후 다시 시도하세요."},
    "API 返回错误（{status}）：{msg}": {"zh-tw": "API 回傳錯誤（{status}）：{msg}", "en": "API error ({status}): {msg}", "ja": "API エラー（{status}）：{msg}", "ko": "API 오류({status}): {msg}"},
    "安全过滤拒绝了这段内容（多半是误判，常见于提到密钥、密码、账号的文字）。\n换个范围框选，避开那几行再试。": {
        "zh-tw": "安全過濾拒絕了這段內容（多半是誤判，常見於提到金鑰、密碼、帳號的文字）。\n換個範圍框選，避開那幾行再試。",
        "en": "The safety filter refused this content (usually a false positive; common with text about keys, passwords or accounts).\nSelect a different area that avoids those lines and try again.",
        "ja": "安全フィルターがこの内容を拒否しました（多くは誤判定で、キーやパスワード、アカウントに関する文字でよく起きます）。\nその行を避けて別の範囲を選び直してください。",
        "ko": "안전 필터가 이 내용을 거부했습니다(대부분 오판이며 키, 비밀번호, 계정 관련 텍스트에서 흔합니다).\n해당 줄을 피해 다른 영역을 선택해 다시 시도하세요."},
    "截图里的文字太多，译文被截断了，请分成几次框选。": {"zh-tw": "截圖裡的文字太多，譯文被截斷了，請分成幾次框選。", "en": "Too much text in the screenshot; the translation was cut off. Select it in smaller parts.", "ja": "文字が多すぎて翻訳が途中で切れました。何回かに分けて選択してください。", "ko": "텍스트가 너무 많아 번역이 잘렸습니다. 여러 번에 나누어 선택하세요."},
    "模型返回的格式不正确，请重试。": {"zh-tw": "模型回傳的格式不正確，請重試。", "en": "The model returned malformed output. Try again.", "ja": "モデルの応答形式が正しくありません。もう一度お試しください。", "ko": "모델의 응답 형식이 올바르지 않습니다. 다시 시도하세요."},
    "⚠ 这几行被安全过滤拦下了（误判），没有译文": {"zh-tw": "⚠ 這幾行被安全過濾攔下了（誤判），沒有譯文", "en": "⚠ These lines were blocked by the safety filter (false positive); no translation", "ja": "⚠ この数行は安全フィルターに止められました（誤判定）。訳文なし", "ko": "⚠ 이 줄들은 안전 필터에 걸렸습니다(오판). 번역 없음"},
    "截图里没有找到需要翻译的文字。": {"zh-tw": "截圖裡沒有找到需要翻譯的文字。", "en": "No text to translate was found in the screenshot.", "ja": "翻訳する文字が見つかりませんでした。", "ko": "번역할 텍스트를 찾지 못했습니다."},
    # ---------- config errors (settings.py) ----------
    "config.ini 格式有误，请检查：\n{e}": {"zh-tw": "config.ini 格式有誤，請檢查：\n{e}", "en": "config.ini is malformed:\n{e}", "ja": "config.ini の書式に誤りがあります：\n{e}", "ko": "config.ini 형식에 오류가 있습니다:\n{e}"},
    "config.ini 里的 provider 填写有误：{provider}\n可选：claude、openai": {"zh-tw": "config.ini 裡的 provider 填寫有誤：{provider}\n可選：claude、openai", "en": "Invalid provider in config.ini: {provider}\nOptions: claude, openai", "ja": "config.ini の provider が正しくありません：{provider}\n選択肢：claude、openai", "ko": "config.ini의 provider가 잘못되었습니다: {provider}\n선택 가능: claude, openai"},
    "config.ini 里的 model 填写有误：{model}\n可选：{models}": {"zh-tw": "config.ini 裡的 model 填寫有誤：{model}\n可選：{models}", "en": "Invalid model in config.ini: {model}\nOptions: {models}", "ja": "config.ini の model が正しくありません：{model}\n選択肢：{models}", "ko": "config.ini의 model이 잘못되었습니다: {model}\n선택 가능: {models}"},
    "config.ini 里的 base_url 要以 http:// 或 https:// 开头：{url}": {"zh-tw": "config.ini 裡的 base_url 要以 http:// 或 https:// 開頭：{url}", "en": "base_url in config.ini must start with http:// or https://: {url}", "ja": "config.ini の base_url は http:// か https:// で始めてください：{url}", "ko": "config.ini의 base_url은 http:// 또는 https://로 시작해야 합니다: {url}"},
    "config.ini 里的 base_url 用的是不加密的 http://，API Key 和截图会明文传出去。请改成 https://（只有本机和局域网地址才允许 http）：{url}": {
        "zh-tw": "config.ini 裡的 base_url 用的是未加密的 http://，API Key 和截圖會以明文傳出去。請改成 https://（只有本機和區域網路位址才允許 http）：{url}",
        "en": "base_url in config.ini uses unencrypted http://, so the API key and screenshots would travel in clear text. Use https:// (http is only accepted for this machine or the local network): {url}",
        "ja": "config.ini の base_url が暗号化されていない http:// です。API キーとスクリーンショットが平文で送信されます。https:// にしてください（http はこの PC とローカルネットワークのみ可）：{url}",
        "ko": "config.ini의 base_url이 암호화되지 않은 http://입니다. API 키와 스크린샷이 평문으로 전송됩니다. https://로 바꾸세요(http는 이 PC와 로컬 네트워크에서만 허용): {url}"},
    "还没有填写 API Key。\n\n请打开本工具文件夹里的 config.ini，把 API Key 粘贴到 “api_key =” 后面，保存即可，不需要重启。": {
        "zh-tw": "還沒有填寫 API Key。\n\n請開啟本工具資料夾裡的 config.ini，把 API Key 貼到 “api_key =” 後面，儲存即可，不需要重新啟動。",
        "en": "No API key yet.\n\nOpen config.ini in the program folder, paste your API key after “api_key =” and save. No restart needed.",
        "ja": "API キーがまだ設定されていません。\n\nプログラムのフォルダーにある config.ini を開き、“api_key =” の後に API キーを貼り付けて保存してください。再起動は不要です。",
        "ko": "아직 API 키가 없습니다.\n\n프로그램 폴더의 config.ini를 열어 “api_key =” 뒤에 API 키를 붙여넣고 저장하세요. 다시 시작할 필요는 없습니다."},
    "config.ini 里的 api_key 含有空格或中文字符，请检查是否粘贴完整、正确。": {"zh-tw": "config.ini 裡的 api_key 含有空格或中文字元，請檢查是否貼上完整、正確。", "en": "api_key in config.ini contains spaces or non-ASCII characters. Check that it was pasted completely and correctly.", "ja": "config.ini の api_key に空白や全角文字が含まれています。正しく貼り付けられているか確認してください。", "ko": "config.ini의 api_key에 공백이나 비ASCII 문자가 있습니다. 올바르게 붙여넣었는지 확인하세요."},
    # ---------- history file ----------
    "【原文】": {"zh-tw": "【原文】", "en": "[Source]", "ja": "【原文】", "ko": "[원문]"},
    "【译文】": {"zh-tw": "【譯文】", "en": "[Translation]", "ja": "【訳文】", "ko": "[번역]"},
    "（日文等本机 OCR 不支持的文字：原文由模型直接从截图读取）": {
        "zh-tw": "（日文等本機 OCR 不支援的文字：原文由模型直接從截圖讀取）",
        "en": "(text the local OCR does not support, e.g. Japanese: the source was read from the screenshot by the model)",
        "ja": "（日本語などローカル OCR が対応していない文字：原文はモデルがスクリーンショットから直接読み取り）",
        "ko": "(일본어 등 로컬 OCR이 지원하지 않는 텍스트: 원문은 모델이 스크린샷에서 직접 읽음)"},
}
