"""綜合測試 GUI E2E（in-process）：UI 結構 + 真 worker 端到端（futu rows，唔使 IB）。

Check: maximized / 八欄 table shape（參數拆多欄）/ 無輸入欄位 / objectNames /
       單行經真 BrokerClient 跑到 ✅ PASS / 成功行「▸ 數據」拆疊展開+收埋 /
       誠實失敗行 ⚠️ EXPECTED FAIL / **3 rows 同時並發**全部完成且正確 /
       Run All 按鈕 wiring（monkeypatch enqueue）/ Stop 清理所有結果（包括運行中嘅行）/ closeEvent 釋放 thread /
       🤖 默認隱藏 EXPECTED FAIL + 按鈕狀態機（idle → Stop 灰；運行中 → Run All 灰、該行 ▶ 灰）/
       🤖 FulltestPage：`.ui` 骨架（root objectName + 零 margin + embedSlot）→ takeCentralWidget 成個
          UI 嵌入頁下面 → 頁面級 QSS 有根 → aboutToQuit 清理鏈照樣收工。
前置：OpenD 開緊（IB 唔使 — 呢個 E2E 只跑 futu rows）。
Run: python .scratch/e2e_gui_fulltest.py   (exit 0=PASS / 1=FAIL)

🤖 print 全部 ASCII（cp950 console 規則）；worker_ready race rule：等 thread.worker 之後 pump(0.5) 先撳按鈕。
"""
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtCore import QMargins
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QSpinBox, QTableWidget

app = QApplication(sys.argv)   # 🤖 先建 app 再 import gui_fulltest（widget 建立時要 QGuiApplication）

from gateway.pages import gui_fulltest


def _asc(s):
    """print-safe：非 ASCII → '?'（cp950 console 唔會炸）。"""
    return str(s).encode('ascii', 'replace').decode()


FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" [{_asc(detail)}]" if detail and not cond else ""), flush=True)
    if not cond:
        FAILS.append(name)


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


win = gui_fulltest.MainWindow()
win.showMaximized()   # 同 production main() 同一條路

# worker_ready race（memory rule）：等 thread.worker 之後 pump(0.5) 先至 signal slot connect 好
end = time.monotonic() + 10
while time.monotonic() < end and win.thread.worker is None:
    app.processEvents()
    time.sleep(0.01)
check("worker ready", win.thread.worker is not None)
pump(0.5)

# ── structure ──
check("maximized", win.isMaximized())
t = win.table
check("table shape 8-col", t.rowCount() == len(gui_fulltest.TESTS) + len(gui_fulltest.BROKER_SECTIONS)
      and t.columnCount() == 8, f"rows={t.rowCount()} cols={t.columnCount()}")
hdrs = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())] if t.columnCount() else []
check("param columns split", hdrs[:7] == ["測試", "code", "market", "ktype", "num", "broker", "method"], str(hdrs))
# 🤖 broker 分區：每段一個 header row（col 0 有 ▍ 文字 + setSpan 橫跨全欄）
hdr_rows = [r for r in range(t.rowCount()) if t.item(r, 0) is not None and t.item(r, 0).text().startswith("▍")]
check("section headers count", len(hdr_rows) == len(gui_fulltest.BROKER_SECTIONS), f"rows={hdr_rows}")
for r, (_bk, title) in zip(hdr_rows, gui_fulltest.BROKER_SECTIONS):
    check(f"header text {title.split(' · ')[0]}", title.split(' · ')[0] in t.item(r, 0).text(),
          str(t.item(r, 0).text()))
    # 🤖 PySide6 冇綁定 spanAt() — 用 rowSpan()/columnSpan() 讀返 setSpan(1, 8)
    check(f"header span row{r}", (t.rowSpan(r, 0), t.columnSpan(r, 0)) == (1, 8),
          f"rowSpan={t.rowSpan(r, 0)} colSpan={t.columnSpan(r, 0)}")
# 🤖 row mapping：每個 test idx → 唯一非 header table row
check("row mapping complete", all(win._test_to_row[i] is not None for i in range(len(gui_fulltest.TESTS)))
      and len(set(win._test_to_row)) == len(gui_fulltest.TESTS)
      and all(r not in hdr_rows for r in win._test_to_row))
check("no input fields", len(t.findChildren(QLineEdit)) == 0
      and len(win.centralWidget().findChildren(QSpinBox)) == 0)
check("objectNames", win.run_all_btn.objectName() == 'run_all'
      and win.stop_btn.objectName() == 'stop'
      and win.progress_lbl.objectName() == 'progress'
      and win.summary_lbl.objectName() == 'summary')

# 🤖 用戶要求：默認隱藏 EXPECTED FAIL（display-only — Run All 仍然 enqueue 全部 18）
check("hide_ef btn", win.hide_ef_btn.objectName() == 'hide_expected_fail' and win.hide_ef_btn.isCheckable())
check("hide_ef default ON (默認隱藏 EF)", win.hide_ef_btn.isChecked())
default_hidden = all(t.isRowHidden(win._test_to_row[i]) for i, s in enumerate(gui_fulltest.TESTS) if s['expect'] is False)
default_visible = all(not t.isRowHidden(win._test_to_row[i]) for i, s in enumerate(gui_fulltest.TESTS) if s['expect'] is not False)
check("default hides expected-fail rows only", default_hidden and default_visible)

# 🤖 用戶要求：RUN/STOP/RUN ALL 未開始要灰 — 狀態機 idle 態（worker ready、冇運行中）
check("idle: run_all + row ▶ enabled, stop grey",
      win.run_all_btn.isEnabled() and all(b.isEnabled() for b in win._row_btns)
      and not win.stop_btn.isEnabled())

win.hide_ef_btn.click()   # toggle OFF → 全部顯示返
pump(0.2)
check("toggle OFF shows all rows", all(not t.isRowHidden(r) for r in win._test_to_row))
captured = []
orig_enqueue = win._worker.enqueue
win._worker.enqueue = lambda idxs: captured.append(list(idxs))
try:
    win.run_all_btn.click()
    pump(0.2)
finally:
    win._worker.enqueue = orig_enqueue
check("run all unaffected by hidden rows", len(captured) == 1 and captured[0] == list(range(len(gui_fulltest.TESTS))))
win._inflight.clear()
win._sync_run_controls()   # 🤖 手動清 inflight → 同步返 UI idle 態
win.hide_ef_btn.click()   # toggle back ON（恢復默認隱藏）
pump(0.2)


def find_row(broker, method, code):
    for i, s in enumerate(gui_fulltest.TESTS):
        if (s['broker'], s['method'], s['code']) == (broker, method, code):
            return i
    raise AssertionError(f"row not found: {broker}/{method}/{code}")


def wait_row_done(i, deadline=30):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)
        txt = win._res_labels[i].text()   # 🤖 col 2 而家係 cell widget（QLabel），唔再係 QTableWidgetItem
        if txt != "—" and "運行中" not in txt:
            return txt
    return win._res_labels[i].text()


# ── real run #1：futu get US.NVDA → ✅ PASS + 數據拆疊 ──
i = find_row('futu', 'get_kline', 'US.NVDA')
win.run_row(i)
txt = wait_row_done(i)
check("futu get US.NVDA -> PASS", txt.startswith("✅"), txt[:120])

# 數據拆疊：成功行「▸ 數據」按鈕出現 → 展開（mini table 最後 50 rows，headers 有 time_key/close）→ 再收埋
dbtn, dtbl = win._data_btns[i], win._data_tables[i]
check("data btn visible on success", dbtn.isVisible())
pump(0.2)
dbtn.click()
pump(0.2)
hdrs = [dtbl.horizontalHeaderItem(c).text() for c in range(dtbl.columnCount())] if dtbl.columnCount() else []
check("data table expanded", dtbl.isVisible() and 0 < dtbl.rowCount() <= 50, f"rows={dtbl.rowCount()}")
check("data headers sane", 'time_key' in hdrs and 'close' in hdrs, str(hdrs))
# 🤖 按時間反向排序：最新 bar 喺第一行（ISO time_key 字串比較有效）
tk_col = hdrs.index('time_key')
first_tk = dtbl.item(0, tk_col).text()
last_tk = dtbl.item(dtbl.rowCount()-1, tk_col).text()
check("data table newest-first", first_tk >= last_tk, f"first={first_tk} last={last_tk}")
dbtn.click()
pump(0.2)
check("data table collapsed again", not dtbl.isVisible())

# ── real run #2：futu get US.NONEXIST123 → ⚠️ EXPECTED FAIL（誠實失敗路徑）──
j = find_row('futu', 'get_kline', 'US.NONEXIST123')
win.run_row(j)
txt = wait_row_done(j)
check("futu NONEXIST -> expected fail", txt.startswith("⚠️"), txt[:160])

# ── 並發：3 futu rows 同時開跑（真 enqueue）→ 全部完成且正確 ──
pa = find_row('futu', 'get_kline', 'HK.00700')
pb = find_row('futu', 'get_kline', 'HK.HSImain')
pc = find_row('futu', 'stream_kline', 'HK.00700')
t0 = time.monotonic()
for r in (pa, pb, pc):
    win.run_row(r)   # 🤖 三個 enqueue 即刻各開一個 task — 同時並發（3 條獨立 OpenD 連線）
end = time.monotonic() + 60
while time.monotonic() < end:
    app.processEvents()
    time.sleep(0.05)
    if all(win._res_labels[r].text() != "—" and "運行中" not in win._res_labels[r].text() for r in (pa, pb, pc)):
        break
elapsed = time.monotonic() - t0
sa, sb, sc = (win._res_labels[r].text() for r in (pa, pb, pc))
check("parallel HK.00700 get -> PASS", sa.startswith("✅"), sa[:120])
check("parallel HSImain get -> PASS", sb.startswith("✅"), sb[:120])
check("parallel 00700 stream -> PASS", sc.startswith("✅"), sc[:120])
print(f"INFO parallel 3 rows elapsed={elapsed:.1f}s (sequential would be ~15s+)", flush=True)

# ── Run All 按鈕 wiring（monkeypatch enqueue capture，唔真跑）──
captured = []
orig_enqueue = win._worker.enqueue
win._worker.enqueue = lambda idxs: captured.append(list(idxs))
try:
    win.run_all_btn.click()
    pump(0.2)
finally:
    win._worker.enqueue = orig_enqueue
check("run all wired", len(captured) == 1 and captured[0] == list(range(len(gui_fulltest.TESTS))),
      str(captured)[:160])
win._inflight.clear()   # 🤖 run_all 已標記全部 inflight — 清走先至可以繼續真跑
win._sync_run_controls()   # 🤖 同步返 UI idle 態

# ── Stop：stream row 運行中撳 Stop → 所有結果清理（包括運行中嗰行）──
k = find_row('futu', 'stream_kline', 'US.NVDA')
win.run_row(k)
end = time.monotonic() + 10
while time.monotonic() < end and "運行中" not in win._res_labels[k].text():
    app.processEvents()
    time.sleep(0.05)
check("stream row started", "運行中" in win._res_labels[k].text(), win._res_labels[k].text())
# 🤖 狀態機：運行中 → Run All 灰（防重入）、Stop 亮、運行中嗰行 ▶ 灰
check("running: run_all grey, stop lit, running row ▶ grey",
      not win.run_all_btn.isEnabled() and win.stop_btn.isEnabled()
      and not win._row_btns[k].isEnabled())
win.stop_btn.click()   # 🤖 stop() 同步清 GUI；worker cleanup（cancel + broker teardown）之後 stopped signal 再清一次
end = time.monotonic() + 8
while time.monotonic() < end:
    app.processEvents()
    time.sleep(0.05)
    if all(win._res_labels[r].text() == "—" for r in range(len(gui_fulltest.TESTS))):
        break
cleared = [win._res_labels[r].text() for r in range(len(gui_fulltest.TESTS))]
check("stop clears all results", all(x == "—" for x in cleared), str(cleared)[:200])
check("after stop: back to idle (run_all lit, stop grey)",
      win.run_all_btn.isEnabled() and not win.stop_btn.isEnabled())
check("stop clears summary", win.summary_lbl.text() == "")

# ── closeEvent：thread 乾淨退出（釋放 client）──
win.close()
end = time.monotonic() + 5
while time.monotonic() < end and win.thread.isRunning():
    app.processEvents()
    time.sleep(0.01)
check("thread exited after close", not win.thread.isRunning())

# ── FulltestPage: .ui skeleton + takeCentralWidget embedding (lazy import: app must exist first) ──
from gateway.pages.fulltest_page import FulltestPage

page = FulltestPage()
page.show()
pump(0.3)
check("page .ui root objectName + zero-margin layout",
      page.objectName() == 'fulltest_page' and page.layout() is not None
      and page.layout().contentsMargins() == QMargins(0, 0, 0, 0))
check("embedSlot is the only slot and holds the embedded widget",
      page.embedSlot.count() == 1 and page.embedSlot.itemAt(0).widget() is not None)
check("whole gui_fulltest UI lives under the page (QSS cascade has a root)",
      page.findChild(QPushButton, 'run_all') is not None
      and len(page.findChildren(QTableWidget)) >= 1)
check("page-level theme QSS applied", 'QWidget#fulltest_page' in page.styleSheet())
page._on_app_quit()   # aboutToQuit path -> original closeEvent cleanup chain
end = time.monotonic() + 5
while time.monotonic() < end and page._win.thread.isRunning():
    app.processEvents()
    time.sleep(0.01)
check("page quit closes embedded thread (idempotent)", not page._win.thread.isRunning())
page._on_app_quit()
check("second _on_app_quit is a no-op", page._quit_done)

print("=" * 50, flush=True)
print("E2E_GUI_FULLTEST " + ("PASS" if not FAILS else f"FAIL ({len(FAILS)}): {FAILS}"), flush=True)
sys.exit(1 if FAILS else 0)
