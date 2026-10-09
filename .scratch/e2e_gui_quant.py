"""E2E GUI test — 量化交易頁（ticket #34d）。

Run: python .scratch/e2e_gui_quant.py   (from project root; QT_QPA_PLATFORM=offscreen 自動設定)
全 hermetic：fake 交易 broker（注入 client_factory）+ fake symbol index + tmp state 檔 —
唔打真 broker、唔食真 symbol index、唔受開市時段影響。

Flow:
1. `.ui` 骨架：root objectName / margin / 全部 57 個 objectName / og stamp / QSS 根 /
   ⚠️ 回歸閘：冇任何 objectName 以 `qt_` 開頭（QUiLoader 唔會為 `qt_` 前綴注入 Python 屬性）
2. 選項內容全部由 domain 填：券商 = registry、環境 = trade_base.TRADE_ENVS、週期 = quotes KTYPES、
   持倉模式 = position_model.MODES（同回測共用）、spin 範圍 = quant_exec 常數、策略 = StrategyManager
3. 加綁定 → 開始監控 → baseline：訂閱時已存在嘅歷史訊號一律略過**並計數**（唔靜默）
4. 全自動口徑逐項斷言（同回測同一個 trade_marks + position_model.step）：
   開倉 → 持倉中同向如實 IGNORE → **雙向模式無「平倉」只有反手 = 2 張單**（先平後開、同根收盤、
   持倉數唔增加、每日筆數計 2 筆）→ 沽空（方向 = short）→ 一次 poll 只行動一個（deferred 下一輪補做）
4b. ⚠️ 持倉模式真係跟每條綁定（用戶：長倉/短倉/雙向 兩頁都要可选到）：第二條綁定（唔同週期 + 唔同策略 +
   **長倉**）→ **同一個 S 訊號：雙向 = 反手 2 張、長倉 = 只平 1 張**；兩條綁定各自持倉、baseline 獨立
5. 風控三項如實擋（冷卻 / 最大持倉 / 每日筆數），0 = 最嚴、兩項獨立、即時生效
   （⚠️ 雙向模式永遠唔會空倉 → 最大持倉數必須用長倉綁定先測到，測試口徑都要如實）
6. 半自動：訊號入待執行（唔推進狀態）、如實顯示「若全自動會被邊項擋」、人確認先落單 / 拒絕 / 清空
7. 落單失敗 → `requeue()` 還原持倉狀態（狀態同實際落單唔會脫節）→ 其後重試成功
8. 帳戶／持倉／訂單 = 讀券商真相（冇揀帳戶如實擋）；冇交易能力嘅券商**唔靜默轉券商**
9. 三語 retranslate（表頭／模式／狀態／占位文字全部跟語言）、頁可引用嘅全部 i18n 鍵三語齊、
   shell registry 註冊（PAGE_KEYS / _PAGE_CLASSES / NAV_DIRECT 頂層直按）
10. 持久化 + 重載（還原參數／綁定但**唔會**自動開始監控）+ aboutToQuit 收線（thread + broker 一齊釋放）
"""
import asyncio
import os
import re
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gateway.state_store as state_store  # noqa: E402

_TMPDIR = tempfile.mkdtemp(prefix='e2e_quant_')
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state.json'   # 🤖 hermetic：tmp state 檔

from PySide6.QtCore import QMargins, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableView  # noqa: E402
import pandas as pd  # noqa: E402

import gateway.accounts as acc  # noqa: E402 — 帳戶顯示名與別名屬共用模組（三頁同一份規則）
import gateway.position_model as pm  # noqa: E402
import gateway.quant_exec as qe  # noqa: E402
import gateway.strategies as st  # noqa: E402
from gateway.i18n import t  # noqa: E402
from gateway.pages import quant_page as bp  # noqa: E402
from gateway.ui.loader import UI_DIR  # noqa: E402
from modules import trade_base as tb  # noqa: E402


def expect_name(lang, acc_id, env_key='trade_env_sim', broker='fake', alias=''):
    """帳戶顯示名的期望值：由 i18n 重組一次。

    斷言不呼叫被測的 `accounts.display_name`，否則檢查會變成同義反復（生成規則寫錯也照樣綠）。
    `broker='fake'` = FakeTradeClient.trade_supported 回傳的券商 key；未知券商的標籤即券商名本身
    （`accounts.broker_label` 的回落行為），因此不需要 i18n key。
    """
    label = alias or t('ta_label_fmt', lang).format(broker=broker, env=t(env_key, lang))
    return t('ta_name_fmt', lang).format(label=label, acc_id=acc_id)


FAILURES = []
TOTAL = []


def check(name, ok, detail=''):
    TOTAL.append(1)
    print(('  ✅ ' if ok else '  ❌ ') + name + (f'  [{detail}]' if not ok and detail else ''))
    if not ok:
        FAILURES.append(name)


def wait_for(app, cond, what='?', timeout=20.0):
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


# ── fake 資料：同行情頁 E2E 同一份 K 線 contract ──
SERIES = [100.0 + i for i in range(60)]     # 全部唔同價 → 斷言無歧義


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


class FakeSearchDir:
    def __init__(self):
        self._one = {'code': 'HK.00700', 'name': '騰訊控股', 'name_zh': '腾讯控股',
                     'name_en': 'Tencent'}
        self._by_code = {'HK.00700': self._one}

    def search(self, kw, limit=20, types=None):   # ⚠️ 要食埋 `types=`：symbol_input 一定帶呢個 kwarg
        return [self._one] if 'ten' in str(kw).lower() or '訊' in str(kw) else []

    def has_code(self, code):
        return str(code).upper() in self._by_code

    def get(self, code):
        return self._by_code.get(str(code).upper())


class FakeTradeClient:
    """交易契約 fake（`modules/trade_base.py` 形狀）：所有调用 → (status, data, message)。
       stream generator 每次 loop 都重讀 `self.frame` → 測試任何一刻 push 新 K 線都得。"""

    def __init__(self):
        self.entered = 0
        self.exited = 0
        self.frame = {}          # (code,ktype) -> df
        self.stream_calls = []
        self.place_calls = []
        self.place_fail = ''     # 非空 = 模擬券商拒絕（回執失敗 + 原文 message）
        self.supported = True
        self.refreshed = []      # 邊個 read API 被 call 過
        self.accounts = [{'acc_id': '7001', 'trd_env': 'SIMULATE', 'acc_type': 'MARGIN',
                          'trdmarket_auth': 'HK', 'acc_status': 'ACTIVE'},
                         {'acc_id': '7002', 'trd_env': 'REAL', 'acc_type': 'MARGIN',
                          'trdmarket_auth': 'HK', 'acc_status': 'ACTIVE'}]
        # ⚠️ 呢兩組 fixture 唔好叫 `positions`/`open_orders`：會遮蔽下面兩個 async 方法，
        #    頁 `getattr(client, api)(...)` 就會拿到 list → 'list' object is not callable
        self.pos_rows = [{'code': 'HK.00700', 'qty': 2, 'can_sell_qty': 2,
                          'market_name': 'HK', 'pl_val': 12.0, 'pl_ratio': 0.06}]
        self.order_rows = [{'order_id': '9001', 'code': 'HK.00700', 'qty': 2,
                            'price': 120.0, 'status': 'SUBMITTED'}]

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *a):
        self.exited += 1

    # ⚠️ 真 `BrokerClient.trade_supported()` 係 SYNC（能力旗標唔使打網絡）
    # 第二個回傳值 = 實際提供服務的券商 key（真實現在 registry 一律小寫）。帳戶顯示名要用呢個值
    # 生成 acc_key，所以 fake 必須跟同一個大小寫，否則測試驗證的 key 形狀同真實環境唔同。
    def trade_supported(self, broker=None):
        if self.supported:
            return True, 'fake', ''
        return False, 'fake', '呢家券商唔支援交易：FAKE'

    async def stream_kline(self, *, code, ktype, broker=None, kline_num=None):
        self.stream_calls.append({'code': code, 'ktype': ktype, 'broker': broker})
        key = (code, ktype)

        async def gen():
            while True:
                df = self.frame.get(key)
                if df is not None:
                    yield df
                await asyncio.sleep(0.02)
        return True, gen(), ''

    async def unlock_status(self, *, account=None, env=None, broker=None):
        return True, {'unlocked': True, 'known': True, 'hint': ''}, ''

    async def place_order(self, *, code, side, qty, price, account=None, env=None,
                          order_type=None, tif=None, broker=None):
        rec = {'code': code, 'side': side, 'qty': qty, 'price': price,
               'account': account, 'env': env, 'broker': broker}
        self.place_calls.append(rec)
        if self.place_fail:
            return False, None, self.place_fail
        return True, {'order_id': f'OD{len(self.place_calls)}', 'status': 'SUBMITTED'}, ''

    async def trade_accounts(self, *, broker=None):
        self.refreshed.append('accounts')
        return True, list(self.accounts), ''

    async def positions(self, *, account=None, env=None, broker=None):
        self.refreshed.append('positions')
        return True, list(self.pos_rows), ''

    async def open_orders(self, *, account=None, env=None, broker=None):
        self.refreshed.append('open_orders')
        return True, list(self.order_rows), ''


# ── Part 0：fixture（訊號一律由 patch 咗嘅 trade_marks 決定 → 唔靠策略計算，口徑先可控）──
print('\n=== Part 0: fixture ===')
st.reset_manager_for_test()
MARKS = {}          # strategy id -> [(bar, side)]：每條綁定（= 每個策略）各自一套訊號
_real_marks = st.trade_marks


def _marks(entry, ohlc):
    raw = (ohlc or {}).get('c')
    n = 0 if raw is None else len(raw)   # ⚠️ 唔准 `or []`：`c` 係 ndarray，truth value 直接炸
    sid = str((entry or {}).get('id') or '')
    return [(i, float(SERIES[i]), s) for (i, s) in MARKS.get(sid, []) if i < n]


st.trade_marks = _marks
# ⚠️ 一定要 patch `quant_exec` 嗰份：`quant_exec` 喺 import 期已經 bind 咗 `trade_marks`
#    （`from gateway.strategies import trade_marks`），改 `gateway.strategies` 對佢冇效。
qe.trade_marks = _marks
mgr = st.get_manager()
ok_add, msg_add, strat = mgr.add('QT Cross',
                                 [{'type': 'ma_cross', 'side': 'above',
                                   'params': {'fast': 2, 'slow': 5}, 'score': 100}],
                                 [{'type': 'ma_cross', 'side': 'below',
                                   'params': {'fast': 2, 'slow': 5}, 'score': 100}])
SID = (strat or {}).get('id') or ''
check('fixture：策略建立成功', ok_add and bool(SID), msg_add)

app = QApplication.instance() or QApplication([])
fake = FakeTradeClient()
page = bp.QuantPage(client_factory=lambda: fake)
page._directory = FakeSearchDir()   # 🤖 hermetic：唔起真 symbol index
page.show()
check('fixture：worker ready', wait_for(app, lambda: page._worker is not None, what='worker ready'))

CODE = 'HK.00700'
KEY = (CODE, 'K_DAY')
KEY2 = (CODE, 'K_5M')     # 第二條綁定：同一標、唔同週期（去重 key 含週期 → 唔算重覆）


def log_text():
    return page.qnt_event_log.toPlainText()


def has_log(key, lang='zh_hk'):
    return t(key, lang) in log_text()


def has_log_fmt(key, lang='zh_hk', **args):
    """帶參嘅文案（{n}/{m}/{e}）→ 斷言實際渲染出嚟嘅句子，唔係 template。"""
    return t(key, lang).format(**args) in log_text()


def bind_rows():
    return page.bind_model.rows()


def pending_rows():
    return page.pending_model.rows()


def cell(model, row, col):
    return model.data(model.index(row, col), Qt.DisplayRole)


def push(n):
    """push 一段新 K 線（前 n 根）→ 下一個 poll 自然食到。"""
    fake.frame[KEY] = _df(SERIES[:n])


def push2(n):
    fake.frame[KEY2] = _df(SERIES[:n])


def add_mark(bar, side, sid=None):
    MARKS.setdefault(str(sid or SID), []).append((bar, side))


def set_risk_sync(pos=None, trades=None, cool=None):
    """改風控 → 等 worker 真收咗先繼續（否則會用住舊參數 consume 咗個訊號）。"""
    if pos is not None:
        page.qnt_risk_pos.setValue(pos)
    if trades is not None:
        page.qnt_risk_trades.setValue(trades)
    if cool is not None:
        page.qnt_risk_cool.setValue(cool)
    want = {'max_positions': page.qnt_risk_pos.value(),
            'max_trades_day': page.qnt_risk_trades.value(),
            'cooldown_bars': page.qnt_risk_cool.value()}
    return wait_for(app, lambda: (page._worker._guard.risk == want), what=f'risk={want}')


def set_mode_sync(semi):
    (page.qnt_mode_semi if semi else page.qnt_mode_auto).click()
    return wait_for(app, lambda: page._worker._auto is (not semi),
                    what=f'auto={not semi}')


# ── Part 1：`.ui` 骨架 ──
print('\n=== Part 1: .ui 骨架（排版喺 gateway/ui/quant_page.ui）===')
check('root objectName = quant_page', page.objectName() == 'quant_page')
lay = page.layout()
check('root layout margin/spacing 跟 .ui',
      lay.contentsMargins() == QMargins(10, 8, 10, 8) and lay.spacing() == 6,
      f'{lay.contentsMargins()} {lay.spacing()}')
NEEDED = ('qnt_broker_lbl', 'qnt_broker', 'qnt_env_lbl', 'qnt_env', 'qnt_account_lbl',
          'qnt_account', 'qnt_acc_refresh', 'qnt_mode_lbl', 'qnt_mode_auto', 'qnt_mode_semi',
          'qnt_symbol', 'qnt_ktype_lbl', 'qnt_ktype', 'qnt_strategy_lbl', 'qnt_strategy',
          'qnt_mode_pos_lbl', 'qnt_mode', 'qnt_qty_lbl', 'qnt_qty', 'qnt_add_btn',
          'qnt_remove_btn', 'qnt_watch_btn', 'qnt_status', 'qnt_hint', 'qnt_bind_card',
          'qnt_bind_table', 'qnt_risk_card', 'qnt_risk_positions_lbl', 'qnt_risk_pos',
          'qnt_risk_trades_lbl', 'qnt_risk_trades', 'qnt_risk_cooldown_lbl', 'qnt_risk_cool',
          'qnt_risk_today_lbl', 'qnt_today_val', 'qnt_risk_open_lbl', 'qnt_open_val',
          'qnt_risk_zero_hint', 'qnt_pending_card', 'qnt_confirm_btn', 'qnt_reject_btn',
          'qnt_clear_pending', 'qnt_confirm_hint', 'qnt_pending_table', 'qnt_tabs',
          'tab_positions', 'qnt_refresh_pos', 'qnt_pos_note', 'qnt_pos_table',
          'tab_orders', 'qnt_refresh_orders', 'qnt_orders_note', 'qnt_order_table',
          'tab_log', 'qnt_log_clear', 'qnt_event_log',
          # ticket #35：頁級使用說明 + 分區備注（文案屬 i18n，控件屬 `.ui`）
          'qnt_page_note', 'qnt_bind_note', 'qnt_pending_note', 'qnt_log_note')
missing = [n for n in NEEDED if getattr(page, n, None) is None]
check(f'全部 {len(NEEDED)} 個 objectName 都注入到 Python 屬性', not missing, str(missing))
# ⚠️ 以 `.ui` 宣告為真相：`findChildren` 會混埋 Qt 自己嘅內部名（qt_scrollarea_viewport 等）
declared = set(re.findall(r'<widget class="[^"]*" name="([^"]+)"',
                          (UI_DIR / 'quant_page.ui').read_text(encoding='utf-8')))
check(f'.ui 宣告嘅 {len(declared)} 個 objectName（root + {len(NEEDED)} 個）全部有 Python 屬性、冇漏網',
      declared - {page.objectName()} == set(NEEDED) and not missing,
      f'ui-only={sorted(declared - set(NEEDED) - {page.objectName()})}')
check('⚠️ 回歸閘：`.ui` 冇宣告任何以 `qt_` 開頭嘅 objectName（QUiLoader 唔為呢類保留前綴注入 Python 屬性）',
      not [n for n in declared if n.startswith('qt_')],
      str([n for n in declared if n.startswith('qt_')]))
check('四個表都係 QTableView',
      all(isinstance(getattr(page, n), QTableView)
          for n in ('qnt_bind_table', 'qnt_pending_table', 'qnt_pos_table', 'qnt_order_table')))
check('三個 tab（持倉/訂單/日志）喺 qnt_tabs',
      page.qnt_tabs.count() == 3 and page.qnt_tabs.widget(0).objectName() == 'tab_positions')
check('og stamp 已注入（primary/watch/mode/status）',
      page.qnt_add_btn.property('og') == 'qtprimary' and page.qnt_watch_btn.property('og') == 'qtwatch'
      and page.qnt_mode_auto.property('og') == 'qtmode' and page.qnt_status.property('og') == 'qtstatus')
check('頁面級 QSS 根 selector 喺度', 'QWidget#quant_page' in page.styleSheet())
check('事件日志 QPlainTextEdit 唯讀 + 有 block 上限',
      page.qnt_event_log.isReadOnly() and page.qnt_event_log.maximumBlockCount() > 0)

# ── Part 2：選項內容全部由 domain / registry 填 ──
print('\n=== Part 2: 選項由 domain 填（呢頁唔會自創第二份清單）===')
check('spin 範圍 = quant_exec 常數（qty/持倉/筆數/冷卻）',
      (page.qnt_qty.minimum(), page.qnt_qty.maximum()) == (qe.QTY_LO, qe.QTY_HI)
      and (page.qnt_risk_pos.minimum(), page.qnt_risk_pos.maximum()) == (qe.POS_LO, qe.POS_HI)
      and (page.qnt_risk_trades.minimum(), page.qnt_risk_trades.maximum()) == (qe.TRADES_LO, qe.TRADES_HI)
      and (page.qnt_risk_cool.minimum(), page.qnt_risk_cool.maximum()) == (qe.COOL_LO, qe.COOL_HI))
check('預設值 = domain 預設（風控 3/10/1、qty 1）',
      (page.qnt_risk_pos.value(), page.qnt_risk_trades.value(), page.qnt_risk_cool.value())
      == (qe.MAX_POSITIONS_DEFAULT, qe.MAX_TRADES_DAY_DEFAULT, qe.COOLDOWN_DEFAULT)
      and page.qnt_qty.value() == qe.QTY_DEFAULT)
from gateway.pages.quotes_page import KTYPES  # noqa: E402
check('週期清單 = 行情頁 KTYPES（一份，唔重覆定義）',
      [page.qnt_ktype.itemText(i) for i in range(page.qnt_ktype.count())] == list(KTYPES))
from modules.registry import BROKERS  # noqa: E402
check('券商 = registry，第 0 項 = 跟設定（broker=None）',
      [page.qnt_broker.itemData(i) for i in range(page.qnt_broker.count())]
      == [''] + [b.upper() for b in BROKERS])
check('環境 = trade_base.TRADE_ENVS（實盤＋模擬都可选，用戶要求）',
      [page.qnt_env.itemData(i) for i in range(page.qnt_env.count())] == list(tb.TRADE_ENVS)
      and page.qnt_env.currentData() == tb.TRADE_ENV_DEFAULT)
check('持倉模式 = position_model.MODES（長倉/短倉/雙向，同回測共用同一份 enum）',
      [page.qnt_mode.itemData(i) for i in range(page.qnt_mode.count())] == list(pm.MODES)
      and 'both' in [page.qnt_mode.itemData(i) for i in range(page.qnt_mode.count())])
mgr = st.get_manager()
check('策略下拉 = StrategyManager 現況（id 作 data、名作文字，唔自創一份策略清單）',
      [page.qnt_strategy.itemData(i) for i in range(page.qnt_strategy.count())]
      == [e['id'] for e in mgr.items()]
      and [page.qnt_strategy.itemText(i) for i in range(page.qnt_strategy.count())]
          == [e['name'] for e in mgr.items()] and SID in
      [page.qnt_strategy.itemData(i) for i in range(page.qnt_strategy.count())])
# ⚠️ 一定要俾齊 buy + sell：`StrategyManager.add` 對空 sell 一律回 `str_no_rules`（唔係 listener 壞）
_, _, s2 = mgr.add('QT Second', [{'type': 'ma_cross', 'side': 'above',
                                  'params': {'fast': 1, 'slow': 2}}],
                                 [{'type': 'ma_cross', 'side': 'below',
                                   'params': {'fast': 1, 'slow': 2}}])
SID2 = (s2 or {}).get('id') or ''
check('策略新增 → 呢頁下拉即時跟（喺策略頁改，唔使人重新開頁）',
      SID2 in [page.qnt_strategy.itemData(i) for i in range(page.qnt_strategy.count())])
# 開頁都會推一次 snapshot（`set_risk` 必發）→ 提示/狀態要如實反映「乜都未發生」
wait_for(app, lambda: page.qnt_confirm_hint.text() == t('qt_pending_empty'), what='first snapshot')
check('開頁：四張表全空、狀態 = 閒置、待執行提示 = 空',
      len(bind_rows()) == 0 and len(pending_rows()) == 0
      and page.pos_model.rowCount() == 0 and page.order_model.rowCount() == 0
      and page.qnt_status.text() == t('qt_status_idle')
      and page.qnt_confirm_hint.text() == t('qt_pending_empty'))
check('未落單：今日筆數／持倉數 = 0（唔扮數）',
      page.qnt_today_val.text() == '0' and page.qnt_open_val.text() == '0')
check('全自動 = 預設 checked、半自動未 checked', page.qnt_mode_auto.isChecked()
      and not page.qnt_mode_semi.isChecked())

# ── Part 3：加綁定 → 開始監控 → baseline（歷史訊號如實計數）──
print('\n=== Part 3: 加綁定 / 開始監控 / baseline 略過歷史訊號 ===')
page.qnt_mode.setCurrentIndex(page.qnt_mode.findData('both'))    # 用戶：長短雙向（支援沽空）
page.qnt_qty.setValue(2)
check('改數量即時推到 worker', wait_for(app, lambda: page._worker._qty == 2, what='qty=2'))
page.qnt_strategy.setCurrentIndex(page.qnt_strategy.findData(SID))
page.qnt_symbol.setText('tencent')
# ⚠️ 模糊輸入要行足真 user path：textChanged → debounce(200ms) → set_query → 候選 model →
#    用戶喺 popup 揀第一項（activated）→ `apply_item` 將欄變成淨返 canonical CODE，並經
#    `on_activate` **即時加綁定**（呢頁嘅設計：揀完即加，唔使人再撳一次「加綁定」）。
#    所以順序必須係「先配齊模式/數量/策略，後揀標的」；倒轉就會用預設值加咗一條。
comp = page.qnt_symbol.completer()
check('打「tencent」→ 模糊搜尋俾到候選（make_search + fake index，唔起真 index）',
      wait_for(app, lambda: bool(comp.model().stringList()), what='completer hits', timeout=6.0))
check('⚠️ 候選文字 =「CODE  名稱」（打中文名都睇到），唔係得 code',
      comp.model().stringList()[0].startswith(CODE) and len(comp.model().stringList()[0]) > len(CODE),
      str(comp.model().stringList()))
n_before = len(bind_rows())
comp.activated.emit(comp.model().stringList()[0])
pump(app, 5)   # apply_item 用 singleShot(0) 喺事件尾蓋返過嚟
check('揀咗候選 → 欄入面係 canonical CODE（唔係「HK.00700  騰訊控股」）',
      page.qnt_symbol.text() == CODE, page.qnt_symbol.text())
check('加綁定：經真 widget（揀候選即加）→ worker → 表出現一行',
      n_before == 0 and wait_for(app, lambda: len(bind_rows()) == 1, what='1 binding'))
r0 = bind_rows()[0]
check('綁定內容 = 輸入（code 正規化做 Futu canonical、週期、策略、模式 both）',
      r0['code'] == CODE and r0['ktype'] == 'K_DAY' and r0['mode'] == 'both'
      and r0['strategy'] == 'QT Cross', str(r0))
page.qnt_add_btn.click()
check('同一個 標的×週期×策略 唔會雙綁定（如實擋）',
      wait_for(app, lambda: has_log('qt_err_exists')) and len(bind_rows()) == 1)
page.qnt_watch_btn.click()
check('開始監控 → 訂閱（stream_kline 收到 code/ktype/broker=None = 跟設定）',
      wait_for(app, lambda: len(fake.stream_calls) == 1
               and fake.stream_calls[0] == {'code': CODE, 'ktype': 'K_DAY', 'broker': None},
               what='stream subscribe'))
check('訂閱成功 → 狀態行 = 監控中(1)、按鈕 checked、日誌有訂閱成功',
      wait_for(app, lambda: page.qnt_watch_btn.isChecked()
               and page.qnt_status.text() == t('qt_status_watching_fmt').format(n=1)
               and has_log('qt_sub_ok'), what='watching'))
check('全自動但未揀帳戶 → 只提示、唔硬擋（IB 唔使 acc_id → 擋死會錯殺）',
      has_log('qt_err_no_account'))
check('解鎖提示：已解鎖 → 唔會誤報 qt_unlock_hint', not has_log('qt_unlock_hint'))

# baseline：訂閱時已存在嘅訊號一律唔落單，但如實計數
add_mark(3, 'B')
add_mark(10, 'S')
push(20)
check('首個 snapshot：baseline 前嘅歷史訊號一律略過**並計數**（n=2，唔靜默）',
      wait_for(app, lambda: has_log_fmt('qt_baseline_skip_fmt', n=2), what='baseline skip'))
check('歷史訊號零落單（一開頁唔會追落幾十張單）',
      len(fake.place_calls) == 0 and bind_rows()[0]['acted'] == 0)

BID = f'{CODE}|K_DAY|{SID}'
BID2 = f'{CODE}|K_5M|{SID2}'


def pos_dir(bid=BID):
    """直接睇 worker 嘅持倉方向（表唔顯示方向，但風控/落單全部食呢個 → 必須如實）。"""
    b = page._worker._bindings.get(bid)
    return (b or {}).get('state', {}).get('dir')


def pos_dir2():
    return pos_dir(BID2)


def dbg():
    """失敗時要睇到真相：落單數 / 頁上兩個計數 / 每條綁定嘅方向同統計（⚠️ sig 會因為 deferred
       而每 poll 重覆計同一個訊號 → 只作診斷，唔作斷言）。"""
    rows = bind_rows() or []
    return (f'calls={len(fake.place_calls)} today={page.qnt_today_val.text()!r} '
            f'open={page.qnt_open_val.text()!r} '
            f'dirs={[b.get("state", {}).get("dir") for b in page._worker._bindings.values()]} '
            + ' '.join(f"[{r.get('ktype')} acted={r.get('acted')} blk={r.get('blocked')} "
                       f"ign={r.get('ignored')} sig={r.get('sig')}]" for r in rows)
            + f' risk={page._worker._guard.risk}')


# ── Part 4：全自動口徑（同回測同一個 trade_marks + position_model.step）──
print('\n=== Part 4: 全自動（雙向）— 開倉 / 同向忽略 / 反手 = 2 張 / 沽空 / deferred ===')
add_mark(20, 'B')
push(24)
check('開倉（long）→ 1 張 BUY，價 = 訊號根收盤（同回測成交價口徑一致）',
      wait_for(app, lambda: len(fake.place_calls) == 1, what='open BUY'))
o1 = fake.place_calls[0]
check('落單參數如實：code/side/qty(頁輸入 2)/price/env=SIMULATE/broker=None',
      o1['code'] == CODE and o1['side'] == 'BUY' and o1['qty'] == 2
      and o1['price'] == SERIES[20] and o1['env'] == tb.TRADE_ENV_DEFAULT and o1['broker'] is None,
      str(o1))
check('成功 = 「已送出」（回執唔帶成交與否 → 用字如實），日誌帶回執 order_id',
      wait_for(app, lambda: has_log('qt_ok_sent') and 'order_id=OD1' in log_text(), what='ok_sent'))
check('綁定行 acted=1；今日筆數=1、持倉數=1、持倉方向=long',
      wait_for(app, lambda: bind_rows()[0]['acted'] == 1 and page.qnt_today_val.text() == '1'
               and page.qnt_open_val.text() == '1' and pos_dir() == pm.DIR_LONG, what='acted=1'))
add_mark(22, 'B')
push(26)
check('持倉中同一方向訊號 → 如實計入 ignored 並講明原因（bt_ig_inpos）',
      wait_for(app, lambda: bind_rows()[0]['ignored'] == 1 and has_log('bt_ig_inpos')),
      'expected ignored += 1 with reason bt_ig_inpos')
check('持倉中忽略唔多落一張單（單倉模型，唔加倉）', len(fake.place_calls) == 1)
add_mark(24, 'S')
push(28)
check('⚠️ 雙向模式冇「平倉」呢個動作：反向訊號 = 反手 → 先平後開 = 2 張 SELL（同一根收盤）',
      wait_for(app, lambda: len(fake.place_calls) == 3, what='flip 2 SELL'), dbg())
check('反手兩張都係 SELL（平多 = 沽出、開空亦 = 沽出），價 = 訊號根收盤',
      all(o['side'] == 'SELL' and o['price'] == SERIES[24] for o in fake.place_calls[1:3]),
      str(fake.place_calls[1:3]))
check('反手 = 每日筆數 +2（先平後開如實計兩筆）、持倉數仍 = 1（反手唔增加持倉）',
      wait_for(app, lambda: page.qnt_today_val.text() == '3' and page.qnt_open_val.text() == '1',
               what='today=3 open=1'), dbg())
check('沽空：反手後持倉方向 = short（用戶：長短雙向、支援沽空）', pos_dir() == pm.DIR_SHORT, dbg())
check('一次行動 = 一行 acted（唔係一張單）→ acted = 2', bind_rows()[0]['acted'] == 2, dbg())
add_mark(26, 'S')
push(30)
check('持空倉遇 S → 一樣如實 IGNORE（雙向 ≠ 雙倉）',
      wait_for(app, lambda: bind_rows()[0]['ignored'] == 2 and len(fake.place_calls) == 3), dbg())
add_mark(28, 'B')
add_mark(29, 'S')
push(32)
check('一次 poll 只行動一個訊號 → 其餘 deferred（留返下一輪，唔會漏）',
      wait_for(app, lambda: has_log_fmt('qt_deferred_fmt', n=1), what='deferred'),
      f'log=\n{log_text()}')
check('deferred 嗰輪先做嘅係 bar 28 反手（short→long）= 2 張 BUY @ 訊號根收盤',
      wait_for(app, lambda: len(fake.place_calls) >= 5
               and all(o['side'] == 'BUY' and o['price'] == SERIES[28]
                       for o in fake.place_calls[3:5]), what='flip @28'), str(fake.place_calls[3:5]))
check('被 deferred 嘅訊號下一輪自動補做：bar 29 反手（long→short）= 2 張 SELL',
      wait_for(app, lambda: len(fake.place_calls) == 7
               and all(o['side'] == 'SELL' and o['price'] == SERIES[29]
                       for o in fake.place_calls[5:7]), what='deferred flip @29'),
      str(fake.place_calls[5:7]))
check('兩輪反手後：今日筆數 = 7、持倉數仍然 = 1、acted = 4、方向 = short',
      wait_for(app, lambda: page.qnt_today_val.text() == '7' and page.qnt_open_val.text() == '1'
               and bind_rows()[0]['acted'] == 4 and pos_dir() == pm.DIR_SHORT,
               what='today=7 acted=4'), dbg())
check('持倉模式欄顯示 = 三語 mode 文案（雙向）',
      cell(page.bind_model, 0, 3) == t('mode_both'))
check('監控狀態欄顯示 = 監控中', cell(page.bind_model, 0, 4) == t('qt_st_watching'))

# ── Part 4b：持倉模式真係跟每條綁定（用戶：「長倉，短倉，雙向，BACKTEST 都可以選」）──
print('\n=== Part 4b: 第二條綁定（長倉）— 同一個 S 訊號：雙向 = 反手 2 張、長倉 = 只平 1 張 ===')
page.qnt_mode.setCurrentIndex(page.qnt_mode.findData('long'))
page.qnt_ktype.setCurrentIndex(page.qnt_ktype.findText('K_5M'))
page.qnt_strategy.setCurrentIndex(page.qnt_strategy.findData(SID2))
page.qnt_add_btn.click()
check('監控中加第二條綁定（唔同週期 + 唔同策略 + 長倉模式）→ 即時訂閱，唔使人停止再開始',
      wait_for(app, lambda: len(bind_rows()) == 2 and len(fake.stream_calls) == 2
               and fake.stream_calls[1] == {'code': CODE, 'ktype': 'K_5M', 'broker': None},
               what='2nd binding'), str(fake.stream_calls))
check('第二條綁定如實標成長倉（worker 同表都係 long）',
      wait_for(app, lambda: (page._worker._bindings.get(BID2) or {}).get('mode') == pm.MODE_LONG)
      and bind_rows()[1]['mode'] == pm.MODE_LONG
      and cell(page.bind_model, 1, 3) == t('mode_long'))
check('狀態欄如實 = 監控中(2)',
      wait_for(app, lambda: page.qnt_status.text() == t('qt_status_watching_fmt').format(n=2)))
push2(20)
check('每條綁定自己嘅 baseline（watermark 獨立，唔共用 bar index）',
      wait_for(app, lambda: (page._worker._bindings.get(BID2) or {}).get('gate', {}).get('baseline') == 19,
               what='baseline2'))
add_mark(20, 'B', SID2)
push2(24)
check('長倉模式：空倉遇 B → 開倉 1 張 BUY（開倉口徑同雙向完全一致）',
      wait_for(app, lambda: len(fake.place_calls) == 8
               and fake.place_calls[7]['side'] == 'BUY' and fake.place_calls[7]['price'] == SERIES[20],
               what='long open'), str(fake.place_calls[7:]))
check('兩條綁定各自持倉：持倉數 = 2、今日筆數 = 8（計數按落單張數，唔係按行動）',
      wait_for(app, lambda: page.qnt_open_val.text() == '2' and page.qnt_today_val.text() == '8'), dbg())
add_mark(24, 'S', SID2)
push2(28)
check('⚠️ 同一個 S 訊號：長倉模式 = 只平倉 1 張 SELL（唔反手）→ 持倉模式真係落到執行',
      wait_for(app, lambda: len(fake.place_calls) == 9
               and fake.place_calls[8]['side'] == 'SELL' and fake.place_calls[8]['price'] == SERIES[24],
               what='long close'), str(fake.place_calls[8:]))
check('長倉平倉後該綁定返空倉（方向 None）、持倉數 2→1；另一條雙向綁定完全唔受影響',
      wait_for(app, lambda: page.qnt_open_val.text() == '1' and pos_dir2() is None
               and pos_dir() == pm.DIR_SHORT), dbg())

# ── Part 5：風控三項如實擋（用戶：「最小三項」，全部可調、0 = 最嚴）──
# ⚠️ 雙向模式永遠唔會空倉 → 「最大同時持倉數」只可以喺長倉綁度（Part 4b 嗰條）先測到，
#    測試口徑都要如實，唔可以喺雙向綁度假扮擋到。
print('\n=== Part 5: 風控三項（冷卻 / 最大持倉 / 每日筆數）===')
check('改風控即時推到 worker（唔使停止再開始）', set_risk_sync(cool=999))
add_mark(30, 'B')
push(36)
check('冷卻未過 → 如實擋（qt_rk_cooldown）、計入 blocked、零落單',
      wait_for(app, lambda: has_log('qt_rk_cooldown') and bind_rows()[0]['blocked'] == 1)
      and len(fake.place_calls) == 9, dbg())
pump(app, 20)
check('被擋嘅訊號已 consume → 唔會每 poll 重覆報告（日誌只一次）',
      log_text().count(t('qt_rk_cooldown')) == 1)
check('被擋唔會推進持倉（照住 short）', pos_dir() == pm.DIR_SHORT, dbg())
check('放寬風控（冷卻歸零、每日筆數 99）即時推到 worker', set_risk_sync(cool=0, trades=99))
add_mark(32, 'B')
push(38)
check('放寬之後同一個方向嘅反手照常執行 → 2 張 BUY @ 訊號根收盤（即時生效，唔使重新監控）',
      wait_for(app, lambda: len(fake.place_calls) == 11
               and all(o['side'] == 'BUY' and o['price'] == SERIES[32]
                       for o in fake.place_calls[9:11]), what='flip @32'), str(fake.place_calls[9:]))
check('反手成功 → 持倉數仍 = 1（雙向唔增加持倉）、今日筆數 = 11、方向 = long',
      wait_for(app, lambda: page.qnt_open_val.text() == '1' and page.qnt_today_val.text() == '11'
               and pos_dir() == pm.DIR_LONG, what='today=11'), dbg())
set_risk_sync(pos=0)
add_mark(26, 'B', SID2)
push2(30)
check('最大同時持倉數 = 0（0 = 最嚴，唔係無限）→ 開倉被擋 qt_rk_positions',
      wait_for(app, lambda: has_log('qt_rk_positions') and bind_rows()[1]['blocked'] == 1)
      and len(fake.place_calls) == 11, dbg())
check('開倉被擋 → 該綁定仍然空倉（唔扮持倉）、頁上持倉數冇變',
      pos_dir2() is None and page.qnt_open_val.text() == '1', dbg())
check('放寬最大持倉數到 2 即時推到 worker', set_risk_sync(pos=2))
add_mark(28, 'B', SID2)
push2(32)
check('放寬後先至開到倉（1 張 BUY）：持倉數 = 2（上限就係上限，0 唔係無限）',
      wait_for(app, lambda: len(fake.place_calls) == 12 and page.qnt_open_val.text() == '2'
               and pos_dir2() == pm.DIR_LONG, what='pos=2 open'), dbg())
set_risk_sync(pos=2, trades=0)
add_mark(30, 'S', SID2)
push2(34)
check('每日最大筆數 = 0 → 即使持倉數夠（2 ≤ 2）亦照擋（兩項風控各自獨立）',
      wait_for(app, lambda: has_log('qt_rk_trades') and bind_rows()[1]['blocked'] == 2)
      and len(fake.place_calls) == 12, dbg())
check('被擋唔改持倉：長倉綁定照住 long', pos_dir2() == pm.DIR_LONG, dbg())
check('風控提示一律喺事件日志講明（唔靜默吃訊號）',
      all(has_log(k) for k in (qe.RK_COOLDOWN, qe.RK_POSITIONS, qe.RK_TRADES)))

# ── Part 6：半自動（用戶：「兩者都要，頁上有開關」）──
print('\n=== Part 6: 半自動 — 訊號入待執行、人撳掣先落單 ===')
check('頁上開關轉半自動：掣跟住、日誌講明切換到邊個模式',
      set_mode_sync(True) and wait_for(app, lambda: has_log_fmt('qt_mode_switched_fmt',
                                                                m=t('qt_mode_semi'))))
set_risk_sync(pos=2, trades=0, cool=0)
add_mark(34, 'S')
push(40)
check('半自動：訊號入待執行，**唔自動落單、唔推進持倉**',
      wait_for(app, lambda: len(pending_rows()) == 1) and len(fake.place_calls) == 12
      and pos_dir() == pm.DIR_LONG and page.qnt_open_val.text() == '2')
p = pending_rows()[0]
check('待執行行講清楚會做乜：反手 / 沽出 / 訊號根收盤 / 頁輸入數量 / 根數 / 反手 = 2 張',
      p['action'] == pm.ACT_FLIP and p['side'] == pm.SIDE_S and p['price'] == SERIES[34]
      and p['qty'] == 2 and p['n_orders'] == 2 and p['bar'] == 34, str(p))
check('待執行如實預告「若全自動會被邊項擋」（而家 max_trades_day=0 → 每日筆數已達上限）',
      p['reason'] == qe.RK_TRADES and cell(page.pending_model, 0, 7) == t('qt_rk_trades'), str(p))
check('有待執行 → 提示轉做確認文案（唔係空清單文案）',
      page.qnt_confirm_hint.text() == t('qt_confirm_hint'))
check('半自動唔食風控（人就係風控）：今日筆數冇因為訊號而變', page.qnt_today_val.text() == '12')
check('未確認前唔計冷卻（冇行動就冇冷卻）',
      page._worker._guard._last_bar.get((CODE, BID)) == 32)
check('放寬每日筆數即時推到 worker', set_risk_sync(trades=99))
page.qnt_pending_table.selectRow(0)
page.qnt_confirm_btn.click()
check('人確認 → 先至落單：反手 = 2 張 SELL @ 訊號根收盤、qty 跟頁輸入',
      wait_for(app, lambda: len(fake.place_calls) == 14
               and all(o['side'] == 'SELL' and o['price'] == SERIES[34] and o['qty'] == 2
                       for o in fake.place_calls[12:14]), what='confirmed flip'),
      str(fake.place_calls[12:]))
check('確認後：待執行清空、提示返做空清單、持倉推進 short、今日 = 14',
      wait_for(app, lambda: len(pending_rows()) == 0 and page.qnt_open_val.text() == '2'
               and page.qnt_today_val.text() == '14' and pos_dir() == pm.DIR_SHORT
               and page.qnt_confirm_hint.text() == t('qt_pending_empty')), dbg())
# acted 累計口徑 = 「行動」唔係「單」：Part 4 四次（開/反手/反手/反手）+ Part 5 反手 1 次 + 呢次確認 = 6
check('人確認一次 = 一行 acted（唔係一張單）→ acted = 6', bind_rows()[0]['acted'] == 6, dbg())
add_mark(36, 'B')
push(42)
check('反手訊號都入待執行（半自動一律由人決定）；風控全過 → 原因欄空白',
      wait_for(app, lambda: len(pending_rows()) == 1
               and pending_rows()[0]['action'] == pm.ACT_FLIP
               and pending_rows()[0]['reason'] == ''), str(pending_rows()))
check('表上 action/side 欄顯示 = 三語文案（反手 + 如實標明係 2 張 / 買入）',
      cell(page.pending_model, 0, 2).startswith(t('qt_act_flip'))
      and t('qt_n_orders_fmt').format(n=2) in cell(page.pending_model, 0, 2)
      and cell(page.pending_model, 0, 3) == t('bt_side_b'),
      f"action={cell(page.pending_model, 0, 2)!r} side={cell(page.pending_model, 0, 3)!r}")
page.qnt_pending_table.selectRow(0)
page.qnt_reject_btn.click()
check('人拒絕 → 唔落單、行消失、日誌講明被人拒絕；持倉唔變',
      wait_for(app, lambda: len(pending_rows()) == 0 and has_log('qt_rejected'))
      and len(fake.place_calls) == 14 and pos_dir() == pm.DIR_SHORT, dbg())
add_mark(38, 'B')
push(44)
check('清空待執行 → 如實講清咗幾個（n=1）',
      wait_for(app, lambda: len(pending_rows()) == 1)
      and (page.qnt_clear_pending.click(),
           wait_for(app, lambda: has_log_fmt('qt_cleared_fmt', n=1)))[1])

# ── Part 7：落單失敗 → 狀態還原（即時先有嘅風險）──
print('\n=== Part 7: 落單失敗 → requeue 還原持倉（狀態同實際落單唔會脫節）===')
check('開關轉返全自動', set_mode_sync(False))
set_risk_sync(pos=2, trades=99, cool=0)
fake.place_fail = '模擬拒絕：交易未解鎖'
add_mark(40, 'B')
push(46)
check('落單失敗 → 如實報告（qt_err_place + 券商原文照錄，唔吞 message）',
      wait_for(app, lambda: has_log('qt_err_place') and '模擬拒絕：交易未解鎖' in log_text()))
check('⚠️ 落單失敗 → 投機推進已撤回：持倉方向仍然 short（唔會當自己已反手）',
      pos_dir() == pm.DIR_SHORT and page.qnt_open_val.text() == '2', dbg())
check('失敗唔計入 acted / 今日筆數 / 冷卻（冇成交就冇呢啲數）',
      bind_rows()[0]['acted'] == 6 and page.qnt_today_val.text() == '14'
      and page._worker._guard._last_bar.get((CODE, BID)) == 34, dbg())
check('失敗嘅嘗試照樣算一次嘗試（fake 記錄咗 2 張被拒嘅單 → 唔扮冇發生）',
      len(fake.place_calls) == 16, f'calls={len(fake.place_calls)}')
fake.place_fail = ''
add_mark(42, 'B')
push(48)
check('其後再嚟嘅訊號正常執行 → 先至真正反手（2 張 BUY @ 訊號根收盤）',
      wait_for(app, lambda: len(fake.place_calls) == 18 and pos_dir() == pm.DIR_LONG
               and all(o['side'] == 'BUY' and o['price'] == SERIES[42]
                       for o in fake.place_calls[16:18]), what='retry flip'), str(fake.place_calls[16:]))
check('成功後 acted = 7、今日 = 16（失敗嗰次冇被偷偷計咗）',
      wait_for(app, lambda: bind_rows()[0]['acted'] == 7 and page.qnt_today_val.text() == '16'), dbg())

# ── Part 8：移除綁定 / 帳戶 / 持倉 / 訂單 = 讀券商真相 ──
print('\n=== Part 8: 移除綁定 / 帳戶 / 持倉 / 訂單（全部讀券商真相）===')
page.qnt_ktype.setCurrentIndex(page.qnt_ktype.findText('K_60M'))
page.qnt_add_btn.click()
check('監控中加第三個綁定（唔同週期）→ 即時訂閱，唔使人停止再開始',
      wait_for(app, lambda: len(bind_rows()) == 3 and len(fake.stream_calls) == 3
               and fake.stream_calls[2]['ktype'] == 'K_60M', what='3rd binding'))
check('狀態欄如實 = 監控中(3)',
      wait_for(app, lambda: page.qnt_status.text() == t('qt_status_watching_fmt').format(n=3)))
page.qnt_bind_table.selectRow(2)
page.qnt_remove_btn.click()
check('移除綁定 → 行消失、日誌講明、剩返 2 個（去重 key 含週期 → K_60M 唔算重覆）',
      wait_for(app, lambda: len(bind_rows()) == 2 and has_log('qt_removed')))
check('冇揀帳戶 → 刷新持倉如實擋喺前面（唔打網絡、唔扮空表）',
      (page.qnt_refresh_pos.click(), page.qnt_status.text() == t('qt_need_account'))[1]
      and 'positions' not in fake.refreshed)
check('⚠️ 未取過帳戶前下拉留空（未問過券商就聲稱「無帳戶」係說謊）', page.qnt_account.count() == 0)
page.qnt_acc_refresh.click()
check('刷新帳戶 → 帳戶下拉由券商真相填（兩筆帳戶）',
      wait_for(app, lambda: page.qnt_account.count() >= 2, what='accounts'))
idx = page.qnt_account.findData('7001')
check('帳戶項 data = acc_id（落單先至帶啱帳戶）', idx >= 0)
# 項目文字 = accounts.display_name（與交易帳戶頁同一份生成規則）；acc_id 保留在括號內
check('帳戶項文字 = 顯示名（券商+環境+帳戶ID，由 i18n 生成）',
      page.qnt_account.itemText(idx) == expect_name(page._lang, '7001'))
# 別名屬全域儲存：在交易帳戶頁設定 → 本頁下拉即取代生成名（「全域以別名選擇帳戶」的最低要求）
acc.set_alias('fake:7001', '主帳戶')
page._refill_account_combo()
idx = page.qnt_account.findData('7001')
check('全域別名生效：取代生成名、保留 acc_id',
      page.qnt_account.itemText(idx) == expect_name(page._lang, '7001', alias='主帳戶'))
acc.set_alias('fake:7001', '')   # 空值即刪除該筆 → 回落生成名
page._refill_account_combo()
idx = page.qnt_account.findData('7001')
check('重設別名後回落生成名',
      page.qnt_account.itemText(idx) == expect_name(page._lang, '7001'))
page.qnt_account.setCurrentIndex(idx)
page.qnt_account.activated.emit(idx)   # ⚠️ 程式化 setCurrentIndex 唔發 activated（Qt 只喺用戶揀時發）
check('⚠️ 揀咗帳戶 → worker 一定要收（否則落單永遠 account=None = 錯錢）',
      wait_for(app, lambda: page._worker._account == '7001', what='worker account'))
page.qnt_refresh_pos.click()
check('刷新持倉 → 表由券商真相填，並註明「持倉讀自券商帳戶」（唔係本頁假設）',
      wait_for(app, lambda: page.pos_model.rowCount() == 1 and t('qt_pos_note') in page.qnt_pos_note.text(),
               what='positions'))
c_pl = [i for i, (k, _, _) in enumerate(bp.POS_COLS) if k == 'pl_val'][0]
check('持倉數字欄右對齊 + 小數格式（唔係 str(dict)）',
      cell(page.pos_model, 0, c_pl) == '12.00', cell(page.pos_model, 0, c_pl))
page.qnt_refresh_orders.click()
check('刷新訂單 → 表由券商真相填（order_id 可見）',
      wait_for(app, lambda: page.order_model.rowCount() == 1 and 'positions' in fake.refreshed
               and 'open_orders' in fake.refreshed, what='orders'))
check('持倉/訂單兩張表都係 contract 欄（唔自創欄位）',
      [k for k, _, _ in bp.POS_COLS] == list(tb.POS_COLS)
      and [k for k, _, _ in bp.ORDER_COLS] == list(tb.ORDER_COLS))
page.qnt_watch_btn.click()   # 停止監控
check('停止監控 → 訂閱收線、日誌講明「持倉狀態保留」（停止 ≠ 平倉）',
      wait_for(app, lambda: not page.qnt_watch_btn.isChecked() and has_log('qt_watch_stopped')
               and page.qnt_status.text() == t('qt_status_idle')))
check('⚠️ 停止監控唔會動持倉狀態（停止 ≠ 平倉：倉仲喺券商度，要人自己平）',
      pos_dir() == pm.DIR_LONG and page.qnt_open_val.text() == '2', dbg())
fake.supported = False
page.qnt_watch_btn.click()
check('券商冇交易能力 → 如實擋（qt_no_trade_broker）、**唔靜默轉券商**、按鈕返轉 unchecked、零新訂閱',
      wait_for(app, lambda: has_log('qt_no_trade_broker') and not page.qnt_watch_btn.isChecked())
      and len(fake.stream_calls) == 3, str(fake.stream_calls))
check('⚠️ 出錯一律照到狀態行（唔好郁唔聲），而且連券商講嘅一齊列（唔吞 message）',
      '呢家券商唔支援交易：FAKE' in page.qnt_status.text() and '呢家券商唔支援交易：FAKE' in log_text(),
      f'status={page.qnt_status.text()!r}')

# ── Part 9：三語跟隨 + i18n 齊鍵 + 外殼註冊 ──
print('\n=== Part 9: 三語 retranslate / i18n 齊鍵 / 外殼 registry ===')
from gateway.i18n import LANGS  # noqa: E402
import gateway.app as appmod  # noqa: E402


def missing(key, lang):
    try:
        return None if t(key, lang) else f'{key}/{lang} 空白'
    except KeyError:
        return f'{key}/{lang}'


check('外殼 registry：quant 喺 PAGE_KEYS / _PAGE_CLASSES / NAV_DIRECT（用戶指定頂層直按）',
      'quant' in appmod.PAGE_KEYS and appmod._PAGE_CLASSES.get('quant') is bp.QuantPage
      and 'quant' in appmod.NAV_DIRECT)
check('導航 / 頁標題三語都有（唔靠 fallback）',
      all(t(k, l) for k in ('nav_quant', 'page_quant_title') for l in LANGS))

page.retranslate('en')
pump(app, 5)
check('切 EN：卡片標題 / 表頭 / 模式 / 監控狀態 / 按鈕全部跟語言（含已停止狀態同按鈕文案）',
      page.qnt_bind_card.title() == t('qt_bind_card', 'en')
      and page.bind_model.headerData(0, Qt.Horizontal, Qt.DisplayRole) == t('qt_head_code', 'en')
      and cell(page.bind_model, 0, 3) == t('mode_both', 'en')
      and cell(page.bind_model, 0, 4) == t('qt_st_stopped', 'en')
      and page.qnt_watch_btn.text() == t('qt_watch_start', 'en'))
# 帳戶項文字屬生成文字 → 換語言必須由新語言重生成（改舊 itemText 會留低舊語言）
check('切 EN：帳戶下拉的顯示名由新語言重新生成（選中帳戶不變）',
      page.qnt_account.currentData() == '7001'
      and page.qnt_account.itemText(page.qnt_account.findData('7001')) == expect_name('en', '7001'))
page.retranslate('zh_cn')
pump(app, 5)
check('切 zh_cn：同一套 key、简体文案（冇第二份字串）',
      page.qnt_risk_card.title() == t('qt_risk_card', 'zh_cn')
      and page.qnt_mode_semi.text() == t('qt_mode_semi', 'zh_cn')
      and page.qnt_symbol.placeholderText() == t('qt_symbol_ph', 'zh_cn'))
page.retranslate('zh_hk')
pump(app, 5)
check('切返 zh_hk（預設）', page.qnt_bind_card.title() == t('qt_bind_card')
      and cell(page.bind_model, 0, 3) == t('mode_both'))

# ── ticket #35：使用說明備注 = `.ui` 控件 + i18n 三語 + QSS role 契約（三者少一樣都係靜默失敗）──
NOTES = {'qnt_page_note': ('qt_page_note', 'pagebody'),
         'qnt_bind_note': ('qt_bind_note', 'usagehint'),
         'qnt_pending_note': ('qt_pending_note', 'usagehint'),
         'qnt_log_note': ('qt_log_note', 'usagehint'),
         'qnt_risk_zero_hint': ('qt_risk_zero_hint', 'usagehint')}
check(f'使用說明備注（{len(NOTES)} 條）三語齊全、無空白',
      all(t(k, l).strip() for _, (k, _) in NOTES.items() for l in LANGS))
check('備注已套用文案並帶 role（無 role → QSS 無聲失效，等於用戶睇唔到淡色提示）',
      all(getattr(page, n).text() == t(k) and str(getattr(page, n).property('role')) == r
          for n, (k, r) in NOTES.items()))
page.retranslate('en')
pump(app, 5)
check('切 EN：使用說明照跟語言（唔係寫死母語）',
      all(getattr(page, n).text() == t(k, 'en') for n, (k, _) in NOTES.items()))
page.retranslate('zh_hk')
pump(app, 5)

src = Path(bp.__file__).read_text(encoding='utf-8')
KEYS = {k for k in re.findall(r"'((?:qt|mode|bt|col)_[a-z0-9]+)'", src)}
KEYS |= set(bp._TEXT.values()) | set(bp._PH.values()) | {k for _, k in bp._TAB_KEYS}
KEYS |= set(bp._ENV_KEYS.values())
KEYS |= {h for _, h, _ in bp.BIND_COLS} | {h for _, h, _ in bp.PENDING_COLS}
KEYS |= {h for _, h, _ in bp.POS_COLS} | {h for _, h, _ in bp.ORDER_COLS}
KEYS |= {f'qt_st_{v}' for v in ('watching', 'stopped', 'error', 'nostrat')}
KEYS |= {f'mode_{m}' for m in pm.MODES}
KEYS |= {f'qt_act_{a}' for a in (pm.ACT_OPEN, pm.ACT_CLOSE, pm.ACT_FLIP)}
KEYS |= {f'qt_lv_{l}' for l in ('info', 'warn', 'err')}
KEYS |= {pm.IG_INPOS, pm.IG_FLAT, pm.IG_BADPRICE,
         qe.RK_POSITIONS, qe.RK_TRADES, qe.RK_COOLDOWN, qe.ERR_QTY}
KEYS |= {'nav_quant', 'page_quant_title', 'bt_broker_default', 'bt_side_b', 'bt_side_s'}
gaps = [m for k in sorted(KEYS) for l in LANGS for m in [missing(k, l)] if m]
check(f'頁可以引用嘅全部 i18n 鍵（{len(KEYS)} 個）三語齊、無空白', not gaps, str(gaps[:10]))

# ── Part 10：本地記憶 / 重載唔自動監控 / 收線 ──
print('\n=== Part 10: 持久化、重載唔自動監控、aboutToQuit 收線 ===')
# 持久化食嘅就係而家嘅輸入（綁定表先係真相 → 輸入得返做「下次加嘅預設」）
page.qnt_ktype.setCurrentIndex(page.qnt_ktype.findText('K_DAY'))
page.qnt_strategy.setCurrentIndex(page.qnt_strategy.findData(SID))
page.qnt_mode.setCurrentIndex(page.qnt_mode.findData('both'))
page._on_app_quit()
check('收線 → worker thread 真收線（唔留幽魂 event loop）', page._thread.isFinished())
check('收線 → broker 連線一齊釋放（__aexit__ 被調）', fake.exited == 1, f'exited={fake.exited}')
saved = state_store.load_section(qe.SECTION, {})
check('本地記憶寫入 section `quant`（名屬領域層 quant_exec.SECTION，唔係頁自創）',
      bool(saved.get('bindings')))
check('持久化 = 設定 + **全部**綁定（每條自帶持倉模式）；**唔 save** worker 內部持倉/計數（嗰啲要由券商真相返嚟）',
      len(saved['bindings']) == 2
      and set(saved['bindings'][0]) == {'code', 'ktype', 'strategy', 'mode'}
      and saved['bindings'][0]['code'] == CODE and saved['bindings'][0]['mode'] == 'both'
      and saved['bindings'][0]['strategy'] == SID
      and saved['bindings'][1]['ktype'] == 'K_5M' and saved['bindings'][1]['mode'] == pm.MODE_LONG
      and saved['bindings'][1]['strategy'] == SID2
      and saved['ktype'] == 'K_DAY' and saved['mode'] == 'both' and saved['strategy'] == SID
      and saved['qty'] == 2 and saved['auto'] is True
      and saved['risk'] == {'max_positions': 2, 'max_trades_day': 99, 'cooldown_bars': 0}
      and saved['env'] == tb.TRADE_ENV_DEFAULT and saved['account'] == '7001', str(saved)[:300])

fake2 = FakeTradeClient()
page2 = bp.QuantPage(client_factory=lambda: fake2)
page2._directory = FakeSearchDir()
page2.show()
check('重載 → 兩條綁定全部由本地記憶重建（唔使人再揀一次）',
      wait_for(app, lambda: page2._worker is not None and len(page2.bind_model.rows()) == 2,
               timeout=25.0, what='restored bindings'))
r2 = page2.bind_model.rows()[0]
check('重載還原：參數 / 風控 / 環境 / 數量 / 模式開關全部跟返上次',
      page2.qnt_qty.value() == 2 and page2.qnt_risk_pos.value() == 2
      and page2.qnt_risk_trades.value() == 99 and page2.qnt_risk_cool.value() == 0
      and page2.qnt_mode.currentData() == 'both' and page2.qnt_ktype.currentText() == 'K_DAY'
      and page2.qnt_env.currentData() == tb.TRADE_ENV_DEFAULT
      and page2.qnt_mode_auto.isChecked() and r2['mode'] == 'both', str(r2))
check('⚠️ 每條綁定嘅持倉模式各自還原（長倉嗰條唔會俾人統一變返雙向）',
      page2.bind_model.rows()[1]['mode'] == pm.MODE_LONG
      and cell(page2.bind_model, 1, 3) == t('mode_long'), str(page2.bind_model.rows()[1]))
check('⚠️ 重載**唔會**自動開始監控（即時落單必須人明確啟動 — 唔可以偷偷開火）',
      page2._worker._watching is False and not page2.qnt_watch_btn.isChecked()
      and page2.qnt_status.text() == t('qt_status_idle') and fake2.stream_calls == [])
check('重載後持倉計數由 0 開始（唔扮有倉；真相要刷新持倉先至知）',
      page2.qnt_today_val.text() == '0' and page2.qnt_open_val.text() == '0')
page2._on_app_quit()
check('⚠️ 第二個頁收線同樣乾淨；未開始監控就根本唔會開連線（所以都冇嘢好關）',
      page2._thread.isFinished() and fake2.entered == 0 and fake2.exited == 0,
      f'entered={fake2.entered} exited={fake2.exited}')

print('\n' + ('❌ 失敗 ' + str(len(FAILURES)) + ' 項：' + ' | '.join(FAILURES)
              if FAILURES else f'✅ 全部通過（{len(TOTAL)} 項）— 量化交易頁 34d 交付'))
sys.exit(1 if FAILURES else 0)
