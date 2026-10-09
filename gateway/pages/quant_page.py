"""One Gate Page — 量化交易：策略訊號 → 即時落單（全自動／半自動）。

用戶明確取捨（全部已落碼）：
- 「兩者都要，頁上有開關」→ 全自動（行風控、即行即落單）⇄ 半自動（訊號入「待執行」，人確認先落單）。
- 「實盤模擬都可以」「FUTU + IB 都要」→ 環境 = `trade_base.TRADE_ENVS`、券商 = `modules.registry.BROKERS`；
  冇交易能力嘅券商**唔靜默轉券商**（`trade_supported` 能力旗標如實擋）。
- 「長短雙向（支援沽空）」+「回測試交易都可以選」→ 持倉模式一律用 `position_model.MODES`
  （同回測頁共用同一份 enum、同一個 `step()` 狀態機）。
- 風控「最小三項」：最大同時持倉數／每日最大筆數／同一標的冷卻根數，全部可調；0 = 最嚴（唔係無上限）。

架構（點解咁分）：
- **口徑全部喺 `gateway/quant_exec.py` + `position_model.py`**（純計算、冇 Qt）：訊號 = `strategies.trade_marks`、
  持倉 = `position_model.step` → 同回測用**完全同一個函數**，唔存在第二份訊號/持倉邏輯，所以「回測表現」
  同「實際落單」唔會講兩套數。呢頁只做輸入 + 渲染。
- **狀態住喺 worker thread**：綁定、持倉、風控、待執行全部喺 `QuantWorker`；GUI 只 send 指令、收
  snapshot（`state`）／事件（`event`）／券商資料（`trade_data`）。stream callback 同使用者操作係并发嘢，
  狀態集中喺一处先至唔會 race。
- **事件 key 語言中立**：worker 只發 i18n key + 事實 detail，由頁用**而家**嘅語言翻譯 → 轉語言唔會令
  已經寫低嘅日誌變錯。
- **如實優先**：落單成功 = 「已送出（未確認成交）」（`trade_base` 契約：回執唔帶成交與否）；持倉／訂單直接
  讀券商帳戶（唔係本頁模擬）；訂閱時已存在嘅歷史訊號一律略過**並計數**；落單失敗 → `requeue()` 還原持倉
  狀態（狀態同實際落單唔會脫節）；半自動確認時若持倉已中途變過 → 如實講「冇推進狀態」。
- **唔會自動開始監控**：真錢自動落單必須係人明確撳一下；本地記憶只還原綁定／參數。
- **排版**喺 `gateway/ui/quant_page.ui`；選項內容（券商／環境／週期／策略／帳戶／持倉模式／spin 範圍）
  屬資料 → 一律由 registry / domain 填。Theme / i18n 照其他頁 recipe。

單獨運行：`python gateway/pages/quant_page.py`。
"""
import asyncio
import logging
import math
import os
import string
import sys
import time
from collections import OrderedDict

# ── standalone bootstrap（同其他頁同一 convention；package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QAbstractTableModel, QObject, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QApplication,  # noqa: E402
                               QHeaderView, QSpinBox, QWidget)

import gateway.quant_exec as qe  # noqa: E402 — 即時執行口徑單一來源（同回測共用 position_model/strategies）
import gateway.strategies as strategies  # noqa: E402
import gateway.theme as theme_mod  # noqa: E402
from gateway import position_model as pm, state_store  # noqa: E402 — 持倉方向 enum 一來源
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.kline_stream import ClientHolderMixin, LoopThreadBase  # noqa: E402 — 同行情/K線頁共用 stream 基建
from gateway.pages import gui_kline as gk  # noqa: E402 — 紅漲綠跌常數單一來源
from gateway.pages.quotes_page import KTYPES  # noqa: E402 — 週期清單同行情頁共用一份
from gateway.symbol_input import attach_symbol_input, make_search  # noqa: E402 — 全域模糊輸入
from gateway.ui.bind import apply_text, stamp  # noqa: E402
from gateway.ui.loader import apply_ui  # noqa: E402
from modules import trade_base as tb  # noqa: E402 — 交易契約（環境 enum、欄位定義、數值欄）
from modules.registry import BROKERS as BROKER_REGISTRY  # noqa: E402 — 券商名單一來源

DASH = '—'             # 冇值一律如實顯示 '—'（唔扮似有值）
MAX_BINDINGS = 12      # 每個綁定 = 一條 live 訂閱 → 唔可以無限開（會打爆券商連線），到頂如實擋
_RIGHT_FMTS = ('money', 'int', 'pl')

# 表欄 spec = (row key, 欄頭 i18n key, 格式) — 欄頭/格式/排序/對齊/紅綠一處過（唔使第二份欄名）
BIND_COLS = (('code', 'qt_head_code', 'text'),
             ('ktype', 'qt_head_ktype', 'text'),
             ('strategy', 'qt_head_strategy', 'text'),
             ('mode', 'qt_head_mode', 'mode'),
             ('state', 'qt_head_state', 'state'),
             ('sig', 'qt_head_sig', 'int'),
             ('acted', 'qt_head_acted', 'int'),
             ('blocked', 'qt_head_blocked', 'int'),
             ('ignored', 'qt_head_ignored', 'int'),
             ('last', 'qt_head_last', 'last'))
PENDING_COLS = (('time', 'qt_head_time', 'text'),
                ('code', 'qt_head_code', 'text'),
                ('action', 'qt_head_action', 'action'),
                ('side', 'qt_head_side', 'side'),
                ('price', 'qt_head_price', 'money'),
                ('qty', 'qt_head_qty', 'int'),
                ('bar', 'qt_head_bar', 'int'),
                ('reason', 'qt_head_reason', 'would'))


def _contract_cols(cols):
    """券商契約欄位（`trade_base.POS_COLS`/`ORDER_COLS`）→ 表欄 spec。
       欄名顯示用現成嘅 `col_*` keys；邊欄係數值由 `NUMERIC_COLS` 決定（右對齊/紅綠）→
       加欄改契約就得，呢度唔會有多一份欄名。"""
    out = []
    for c in cols:
        if c in ('pl_val', 'pl_ratio'):
            fmt = 'pl'                      # 得返「本身即賺/蝕」嘅欄先上色
        elif c in ('qty', 'can_sell_qty', 'dealt_qty'):
            fmt = 'int'
        elif c in tb.NUMERIC_COLS:
            fmt = 'money'
        else:
            fmt = 'text'
        out.append((c, f'col_{c}', fmt))
    return tuple(out)


POS_COLS = _contract_cols(tb.POS_COLS)
ORDER_COLS = _contract_cols(tb.ORDER_COLS)

_BIND_WIDTHS = {0: 96, 1: 62, 2: 130, 3: 118, 4: 74, 5: 58, 6: 58, 7: 70, 8: 58}
_PENDING_WIDTHS = {0: 130, 1: 96, 2: 96, 3: 62, 4: 84, 5: 58, 6: 58}

# `.ui` 入面嘅靜態 widget：QSS property（Designer 帶唔住）+ 文字來源（見 gateway/ui/bind.py）
_STAMP = {
    'quant_page': {},   # bare QWidget 要 WA_StyledBackground 先食到頁面級背景 QSS
    'qnt_broker_lbl': {'og': 'qtlbl'}, 'qnt_env_lbl': {'og': 'qtlbl'},
    'qnt_account_lbl': {'og': 'qtlbl'}, 'qnt_mode_lbl': {'og': 'qtlbl'},
    'qnt_ktype_lbl': {'og': 'qtlbl'}, 'qnt_strategy_lbl': {'og': 'qtlbl'},
    'qnt_mode_pos_lbl': {'og': 'qtlbl'}, 'qnt_qty_lbl': {'og': 'qtlbl'},
    'qnt_risk_positions_lbl': {'og': 'qtlbl'}, 'qnt_risk_trades_lbl': {'og': 'qtlbl'},
    'qnt_risk_cooldown_lbl': {'og': 'qtlbl'}, 'qnt_risk_today_lbl': {'og': 'qtlbl'},
    'qnt_risk_open_lbl': {'og': 'qtlbl'},
    'qnt_hint': {'og': 'qtnote'}, 'qnt_risk_zero_hint': {'og': 'qtnote'},
    'qnt_confirm_hint': {'og': 'qtnote'}, 'qnt_pos_note': {'og': 'qtnote'},
    'qnt_orders_note': {'og': 'qtnote'},
    'qnt_status': {'og': 'qtstatus'},
    'qnt_today_val': {'og': 'qtval'}, 'qnt_open_val': {'og': 'qtval'},
    'qnt_add_btn': {'og': 'qtprimary'}, 'qnt_confirm_btn': {'og': 'qtprimary'},
    'qnt_watch_btn': {'og': 'qtwatch'},
    'qnt_mode_auto': {'og': 'qtmode'}, 'qnt_mode_semi': {'og': 'qtmode'},
}
_TEXT = {'qnt_broker_lbl': 'qt_broker_lbl', 'qnt_env_lbl': 'qt_env_lbl',
         'qnt_account_lbl': 'qt_account_lbl', 'qnt_mode_lbl': 'qt_mode_lbl',
         'qnt_ktype_lbl': 'qt_ktype_lbl', 'qnt_strategy_lbl': 'qt_strategy_lbl',
         'qnt_mode_pos_lbl': 'mode_lbl', 'qnt_qty_lbl': 'qt_qty_lbl',
         'qnt_add_btn': 'qt_add_btn', 'qnt_remove_btn': 'qt_remove_btn',
         'qnt_acc_refresh': 'qt_acc_refresh',
         'qnt_mode_auto': 'qt_mode_auto', 'qnt_mode_semi': 'qt_mode_semi',
         'qnt_confirm_btn': 'qt_confirm_btn', 'qnt_reject_btn': 'qt_reject_btn',
         'qnt_clear_pending': 'qt_clear_pending',
         'qnt_refresh_pos': 'qt_refresh_pos', 'qnt_refresh_orders': 'qt_refresh_orders',
         'qnt_log_clear': 'qt_log_clear',
         'qnt_bind_card': 'qt_bind_card', 'qnt_risk_card': 'qt_risk_card',
         'qnt_pending_card': 'qt_tab_pending',
         'qnt_risk_positions_lbl': 'qt_risk_positions', 'qnt_risk_trades_lbl': 'qt_risk_trades',
         'qnt_risk_cooldown_lbl': 'qt_risk_cooldown', 'qnt_risk_today_lbl': 'qt_risk_today',
         'qnt_risk_open_lbl': 'qt_risk_open', 'qnt_risk_zero_hint': 'qt_risk_zero_hint',
         'qnt_confirm_hint': 'qt_confirm_hint',
         'qnt_pos_note': 'qt_pos_note', 'qnt_orders_note': 'qt_pos_note',
         'qnt_hint': 'qt_hint'}
_PH = {'qnt_symbol': 'qt_symbol_ph'}
_TAB_KEYS = (('tab_positions', 'qt_tab_positions'), ('tab_orders', 'qt_tab_orders'),
             ('tab_log', 'qt_tab_log'))
_ENV_KEYS = {'SIMULATE': 'qt_env_sim', 'REAL': 'qt_env_real'}


def _num(v, fmt):
    """數字 → 千分位字串；唔係數／非有限 → ''（由 `_cell` 轉 '—'，唔扮似有值）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ''
    if not math.isfinite(f):
        return ''
    return f'{int(round(f)):,d}' if fmt == 'int' else f'{f:,.2f}'


def _sort_key(v, fmt):
    if fmt in _RIGHT_FMTS:
        try:
            return float(v)
        except (TypeError, ValueError):
            return float('-inf')
    return str(v or '')


def _sign_color(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ''
    if not math.isfinite(f) or f == 0:
        return ''
    return gk.C_UP if f > 0 else gk.C_DOWN   # 紅漲綠跌（語義色單一來源）


class QuantWorker(QObject, ClientHolderMixin):
    """住喺 worker thread：所有即時狀態（綁定／持倉／風控／待執行）都喺呢度，GUI 只 send 指令 + 收 snapshot。
       口徑一律外判 `quant_exec` / `position_model` / `strategies` → 呢檔只負責「即時先有嘅嘢」：
       訂閱、去重排程、落單、如實記錄。"""

    state = Signal(object)        # snapshot：綁定行／待執行行／watching／auto／今日筆數／持倉方向數／風控
    event = Signal(object)        # {'level','key','args','detail','ts'} — key 屬 i18n，語言由頁決定
    trade_data = Signal(object)   # {'kind':'accounts'|'positions'|'orders','rows','error','detail'}

    def __init__(self, loop, client_factory=None):
        super().__init__()
        self.init_client_holder(loop, client_factory)
        self._bindings = OrderedDict()    # binding_id → binding（qe.new_binding + strategy_id/name）
        self._by_key = {}                 # (code, ktype, strategy_id) → binding_id（去重綁定）
        self._stats = {}                  # binding_id → 計數/最近動作/監控狀態（只俾 snapshot 用）
        self._tasks = {}                  # binding_id → consumer task（一條綁定一條 stream）
        self._consume_tasks = set()
        self._pending = qe.PendingQueue()
        self._guard = qe.RiskGuard()
        self._auto = True
        self._watching = False
        self._qty = qe.QTY_DEFAULT
        self._env = tb.TRADE_ENV_DEFAULT
        self._account = ''
        self._broker = ''
        self._today = 0
        self._date_key = time.strftime('%Y-%m-%d')

    # ── GUI-thread facade（全部非阻塞；loop 停咗就如實一句，唔靜默）──
    def add_binding(self, code, ktype, strategy_id, mode):
        self._send(self._add_binding, code, ktype, strategy_id, mode)

    def remove_bindings(self, ids):
        self._send(self._remove_bindings, list(ids))

    def start_watch(self):
        self._send(self._start_watch)

    def stop_watch(self):
        self._send(self._stop_watch)

    def set_auto(self, auto):
        self._send(self._set_auto, bool(auto))

    def set_env(self, env):
        self._send(self._set_env, tb.clamp_env(env))

    def set_account(self, account):
        self._send(self._set_account, str(account or ''))

    def set_broker(self, broker):
        self._send(self._set_broker, str(broker or ''))

    def set_qty(self, qty):
        self._send(self._set_qty, qe.clamp_qty(qty))

    def set_risk(self, risk):
        self._send(self._set_risk, risk)

    def confirm(self, key):
        self._send(self._confirm, tuple(key))

    def reject(self, key):
        self._send(self._reject, tuple(key))

    def clear_pending(self):
        self._send(self._clear_pending)

    def refresh_accounts(self):
        self._send(self._refresh_accounts)

    def refresh_positions(self):
        self._send(self._refresh_positions)

    def refresh_orders(self):
        self._send(self._refresh_orders)

    def _send(self, fn, *a, **kw):
        try:
            asyncio.run_coroutine_threadsafe(self._guarded(fn, a, kw), self._loop)
        except RuntimeError as e:   # loop 剛好停咗（收工中）→ 如實講，唔靜默吞指令
            self._emit_event('err', 'qt_err_worker', f'{type(e).__name__}: {e}')

    async def _guarded(self, fn, a, kw):
        """所有 loop-thread 指令經呢度：未處理 exception 會淨係留低一条 asyncio warning，
           用戶永遠睇唔到出事 → 一律如實入事件日誌。"""
        try:
            await fn(*a, **kw)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.exception('quant: 指令失敗')
            self._emit_event('err', 'qt_err_worker', f'{type(e).__name__}: {e}')

    async def _cancel_work(self):
        for task in list(self._consume_tasks):
            task.cancel()
        if self._consume_tasks:
            await asyncio.gather(*list(self._consume_tasks), return_exceptions=True)
        self._consume_tasks.clear()
        self._tasks.clear()

    # ── 記錄／snapshot ──
    def _emit_event(self, level, key, detail='', args=None):
        self.event.emit({'level': str(level), 'key': str(key), 'args': dict(args or {}),
                         'detail': str(detail or ''), 'ts': time.time()})

    def _desc(self, b):
        """日誌入面嘅「邊個綁定」= 純事實（唔帶語言）→ 得返文案由頁翻譯。"""
        return ' '.join(x for x in (b.get('code'), b.get('ktype'), b.get('strategy_name')) if x)

    def _sig_desc(self, sig):
        return f"bar {sig.get('bar_idx')} {sig.get('side')} @ {sig.get('price')}"

    def _stat(self, bid):
        return self._stats.get(bid)

    def _set_monitor(self, bid, state):
        st = self._stat(bid)
        if st is not None:
            st['state'] = state

    def _open_dirs(self):
        return qe.open_dirs(list(self._bindings.values()))

    def _snapshot(self):
        rows = []
        for bid, b in self._bindings.items():
            st = self._stat(bid) or _new_stats()
            rows.append({'binding_id': bid, 'strategy_id': b.get('strategy_id') or '',
                         'code': b['code'], 'ktype': b['ktype'],
                         'strategy': b.get('strategy_name') or '', 'mode': b['mode'],
                         'state': st['state'], 'sig': st['sig'], 'acted': st['acted'],
                         'blocked': st['blocked'], 'ignored': st['ignored'], 'last': st['last']})
        pending = []
        for act in self._pending.items():
            sig = act['sig']
            orders = act.get('orders') or []
            pending.append({'key': list(qe.sig_key(sig)), 'time': sig.get('time') or '',
                            'code': (self._bindings.get(act['binding_id']) or {}).get('code') or '',
                            'action': act['action'], 'side': sig['side'], 'price': sig['price'],
                            'qty': (orders[0].get('qty') if orders else 0),
                            'n_orders': len(orders), 'bar': sig.get('bar_idx'),
                            'reason': act.get('would_block') or ''})
        return {'rows': rows, 'pending': pending, 'watching': self._watching, 'auto': self._auto,
                'today': self._today, 'open_dirs': len(self._open_dirs()),
                'risk': dict(self._guard.risk)}

    def _emit_state(self):
        self.state.emit(self._snapshot())

    def _roll_date(self):
        """「每日最大筆數」要跨日自動重設（唔使人記得撳重設）。
           ⚠️ 唔好調 `guard.reset()`：冷卻係按 bar index，跨日重設會令冷卻形同虛設。"""
        key = time.strftime('%Y-%m-%d')
        if key != self._date_key:
            self._date_key = key
            self._today = 0

    # ── 綁定 ──
    async def _add_binding(self, code, ktype, strategy_id, mode):
        code, ktype, strategy_id = str(code or ''), str(ktype or ''), str(strategy_id or '')
        key = (code, ktype, strategy_id)
        if key in self._by_key:
            self._emit_event('warn', 'qt_err_exists', f'{code} {ktype}')
            return
        if len(self._bindings) >= MAX_BINDINGS:
            self._emit_event('warn', 'qt_err_too_many_fmt', args={'n': MAX_BINDINGS})
            return
        entry = strategies.get_manager().get(strategy_id)
        if entry is None:   # 策略已被刪除 → 如實講，唔起一條執行唔到嘅訂閱
            self._emit_event('err', 'qt_err_no_entry', f'{code} {ktype}')
            return
        bid = '|'.join(key)   # 穩定 id（同一個 標的×週期×策略 永遠同一個 key → 去重/日誌都對得上）
        b = qe.new_binding(binding_id=bid, code=code, ktype=ktype, mode=mode, entry=entry)
        b['strategy_id'] = strategy_id
        b['strategy_name'] = entry.get('name') or ''
        self._bindings[bid] = b
        self._by_key[key] = bid
        self._stats[bid] = _new_stats()
        if self._watching:   # 已喺度監控 → 新綁定即時訂閱，唔使人停止再開始
            await self._subscribe(bid)
        self._emit_state()

    async def _remove_bindings(self, ids):
        for bid in ids:
            b = self._bindings.get(bid)
            if b is None:
                continue
            await self._stop_stream(bid)
            self._by_key.pop((b['code'], b['ktype'], b.get('strategy_id') or ''), None)
            self._bindings.pop(bid, None)
            self._stats.pop(bid, None)
            # 待執行入面屬於呢個綁定嘅一齊清：留返落唔到單嘅嘢俾人誤撳，比冇咗更危險
            for act in [a for a in self._pending.items() if a['binding_id'] == bid]:
                self._pending.take(qe.sig_key(act['sig']))
            self._emit_event('info', 'qt_removed', self._desc(b))
        self._emit_state()

    # ── 設定 ──
    async def _set_auto(self, auto):
        if auto == self._auto:
            return
        self._auto = auto
        self._emit_event('info', 'qt_mode_switched_fmt',
                         args={'m': '@qt_mode_auto' if auto else '@qt_mode_semi'})
        self._emit_state()

    async def _set_env(self, env):
        if env == self._env:
            return
        self._env = env
        self._emit_event('info', 'qt_env_switched_fmt', args={'e': '@' + _ENV_KEYS[env]})
        self._emit_state()

    async def _set_account(self, account):
        self._account = account

    async def _set_broker(self, broker):
        """改券商 → 帳戶清單一定唔再適用（帳戶屬個別券商）→ 如實清走，唔留返落唔到單嘅選擇。"""
        self._broker = broker
        self._account = ''

    async def _set_qty(self, qty):
        self._qty = qty

    async def _set_risk(self, risk):
        self._guard.update(risk)
        self._emit_state()

    # ── 監控（訂閱）──
    async def _start_watch(self):
        if self._watching:
            return
        if not self._bindings:
            self._emit_event('warn', 'qt_err_no_bind')
            self._emit_state()   # 撳咗但被擋 → 按鈕要返轉 unchecked，唔好扮緊監控
            return
        try:
            client = await self.ensure_client()
            supported, name, message = client.trade_supported()
        except Exception as e:
            logging.exception('quant: trade_supported 失敗')
            self._emit_event('err', 'qt_err_worker', f'{type(e).__name__}: {e}')
            self._emit_state()
            return
        if not supported:   # 唔靜默轉券商（用戶：「FUTU + IB」→ 冇能力就照講冇能力）
            self._emit_event('err', 'qt_no_trade_broker',
                             '; '.join(x for x in (str(message or ''), str(name or '')) if x))
            self._emit_state()
            return
        if self._auto and not self._account:
            # 只提示、唔硬擋：futu 一定要 acc_id，IB 唔使（用連線本身）→ 擋死會錯殺 IB
            self._emit_event('warn', 'qt_err_no_account')
        await self._check_unlock()
        self._watching = True
        self._emit_event('info', 'qt_watch_started')
        for bid in list(self._bindings):
            await self._subscribe(bid)
        self._emit_state()

    async def _check_unlock(self):
        """解鎖與否只有全自動先有意義（半自動由人撳掣，唔會靜默落單）→ 未解鎖就提示，唔阻半自動。"""
        if not self._auto:
            return
        try:
            client = await self.ensure_client()
            ok, info, message = await client.unlock_status(account=self._account or None,
                                                           env=self._env)
        except Exception as e:
            logging.exception('quant: unlock_status 失敗')
            return
        if ok and info and info.get('known') and not info.get('unlocked'):
            self._emit_event('warn', 'qt_unlock_hint',
                             '; '.join(x for x in (str(info.get('hint') or ''),
                                                   str(message or '')) if x))

    async def _stop_watch(self):
        if not self._watching:
            return
        self._watching = False
        for bid in list(self._bindings):
            await self._stop_stream(bid)
            self._set_monitor(bid, 'stopped')
        # 持倉狀態刻意保留（「停止監控」唔係平倉，亦唔係忘記自己持緊咁）
        self._emit_event('info', 'qt_watch_stopped')
        self._emit_state()

    async def _subscribe(self, bid):
        await self._stop_stream(bid)
        b = self._bindings.get(bid)
        if b is None:
            return
        desc = self._desc(b)
        try:
            client = await self.ensure_client()
            status, gen, message = await client.stream_kline(code=b['code'], ktype=b['ktype'],
                                                             broker=self._broker or None)
        except Exception as e:
            logging.exception('quant: stream_kline 失敗')
            self._set_monitor(bid, 'error')
            self._emit_event('err', 'qt_sub_fail', f'{desc}｜{type(e).__name__}: {e}')
            return
        if not status or gen is None:
            self._set_monitor(bid, 'error')
            self._emit_event('err', 'qt_sub_fail', f'{desc}｜{str(message or "")}')
            return
        self._set_monitor(bid, 'watching')
        self._emit_event('info', 'qt_sub_ok', desc)

        async def consume():
            first = True
            try:
                async for df in gen:
                    await self._on_df(bid, df, first)
                    first = False
            except asyncio.CancelledError:
                raise
            except Exception as e:   # 訂閱中途斷 → 一定要見到，唔可以靜默收線
                logging.exception('quant: stream 中斷')
                self._set_monitor(bid, 'error')
                self._emit_event('err', 'qt_sub_fail', f'{desc}｜{type(e).__name__}: {e}')
                self._emit_state()

        task = asyncio.create_task(consume())
        self._tasks[bid] = task
        self._consume_tasks.add(task)

        def _cleanup(done, bid=bid, task=task):
            self._consume_tasks.discard(task)
            if self._tasks.get(bid) is task:
                self._tasks.pop(bid, None)
        task.add_done_callback(_cleanup)

    async def _stop_stream(self, bid):
        task = self._tasks.pop(bid, None)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # ── 一次行情更新 → 做乜（全部喺 loop thread）──
    async def _on_df(self, bid, df, first):
        b = self._bindings.get(bid)
        if b is None:
            return
        entry = strategies.get_manager().get(b.get('strategy_id') or '')
        if entry is None:   # 策略喺監控期間俾人刪咗 → 如實標出，唔靜默繼續
            b['entry'] = None
            self._set_monitor(bid, 'nostrat')
            self._emit_event('warn', 'qt_err_no_entry', self._desc(b))
            self._emit_state()
            return
        b['entry'] = entry
        b['strategy_name'] = entry.get('name') or b.get('strategy_name') or ''
        try:
            ohlc = strategies.ohlc_from_kline(df)
        except Exception as e:
            logging.exception('quant: ohlc 轉換失敗')
            self._emit_event('err', 'qt_err_worker', f'{self._desc(b)}｜{type(e).__name__}: {e}')
            return
        n = len(df)
        if first or b['gate']['baseline'] is None:
            qe.arm_baseline(b['gate'], n)   # ⚠️ 每次（重）訂閱都要 re-arm：bar index 由 0 重數
        self._roll_date()
        res = qe.on_bars(b, entry, ohlc,
                         times=(df['time_key'] if 'time_key' in df.columns else None),
                         guard=self._guard, auto=self._auto, qty=self._qty,
                         open_dirs_=self._open_dirs(), today_count=self._today)
        st = self._stat(bid) or _new_stats()
        st['sig'] += len(res['signals'])
        if first and res['skipped_history']:   # 只有第一輪嘅數先係真·歷史數（之後每 poll 都會重數）
            self._emit_event('info', 'qt_baseline_skip_fmt', self._desc(b),
                             args={'n': res['skipped_history']})
        for it in res['ignored']:
            st['ignored'] += 1
            st['last'] = {'kind': 'reason', 'key': it['reason_key']}
            self._emit_event('info', it['reason_key'], self._sig_desc(it['sig']))
        for blk in res['blocked']:
            st['blocked'] += 1
            st['last'] = {'kind': 'reason', 'key': blk['reason_key']}
            self._emit_event('warn', blk['reason_key'], self._sig_desc(blk['sig']))
        for act in res['pending']:
            # 半自動唔行風控（人就係風控），但如實講「若全自動會被邊項擋」→ 人揀之前睇到代價
            ok, rk = self._guard.check(code=b['code'], binding_id=bid,
                                       bar_idx=act['sig']['bar_idx'], prev=act['prev_state'],
                                       nxt=act['next_state'], open_dirs=self._open_dirs(),
                                       today_count=self._today)
            act['would_block'] = '' if ok else (rk or '')
            self._pending.add(act)
        if res['deferred']:
            self._emit_event('info', 'qt_deferred_fmt', self._desc(b),
                             args={'n': res['deferred']})
        for act in res['orders']:
            await self._place(b, act)
        if self._pending.dropped:   # 佇列過量丟咗最舊 → 唔靜默吃咗
            self._emit_event('warn', 'qt_cleared_fmt', args={'n': self._pending.dropped})
            self._pending.dropped = 0
        self._emit_state()

    # ── 落單 ──
    async def _send_orders(self, orders):
        """逐張送出 → (成功張數, 失敗原因[], 回執摘要)。
           ⚠️ 成功 = 已送出俾券商，唔係已成交（`trade_base` 契約）→ 用字必須如實。
           價一律用訊號根收盤：同回測成交價口徑一致，而且兩家默認都係限價單。"""
        sent, fails, receipts = 0, [], []
        for o in orders:
            try:
                client = await self.ensure_client()
                ok, receipt, message = await client.place_order(
                    code=o['code'], side=o['side'], qty=o['qty'], price=o['price'],
                    account=self._account or None, env=self._env, broker=self._broker or None)
            except Exception as e:
                logging.exception('quant: place_order 例外')
                ok, receipt, message = False, None, f'{type(e).__name__}: {e}'
            if ok:
                sent += 1
                rid = (receipt or {}).get('order_id') or ''
                st_ = (receipt or {}).get('status') or ''
                receipts.append(' '.join(x for x in (f"{o['side']} {o['qty']} @ {o['price']}",
                                                     f'order_id={rid}' if rid else '',
                                                     f'status={st_}' if st_ else '') if x))
            else:
                fails.append(str(message) or '')
        return sent, fails, receipts

    async def _place(self, b, act):
        """全自動：落單 → 全部成功先 `commit()`；任何一張失敗 → `requeue()` 還原持倉狀態。
           （狀態同實際落單脫節係最危險嘅錯：會以為持緊倉而其實冇落單。）"""
        sent, fails, receipts = await self._send_orders(act['orders'])
        if not fails:
            self._confirm_action(b, act, receipts)
            return
        if not qe.requeue(b, act):
            self._emit_event('warn', 'qt_stale', self._sig_desc(act['sig']))
        if sent:
            self._emit_event('warn', 'qt_partial_fmt', args={'n': sent})
        self._emit_event('err', 'qt_err_place',
                         '; '.join(x for x in (self._desc(b), self._sig_desc(act['sig']),
                                               '; '.join(f for f in fails if f)) if x))

    def _confirm_action(self, b, act, receipts):
        if not qe.commit(b, act, guard=self._guard):
            self._emit_event('warn', 'qt_stale', self._sig_desc(act['sig']))
        self._today += len(act['orders'])
        st = self._stat(b['binding_id'])
        if st is not None:
            st['acted'] += 1
            st['last'] = {'kind': 'act', 'action': act['action'], 'time': act['sig'].get('time') or '',
                          'price': act['sig'].get('price'),
                          'sides': [o['side'] for o in act['orders']],
                          'qty': (act['orders'][0].get('qty') if act['orders'] else 0)}
        self._emit_event('info', 'qt_ok_sent',
                         ' ｜ '.join(x for x in (self._desc(b), self._sig_desc(act['sig']),
                                                 ' ｜ '.join(receipts)) if x))

    async def _confirm(self, key):
        """半自動：人確認 → 先落單、成功先推進持倉狀態（`commit`）。失敗留返喺待執行俾人重試。"""
        act = self._pending.take(key)
        if act is None:
            self._emit_event('warn', 'qt_pick_row')
            self._emit_state()
            return
        b = self._bindings.get(act['binding_id'])
        if b is None:
            self._emit_event('err', 'qt_err_no_entry', str(key))
            self._emit_state()
            return
        sent, fails, receipts = await self._send_orders(act['orders'])
        if not fails:
            self._confirm_action(b, act, receipts)
        else:
            self._pending.add(act)   # 留返俾人重試（唔靜默丟人地揀咗嘅嘢）
            if sent:
                self._emit_event('warn', 'qt_partial_fmt', args={'n': sent})
            self._emit_event('err', 'qt_err_place',
                             '; '.join(x for x in (self._desc(b), self._sig_desc(act['sig']),
                                                   '; '.join(f for f in fails if f)) if x))
        self._emit_state()

    async def _reject(self, key):
        act = self._pending.reject(key)
        if act is None:
            self._emit_event('warn', 'qt_pick_row')
        else:
            self._emit_event('info', 'qt_rejected', self._sig_desc(act['sig']))
        self._emit_state()

    async def _clear_pending(self):
        self._emit_event('info', 'qt_cleared_fmt', args={'n': self._pending.clear()})
        self._emit_state()

    # ── 券商帳戶真相（持倉／訂單／帳戶）──
    async def _refresh_accounts(self):
        try:
            client = await self.ensure_client()
            ok, rows, message = await client.trade_accounts()
        except Exception as e:
            logging.exception('quant: trade_accounts 失敗')
            ok, rows, message = False, None, f'{type(e).__name__}: {e}'
        self.trade_data.emit({'kind': 'accounts', 'rows': rows or [], 'error': '',
                              'detail': str(message or '') if not ok else ''})

    async def _refresh_positions(self):
        await self._refresh_trade('positions', 'positions')

    async def _refresh_orders(self):
        await self._refresh_trade('open_orders', 'orders')

    async def _refresh_trade(self, api, kind):
        try:
            client = await self.ensure_client()
            ok, rows, message = await getattr(client, api)(account=self._account or None,
                                                           env=self._env)
        except Exception as e:
            logging.exception(f'quant: {api} 失敗')
            ok, rows, message = False, None, f'{type(e).__name__}: {e}'
        self.trade_data.emit({'kind': kind, 'rows': rows or [], 'error': '',
                              'detail': str(message or '') if not ok else ''})


def _new_stats():
    return {'state': 'stopped', 'sig': 0, 'acted': 0, 'blocked': 0, 'ignored': 0, 'last': None}


class _LoopThread(LoopThreadBase):
    worker_cls = QuantWorker


class _TableModel(QAbstractTableModel):
    """通用表 model：欄 spec 決定欄頭/格式/排序/對齊/紅綠 → 加欄改 spec 就得。"""

    def __init__(self, page, cols):
        super().__init__()
        self._page = page
        self._cols = tuple(cols)
        self._rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = list(rows or [])
        self.endResetModel()

    def rows(self):
        return self._rows

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def columnCount(self, parent=None):
        return len(self._cols)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        key, _head, fmt = self._cols[index.column()]
        row = self._rows[index.row()]
        if role in (Qt.DisplayRole, Qt.EditRole):
            return self._page._cell(row, key, fmt)
        if role == Qt.TextAlignmentRole and fmt in _RIGHT_FMTS:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole and fmt == 'pl':
            c = _sign_color(row.get(key))
            return QColor(c) if c else None
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return t(self._cols[section][1], self._page._lang)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        key, _head, fmt = self._cols[column]
        self._rows.sort(key=lambda r: _sort_key(r.get(key), fmt),
                        reverse=(order == Qt.DescendingOrder))
        self.layoutChanged.emit()


class QuantPage(QWidget):
    def __init__(self, client_factory=None):
        super().__init__()
        apply_ui(self, 'quant_page')   # 排版（兩行輸入／綁定表／風控卡／待執行／三個 tab）全部喺 `.ui`
        stamp(self, _STAMP)            # og / WA_StyledBackground：Designer 帶唔住 dynamic property
        self._lang = DEFAULT_LANG
        self._client_factory = client_factory
        self._thread = None
        self._worker = None
        self._directory = None         # lazy：標的 index（含 futu import）唔拖慢開頁
        self._search_fn = None
        self._state = None             # 最近一次 worker snapshot
        self._watch_shown = (False, 0)  # 狀態行只喺 (監控中?, 綁定數) 變嗰陣先改（唔食 input guard）

        self._restore_state()
        self._setup_inputs()
        self._setup_tables()
        self._connect_signals()

        self._retranslate_widgets()
        theme_mod.add_listener(self._on_theme_changed)
        self._apply_theme_qss(theme_mod.CURRENT)
        self._set_status(t('qt_status_idle', self._lang))
        self._start_thread()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

    # ── 本地記憶（state_store section `quant`）。⚠️ 只還原設定／綁定，唔會自動開始監控 ──
    def _restore_state(self):
        s = state_store.load_section(qe.SECTION, {}) or {}
        self._symbol_raw = str(s.get('symbol', '') or '')
        self._strategy_id = str(s.get('strategy', '') or '')
        self._broker_key = str(s.get('broker', '') or '').upper()
        ktype = str(s.get('ktype', '') or '')
        self._ktype = ktype if ktype in KTYPES else 'K_DAY'
        self._mode = pm.clamp_mode(s.get('mode'))
        self._qty = qe.clamp_qty(s.get('qty'))
        self._env = tb.clamp_env(s.get('env'))
        self._account = str(s.get('account', '') or '')
        self._auto_mode = bool(s.get('auto', True))
        risk = qe.norm_risk(s.get('risk'))
        self._risk = risk
        raw = s.get('bindings') or []
        self._bindings_cfg = [{'code': str(r.get('code') or ''), 'ktype': str(r.get('ktype') or ''),
                               'strategy': str(r.get('strategy') or ''),
                               'mode': pm.clamp_mode(r.get('mode'))}
                              for r in raw if isinstance(r, dict)]

    def _save_state(self):
        state_store.save_section(qe.SECTION, {
            'symbol': self.qnt_symbol.text().strip(),
            'strategy': self.qnt_strategy.currentData() or '',
            'broker': self.qnt_broker.currentData() or '',
            'ktype': self.qnt_ktype.currentText(),
            'mode': self.qnt_mode.currentData() or pm.MODE_DEFAULT,
            'qty': self.qnt_qty.value(),
            'env': self.qnt_env.currentData() or tb.TRADE_ENV_DEFAULT,
            'account': self.qnt_account.currentData() or '',
            'auto': self.qnt_mode_auto.isChecked(),
            'risk': self._risk_values(),
            'bindings': self._bindings_out(),
        })

    def _bindings_out(self):
        if self._state is None:   # worker 未推過 snapshot → 用返啟動還原嘅清單，唔好靜默清走綁定
            return self._bindings_cfg
        return [{'code': r['code'], 'ktype': r['ktype'], 'strategy': r['strategy_id'],
                 'mode': r['mode']} for r in self._state.get('rows') or []]

    # ── 輸入欄（選項內容屬資料 → 一律由 registry / domain 填）──
    def _setup_inputs(self):
        self.qnt_symbol.setText(self._symbol_raw)
        attach_symbol_input(self.qnt_symbol, self._symbol_search, lang=self._lang,
                            on_activate=lambda _code: self._on_add())

        self.qnt_broker.addItem(t('bt_broker_default', self._lang), '')   # 空 = broker=None → 跟設定
        for name in BROKER_REGISTRY:
            self.qnt_broker.addItem(name.upper(), name.upper())
        self.qnt_broker.setCurrentIndex(max(0, self.qnt_broker.findData(self._broker_key)))

        for env in tb.TRADE_ENVS:   # 環境 enum 屬契約（唔喺呢度或 `.ui` 寫死）
            self.qnt_env.addItem(t(_ENV_KEYS[env], self._lang), env)
        self.qnt_env.setCurrentIndex(max(0, self.qnt_env.findData(self._env)))

        for k in KTYPES:
            self.qnt_ktype.addItem(k)   # 週期係 futu enum 字串（語言中立，同行情頁同一份）
        self.qnt_ktype.setCurrentIndex(max(0, KTYPES.index(self._ktype)))

        for m in pm.MODES:   # 持倉模式 = 同回測頁共用嘅 domain enum（用戶：兩邊都要可揀）
            self.qnt_mode.addItem(t(f'mode_{m}', self._lang), m)
        self.qnt_mode.setCurrentIndex(max(0, self.qnt_mode.findData(self._mode)))

        # spin 範圍屬業務口徑 → 一律跟 quant_exec（`.ui` 得返空 spin）
        # setRange 必須喺 setValue 之前：setRange 會即刻 clamp 現有值，順序倒轉就攞錯值
        for spin, lo, hi, val in ((self.qnt_qty, qe.QTY_LO, qe.QTY_HI, self._qty),
                                  (self.qnt_risk_pos, qe.POS_LO, qe.POS_HI,
                                   self._risk['max_positions']),
                                  (self.qnt_risk_trades, qe.TRADES_LO, qe.TRADES_HI,
                                   self._risk['max_trades_day']),
                                  (self.qnt_risk_cool, qe.COOL_LO, qe.COOL_HI,
                                   self._risk['cooldown_bars'])):
            spin.setRange(lo, hi)
            spin.setValue(val)

        self._rebuild_strategy_combo()

    def _rebuild_strategy_combo(self):
        self.qnt_strategy.blockSignals(True)
        self.qnt_strategy.clear()
        for e in strategies.get_manager().items():
            self.qnt_strategy.addItem(e['name'], e['id'])
        self.qnt_strategy.setCurrentIndex(max(0, self.qnt_strategy.findData(self._strategy_id)))
        self._strategy_id = self.qnt_strategy.currentData() or ''
        self.qnt_strategy.blockSignals(False)

    def _risk_values(self):
        return {'max_positions': self.qnt_risk_pos.value(),
                'max_trades_day': self.qnt_risk_trades.value(),
                'cooldown_bars': self.qnt_risk_cool.value()}

    def _symbol_search(self, q):
        """模糊搜尋來源：第一次真搜索先至起 index（`get_directory()` 係 singleton，唔會重複 load）。"""
        if self._search_fn is None:
            self._search_fn = make_search(self._get_directory())
        return self._search_fn(q)

    def _get_directory(self):
        if self._directory is None:
            import modules.symbol_search as ss
            self._directory = ss.get_directory()
        return self._directory

    def _canonical_code(self, raw):
        """index canonical（同行情/回測/交易頁同一把尺）；撳唔到 → upper()，交俾 broker 如實答。"""
        try:
            e = self._get_directory().get(raw)
            if e:
                return e['code']
        except Exception:
            pass
        return raw.upper()

    # ── 四個表（控件喺 `.ui`，呢度只配 model 同欄行為）──
    def _setup_tables(self):
        self.bind_model = _TableModel(self, BIND_COLS)
        self.pending_model = _TableModel(self, PENDING_COLS)
        self.pos_model = _TableModel(self, POS_COLS)
        self.order_model = _TableModel(self, ORDER_COLS)
        for table, model, widths in ((self.qnt_bind_table, self.bind_model, _BIND_WIDTHS),
                                     (self.qnt_pending_table, self.pending_model, _PENDING_WIDTHS),
                                     (self.qnt_pos_table, self.pos_model, None),
                                     (self.qnt_order_table, self.order_model, None)):
            table.setModel(model)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSortingEnabled(True)
            table.verticalHeader().setVisible(False)
            hdr = table.horizontalHeader()
            hdr.setSectionResizeMode(QHeaderView.Interactive)
            hdr.setStretchLastSection(True)
            if widths:
                for col, w in widths.items():
                    table.setColumnWidth(col, w)

    def _cell(self, row, key, fmt):
        v = row.get(key)
        if fmt == 'text':
            return str(v) if v not in (None, '') else DASH
        if fmt == 'mode':
            return t(f'mode_{pm.clamp_mode(v)}', self._lang)
        if fmt == 'state':
            return t(f'qt_st_{v}', self._lang)
        if fmt == 'side':
            return t('bt_side_b' if v == pm.SIDE_B else 'bt_side_s', self._lang)
        if fmt == 'action':
            n = int(row.get('n_orders') or 1)
            s = t(f'qt_act_{v}', self._lang)
            return f'{s} · {t("qt_n_orders_fmt", self._lang).format(n=n)}' if n > 1 else s
        if fmt in ('reason', 'would'):
            return t(v, self._lang) if v else DASH
        if fmt == 'last':
            return self._last_text(v)
        if fmt in _RIGHT_FMTS:
            return _num(v, fmt) or DASH
        return DASH

    def _last_text(self, last):
        """最近動作：成功行動 → 動作 + 方向/數量/價；被擋/被忽略 → 原因（一律如實，唔只寫「有動作」）。"""
        if not isinstance(last, dict):
            return DASH
        if last.get('kind') == 'act':
            sides = '/'.join(str(s) for s in (last.get('sides') or []))
            parts = [t(f'qt_act_{last.get("action")}', self._lang),
                     ' '.join(x for x in (sides, f"x{last.get('qty')}" if last.get('qty') else '',
                                          f"@ {last.get('price')}") if x)]
            return ' '.join(p for p in parts if p)
        key = last.get('key') or ''
        return t(key, self._lang) if key else DASH

    # ── 接駁 ──
    def _connect_signals(self):
        self.qnt_broker.activated.connect(lambda _i: (self._on_broker(), self._save_state()))
        self.qnt_env.activated.connect(lambda _i: (self._on_env(), self._save_state()))
        self.qnt_ktype.activated.connect(lambda _i: self._save_state())
        self.qnt_mode.activated.connect(lambda _i: self._save_state())
        self.qnt_account.activated.connect(lambda _i: (self._on_account(), self._save_state()))
        self.qnt_strategy.activated.connect(lambda _i: self._on_strategy_choice())
        for spin in (self.qnt_qty, self.qnt_risk_pos, self.qnt_risk_trades, self.qnt_risk_cool):
            spin.valueChanged.connect(lambda _v: self._on_settings_changed())
        self.qnt_symbol.editingFinished.connect(self._save_state)
        self.qnt_add_btn.clicked.connect(self._on_add)
        self.qnt_remove_btn.clicked.connect(self._on_remove)
        self.qnt_watch_btn.clicked.connect(self._on_watch_clicked)
        self.qnt_acc_refresh.clicked.connect(self._on_refresh_accounts)
        self.qnt_refresh_pos.clicked.connect(self._on_refresh_positions)
        self.qnt_refresh_orders.clicked.connect(self._on_refresh_orders)
        self.qnt_confirm_btn.clicked.connect(self._on_confirm)
        self.qnt_reject_btn.clicked.connect(self._on_reject)
        self.qnt_clear_pending.clicked.connect(self._on_clear_pending)
        self.qnt_log_clear.clicked.connect(self.qnt_event_log.clear)
        self.qnt_mode_auto.clicked.connect(lambda _c: self._worker and self._worker.set_auto(True))
        self.qnt_mode_semi.clicked.connect(lambda _c: self._worker and self._worker.set_auto(False))
        strategies.get_manager().add_listener(self._on_strat_config)

    def _start_thread(self):
        self._thread = _LoopThread(
            client_factory=lambda: self._client_factory() if self._client_factory else None)
        self._thread.worker_ready.connect(self._on_worker_ready)
        self._thread.start()

    def _on_worker_ready(self, worker):
        self._worker = worker
        worker.state.connect(self._on_state)
        worker.event.connect(self._on_event)
        worker.trade_data.connect(self._on_trade_data)
        # 還原本地記憶：先推設定，再重建綁定。**唔會**自動開始監控 — 真錢自動落單要人明確撳一下。
        self._worker.set_broker(self._broker_key)
        self._worker.set_env(self._env)
        self._worker.set_account(self._account)
        self._worker.set_qty(self.qnt_qty.value())
        self._worker.set_risk(self._risk_values())
        self._worker.set_auto(self._auto_mode)
        for cfg in self._bindings_cfg:
            self._worker.add_binding(cfg['code'], cfg['ktype'], cfg['strategy'], cfg['mode'])

    def _on_settings_changed(self):
        if self._worker is not None:
            self._worker.set_qty(self.qnt_qty.value())
            self._worker.set_risk(self._risk_values())
        self._save_state()

    def _on_broker(self):
        """改券商 → 帳戶清單一定唔再適用 → 如實清走（唔留返另一家券商嘅帳戶）。"""
        if self._worker is not None:
            self._worker.set_broker(self.qnt_broker.currentData() or '')
        self._fill_accounts([])

    def _on_env(self):
        """改環境（實盤⇄模擬）→ 帳戶屬個別環境 → 照券商一樣如實清走，唔留返落錯環境嘅帳戶。"""
        if self._worker is not None:
            self._worker.set_env(self.qnt_env.currentData() or tb.TRADE_ENV_DEFAULT)
            self._worker.set_account('')
        self._fill_accounts([])

    def _on_account(self):
        """揀咗帳戶一定要推到 worker — 否則落單永遠 `account=None`（錯帳戶 = 錯錢）。"""
        if self._worker is not None:
            self._worker.set_account(self.qnt_account.currentData() or '')

    def _on_strategy_choice(self):
        self._strategy_id = self.qnt_strategy.currentData() or ''
        self._save_state()

    def _on_strat_config(self, origin, kind):
        """策略變（任何 origin，包括策略頁）→ 下拉即時跟；已綁定嘅執行個體喺下一根行情自動用新參數
           （`_on_df` 每次都重新 `get()`）→ 唔使人重新綁定。"""
        self._rebuild_strategy_combo()

    # ── 使用者動作 ──
    def _guard_worker(self):
        if self._worker is None:
            self._set_status(t('qt_err_worker', self._lang))
            return False
        return True

    def _on_add(self):
        if not self._guard_worker():
            return
        raw = self.qnt_symbol.text().strip()
        if not raw:
            self._set_status(t('qt_err_no_symbol', self._lang))
            return
        sid = self.qnt_strategy.currentData() or ''
        if not sid:
            self._set_status(t('qt_err_no_strategy', self._lang))
            return
        if self.qnt_qty.value() <= 0:
            self._set_status(t('qt_err_no_qty', self._lang))
            return
        self._save_state()
        self._worker.add_binding(self._canonical_code(raw), self.qnt_ktype.currentText(), sid,
                                self.qnt_mode.currentData() or pm.MODE_DEFAULT)

    def _selected_rows(self, table, model):
        rows, all_rows = [], model.rows()
        for idx in table.selectionModel().selectedRows():
            if 0 <= idx.row() < len(all_rows):
                rows.append(all_rows[idx.row()])
        return rows

    def _on_remove(self):
        if not self._guard_worker():
            return
        ids = [r['binding_id'] for r in self._selected_rows(self.qnt_bind_table, self.bind_model)]
        if not ids:
            return
        self._worker.remove_bindings(ids)

    def _on_watch_clicked(self):
        if not self._guard_worker():
            self.qnt_watch_btn.blockSignals(True)
            self.qnt_watch_btn.setChecked(False)
            self.qnt_watch_btn.setText(t('qt_watch_start', self._lang))
            self.qnt_watch_btn.blockSignals(False)
            return
        if self.qnt_watch_btn.isChecked():
            self._worker.start_watch()
        else:
            self._worker.stop_watch()

    def _on_refresh_accounts(self):
        if self._guard_worker():
            self._worker.refresh_accounts()

    def _on_refresh_positions(self):
        if not self._guard_worker():
            return
        if not self.qnt_account.currentData():
            self._set_status(t('qt_need_account', self._lang))
            return
        self._worker.refresh_positions()

    def _on_refresh_orders(self):
        if not self._guard_worker():
            return
        if not self.qnt_account.currentData():
            self._set_status(t('qt_need_account', self._lang))
            return
        self._worker.refresh_orders()

    def _pending_key_clicked(self):
        rows = self._selected_rows(self.qnt_pending_table, self.pending_model)
        if not rows:
            self._set_status(t('qt_pick_row', self._lang))
            return None
        return rows[0].get('key')

    def _on_confirm(self):
        if not self._guard_worker():
            return
        key = self._pending_key_clicked()
        if key is not None:
            self._worker.confirm(key)

    def _on_reject(self):
        if not self._guard_worker():
            return
        key = self._pending_key_clicked()
        if key is not None:
            self._worker.reject(key)

    def _on_clear_pending(self):
        if self._guard_worker():
            self._worker.clear_pending()

    # ── 渲染 snapshot ──
    def _on_state(self, snap):
        self._state = snap
        self.bind_model.set_rows(snap.get('rows') or [])
        self.pending_model.set_rows(snap.get('pending') or [])
        self.qnt_today_val.setText(_num(snap.get('today'), 'int'))
        self.qnt_open_val.setText(_num(snap.get('open_dirs'), 'int'))
        self._sync_watch(bool(snap.get('watching')), len(snap.get('rows') or []))
        self._sync_mode(bool(snap.get('auto')))
        self._sync_pending_hint(len(snap.get('pending') or []))

    def _sync_watch(self, watching, n):
        self.qnt_watch_btn.blockSignals(True)
        self.qnt_watch_btn.setChecked(watching)
        self.qnt_watch_btn.setText(t('qt_watch_stop' if watching else 'qt_watch_start', self._lang))
        self.qnt_watch_btn.blockSignals(False)
        if (watching, n) != self._watch_shown:   # 狀態行只喺呢度轉（input guard 唔會被 snapshot 蓋走）
            self._watch_shown = (watching, n)    # ⚠️ 綁定數都要跟：監控中加綁 → 如實講(N)，唔留低訂閱嗰陣嘅數
            if watching:
                self._set_status(t('qt_status_watching_fmt', self._lang).format(n=n))
            else:
                self._set_status(t('qt_status_idle', self._lang))

    def _sync_mode(self, auto):
        for btn, on in ((self.qnt_mode_auto, auto), (self.qnt_mode_semi, not auto)):
            btn.blockSignals(True)
            btn.setChecked(on)
            btn.blockSignals(False)

    def _sync_pending_hint(self, n):
        self.qnt_confirm_hint.setText(t('qt_confirm_hint' if n else 'qt_pending_empty', self._lang))

    # ── 事件日誌（worker 只發 key + 事實 → 用而家嘅語言翻譯；日誌行保留寫低當時嘅語言）──
    def _translate_event(self, ev):
        msg = t(ev.get('key') or 'qt_err_worker', self._lang)
        args = {}
        for k, v in (ev.get('args') or {}).items():
            args[k] = t(v[1:], self._lang) if isinstance(v, str) and v.startswith('@') else v
        try:
            msg = msg.format(**args)
        except (KeyError, IndexError, ValueError):
            pass   # 模板同 args 唔夾 → 保留原文，唔為咗格式化而炸或食字
        detail = str(ev.get('detail') or '')
        return f'{msg}｜{detail}' if detail else msg

    def _on_event(self, ev):
        msg = self._translate_event(ev)
        level = ev.get('level') or 'info'
        stamp_ = time.strftime('%H:%M:%S', time.localtime(ev.get('ts') or time.time()))
        self.qnt_event_log.appendPlainText(
            '[{}] [{}] {}'.format(stamp_, t(f'qt_lv_{level}', self._lang), msg))
        if level == 'err':   # 出錯一律照到狀態行（最緊要睇到），但一次性資訊一律入日誌
            self._set_status(msg)

    # ── 券商帳戶資料（帳戶清單／持倉／訂單 = 券商嘅真相，唔係本頁模擬）──
    def _on_trade_data(self, payload):
        kind = payload.get('kind')
        rows = payload.get('rows') or []
        detail = str(payload.get('detail') or '')
        if kind == 'accounts':
            self._fill_accounts(rows)
            if detail:
                self._set_status(detail)   # 券商嘅原文（例如「呢家券商唔支援交易」）→ 如實轉達
            return
        if kind == 'positions':
            self.pos_model.set_rows(rows)
            self.qnt_pos_note.setText(t('qt_pos_note', self._lang) +
                                     (' ' + t('qt_pos_empty', self._lang) if not rows and not detail
                                      else ''))
        elif kind == 'orders':
            self.order_model.set_rows(rows)
            self.qnt_orders_note.setText(t('qt_pos_note', self._lang) +
                                        (' ' + t('qt_orders_empty', self._lang) if not rows and not detail
                                         else ''))
        if detail:
            self._set_status(detail)

    def _fill_accounts(self, rows):
        keep = self.qnt_account.currentData() or self._account
        self.qnt_account.blockSignals(True)
        self.qnt_account.clear()
        for r in rows:
            acc = str(r.get('acc_id') or '')
            if not acc:
                continue
            self.qnt_account.addItem(' '.join(x for x in (acc, str(r.get('trd_env') or ''),
                                                         str(r.get('acc_status') or '')) if x), acc)
        if self.qnt_account.count() == 0:   # 冇帳戶都要如實講冇，唔留返空下拉令人以為仲揀緊
            self.qnt_account.addItem(t('qt_acc_none', self._lang), '')
        i = self.qnt_account.findData(keep)
        self.qnt_account.setCurrentIndex(i if i >= 0 else 0)
        self._account = self.qnt_account.currentData() or ''
        self.qnt_account.blockSignals(False)

    def _set_status(self, text):
        self.qnt_status.setText(text)

    # ── theme / i18n ──
    def _apply_theme_qss(self, name):
        pal = theme_mod.THEMES[name]
        self.setStyleSheet(string.Template(_PAGE_QSS).substitute(
            window=pal['window'], surface=pal['surface'], card=pal['card'],
            border=pal['border'], text=pal['text'], muted=pal['muted'],
            accent=pal['accent'], accent_pressed=pal['accent_pressed'],
            up=gk.C_UP, down=gk.C_DOWN))

    def _on_theme_changed(self, name):
        self._apply_theme_qss(name)

    def _retranslate_widgets(self):
        lang = self._lang
        apply_text(self, _TEXT, lang)
        for name, key in _PH.items():   # placeholder 唔屬 setText
            getattr(self, name).setPlaceholderText(t(key, lang))
        for i, (_tab, key) in enumerate(_TAB_KEYS):   # tab 標題唔係 widget → setTabText
            self.qnt_tabs.setTabText(i, t(key, lang))
        self.qnt_broker.setItemText(0, t('bt_broker_default', lang))   # 得返第一項屬文案
        for i, env in enumerate(tb.TRADE_ENVS):
            if self.qnt_env.itemText(i) and self.qnt_env.itemData(i) in _ENV_KEYS:
                self.qnt_env.setItemText(i, t(_ENV_KEYS[self.qnt_env.itemData(i)], lang))
        for i, m in enumerate(pm.MODES):
            if self.qnt_mode.itemData(i) in ('long', 'short', 'both'):
                self.qnt_mode.setItemText(i, t(f'mode_{m}', lang))
        if self._state is not None:
            self._sync_watch(bool(self._state.get('watching')),
                             len(self._state.get('rows') or []))
            self._sync_pending_hint(len(self._state.get('pending') or []))
        self._rebuild_strategy_combo()
        for model in (self.bind_model, self.pending_model, self.pos_model, self.order_model):
            model.headerDataChanged.emit(Qt.Horizontal, 0, model.columnCount() - 1)

    def retranslate(self, lang):
        self._lang = lang
        self._retranslate_widgets()
        if self._state is not None:   # 表內容有 i18n 欄（模式/狀態/動作）→ 強制重畫
            self.bind_model.beginResetModel()
            self.bind_model.endResetModel()
            self.pending_model.beginResetModel()
            self.pending_model.endResetModel()

    def _on_app_quit(self):
        self._save_state()
        if self._thread is not None:
            try:
                self._thread.request_shutdown()
                self._thread.wait(3000)
            except Exception:
                pass


_PAGE_QSS = """
QWidget#quant_page { background: ${window}; }
QLabel[og="qtlbl"] { color: ${muted}; }
QLabel[og="qtnote"] { color: ${muted}; }
QLabel[og="qtstatus"] { color: ${text}; font-weight: 600; }
QLabel[og="qtval"] { color: ${text}; font-weight: 600; }
QLineEdit, QComboBox, QSpinBox {
    background: ${surface}; color: ${text}; border: 1px solid ${border};
    border-radius: 4px; padding: 3px 6px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: ${accent}; }
QPushButton {
    background: ${surface}; color: ${text}; border: 1px solid ${border};
    border-radius: 4px; padding: 4px 10px;
}
QPushButton:hover { border-color: ${accent}; }
QPushButton[og="qtprimary"] { background: ${accent}; border-color: ${accent}; color: ${window}; }
QPushButton[og="qtprimary"]:hover { background: ${accent_pressed}; }
QPushButton[og="qtwatch"]:checked { background: ${accent}; border-color: ${accent}; color: ${window}; }
QPushButton[og="qtmode"]:checked { background: ${accent}; border-color: ${accent}; color: ${window}; }
QPlainTextEdit#qnt_event_log {
    background: ${surface}; color: ${text}; border: 1px solid ${border};
}
QTabWidget::pane { border: 1px solid ${border}; background: ${surface}; }
QTabBar::tab {
    background: ${surface}; color: ${muted}; border: 1px solid ${border};
    padding: 5px 12px;
}
QTabBar::tab:selected { color: ${text}; border-bottom: 2px solid ${accent}; }
QGroupBox {
    border: 1px solid ${border}; border-radius: 6px; margin-top: 14px;
    background: ${surface};
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: ${muted}; }
QTableView {
    background: ${surface}; color: ${text}; border: 1px solid ${border};
    gridline-color: ${border};
}
QHeaderView::section {
    background: ${card}; color: ${muted}; border: none; padding: 4px;
}
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(QuantPage, 'page_quant_title')
