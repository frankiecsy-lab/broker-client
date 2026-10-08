"""One Gate 全域標的代碼輸入 — 模糊輸入（fuzzy）單一事實來源。

所有頁嘅「標的代碼」QLineEdit（K線 / 行情格 / FUTU 交易 / 標的收藏）一律經呢度，
唔准各頁自己砌 QCompleter（曾經砌過三份，行為唔齊：收藏頁打中文名永遠冇候選）。

- **FuzzyCompleter**：model = 搜尋 hits，item =「CODE  名稱（跟語言）」→ 打中文名都有候選；
  `filterAcceptsRow` 全放行（排序已經喺 search 做咗，QCompleter 內建 prefix filter 唔識重排、
  亦唔識中文名 — 靠佢 filter 就永遠 match 唔到）。
- **pass-through**：冇 hit / 輸入已經係準確 code → 空 model 唔彈 popup，用戶打咩都照樣提交。
- **attach_symbol_input()**：一次裝好 debounce + completer + activated（揀咗淨返乾淨 CODE 入欄）。
- 搜尋來源由頁提供：`make_search(directory)` = 本地 index；異步來源（交易頁 worker）自己 call
  `set_hits(entries)` — 同一個 item 格式 / popup 規則。

🤖 modules.symbol_search 頂層 import futu（要幾秒）→ display_for 首次用先 lazy import：
import 呢個檔本身唔應該拖慢 page 啟動（交易頁靠 warm-up Hide 成本）。
"""
from PySide6.QtCore import Qt, QTimer, QStringListModel  # 🤖 QStringListModel 喺 QtCore（唔係 QtWidgets）
from PySide6.QtWidgets import QCompleter  # noqa: E402

from gateway.i18n import DEFAULT_LANG  # noqa: E402

DEBOUNCE_MS = 200          # 打完先 query — 唔係每按一次都 search 一次全 index
CAND_LIMIT = 50            # dropdown 候選上限（用戶：任何一段字母/中文都要「睇到」對應結果 → 20 太容易截走）

_TYPES_CORE = object()     # sentinel → 跟 symbol_search.search() 預設（autocomplete 唔被窩輪污染）

_display_for = None


def display_name(entry, lang):
    """entry → 顯示名跟語言（symbol_search.display_for 同一把尺；lazy：唔拖慢 import）。
       頁要喺 completer 以外顯示名稱（例：交易頁 hint）都照用呢個，唔准自己再寫一份語言分支。"""
    global _display_for
    if _display_for is None:
        from modules.symbol_search import display_for
        _display_for = display_for
    return _display_for(entry, lang)


def code_of(item_text):
    """dropdown item =「CODE  名稱」→ CODE（code 本身唔會含空格）。"""
    return str(item_text).split(maxsplit=1)[0] if str(item_text).strip() else ''


class FuzzyCompleter(QCompleter):
    """模糊輸入 completer。search_fn(q) → [entry]（已排序）；None = 頁自己餵 set_hits（異步來源）。"""

    def __init__(self, search_fn=None, parent=None, lang=DEFAULT_LANG):
        super().__init__([], parent)
        self._search_fn = search_fn
        self.lang = lang   # 頁 retranslate 直接改呢個 attribute（display_for 食 GUI 語言碼）
        self.setCaseSensitivity(Qt.CaseInsensitive)
        self.setCompletionMode(QCompleter.PopupCompletion)
        self.setModel(QStringListModel())

    def set_query(self, q):
        """每次 keypress（debounce 之後）call。回傳有冇 hit。"""
        if self._search_fn is None:
            return False   # 異步來源（例：交易頁 worker）→ 由頁 call set_hits()
        q = str(q).strip()
        return self.set_hits(self._search_fn(q) if q else [])

    def set_hits(self, hits):
        """hits（index entry）→「CODE  名稱」model + 彈 popup。回傳有冇 hit。
           🤖 異步來源（worker 返嚟）都要經呢度 — 先至會主動 complete()：QCompleter 只喺 key event
           嗰刻彈 popup，model 之後先填滿使用者實測係「冇反應」。"""
        items = []
        for e in hits:
            name = display_name(e, self.lang)
            items.append(f"{e['code']}  {name}" if name else e['code'])
        self.model().setStringList(items)
        if items and self._typing_here():
            self.complete()
        return bool(items)

    def _typing_here(self):
        """得用戶緊喺呢個欄輸入先彈窗 — 狀態還原 / 程式 setText 唔好彈出阻眼。"""
        w = self.widget()
        return w is None or w.hasFocus()

    def filterAcceptsRow(self, index, parent):
        return True   # model 只含 search hits — 全部接受（排序已經喺 search 做咗）


def make_search(directory, types=_TYPES_CORE, limit=CAND_LIMIT):
    """本地 symbol index 標準搜尋 fn：已係準確 code → 冇候選（揀完唔會再彈返 popup 阻眼）。
       types：預設 CORE（交易/行情/收藏 autocomplete）；傳 None = 全量（含窩輪）。"""
    def _search(q):
        t = types
        if t is _TYPES_CORE:
            from modules.symbol_search import CORE_TYPES
            t = CORE_TYPES
        return [] if directory.has_code(q) else directory.search(q, limit=limit, types=t)
    return _search


def apply_item(edit, item_text, on_activate=None):
    """揀咗候選 → 欄入面淨返 CODE。🤖 QCompleter 會喺 activated 之後先將成串 item 文字寫入欄
       → singleShot(0) 喺事件尾蓋返過嚟（欄入面永遠係乾淨 code，落單/訂閱唔會混中文名）。"""
    code = code_of(item_text)
    if not code:
        return
    edit.setText(code)
    QTimer.singleShot(0, lambda: edit.setText(code))
    if on_activate is not None:
        on_activate(code)


def attach_symbol_input(edit, search_fn=None, lang=DEFAULT_LANG, debounce_ms=DEBOUNCE_MS,
                        on_activate=None):
    """幫標的代碼 QLineEdit 裝上模糊輸入：textChanged → debounce → set_query；
       activated → 淨返 CODE 入欄 + call on_activate(code)。
       search_fn=None → 唔裝 debounce（頁自己 set_hits，例：交易頁 worker 異步）。
       回傳 completer（頁攞住引用：防 GC + 可改 lang / 餵 hits）。"""
    comp = FuzzyCompleter(search_fn, edit, lang=lang)
    edit.setCompleter(comp)
    if search_fn is not None:
        timer = QTimer(edit)
        timer.setSingleShot(True)
        timer.setInterval(debounce_ms)
        timer.timeout.connect(lambda: comp.set_query(edit.text()))
        edit.textChanged.connect(lambda _q: timer.start())
    comp.activated.connect(lambda text: apply_item(edit, text, on_activate))
    return comp
