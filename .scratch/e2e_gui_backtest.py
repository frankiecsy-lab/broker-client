"""E2E GUI test — 回測頁（ticket #33b + #33c 覆核圖 + #34e 持倉模式）。

Run: python .scratch/e2e_gui_backtest.py   (from project root; QT_QPA_PLATFORM=offscreen 自動設定)
全 hermetic：fake broker（注入 client_factory）+ fake symbol index + tmp state 檔 —
唔打真 broker、唔食真 symbol index（唔拖慢、唔受開市影響）。

Flow:
1. `.ui` 骨架：root objectName / 輸入控件 / 四個 tab / 四個 grid slot / 兩個表 / og stamp / QSS 根
2. 選項內容全部由 domain 填：券商 = registry、週期 = quotes KTYPES、spin 範圍 = backtest.clamp_*、
   持倉模式 = position_model.MODES（同量化交易頁同一份 enum）、
   指標卡 = METRIC_DEFS（59 張、四組各就各位、未跑一律 '—'、caption = i18n）
3. 執行：經真 widget（填標的 → 揀策略 → 撳 run）→ worker → fake get_kline（斷言收到 kline_num/ktype/broker）
   → 指標卡出數 + 交易明細 + 被忽略訊號表
4. 用戶核心口徑（唔准復利 / 持倉處理 / 成本可調）逐項斷言：total_ret = Σ net、每筆等額注碼、
   成本逐邊計（入場 ×(1+slip)、出場 ×(1−slip)、手續費 ×2）、warmup = max(slow)、
   持倉中 B 被如實計數並入表、未平倉 mark-to-market
4b. 持倉模式三档（長倉/短倉/雙向，用戶：「BACKTEST 都可以選」）：方向欄逐筆如實、沽空反號
    （價升 → 短倉虧）、雙向反手 = 同一根先平後開、被忽略兩邊都計、n_short_trades、轉返長倉結果可重現
5. 遲到結果作廢（token）、三句唔同失敗、表排序、紅漲綠跌 sign property、
   state 持久化 + 重載（含持倉模式，垃圾 mode 如實 clamp 返默認）
6. 覆核圖：兩張入 `bt_chartSlot`、曲線 = 是次回測嘅 `curve` 原樣、K 圖 bars/entry = 是次回測用開
   嘅一份、圖上 B/S == 交易明細 + 被忽略表（同一個 trade_marks）、QPainter 真行到、
   theme/語言即刻跟（語義色唔變）、失敗即收圖返還提示句
7. 三語 retranslate（tab 標題/表頭/卡 caption/持倉模式選項，揀咗嘅照留）、
   全部 i18n 鍵三語齊、shell 註冊、收工清理
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gateway.state_store as state_store  # noqa: E402

_TMPDIR = tempfile.mkdtemp(prefix='e2e_backtest_')
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state.json'   # 🤖 hermetic：tmp state 檔

from PySide6.QtCore import QMargins, Qt  # noqa: E402
from PySide6.QtWidgets import (QApplication, QGridLayout, QGroupBox,  # noqa: E402
                               QTableView)
import pandas as pd  # noqa: E402

FAILURES = []
TOTAL = 0


def check(name, ok, detail=''):
    global TOTAL
    TOTAL += 1
    print(('  ✅ ' if ok else '  ❌ ') + name + (f'  [{detail}]' if not ok and detail else ''))
    if not ok:
        FAILURES.append(name)


def wait_for(app, cond, what='?', timeout=15.0):
    t0 = time.time()
    while not cond():
        app.processEvents()
        if time.time() - t0 > timeout:
            check(f'TIMEOUT waiting: {what}', False)
            return False
    return True


def pump(app, n=40):
    for _ in range(n):
        app.processEvents()


# ── fake 資料：KLINE_COLUMNS 形狀（同行情頁 E2E 同一份 contract）──
def _df(closes):
    n = len(closes)
    return pd.DataFrame({
        'time_key': pd.date_range('2026-01-01', periods=n, freq='1D').astype(str),
        'open': [c - 0.5 for c in closes],
        'high': [c + 1.0 for c in closes],
        'low': [c - 1.0 for c in closes],
        'close': [float(c) for c in closes],
        'volume': [1000 + i for i in range(n)],
    })


# 升 → 微回頭 → 再升 → 深跌 → 再升：設計到一定出現「持倉中再出 B」（= 被忽略）+ 期末未平倉
SERIES = ([100] * 10 + [101, 103, 106, 110, 115, 121, 128]
          + [124, 119, 115] + [120, 127, 135] + [128, 118, 105, 92, 80] + [88, 97, 108])
FLAT = [100.0] * 12

# 持倉處理要逐條斷言（持倉中 B 被忽略 / 空倉 S 被忽略 / 期末未平倉）→ 訊號由 test 決定。
# `run_backtest` 喺 function 入面先 `from gateway.strategies import trade_marks`，所以 patch
# `gateway.strategies.trade_marks` 有效；MARKS 空 → 用返真策略嘅真訊號。
MARKS = []
MARKS_ALL = [(5, 100.0, 'B'), (8, 100.0, 'B'), (12, 106.0, 'S'),
             (14, 115.0, 'S'), (15, 121.0, 'B')]


class FakeSearchDir:
    """symbol index 契約子集（get/search/has_code）— 證明模糊輸入經 canonical，唔使起真 index。"""

    def __init__(self):
        self._entries = [{'code': 'HK.00700', 'name': '騰訊控股', 'name_zh': '腾讯控股',
                          'name_en': 'Tencent'}]
        self._by_code = {e['code'].upper(): e for e in self._entries}

    def search(self, q, limit=20):
        q = str(q).strip().lower()
        return [e for e in self._entries
                if q in e['code'].lower() or q in e['name'] or q in e['name_zh']
                or q in e['name_en'].lower()][:limit]

    def has_code(self, code):
        return str(code).strip().upper() in self._by_code

    def get(self, code):
        return self._by_code.get(str(code).strip().upper())


class FakeKlineClient:
    """BrokerClient.get_kline 契約替身：一次過返 (status, df, message)。
       `delay` / `fail` / `empty` 用嚟造遲到結果同三句唔同失敗。"""

    def __init__(self):
        self.calls = []
        self.entered = 0
        self.exited = 0
        self.delay = 0.0
        self.fail = None      # (status, df, message)
        self.empty = False
        self.series = SERIES

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *a):
        self.exited += 1
        return None

    async def get_kline(self, code, ktype, broker=None, kline_num=None):
        self.calls.append({'code': code, 'ktype': ktype, 'broker': broker, 'num': kline_num})
        # 撳 request 嗰一刻快照：之後改 series/fail 唔能影響已 submit 嘅請求（否則慢/快對照唔準）
        series, fail, empty, delay = self.series, self.fail, self.empty, self.delay
        if delay:
            await asyncio.sleep(delay)
        if fail is not None:
            return fail
        if empty:
            return True, _df([]), ''
        return True, _df(series), ''


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    # ── Part 0：先起策略（頁構造時下拉要已有內容）──
    import gateway.strategies as st
    from gateway.i18n import t
    import gateway.backtest as bt
    from gateway import position_model as pm   # 持倉模式 enum 單一來源（頁同量化頁都食呢份）
    from modules.registry import BROKERS
    BROKER_FIRST = next(iter(BROKERS)).upper()   # 下拉 index 1 = registry 第一個券商

    st.reset_manager_for_test()
    mgr = st.get_manager()
    # 🤖 訊號可控（持倉處理逐條斷言）+ 記錄真正送入引擎嘅策略 entry（證明下拉揀嘅就係回測用嘅）
    real_marks, real_run = st.trade_marks, bt.run_backtest
    captured = []

    def _marks(entry, ohlc):
        if not MARKS:
            return real_marks(entry, ohlc)
        n = len(ohlc['c'])
        return [(i, p, s) for (i, p, s) in MARKS if i < n]

    def _run_capture(entry, ohlc, **kw):
        captured.append(entry)
        return real_run(entry, ohlc, **kw)

    st.trade_marks = _marks
    bt.run_backtest = _run_capture
    ok, msg, s1 = mgr.add('BT Cross',
                          [{'type': 'ma_cross', 'side': 'above',
                            'params': {'fast': 2, 'slow': 5}, 'score': 100}],
                          [{'type': 'ma_cross', 'side': 'below',
                            'params': {'fast': 3, 'slow': 8}, 'score': 100}])
    check('fixture：策略建立成功', ok, msg)

    from gateway.pages import backtest_page as bp
    from gateway.pages.backtest_page import BacktestPage
    from gateway.pages.quotes_page import KTYPES

    fake = FakeKlineClient()
    page = BacktestPage(client_factory=lambda: fake)
    page._directory = FakeSearchDir()   # 🤖 hermetic：唔起真 symbol index（唔拖慢）
    page.show()
    # 等 worker_ready 真係送到先落手（pump 唔等 wall-clock，唔能當等 thread 起好）
    check('後台 thread 起好 → worker_ready 送到先可以執行',
          wait_for(app, lambda: page._worker is not None, what='worker ready'))

    # ══ Part 1：`.ui` 骨架（排版喺 gateway/ui/backtest_page.ui）══
    print('── Part 1: `.ui` 骨架 / stamp / QSS ──')
    lay = page.layout()
    check('`.ui` root objectName + Designer margin/spacing 照載入',
          page.objectName() == 'backtest_page' and lay is not None
          and lay.contentsMargins() == QMargins(10, 8, 10, 8) and lay.spacing() == 6)
    check('輸入控件全部由 `.ui` 建出（objectName 即身份契約）',
          all(getattr(page, n, None) is not None for n in
              ('bt_symbol', 'bt_strategy', 'bt_broker', 'bt_ktype', 'bt_bars', 'bt_run_btn',
               'bt_capital', 'bt_fee', 'bt_slip', 'bt_rf', 'bt_mode', 'bt_mode_lbl', 'bt_note',
               'bt_status', 'bt_tabs', 'bt_trade_table', 'bt_ig_table', 'bt_chart_hint',
               'bt_cards_area', 'bt_cards_host')))
    check('四個 tab + 四個空 grid slot + 兩個表由 `.ui` 建出',
          page.bt_tabs.count() == 4
          and all(isinstance(getattr(page, f'bt_grid_{g}'), QGridLayout)
                  for g in bt.GROUPS)
          and all(isinstance(getattr(page, n), QTableView)
                  for n in ('bt_trade_table', 'bt_ig_table')))
    check('og / WA_StyledBackground 由 _STAMP 補返（Designer 帶唔住 dynamic property）',
          page.bt_run_btn.property('og') == 'btrun'
          and page.bt_symbol_lbl.property('og') == 'btlbl'
          and page.bt_mode_lbl.property('og') == 'btlbl'
          and page.testAttribute(Qt.WA_StyledBackground))
    check('頁面 QSS 有根 + 紅漲綠跌語義色由 gui_kline 單一來源注入',
          'QWidget#backtest_page' in page.styleSheet()
          and f'color: {bp.gk.C_UP}' in page.styleSheet()
          and f'color: {bp.gk.C_DOWN}' in page.styleSheet())

    # ══ Part 2：選項內容全部由 domain 填（數量屬資料）══
    print('── Part 2: 選項 / spin 範圍 / 指標卡 ═─')
    check('券商下拉 = 「跟隨設定」(data 空 → broker=None) + registry 名單（唔 hardcode）',
          page.bt_broker.itemData(0) == ''
          and page.bt_broker.itemText(0) == t('bt_broker_default', 'zh_hk')
          and [page.bt_broker.itemData(i) for i in range(1, page.bt_broker.count())]
          == [b.upper() for b in BROKERS])
    check('週期下拉 = quotes KTYPES 同一份（唔再第二份）',
          [page.bt_ktype.itemText(i) for i in range(page.bt_ktype.count())] == list(KTYPES))
    check('持倉模式下拉 = `position_model.MODES` 同一份 enum（同量化頁共用 → 兩頁不可能兩套口徑）、'
          '默認 = 長倉',
          [page.bt_mode.itemData(i) for i in range(page.bt_mode.count())] == list(pm.MODES)
          and [page.bt_mode.itemText(i) for i in range(page.bt_mode.count())]
          == [t(f'mode_{m}', 'zh_hk') for m in pm.MODES]
          and bp.pm.MODES is pm.MODES
          and page.bt_mode.currentData() == pm.MODE_DEFAULT == page._mode
          and page._opts()['mode'] == pm.MODE_DEFAULT,
          f'items={[(page.bt_mode.itemData(i), page.bt_mode.itemText(i)) for i in range(page.bt_mode.count())]}')
    check('spin 範圍跟 backtest.clamp_*（業務口徑唔入 `.ui`／唔 hardcode）',
          (page.bt_bars.minimum(), page.bt_bars.maximum()) == (bt.BARS_LO, bt.BARS_HI)
          and page.bt_bars.value() == bt.BARS_DEFAULT
          and (page.bt_capital.minimum(), page.bt_capital.maximum(), page.bt_capital.value())
          == (bt.CAPITAL_LO, bt.CAPITAL_HI, bt.CAPITAL_DEFAULT)
          and all((s.minimum(), s.maximum()) == (bt.COST_LO, bt.COST_HI)
                  for s in (page.bt_fee, page.bt_slip))
          and (page.bt_rf.minimum(), page.bt_rf.maximum()) == (bt.RF_LO, bt.RF_HI))
    check('策略下拉由 StrategyManager 填（首項 = 未有策略提示，data 空）',
          page.bt_strategy.itemData(0) == ''
          and page.bt_strategy.itemText(0) == t('bt_no_strategy_yet', 'zh_hk')
          and [page.bt_strategy.itemData(i) for i in range(1, page.bt_strategy.count())]
          == [e['id'] for e in mgr.items()])
    defs = {d.key: d for d in bt.METRIC_DEFS}
    check(f'指標卡 = METRIC_DEFS 全數（{len(bt.METRIC_DEFS)} 張），objectName 可定位',
          len(page._cards) == len(bt.METRIC_DEFS)
          and all(page.findChild(type(page._cards[k][0]), f'bt_cardval_{k}') is page._cards[k][0]
                  for k in defs))
    check('四組分組渲染各就各位（卡喺對應 bt_grid_<group>，唔串組）',
          all(getattr(page, f'bt_grid_{g}').indexOf(page._cards[k][0].parent()) >= 0
              for g in bt.GROUPS for k in defs if defs[k].group == g))
    check('卡 = QFrame[og=btcard] + 數值/說明兩行（og 由動態 stamp 注入）',
          page._cards['total_ret_pct'][0].property('og') == 'btcardval'
          and page._cards['total_ret_pct'][1].property('og') == 'btcardlbl'
          and page._cards['total_ret_pct'][0].parent().property('og') == 'btcard')
    check('未執行：所有卡 ' + repr(bp.DASH) + ' + sign=na（冇數就係冇數，唔扮 0）',
          all(page._cards[k][0].text() == bp.DASH and page._cards[k][0].property('sign') == 'na'
              for k in defs))
    check('未執行：caption = i18n 標籤（表-driven，code 冇逐邊 setText）',
          page._cards['sharpe'][1].text() == t('bt_m_sharpe', 'zh_hk')
          and page._cards['coverage_pct'][1].text() == t('bt_m_coverage', 'zh_hk'))
    check('未執行：兩個表 0 行 + tab 標題三語 key 生效',
          page.trade_model.rowCount() == 0 and page.ig_model.rowCount() == 0
          and page.bt_tabs.tabText(0) == t('bt_tab_metrics', 'zh_hk')
          and page.bt_tabs.tabText(3) == t('bt_tab_chart', 'zh_hk'))
    check('未執行：狀態行 = bt_status_idle + 覆核圖 tab 提示',
          page.bt_status.text() == t('bt_status_idle', 'zh_hk')
          and page.bt_chart_hint.text() == t('bt_chart_empty', 'zh_hk'))

    # ══ Part 3：經真 widget 執行（填標的 → 揀策略 → 改成本 → 撳 run）══
    print('── Part 3: 執行（真 widget 路徑）──')
    page.bt_symbol.setText('hk.00700')          # 細階 → 應被 canonical 成 HK.00700
    page.bt_strategy.setCurrentIndex(1)         # 真下拉揀策略
    page.bt_bars.setValue(300)
    page.bt_capital.setValue(50000)
    page.bt_fee.setValue(1.0)
    page.bt_slip.setValue(0.5)
    page.bt_broker.setCurrentIndex(1)           # 真揀券商（非默認）
    pump(app)
    check('揀策略 → _strategy_id 跟到', page._strategy_id == s1['id'])
    MARKS[:] = MARKS_ALL          # 🤖 可控訊號：B B(持倉中) S S(空倉) B(期末未平倉)
    page.bt_run_btn.click()
    check('撳 run → 即刻 disable + 攞緊 K 線（唔俾連撳）',
          not page.bt_run_btn.isEnabled() and t('bt_fetching', 'zh_hk') in page.bt_status.text())
    ok_wait = wait_for(app, lambda: page.bt_run_btn.isEnabled(), what='backtest result')
    check('結果返到 → 掣返 enable', ok_wait)
    check('worker 收到啱參數（canonical code / ktype / kline_num / broker）',
          fake.calls == [{'code': 'HK.00700', 'ktype': 'K_DAY', 'num': 300,
                          'broker': BROKER_FIRST}], str(fake.calls))

    res = page._last
    trades, ignored = res['trades'], res['ignored']
    m = res['metrics']
    closed = [r for r in trades if not r['open']]
    print(f'     ℹ️ 實際：trades={len(trades)} open={m["n_open"]} '
          f'buy={m["n_buy_signals"]} sell={m["n_sell_signals"]} '
          f'ig_inpos={m["ignored_in_pos"]} ig_flat={m["ignored_flat"]}')
    check('下拉揀嘅策略 entry 原封送入引擎（冇第二份策略定義）',
          len(captured) == 1 and captured[0]['id'] == s1['id']
          and captured[0]['name'] == 'BT Cross'
          and captured[0]['buy'] == s1['buy'] and captured[0]['sell'] == s1['sell'])
    check('有成交、亦有期末未平倉（mark-to-market 如實）',
          len(trades) == 2 and len(closed) == 1 and m['n_open'] == 1, f'trades={len(trades)}')
    check('持倉中再出 B → 冇加倉但如實計數並入表；空倉 S → 如實計數並入表',
          m['ignored_in_pos'] == 1 and m['ignored_flat'] == 1
          and page.ig_model.rowCount() == len(ignored) == 2
          and sorted(r['reason'] for r in ignored) == ['bt_ig_flat', 'bt_ig_inpos'])
    check('訊號數 = 開倉/平倉數 + 被忽略數（冇靜默吞訊號）',
          m['n_buy_signals'] == len(trades) + m['ignored_in_pos'] == 3
          and m['n_sell_signals'] == len(closed) + m['ignored_flat'] == 2)

    # ══ Part 4：用戶核心口徑逐項斷言 ══
    print('── Part 4: 唔准復利 / 等額注碼 / 成本可調 / 持倉 ═─')
    check('總報酬率 = Σ 每筆淨報酬（等額注碼、累加 P&L → 冇復利失真）',
          abs(m['total_ret_pct'] - sum(r['net_pct'] for r in trades)) < 1e-9,
          f"{m['total_ret_pct']} vs {sum(r['net_pct'] for r in trades)}")
    # 成本 = 每邊滑價（價格乘數）+ 每邊手续费；未平倉只計已發生嘅開倉成本
    fee_f, slip_f = 0.01, 0.005

    def _expect_net(r):
        """每筆淨報酬%（獨立覆核引擎 `_settle`）：s = 倉位方向 → 滑價永遠朝唔利自己嗰邊。
           長倉 s=+1：入 ×(1+slip)、出 ×(1−slip)；短倉 s=−1 就調轉。"""
        s = 1.0 if r['dir'] == pm.DIR_LONG else -1.0
        g = 1.0 + s * r['gross_pct'] / 100.0                      # = exit_raw / entry_raw
        mult = (1.0 - s * slip_f) / (1.0 + s * slip_f) if not r['open'] else 1.0 / (1.0 + s * slip_f)
        return s * (g * mult - 1.0) * 100.0 - (2.0 if not r['open'] else 1.0) * fee_f * 100.0
    check('每筆 net% = 每邊滑價（乘數）+ 每邊手续费（參數真係流入引擎、逐筆可覆核）',
          all(abs(r['net_pct'] - _expect_net(r)) < 1e-9 for r in trades),
          str([(round(r['net_pct'], 4), round(_expect_net(r), 4)) for r in trades]))
    check('手续费%：平倉筆 = 2 邊、未平倉筆 = 只計開倉 1 邊（未付出嘅唔預扣）',
          all(abs(r['fee_pct'] - (2.0 if not r['open'] else 1.0)) < 1e-9 for r in trades))
    check('成本% = 毛 − 淨（殘差，含 fee×滑價交互）= fee% + slip%，且 slip% > 0',
          all(abs(r['cost_pct'] - (r['gross_pct'] - r['net_pct'])) < 1e-9
              and abs(r['cost_pct'] - (r['fee_pct'] + r['slip_pct'])) < 1e-9
              and r['slip_pct'] > 0 for r in trades))
    check('每筆損益 = 固定名義 × net%（等額注碼，唔滾雪球）',
          all(abs(r['pnl'] - 50000 * r['net_pct'] / 100.0) < 1e-6 for r in trades))
    check('總成本金額 = 名義 × Σ 每筆成本%（同每筆口徑一致）',
          abs(m['total_cost_amount'] - 50000 * sum(r['cost_pct'] for r in trades) / 100.0) < 1e-6)
    check('策略暖機根數 = max(slow) 並顯示出嚟（唔使人估邊度冇訊號）',
          m['warmup_bars'] == 8 and page._cards['warmup_bars'][0].text() == '8'
          and t('bt_warmup_hint', 'zh_hk').format(n=8) in page.bt_status.text())
    check('持倉根數/日數喺每筆度（持倉處理可見）',
          all(r['bars'] > 0 and r['days'] >= 0 for r in trades)
          and m['avg_hold_bars'] > 0 and m['max_hold_bars'] >= max(r['bars'] for r in trades))
    check('未平倉嗰筆 exit_reason = end（mark-to-market），其餘 = signal',
          [r['exit_reason'] for r in trades][-1] == 'end'
          and all(r['exit_reason'] == 'signal' for r in closed))
    check('時間軸口徑如實標籤（bpy 來源顯示喺狀態行）',
          m['bars_per_year'] > 0 and t(f'bt_bpy_{res["meta"]["bpy_source"]}', 'zh_hk')
          in page.bt_status.text())

    # 表內容（真 model 經真 view 渲染）
    ci = {k: i for i, (k, _h, _f) in enumerate(bp.TRADE_COLS)}
    idx = page.bt_trade_table.model().index
    check('交易明細表 = 逐筆持倉（欄數 = spec、行數 = trades）',
          page.trade_model.columnCount() == len(bp.TRADE_COLS)
          and page.trade_model.rowCount() == len(trades))
    check('表 cell 格式跟 spec：成本% = 引擎值經 fmt、未平倉顯示「未平倉（期末 mark-to-market）」',
          page.trade_model.data(idx(0, ci['cost_pct'])) == bt.fmt_value(trades[0]['cost_pct'], 'pct')
          and page.trade_model.data(idx(len(trades) - 1, ci['exit_reason'])) == t('bt_exit_open', 'zh_hk')
          and page.trade_model.data(idx(0, ci['exit_reason'])) == t('bt_exit_signal', 'zh_hk'),
          f'cost={page.trade_model.data(idx(0, ci["cost_pct"]))!r} expect={bt.fmt_value(trades[0]["cost_pct"], "pct")!r} '
          f'exit0={page.trade_model.data(idx(0, ci["exit_reason"]))!r} exitN={page.trade_model.data(idx(len(trades) - 1, ci["exit_reason"]))!r}')
    check('表 cell 一律經 fmt（千分位/小數位跟 spec），冇值如實 ' + repr(bp.DASH),
          page.trade_model.data(idx(0, ci['entry_raw'])) == bt.fmt_value(trades[0]['entry_raw'], 'money')
          and page.trade_model.data(idx(0, ci['pnl'])) == bt.fmt_value(trades[0]['pnl'], 'money')
          and bt.fmt_value(None, 'pct') == '' and page._cell({'x': None}, 'x', 'pct') == bp.DASH)
    ii = {k: i for i, (k, _h, _f) in enumerate(bp.IG_COLS)}
    igi = page.ig_model.index
    check('被忽略訊號表：時間/訊號/價格/原因四欄，兩句原因各自如實（持倉中冇加倉 / 空倉冇倉可平）',
          page.ig_model.columnCount() == len(bp.IG_COLS)
          and [page.ig_model.data(igi(r, ii['reason'])) for r in range(2)]
          == [t('bt_ig_inpos', 'zh_hk'), t('bt_ig_flat', 'zh_hk')]
          and [page.ig_model.data(igi(r, ii['side'])) for r in range(2)]
          == [t('bt_side_b', 'zh_hk'), t('bt_side_s', 'zh_hk')])
    check('指標卡出數 = 引擎值經 fmt（唔係 ' + repr(bp.DASH) + '）',
          page._cards['n_trades'][0].text() == f'{len(closed):,d}'
          and page._cards['n_open'][0].text() == f'{m["n_open"]:,d}'
          and page._cards['n_buy_signals'][0].text() == f'{m["n_buy_signals"]:,d}'
          and page._cards['ignored_in_pos'][0].text() == f'{m["ignored_in_pos"]:,d}')
    check('紅漲綠跌：得返純賺蝕性質指標上色，回撤/成本/持倉一律 neutral',
          page._cards['total_ret_pct'][0].property('sign')
          in ('pos', 'neg', 'zero')
          and page._cards['max_dd_pct'][0].property('sign') == 'na'
          and page._cards['avg_hold_days'][0].property('sign') == 'na'
          and page._cards['total_cost_pct'][0].property('sign') == 'na')
    check('狀態行 = 完成 + 策略·代碼·週期 + 持倉模式 + 根數 + 時間範圍（換模式就係另一個策略，唔可以唔講）',
          t('bt_done', 'zh_hk') in page.bt_status.text()
          and 'BT Cross' in page.bt_status.text() and 'HK.00700' in page.bt_status.text()
          and f'{t("mode_lbl", "zh_hk")} {t("mode_long", "zh_hk")}' in page.bt_status.text()
          and f'{m["n_bars"]:,}' in page.bt_status.text(), page.bt_status.text())
    check('方向欄放最前（一入眼就知呢筆多定空），cell 如實「多」（長倉模式一律多）',
          ci['dir'] == 0
          and [page.trade_model.data(idx(r, ci['dir'])) for r in range(len(trades))]
          == [t('bt_dir_long', 'zh_hk')] * len(trades) and m['n_short_trades'] == 0)

    # 排序（真 header 點擊路徑）
    page.bt_trade_table.sortByColumn(ci['net_pct'], Qt.DescendingOrder)
    pump(app)
    nets = [page.trade_model.data(idx(r, ci['net_pct'])) for r in range(len(trades))]
    vals = [float(s.replace(',', '')) for s in nets]
    check('祧表頭 → 淨報酬% 按數值排序（唔係字串序）', vals == sorted(vals, reverse=True), str(nets))

    # ══ Part 4b：持倉模式（用戶：「回測…長倉，短倉，雙向，BACKTEST 都可以選」）══
    print('── Part 4b: 持倉模式三档 / 方向欄 / 沽空反號 / 反手 ──')
    page.bt_trade_table.sortByColumn(-1, Qt.AscendingOrder)   # 清返排序指示：下面要按引擎順序逐筆對

    def _run_mode(mode):
        """揀模式 → 經真 widget 再跑一次 → 返 (trades, ignored, metrics)。"""
        page.bt_mode.setCurrentIndex(page.bt_mode.findData(mode))
        page.bt_run_btn.click()
        if not wait_for(app, lambda: page.bt_run_btn.isEnabled(), what=f'run mode={mode}'):
            return None, None, None
        r = page._last
        check(f'模式寫入結果 meta（mode={mode}）', r['meta']['mode'] == mode, str(r['meta']['mode']))
        return r['trades'], r['ignored'], r['metrics']

    def _dirs():
        return [page.trade_model.data(idx(r, ci['dir'])) for r in range(page.trade_model.rowCount())]

    s_trades, s_ig, sm = _run_mode(pm.MODE_SHORT)
    check('短倉模式：買入訊號一律如實入「空倉冇倉可平」（唔靜默當冇見到）',
          len(s_trades) == 1 and sm['n_open'] == 0 and sm['ignored_flat'] == 2
          and sm['ignored_in_pos'] == 1 and sm['n_buy_signals'] == 3 and sm['n_sell_signals'] == 2,
          f'trades={len(s_trades)} ig_flat={sm["ignored_flat"]} ig_inpos={sm["ignored_in_pos"]}')
    check('方向欄如實：短倉模式全部「空」（`dir` 由引擎帶，UI 唔自己推）',
          _dirs() == [t('bt_dir_short', 'zh_hk')] * len(s_trades), str(_dirs()))
    check('沽空反號：價升 → 短倉虧（gross% 反號，唔係照抄長倉），同一式、等額注碼',
          (s_trades[0]['entry_idx'], s_trades[0]['exit_idx'], s_trades[0]['dir'])
          == (12, 15, pm.DIR_SHORT)
          and abs(s_trades[0]['gross_pct'] - (-(121.0 / 106.0 - 1.0) * 100.0)) < 1e-9
          and s_trades[0]['gross_pct'] < 0 and s_trades[0]['net_pct'] < 0
          and abs(s_trades[0]['pnl'] - 50000 * s_trades[0]['net_pct'] / 100.0) < 1e-6,
          str({k: s_trades[0][k] for k in ('entry_idx', 'exit_idx', 'dir', 'gross_pct', 'net_pct')}))
    check('成本口徑跨模式一致：同一個 `_expect_net`（每邊滑價 + 每邊手续费）照樣覆核短倉筆',
          all(abs(r['net_pct'] - _expect_net(r)) < 1e-9 for r in s_trades),
          str([(round(r['net_pct'], 4), round(_expect_net(r), 4)) for r in s_trades]))
    check('短倉筆數入指標卡 + 狀態行如實寫明持倉模式',
          sm['n_short_trades'] == 1
          and page._cards['n_short_trades'][0].text() == f'{sm["n_short_trades"]:,d}'
          and f'{t("mode_lbl", "zh_hk")} {t("mode_short", "zh_hk")}' in page.bt_status.text(),
          page.bt_status.text())
    check('被忽略表照樣逐條列（短倉模式：2 條空倉 + 1 條持倉中，原因各自如實）',
          page.ig_model.rowCount() == len(s_ig) == 3
          and sorted(r['reason'] for r in s_ig) == ['bt_ig_flat', 'bt_ig_flat', 'bt_ig_inpos'])

    b_trades, b_ig, bm = _run_mode(pm.MODE_BOTH)
    check('雙向：相反訊號 = 同一根先平後開（反手兩筆），冇「平倉後變空倉」中間態',
          [(r['entry_idx'], None if r['open'] else r['exit_idx'], r['dir']) for r in b_trades]
          == [(5, 12, pm.DIR_LONG), (12, 15, pm.DIR_SHORT), (15, None, pm.DIR_LONG)]
          and bm['n_open'] == 1 and len(b_trades) == 3,
          str([(r['entry_idx'], r['exit_idx'], r['dir'], r['open']) for r in b_trades]))
    check('雙向先會見到「空」：方向欄逐筆如實（多 / 空 / 多）',
          _dirs() == [t('bt_dir_long', 'zh_hk'), t('bt_dir_short', 'zh_hk'), t('bt_dir_long', 'zh_hk')],
          str(_dirs()))
    check('雙向：同向訊號照樣被忽略（兩邊都計）、空倉忽略自然係 0',
          bm['ignored_in_pos'] == 2 and bm['ignored_flat'] == 0 and len(b_ig) == 2
          and bm['n_buy_signals'] == 3 and bm['n_sell_signals'] == 2,
          f'ig_inpos={bm["ignored_in_pos"]} ig_flat={bm["ignored_flat"]}')
    check('雙向：反手嗰筆平倉成本照計（2 邊）、新倉只計開倉 1 邊 → 同一 `_expect_net` 覆核到',
          all(abs(r['net_pct'] - _expect_net(r)) < 1e-9 for r in b_trades)
          and [r['fee_pct'] for r in b_trades] == [2.0, 2.0, 1.0],
          str([(round(r['net_pct'], 4), r['fee_pct']) for r in b_trades]))
    check('雙向：n_short_trades = 1、總報酬 = Σ 每筆（唔准復利呢個恆等式換模式都成立）',
          bm['n_short_trades'] == 1
          and page._cards['n_short_trades'][0].text() == '1'
          and abs(bm['total_ret_pct'] - sum(r['net_pct'] for r in b_trades)) < 1e-9)

    l_trades, _l_ig, lm = _run_mode(pm.MODE_LONG)
    check('模式係輸入參數、唔係一次性狀態：轉返長倉 → 逐筆同 Part 3/4 一樣（可重現）',
          [(r['entry_idx'], r['dir']) for r in l_trades] == [(5, pm.DIR_LONG), (15, pm.DIR_LONG)],
          str([(r['entry_idx'], r['dir']) for r in l_trades]))
    # 逐筆對數用 entry_idx 做 key：`res['trades']` 同表 model 嘅 `_rows` 係同一個 list
    # （`set_rows` 只係 alias、`sort()` 原位排），Part 4 撳過表頭 → 嗰個 list 已被排咗序
    _nets = lambda rows: {r['entry_idx']: round(r['net_pct'], 10) for r in rows}
    check('轉返長倉嘅每筆淨報酬% 同第一次逐筆相同（模式唔會偷偷改成本/計數口徑）',
          _nets(l_trades) == _nets(res['trades']),
          f'now={_nets(l_trades)} base={_nets(res["trades"])}')
    check('轉返長倉：冇短倉（n_short_trades=0）、方向欄一律「多」',
          lm['n_short_trades'] == 0 and _dirs() == [t('bt_dir_long', 'zh_hk')] * len(l_trades),
          str(_dirs()))

    # ══ Part 5：遲到結果作廢 / 三句唔同失敗 / 輸入守門 ══
    print('── Part 5: token 作廢 / 失敗如實 / 守門 ──')
    MARKS.clear()                    # 之後返返用真策略真訊號（FLAT → 冇訊號 → 0 成交）
    page.bt_symbol.setText('hk.00700')
    fake.delay = 0.4
    fake.series = SERIES
    n_calls = len(fake.calls)
    page.bt_run_btn.click()          # 第一次（慢）
    first_token = page._token
    page.bt_run_btn.click()          # 掣而家 disable 緊
    seen = []                        # 额外 slot：連被作廢嘅結果都睇到（先能證明真係被丟咗）
    page._worker.result.connect(seen.append)
    fake.delay = 0.0
    fake.series = FLAT               # 第二次（快）→ 應該蓋過第一次
    page._on_run()                   # 繞過 disable 掣再跑（= 改參數再跑／標的欄回車呢類入口）
    check('再跑 → token 遞增（in-flight 結果即刻作廢）', page._token == first_token + 1,
          f'first={first_token} now={page._token}')
    got_both = wait_for(app, lambda: len(seen) >= 2, what='both results delivered')
    check('兩次結果真係都返咗嚟（先能證明遲到嗰份係被丟、唔係根本冇返）', got_both,
          f'seen={len(seen)}')
    pump(app, 20)
    # calls 係 worker thread append，所以一定要等結果返埋先數（即刻數會攞到 0）
    check('結果未返前掣 disable → 連撳第二次唔會多一次 request（得返 click + _on_run 兩次）',
          len(fake.calls) == n_calls + 2, f'+{len(fake.calls) - n_calls}')
    check('遲到結果一律作廢（快嗰份蓋過慢嗰份，表照樣係第二次）',
          sorted(x['meta']['n_bars'] for x in seen) == sorted([len(FLAT), len(SERIES)])
          and max(x['token'] for x in seen) == page._token   # 慢嗰份 token 比較細 → 被丟
          and page._last['token'] == page._token
          and page._last['meta']['n_bars'] == len(FLAT)
          and page.trade_model.rowCount() == len(page._last['trades'])
          and t('bt_empty_trades', 'zh_hk') in page.bt_status.text(),
          f'pairs={[(x["token"], x["meta"]["n_bars"]) for x in seen]} now={page._token}')
    check('冇成交 → 卡照樣出數（n_trades=0）、表空 + 狀態如實講冇成交',
          page._cards['n_trades'][0].text() == '0' and page.trade_model.rowCount() == 0)

    fake.fail = (False, None, '行情權限冇')
    page.bt_run_btn.click()
    wait_for(app, lambda: page.bt_run_btn.isEnabled(), what='fail result')
    check('失敗一：攞 K 線失敗 → 連 broker message 一齊如實顯示',
          t('bt_err_kline', 'zh_hk') in page.bt_status.text()
          and '行情權限冇' in page.bt_status.text())
    check('失敗時所有卡返 ' + repr(bp.DASH) + '（唔留返舊數誤導人）',
          all(page._cards[k][0].text() == bp.DASH for k in defs))
    fake.fail = None
    fake.empty = True
    page.bt_run_btn.click()
    wait_for(app, lambda: page.bt_run_btn.isEnabled(), what='empty result')
    check('失敗二：冇收到 K 線 → 另一句（檢查代碼／權限／週期）',
          t('bt_err_kline_empty', 'zh_hk') in page.bt_status.text())
    fake.empty = False
    fake.fail = (True, pd.DataFrame({'x': [1, 2, 3]}), '')   # 有行但冇 OHLC 欄 → 引擎前就炸
    page.bt_run_btn.click()
    wait_for(app, lambda: page.bt_run_btn.isEnabled(), what='worker error result')
    check('失敗三：回測本身出錯 → 第三句（同上面兩句唔同、連異常名一齊顯示）',
          t('bt_err_worker', 'zh_hk') in page.bt_status.text()
          and t('bt_err_kline', 'zh_hk') not in page.bt_status.text(),
          page.bt_status.text())
    fake.fail = None

    page.bt_symbol.setText('   ')
    n_before = len(fake.calls)
    page.bt_run_btn.click()
    check('冇標的 → 唔打 broker，直接如實提示',
          t('bt_err_no_symbol', 'zh_hk') in page.bt_status.text() and len(fake.calls) == n_before,
          f'calls +{len(fake.calls) - n_before} status={page.bt_status.text()!r}')
    page.bt_symbol.setText('hk.00700')
    page.bt_strategy.setCurrentIndex(0)   # 「未有策略」項
    page.bt_run_btn.click()
    check('冇策略 → 如實提示（並提示先去策略頁建立）',
          t('bt_err_no_strategy', 'zh_hk') in page.bt_status.text())
    ok2, _msg, s2 = mgr.add('BT Second', [{'type': 'ma_cross', 'side': 'above',
                                           'params': {'fast': 5, 'slow': 10}, 'score': 100}],
                            [{'type': 'ma_cross', 'side': 'below',
                              'params': {'fast': 5, 'slow': 10}, 'score': 100}])
    pump(app)
    check('策略頁新增/修改 → 下拉即時跟（listener，唔使重開頁）',
          ok2 and [page.bt_strategy.itemData(i) for i in range(page.bt_strategy.count())][1:]
          == [e['id'] for e in mgr.items()])

    # ══ Part 6：持久化 + 重載 ══
    print('── Part 6: state_store section `backtest` ──')
    page.bt_strategy.setCurrentIndex(1)
    page.bt_ktype.setCurrentText('K_60M')
    page.bt_bars.setValue(800)
    page.bt_capital.setValue(250000)
    page.bt_fee.setValue(0.3)
    page.bt_rf.setValue(4.5)
    fake.series = SERIES
    MARKS[:] = MARKS_ALL             # 呢次要有成交 → Part 7 先有表內容可驗三語
    page.bt_run_btn.click()
    wait_for(app, lambda: page.bt_run_btn.isEnabled(), what='run before snapshot')
    doc = __import__('json').loads(state_store.STATE_PATH.read_text(encoding='utf-8'))
    saved = doc.get('backtest', {})
    check('執行即寫盤：section `backtest` 記低全部輸入（持倉模式照記，呢次係默認長倉）',
          saved.get('symbol') == 'hk.00700' and saved.get('strategy') == s1['id']
          and saved.get('broker') == BROKER_FIRST and saved.get('ktype') == 'K_60M'
          and saved.get('bars') == 800 and saved.get('capital') == 250000
          and saved.get('fee_pct') == 0.3 and saved.get('rf_pct') == 4.5
          and saved.get('mode') == pm.MODE_DEFAULT, str(saved))
    # 持倉模式都屬輸入 → 改下拉即寫盤（唔使重跑）；非默認值先測到真係還原咗
    page.bt_mode.setCurrentIndex(page.bt_mode.findData(pm.MODE_BOTH))
    page.bt_mode.activated.emit(page.bt_mode.currentIndex())   # 真 signal 路徑（setCurrentIndex 自己唔會 emit）
    saved2 = __import__('json').loads(state_store.STATE_PATH.read_text(encoding='utf-8')).get('backtest', {})
    check('改持倉模式即寫盤（`activated` → `_save_state`，唔使重跑一次）',
          saved2.get('mode') == pm.MODE_BOTH, str(saved2))
    page2 = BacktestPage(client_factory=lambda: fake)
    page2._directory = FakeSearchDir()
    wait_for(app, lambda: page2._worker is not None, what='page2 worker ready')
    check('重開頁 → 全部輸入還原（包括成本參數同持倉模式）',
          page2.bt_symbol.text() == 'hk.00700'
          and page2.bt_strategy.currentData() == s1['id']
          and page2.bt_broker.currentData() == BROKER_FIRST
          and page2.bt_ktype.currentText() == 'K_60M'
          and page2.bt_bars.value() == 800 and page2.bt_capital.value() == 250000
          and page2.bt_fee.value() == 0.3 and page2.bt_rf.value() == 4.5
          and page2.bt_mode.currentData() == pm.MODE_BOTH and page2._mode == pm.MODE_BOTH)
    page2._on_app_quit()
    page2.deleteLater()

    state_store.save_section('backtest', {'symbol': 'HK.00700', 'strategy': 's-唔存在',
                                          'broker': 'NOPE', 'ktype': 'K_BOGUS', 'bars': 99999,
                                          'capital': 1e15, 'fee_pct': 99, 'slip_pct': -5,
                                          'rf_pct': 99, 'mode': 'bogus'})
    page3 = BacktestPage(client_factory=lambda: fake)
    page3._directory = FakeSearchDir()
    wait_for(app, lambda: page3._worker is not None, what='page3 worker ready')
    check('盤入嘅垃圾輸入（唔知 ktype／超界參數／冇呢個策略／唔知嘅持倉模式）→ 如實 clamp 返合法值，唔炸',
          page3._ktype == 'K_DAY' and page3.bt_bars.value() == bt.BARS_HI
          and page3.bt_capital.value() == bt.CAPITAL_HI
          and page3.bt_fee.value() == bt.COST_HI and page3.bt_slip.value() == bt.COST_LO
          and page3.bt_rf.value() == bt.RF_HI
          and page3.bt_broker.currentIndex() == 0
          # 持倉模式唔認得 → 返默認（`clamp_mode`，唔係留低垃圾當自定義口徑）
          and page3.bt_mode.currentData() == pm.MODE_DEFAULT and page3._mode == pm.MODE_DEFAULT
          # 盤入嘅策略 id 已經冇咗 → 唔留 dangling id：下拉同 _strategy_id 一致咁返去「未有策略」
          and page3.bt_strategy.currentData() == '' and page3._strategy_id == '',
          f'ktype={page3._ktype} bars={page3.bt_bars.value()} cap={page3.bt_capital.value()} '
          f'fee={page3.bt_fee.value()} slip={page3.bt_slip.value()} rf={page3.bt_rf.value()} '
          f'broker={page3.bt_broker.currentIndex()} mode={page3.bt_mode.currentData()!r} '
          f'strat={page3._strategy_id!r}')
    page3._on_app_quit()
    page3.deleteLater()

    # ══ Part 7：覆核圖（淨值曲線 + K 圖 B/S）══
    print('── Part 7: 覆核圖（淨值曲線 / K 圖 B/S 一致性 / theme / 語言）──')
    from gateway import indicators
    from gateway.theme import THEMES, apply_theme
    last = page._last
    check('兩張覆核圖都入咗 `.ui` 嘅 `bt_chartSlot`（淨值曲線先、K 圖後）',
          page.bt_chartSlot.count() == 2
          and page.bt_chartSlot.itemAt(0).widget() is page.bt_chart
          and page.bt_chartSlot.itemAt(1).widget() is page.bt_kline)
    check('K 圖覆核重用 `IndicatorKlineChart`（冇第二份 K 圖實作、冇第二份訊號邏輯）',
          isinstance(page.bt_kline, indicators.IndicatorKlineChart))
    check('有結果 → 兩張圖喺度、提示句收埋（未執行先至相反）',
          not page.bt_chart.isHidden() and not page.bt_kline.isHidden()
          and page.bt_chart_hint.isHidden())
    check('淨值曲線 = 是次回測嘅 `curve` 原樣（等額注碼/單利口徑屬領域層，UI 唔二次計數）',
          page.bt_chart._curve is last['curve']
          and page.bt_chart._capital == last['meta']['capital']
          and len(page.bt_chart._curve['equity']) == last['meta']['n_bars']
          and len(page.bt_chart._curve['bench']) == last['meta']['n_bars'])
    check('K 圖收到嘅就係是次回測用開嘅 K 線（唔再取第二次 → 唔會畫到另一段行情）',
          page.bt_kline._rows == last['bars']
          and len(page.bt_kline._rows) == last['meta']['n_bars'])
    check('K 圖用嘅策略 = 真正送入引擎嘅 entry（中途改下拉都唔會畫錯標記）',
          page.bt_kline._strat is captured[-1] is last['entry'])
    page.bt_kline._ensure_marks()
    marks = {(i, s) for (i, _p, s) in page.bt_kline._marks}
    legs = set()
    for tr in last['trades']:
        legs.add((tr['entry_idx'], 'B'))
        if not tr['open']:
            legs.add((tr['exit_idx'], 'S'))   # 未平倉得返 B（期末 mark-to-market 冇出場標記）
    for ig in last['ignored']:
        legs.add((ig['idx'], ig['side']))
    check('圖上 B/S == 交易明細 + 被忽略表（同一個 trade_marks → 一致性屬結構保證）',
          marks == legs and len(marks) == 5, f'marks={sorted(marks)} legs={sorted(legs)}')
    img_zh = page.bt_chart.grab()
    check('淨值曲線真係行到 QPainter 路徑（offscreen 有尺寸、paintEvent 唔炸）',
          img_zh.width() > 100 and img_zh.height() > 100,
          f'{img_zh.width()}x{img_zh.height()}')

    apply_theme('light')
    pump(app)
    light = THEMES['light']
    check('theme 切換 → 兩張圖即刻跟（自繪圖食 palette、K 圖食 gk 常數）',
          page.bt_chart._pal.get('surface') == light['surface']
          and bp.gk.C_SURFACE == light['surface'] and bp.gk.C_ACCENT == light['accent'])
    check('紅漲綠跌屬語義色 → 跟 theme 不變（唔俾 palette 蓋走）',
          bp.gk.C_UP == '#F23645' and bp.gk.C_DOWN == '#089981')
    apply_theme('dark')
    pump(app)

    page.retranslate('en')
    pump(app)
    img_en = page.bt_chart.grab()
    check('換語言 → 曲線標題/圖例即刻重畫（頂部文字像素唔同）、兩張圖語言碼都跟',
          _pix_sum(img_en, 0, 20) != _pix_sum(img_zh, 0, 20)
          and page.bt_chart._lang == 'en' and page.bt_kline._lang == 'en')
    page.retranslate('zh_hk')
    pump(app)

    fake.fail = (False, None, '收圖測試')
    page.bt_run_btn.click()
    wait_for(app, lambda: page.bt_run_btn.isEnabled(), what='fail result (charts)')
    check('失敗 → 如實收圖並返還提示句（唔留返舊曲線當新結果）',
          page.bt_chart._curve == {} and page.bt_kline._rows == []
          and page.bt_kline._strat is None
          and page.bt_kline.isHidden() and page.bt_chart_hint.isHidden() is False)
    fake.fail = None
    page.bt_run_btn.click()
    wait_for(app, lambda: page.bt_run_btn.isEnabled(), what='run again (charts)')
    check('再跑成功 → 兩張圖即刻返返嚟（提示句收埋）',
          page.bt_chart._curve is page._last['curve']
          and len(page.bt_kline._rows) == page._last['meta']['n_bars']
          and not page.bt_kline.isHidden() and page.bt_chart_hint.isHidden())

    # ══ Part 8：三語 + shell 註冊 + i18n 鍵齊 ══
    print('── Part 8: 三語 / 註冊 / i18n ──')
    mode_sel = page.bt_mode.currentData()      # Part 6 揀咗雙向 → 換語言必須照留
    page.retranslate('en')
    pump(app)
    check('EN：tab 標題 / 表頭 / 卡 caption / 券商默認項全部跟語言',
          page.bt_tabs.tabText(1) == 'Trade Log'
          and page.trade_model.headerData(ci['net_pct'], Qt.Horizontal) == 'Net %'
          and page._cards['sharpe'][1].text() == t('bt_m_sharpe', 'en')
          and page.bt_broker.itemText(0) == t('bt_broker_default', 'en'))
    cells = [page.trade_model.data(idx(r, ci['exit_reason'])) for r in range(page.trade_model.rowCount())]
    check('EN：持倉模式選項文案同方向欄表頭都跟語言，揀咗嘅 mode 照留（靠 itemData，唔靠文案）',
          [page.bt_mode.itemText(i) for i in range(page.bt_mode.count())]
          == [t(f'mode_{m}', 'en') for m in pm.MODES]
          and page.trade_model.headerData(ci['dir'], Qt.Horizontal) == t('bt_head_dir', 'en')
          and page.bt_mode.currentData() == mode_sel == pm.MODE_BOTH)
    check('EN：表 cell 內嘅語義（訊號平倉 / 期末 mark-to-market）都跟語言',
          t('bt_exit_signal', 'en') in cells and t('bt_exit_open', 'en') in cells,
          f'cells={cells} raw={[(r.get("exit_reason"), r.get("open")) for r in page.trade_model._rows]} '
          f'last_n_bars={(page._last or {}).get("meta", {}).get("n_bars")}')
    # 呢家張表係 Part 7 最後嗰次 = 雙向模式嘅結果（Part 6 揀咗雙向、之後冇轉返）→
    # 順手覆核方向欄 cell 喺 EN 都如實（多/空/多），被忽略兩條都係「持倉中冇加倉」
    check('EN：方向欄 cell 如實（多/空/多）— 換語言唔會連倉位方向一齊翻譯錯',
          [page.trade_model.data(idx(r, ci['dir'])) for r in range(page.trade_model.rowCount())]
          == [t('bt_dir_long', 'en'), t('bt_dir_short', 'en'), t('bt_dir_long', 'en')],
          str([page.trade_model.data(idx(r, ci['dir'])) for r in range(page.trade_model.rowCount())]))
    check('EN：被忽略表嘅原因/訊號都跟語言（雙向模式下兩條訊號都係「持倉中冇加倉」）',
          [page.ig_model.data(igi(r, ii['reason'])) for r in range(page.ig_model.rowCount())]
          == [t('bt_ig_inpos', 'en')] * 2
          and [page.ig_model.data(igi(r, ii['side'])) for r in range(page.ig_model.rowCount())]
              == [t('bt_side_b', 'en'), t('bt_side_s', 'en')],
          str([page.ig_model.data(igi(r, ii['reason'])) for r in range(page.ig_model.rowCount())]))
    page.retranslate('zh_cn')
    pump(app)
    check('zh_cn：表頭简体 + 組標題简体 + 持倉模式選項简体（揀咗嘅照留）',
          page.trade_model.headerData(ci['pnl'], Qt.Horizontal) == '净损益'
          and page.findChild(QGroupBox, 'bt_grp_exec').title() == t('bt_grp_exec', 'zh_cn')
          and [page.bt_mode.itemText(i) for i in range(page.bt_mode.count())]
              == [t(f'mode_{m}', 'zh_cn') for m in pm.MODES]
          and page.bt_mode.currentData() == mode_sel)
    page.retranslate('zh_hk')
    pump(app)

    KEYS = (set(bp._TEXT.values()) | set(bp._PH.values())
            | {k for _t, k in bp._TAB_KEYS} | {d.label_key for d in bt.METRIC_DEFS}
            | {h for _k, h, _f in bp.TRADE_COLS} | {h for _k, h, _f in bp.IG_COLS}
            # 持倉模式文案同量化頁共用（`mode_*`）、方向欄 cell 文案
            | {f'mode_{m}' for m in pm.MODES} | {'bt_dir_long', 'bt_dir_short'}
            | {'nav_backtest', 'page_backtest_title', 'bt_status_idle', 'bt_worker_wait',
               'bt_err_no_symbol', 'bt_err_no_strategy', 'bt_no_strategy_yet', 'bt_fetching',
               'bt_err_kline', 'bt_err_kline_empty', 'bt_err_worker', 'bt_err_short',
               'bt_err_data', 'bt_done', 'bt_cancelled', 'bt_bpy_measured',
               'bt_bpy_ktype_table', 'bt_bpy_default', 'bt_warmup_hint', 'bt_exit_signal',
               'bt_exit_end', 'bt_exit_open', 'bt_empty_trades', 'bt_ig_inpos', 'bt_ig_badprice',
               'bt_ig_flat', 'bt_empty_ignored', 'bt_side_b', 'bt_side_s', 'bt_chart_empty',
               'bt_curve_title', 'bt_curve_legend_strategy', 'bt_curve_legend_bh',
               'bt_curve_legend_dd', 'bt_run_btn', 'bt_running_btn', 'bt_note_no_compound'})
    missing = [f'{k}@{lang}' for k in sorted(KEYS) for lang in ('zh_hk', 'zh_cn', 'en')
               if _missing_key(k, lang)]
    check(f'全部 i18n 鍵三語齊（{len(KEYS)} 鍵 × 3 語言）', not missing)
    if missing:
        print('     ⚠️ ' + ', '.join(missing))

    from gateway.app import NAV_DIRECT, PAGE_KEYS, _PAGE_CLASSES
    check("'backtest' 已註冊喺主菜單直按（PAGE_KEYS + _PAGE_CLASSES + NAV_DIRECT）",
          'backtest' in PAGE_KEYS and _PAGE_CLASSES.get('backtest') is BacktestPage
          and 'backtest' in NAV_DIRECT)

    # ══ Part 9：收工清理 ══
    print('── Part 9: 收工 ──')
    page._on_app_quit()
    check('aboutToQuit → request_shutdown + wait → thread 收線、fake __aexit__ 被 call',
          page._thread.isFinished() and fake.exited == 1,
          f'finished={page._thread.isFinished()} exited={fake.exited}')

    print('\n' + (f'全部通過 ✅ — 回測頁 33a–33c + 34e 持倉模式（{TOTAL} 項）' if not FAILURES
                  else f'失敗 {len(FAILURES)} 項：{FAILURES}'))
    return 0 if not FAILURES else 1


def _pix_sum(img, y0, y1):
    """攞 pixmap 頂部一條橫帶嘅像素總和 — 用嚟證明文字真係重畫咗（唔係淨係改咗個 field）。"""
    im = img.toImage()
    return sum(im.pixel(x, y) for y in range(y0, min(y1, im.height()))
               for x in range(0, im.width(), 3))


def _missing_key(k, lang):
    from gateway.i18n import t
    try:
        t(k, lang)
    except Exception:
        return True
    return False


if __name__ == '__main__':
    sys.exit(main())
