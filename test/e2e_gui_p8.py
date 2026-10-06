"""P8 GUI E2E（in-process）：fuzzy completer + i18n 繁中/EN 切換 + FetchThread wiring。

唔打網絡 — FakeDir 包住真 index（is_stale=False → startup 唔會 auto-fetch）。
前置：modules/symbol_index.json 存在（python -m modules.symbol_search fetch 建過）。
Run: python test/e2e_gui_p8.py   (exit 0=PASS / 1=FAIL)

🤖 print 全部 ASCII（cp950 console 規則 — CJK detail 經 _asc() sanitize）；
   worker_ready race rule：等 thread.worker 之後 pump(0.5) 先至 signal slot connect 好。
"""
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
for p in (PROJECT_ROOT, TEST_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)   # 🤖 先建 app 再 import gui_kline（widget 建立時要 QGuiApplication）

import gui_kline
from modules.symbol_search import get_directory as real_get_directory


def _asc(s):
    """print-safe：非 ASCII → '?'（cp950 console 唔會炸）。"""
    return str(s).encode('ascii', 'replace').decode()


class FakeDir:
    """包住真 index：is_stale=False（hermetic — startup 唔會 auto-fetch），其餘 delegate。"""

    def __init__(self, real):
        self._real = real
        self.entries = real.entries
        self.fetched_at = real.fetched_at

    @property
    def is_stale(self):
        return False

    def search(self, q, limit=20):
        return self._real.search(q, limit)

    def display_name(self, code, lang='zh'):
        return self._real.display_name(code, lang)

    def has_code(self, c):
        return self._real.has_code(c)


class FakeFetchDir(FakeDir):
    """fetch() 唔打網絡 — 驗證 FetchThread signal wiring（progress + done）。"""

    def fetch(self, markets=('US', 'HK'), progress_cb=None):
        if progress_cb:
            progress_cb('US/STOCK', 10)
            progress_cb('HK/STOCK', 5)
        return True, "42 symbols (fake)"


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" [{_asc(detail)}]" if detail and not cond else ""), flush=True)
    if not cond:
        FAILS.append(name)


real = real_get_directory()
check("index loaded", len(real.entries) > 0,
      f"entries={len(real.entries)} - run 'python -m modules.symbol_search fetch' first")

gui_kline.get_directory = lambda: FakeDir(real)   # 🤖 hermetic：MainWindow.__init__ 用呢個

win = gui_kline.MainWindow()
win.show()

# worker_ready race（memory rule）：等 thread.worker 之後 pump(0.5) 先至 signal slot connect 好
end = time.monotonic() + 10
while time.monotonic() < end and win.thread.worker is None:
    app.processEvents()
    time.sleep(0.01)
check("worker ready", win.thread.worker is not None)
pump(0.5)

# ── initial state（zh default）──
check("title zh", "stream_kline" in win.windowTitle(), win.windowTitle())
sym = win.sym_label.text()
check("sym label zh has name", sym.startswith("HK.HSImain") and "恒指期貨主連" in sym, sym)

# ── completer：CJK fuzzy（繁體 input → 簡體 match）──
win.code_edit.setText("騰訊")
pump(0.2)
m = win.completer.model()
first = m.stringList()[0] if m.rowCount() > 0 else ""
check("completer CJK hits", m.rowCount() > 0 and first.startswith("HK.00700"),
      f"rows={m.rowCount()} first={first!r}")

# ── completer：pass-through（冇 hit → 空 model，唔彈）──
win.code_edit.setText("ZZZQQQ123")
pump(0.2)
check("completer passthrough empty", win.completer.model().rowCount() == 0,
      f"rows={win.completer.model().rowCount()}")

# ── completer：準確 code → 唔彈（has_code skip）──
win.code_edit.setText("HK.00700")
pump(0.2)
check("completer exact-code no popup", win.completer.model().rowCount() == 0,
      f"rows={win.completer.model().rowCount()}")

# 🤖 還原 default code — i18n section 斷言係為 HK.HSImain（冇 en name → honest fallback）設計
win.code_edit.setText("HK.HSImain")
pump(0.2)

# ── i18n：切 EN → title / panel btn / sym name 跟隨 ──
win.lang_combo.setCurrentIndex(1)
pump(0.2)
check("title en", win.windowTitle() == gui_kline.t('en', 'title'), win.windowTitle())
check("panel btn en", "Data frame" in win.panel.btn.text(), win.panel.btn.text())
sym_en = win.sym_label.text()
# HK.HSImain 冇 en name → honest fallback 原生（簡體）— 唔係繁體
check("sym label en fallback", sym_en.startswith("HK.HSImain") and "期货" in sym_en and "期貨" not in sym_en, sym_en)

# ── i18n：切返繁中 → s2t 轉返繁體 ──
win.lang_combo.setCurrentIndex(0)
pump(0.2)
check("sym label zh back", "恒指期貨主連" in win.sym_label.text(), win.sym_label.text())

# ── FetchThread wiring（fake fetch，唔打網絡）──
ft = gui_kline.FetchThread(FakeFetchDir(real))
events = []
ft.progress.connect(lambda l, n: events.append(('p', l, n)))
ft.done.connect(lambda ok, msg: events.append(('d', ok, msg)))
ft.start()
end = time.monotonic() + 5
while time.monotonic() < end and ft.isRunning():
    app.processEvents()
    time.sleep(0.01)
pump(0.2)
check("fetch progress signals", any(e[0] == 'p' for e in events), str(events))
check("fetch done ok", ('d', True, "42 symbols (fake)") in events, str(events))

# ── E2E hooks（objectName）──
check("objectNames", win.code_edit.objectName() == "code_edit"
      and win.lang_combo.objectName() == "lang_combo"
      and win.fetch_btn.objectName() == "fetch_btn")

win.close()
pump(0.3)

print("=" * 50, flush=True)
print("E2E_GUI_P8 " + ("PASS" if not FAILS else f"FAIL ({len(FAILS)}): {FAILS}"), flush=True)
sys.exit(1 if FAILS else 0)
