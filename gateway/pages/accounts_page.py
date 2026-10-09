# -*- coding: utf-8 -*-
"""One Gate Page — 交易帳戶：跨券商帳戶清單、類型與資金明細（ticket #36）。

用戶要求（全部已落碼）：
- 「FUTU 和 IB 的所有帳號列表，類型資金明細」→ 逐券商取得、合併，每帳戶一張卡片。
- 「按 BROKER 數量加不同欄位，水平排列」→ 每個券商一個直欄，由 `.ui` 按
  `modules/registry.py` 的 `BROKERS` 宣告（缺 slot 即 RuntimeError，不靜默少一欄）。
- 「實盤用紅色主色，模擬帳號用藍色主色」→ 卡片 `env` property + 頁級 QSS。
- 「每個帳號可以有別名設置，以後全域可以用別名顯示來選擇的帳戶」→ 別名即時儲存，
  顯示名由 `gateway/accounts.py` 生成（生成名永不入檔，故切換語言仍正確）。
- 「富途將 STATUS 不是 ACTIVE 帳號的屏蔽」→ 規則只在 `accounts.active_only()` 一处，
  被屏蔽的數量如實回報（`acc_status == 'N/A'` 屬「未知」，不是「已關閉」）。
- 「每個帳號資金等每 10 秒更新一次，增加手動刷新按鍵」→ `FUNDS_REFRESH_MS` QTimer，
  顯示時啟動、隱藏時停止；10 秒週期只取資金，帳戶清單於開頁與手動刷新時取得。

架構（與量化交易頁同一形態：共用領域層，各頁保留自己的執行緒與版面）：
- 規則全部在 `gateway/accounts.py`（純計算、無 Qt）。本頁只做排版與排程。
- 取得動作住在 `AccountsWorker`（worker thread）；GUI 只發請求、收結果。
- **token**：每次請求帶一個遞增 token，GUI 丟棄 token 不是当前的 payload。這是必要項：
  慢的清單回應若在手動刷新之後落地，會把使用者剛移除的帳戶卡片加回來。
- **重入保護**：worker 側 `_listing` / `_funding` 令同一時刻只有一輪在飛；被拒的請求
  仍會回一個 `skipped` 標記，所以按鈕不會卡在「刷新中」。
- **如實優先**：資金缺失顯示 `—` 不顯示 0；單帳戶資金失敗時保留上次好值並標為過期
  （附券商原文與「最後成功：N 秒前」）；單券商失敗只影響該欄；已宣告的欄永不隱藏。
- **誠實的邊界**：IB 不提供帳戶類型/狀態欄位（以「不適用」呈現），REAL/SIMULATE 由編號
  推斷（標明推斷、與上報的 AccountType 矛盾時標矛盾）；資金幣種逐卡標明，不作跨券商合計。

單獨運行：`python gateway/pages/accounts_page.py`。
"""
import asyncio
import logging
import os
import re
import string
import sys
import time

# ── standalone bootstrap（同其他頁同一 convention；package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QObject, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import (QFrame, QGridLayout,  # noqa: E402
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

import gateway.accounts as acc  # noqa: E402 — 帳戶規則單一來源（三頁共用）
import gateway.theme as theme_mod  # noqa: E402
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.kline_stream import ClientHolderMixin, LoopThreadBase  # noqa: E402
from gateway.ui.bind import apply_text, set_prop, stamp  # noqa: E402
from gateway.ui.loader import apply_ui  # noqa: E402
from modules.trade_base import ACCINFO_KEYS, clamp_env  # noqa: E402
from modules.registry import BROKERS as BROKER_REGISTRY  # noqa: E402 — 券商名單一來源

FUNDS_REFRESH_MS = 10_000   # 用戶明確要求「每 10 秒更新一次」（E2E 斷言此常數）
C_REAL = theme_mod.C_REAL   # 實盤主色（定義在 theme：與富途交易頁共用同一份，不再各寫一處）
C_SIM = theme_mod.C_SIM     # 模擬主色

# 卡片欄位 spec = (row key, 標籤 i18n key) — 欄位順序與標籤一處定義
_META_FIELDS = (('broker', 'ta_field_broker'), ('trd_env', 'ta_field_env'),
                ('acc_type', 'ta_field_type'), ('trdmarket_auth', 'ta_field_markets'),
                ('acc_status', 'ta_field_status'))
_SAFE = re.compile(r'[^A-Za-z0-9_]')


def _safe(key):
    """acc_key → 可作 objectName 的形狀（`futu:4654894524` → `futu_4654894524`）。"""
    return _SAFE.sub('_', str(key))


# `.ui` 內靜態 widget 的 QSS property（objectName == 頁面屬性名，見 gateway/ui/bind.py）。
# 每券商的欄錯誤標籤屬資料 → 按 registry 生成，與 `.ui` 的 ta_col_<broker> 同步宣告。
_STAMP = {
    'accounts_page': {},          # 純 QWidget root → 補 WA_StyledBackground，頁級 QSS 才生效
    'ta_header_card': {'og': 'pagecard'},
    'ta_title_lbl': {'role': 'pagetitle'},
    'accounts_page_note': {'role': 'pagebody'},
    'ta_usage': {'role': 'usagehint'},
    'ta_updated_lbl': {'og': 'taupd'},
    'ta_auto_lbl': {'og': 'taupd'},
    'ta_refresh_btn': {'og': 'tarefresh'},
}
for _name in BROKER_REGISTRY:
    _STAMP[f'ta_col_{_name}_err'] = {'og': 'taerr'}

# 純 i18n 文字。狀態驅動的（ta_updated_lbl / ta_auto_lbl / 欄錯誤 / 按鈕文字）屬狀態
# → 由各自的 render 如實生成，不入這張表。
_TEXT = {'ta_title_lbl': 'page_accounts_title', 'accounts_page_note': 'accounts_page_note',
         'ta_usage': 'ta_usage'}


class AccountsWorker(QObject, ClientHolderMixin):
    """住在 worker thread：帳戶清單與資金取得都在這裡，GUI 只發請求、收結果。

    payload 一律語言中立（事實欄位 + i18n key），由頁面以「現在」的語言渲染 →
    切換語言不會令已取得的資料變錯。
    """

    accounts = Signal(object)   # {'token','rows','errors','filtered'} | {'token','failed','error'} | {'token','skipped'}
    funds = Signal(object)      # {'token','acc_key','info','error','error_key','ts'} | {'token','done','skipped'?}

    def __init__(self, loop, client_factory=None):
        super().__init__()
        self.init_client_holder(loop, client_factory)
        self._rows = []
        self._listing = False
        self._funding = False

    # ── GUI-thread facade（非阻塞；loop 已停就如實回報，不靜默吞指令）──
    def refresh_all(self, token):
        self._send(self._do_all, token)

    def refresh_funds(self, token):
        self._send(self._do_funds, token)

    def _send(self, fn, *a):
        try:
            asyncio.run_coroutine_threadsafe(self._guarded(fn, a), self._loop)
        except RuntimeError as e:      # loop 剛好停了（收工中）
            logging.warning('accounts_page: worker 已停止，指令未執行：%s', e)

    async def _guarded(self, fn, a):
        """所有 loop-thread 指令經這裡：未處理的例外只會留下一條 asyncio warning，
           使用者永遠看不到出事 → 一律如實發一個 failed payload。"""
        try:
            await fn(*a)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.exception('accounts: 取得失敗')
            self.accounts.emit({'token': a[0], 'failed': True, 'error': f'{type(e).__name__}: {e}'})

    async def _cancel_work(self):
        """ClientHolderMixin.shutdown 要求：本 worker 沒有長期訂閱 task，取得都是短呼叫。"""
        return

    # ── 取得 ──
    async def _do_all(self, token):
        if self._listing:
            self.accounts.emit({'token': token, 'skipped': True})   # 仍回標記 → 按鈕不會卡住
            return
        self._listing = True
        try:
            client = await self.ensure_client()
            payload = await acc.collect_accounts(client, list(BROKER_REGISTRY))
            self._rows = payload['rows']
            payload['token'] = token
            self.accounts.emit(payload)
        finally:
            self._listing = False
        await self._emit_funds(token, self._rows)

    async def _do_funds(self, token):
        await self._emit_funds(token, self._rows)

    async def _emit_funds(self, token, rows):
        """逐帳戶循序取資金（`accounts.refresh_funds` 的契約），逐筆 emit 讓卡片逐步填好。

        ⚠️ 每個 token 都必須收到一個 `done`：GUI 靠它 settle 按鈕。被重入擋掉的請求也要回，
           否則「刷新中…」會永遠留在畫面上。
        """
        if self._funding:
            self.funds.emit({'token': token, 'done': True, 'skipped': True})
            return
        if not rows:
            self.funds.emit({'token': token, 'done': True})
            return
        self._funding = True
        try:
            client = await self.ensure_client()
            async for item in acc.refresh_funds(client, rows):
                item['token'] = token
                self.funds.emit(item)
        finally:
            self._funding = False
            self.funds.emit({'token': token, 'done': True})


class _LoopThread(LoopThreadBase):
    worker_cls = AccountsWorker


class AccountsPage(QWidget):
    def __init__(self, client_factory=None):
        super().__init__()
        apply_ui(self, 'accounts_page')   # 排版（header / 兩欄 / 卡片槽）全部在 `.ui`
        stamp(self, _STAMP)
        self._lang = DEFAULT_LANG
        self._client_factory = client_factory
        self._thread = None
        self._worker = None

        self._token = 0                # 每次請求遞增；遲到的 payload 一律作廢
        self._inflight = False         # 只驅動按鈕狀態（真正的重入保護在 worker）
        self._loaded = False
        self._pending_first = False    # showEvent 早於 worker 就緒時的待補發標記
        self._rows = []
        self._col_errors = {}          # 最近一輪的逐券商原文（切換語言時要重新解析）
        self._col_filtered = {}        # 最近一輪的逐券商屏蔽數量（同上）
        self._cols_synced = False      # 未取過任何一輪 → 不能顯示「沒有帳戶」（根本還沒問）
        self._cards = {}               # acc_key → 卡片控件（含各標籤的直接引用）
        self._funds = {}               # acc_key → {'info','error','error_key','good_ts'}
        self._aliases = {}
        self._last_update = 0.0
        self._fatal = ''               # 未取得任何資料時的原文（屬異常，不是券商訊息）

        self._timer = QTimer(self)     # 10 秒只取資金；顯示時啟動、隱藏時停止
        self._timer.setInterval(FUNDS_REFRESH_MS)
        self._timer.timeout.connect(self._on_tick)

        self._start_thread()
        self._connect_signals()
        self._retranslate_widgets()
        theme_mod.add_listener(self._on_theme_changed)
        self._apply_theme_qss(theme_mod.CURRENT)

    # ── 執行緒 ──
    def _start_thread(self):
        self._thread = _LoopThread(
            client_factory=lambda: self._client_factory() if self._client_factory else None)
        self._thread.worker_ready.connect(self._on_worker_ready)
        self._thread.start()

    def _on_worker_ready(self, worker):
        self._worker = worker
        worker.accounts.connect(self._on_accounts)
        worker.funds.connect(self._on_funds)
        if self._pending_first:        # 開頁早於 worker 就緒 → 補發，不能靜默無資料
            self._pending_first = False
            self._refresh(all_rows=True)

    # ── 排程 ──
    def showEvent(self, ev):
        super().showEvent(ev)
        if not self._loaded:           # 開頁即取一次清單 + 資金
            self._loaded = True
            self._refresh(all_rows=True)
        self._timer.start()

    def hideEvent(self, ev):
        self._timer.stop()             # 不在本頁就不再向券商發問（QStackedWidget 會觸發 hide）
        super().hideEvent(ev)

    def _on_tick(self):
        self._refresh(all_rows=False)

    def _on_refresh(self):
        self._refresh(all_rows=True)

    def _refresh(self, all_rows):
        if self._worker is None:
            # 執行緒未就緒（開頁即 show 必然發生）：記下待補發。靜默 return 會令整頁無資料。
            self._pending_first = self._pending_first or all_rows
            return
        self._token += 1
        self._inflight = True
        self._sync_refresh_btn()
        if all_rows:
            self._worker.refresh_all(self._token)
        else:
            self._worker.refresh_funds(self._token)

    # ── 結果落地（token 不符即作廢）──
    def _on_accounts(self, payload):
        if payload.get('token') != self._token:
            return
        if payload.get('skipped'):
            self._settle()
            return
        if payload.get('failed'):
            self._fatal = str(payload.get('error') or '')
            self._render_updated()
            self._settle()
            return
        self._fatal = ''
        self._rows = list(payload.get('rows') or [])
        self._last_update = time.time()
        self._aliases = acc.load_aliases()
        self._col_errors = payload.get('errors') or {}
        self._col_filtered = payload.get('filtered') or {}
        self._cols_synced = True
        self._sync_columns(self._col_errors, self._col_filtered)
        self._sync_cards(self._rows)
        self._render_updated()
        for key in self._cards:
            self._render_card(key)
        self._settle()

    def _on_funds(self, payload):
        if payload.get('token') != self._token:
            return                     # 上一輪的遲到結果不能蓋過本輪
        if payload.get('done'):
            self._settle()
            return
        key = payload.get('acc_key')
        if key not in self._cards:     # 帳戶已被移除 → 舊結果不留存
            return
        st = self._funds.setdefault(key, {'info': None, 'error': '', 'error_key': '', 'good_ts': 0.0})
        if payload.get('info') is not None:
            st['info'] = payload['info']
            st['error'] = ''
            st['error_key'] = ''
            st['good_ts'] = payload.get('ts') or time.time()
        else:
            st['error'] = str(payload.get('error') or '')
            st['error_key'] = str(payload.get('error_key') or '')
        self._render_card(key)

    def _settle(self):
        self._inflight = False
        self._sync_refresh_btn()

    # ── 欄：已宣告的欄永不隱藏，無結果/失敗就在該欄如實說明 ──
    def _sync_columns(self, errors, filtered):
        for name in BROKER_REGISTRY:
            lbl = getattr(self, f'ta_col_{name}_err', None)
            if lbl is None:
                continue
            n = int(filtered.get(name) or 0)
            filtered_note = t('ta_filtered_note', self._lang).format(n=n) if n else ''
            if name in errors:
                lbl.setText(str(errors[name] or t('ta_no_accounts', self._lang)))
                lbl.setVisible(True)
                continue
            mine = [r for r in self._rows if r.get('broker') == name]
            if mine:
                lbl.setText(filtered_note)
                lbl.setVisible(bool(filtered_note))
            else:
                lbl.setText(' '.join(x for x in (t('ta_no_accounts', self._lang), filtered_note) if x))
                lbl.setVisible(True)

    # ── 卡片：以 acc_key 做差異增刪（資金 tick 絕不重建 → 別名輸入框不會失焦）──
    def _grid(self, broker):
        grid = getattr(self, f'ta_grid_{broker}', None)
        if grid is None:      # `.ui` 改了 slot 名 → 如實炸出來，不要靜默少一欄
            raise RuntimeError(f'accounts_page: 找不到 ta_grid_{broker}（檢查 gateway/ui/accounts_page.ui）')
        return grid

    def _sync_cards(self, rows):
        want = {r['acc_key'] for r in rows}
        for key in list(self._cards):
            if key not in want:
                card = self._cards.pop(key)
                card['frame'].setParent(None)
                card['frame'].deleteLater()
                self._funds.pop(key, None)
        registry = {}
        for r in rows:
            if r['acc_key'] not in self._cards:
                registry.update(self._build_card(r))
        if registry:
            missing = stamp(self, registry)   # 運行期建立的控件照樣走同一套 QSS 契約
            if missing:
                logging.warning('accounts_page: stamp 找不到 %s', missing)

    def _build_card(self, row):
        """一張卡片 = 一個帳戶。回傳 runtime registry（objectName → QSS property）。"""
        key = row['acc_key']
        safe = _safe(key)
        env = clamp_env(row.get('trd_env'))          # 建立時一次，之後不變 → 不需 re-polish
        reg = {}
        frame = QFrame(self._grid(row['broker']).parentWidget())
        frame.setObjectName(f'ta_card_{safe}')
        frame.setProperty('env', env)
        reg[frame.objectName()] = {'og': 'accard'}
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        title = QLabel(frame)
        title.setObjectName(f'ta_cardtitle_{safe}')
        title.setProperty('env', env)
        title.setWordWrap(True)
        reg[title.objectName()] = {'og': 'acctitle'}
        v.addWidget(title)

        meta = QGridLayout()
        meta.setHorizontalSpacing(8)
        meta.setVerticalSpacing(2)
        fields = {}
        for i, (f, cap_key) in enumerate(_META_FIELDS):
            cap = QLabel(frame)
            cap.setObjectName(f'ta_fldlbl_{safe}_{f}')
            reg[cap.objectName()] = {'og': 'accfldlbl'}
            val = QLabel(frame)
            val.setObjectName(f'ta_fld_{safe}_{f}')
            val.setWordWrap(True)
            reg[val.objectName()] = {'og': 'accfldval'}
            meta.addWidget(cap, i, 0)
            meta.addWidget(val, i, 1)
            fields[f] = {'cap': cap, 'cap_key': cap_key, 'val': val}
        v.addLayout(meta)

        note = QLabel(frame)
        note.setObjectName(f'ta_cardnote_{safe}')
        note.setWordWrap(True)
        reg[note.objectName()] = {'og': 'accnote'}
        v.addWidget(note)

        hdr = QLabel(frame)
        hdr.setObjectName(f'ta_fundshdr_{safe}')
        reg[hdr.objectName()] = {'og': 'accfldlbl'}
        v.addWidget(hdr)

        fgrid = QGridLayout()
        fgrid.setHorizontalSpacing(8)
        fgrid.setVerticalSpacing(2)
        funds = {}
        for i, fk in enumerate(ACCINFO_KEYS):
            cap = QLabel(frame)
            cap.setObjectName(f'ta_fundlbl_{safe}_{fk}')
            reg[cap.objectName()] = {'og': 'accfundlbl'}
            val = QLabel(frame)
            val.setObjectName(f'ta_fundval_{safe}_{fk}')
            val.setProperty('state', 'ok')
            reg[val.objectName()] = {'og': 'accfundval'}
            fgrid.addWidget(cap, i, 0)
            fgrid.addWidget(val, i, 1)
            funds[fk] = {'cap': cap, 'cap_key': acc.FUND_LABEL_KEYS[fk], 'val': val}
        v.addLayout(fgrid)

        err = QLabel(frame)
        err.setObjectName(f'ta_carderr_{safe}')
        err.setWordWrap(True)
        reg[err.objectName()] = {'og': 'accmsg'}
        v.addWidget(err)

        msg = QLabel(frame)
        msg.setObjectName(f'ta_cardmsg_{safe}')
        msg.setWordWrap(True)
        reg[msg.objectName()] = {'og': 'accmsg'}
        v.addWidget(msg)

        row_edit = QHBoxLayout()
        row_edit.setSpacing(6)
        albl = QLabel(frame)
        albl.setObjectName(f'ta_aliaslbl_{safe}')
        reg[albl.objectName()] = {'og': 'accfldlbl'}
        edit = QLineEdit(frame)
        edit.setObjectName(f'ta_alias_{safe}')
        reg[edit.objectName()] = {'og': 'accalias'}
        reset = QPushButton(frame)
        reset.setObjectName(f'ta_aliasreset_{safe}')
        reg[reset.objectName()] = {'og': 'accaliasreset'}
        row_edit.addWidget(albl)
        row_edit.addWidget(edit)
        row_edit.addWidget(reset)
        v.addLayout(row_edit)

        grid = self._grid(row['broker'])
        grid.addWidget(frame, grid.rowCount(), 0)   # 依該欄已佔用的列數追加（移除過也不會重疊）
        frame.show()   # 頁面已顯示才建的控件不會被父控件的 show() 帶出來 → 不 show 就永遠看不見
        edit.textEdited.connect(lambda _t, k=key: self._on_alias_text(k))
        edit.editingFinished.connect(lambda k=key: self._commit_alias(k))
        reset.clicked.connect(lambda _c, k=key: self._reset_alias(k))
        self._cards[key] = {'frame': frame, 'row': row, 'title': title, 'fields': fields,
                            'note': note, 'hdr': hdr, 'funds': funds, 'err': err, 'msg': msg,
                            'albl': albl, 'edit': edit, 'reset': reset}
        self._sync_card_captions(self._cards[key])   # 新建卡必須即刻有標籤文字，不能等下次切換語言
        return reg

    def _sync_card_captions(self, card):
        """卡上所有標籤文字一律由 `cap_key` 即時解析（卡內不存已翻譯字串）。"""
        lang = self._lang
        for slot in list(card['fields'].values()) + list(card['funds'].values()):
            slot['cap'].setText(t(slot['cap_key'], lang))
        card['albl'].setText(t('ta_alias', lang))
        card['edit'].setPlaceholderText(t('ta_alias_ph', lang))
        card['reset'].setText(t('ta_alias_reset', lang))

    # ── 渲染（一律以當前語言解析，卡內不留已翻譯的快取）──
    def _render_card(self, key):
        card = self._cards.get(key)
        if card is None:
            return
        lang, row = self._lang, card['row']
        card['title'].setText(acc.display_name(row, lang, self._aliases))
        for field, slot in card['fields'].items():
            slot['val'].setText(self._meta_value(row, field, lang))
        notes = []
        if acc.env_inferred(row):
            notes.append(t('ta_env_inferred_note', lang))
        if acc.env_conflict(row):
            notes.append(t('ta_env_conflict', lang).format(type=str(row.get('acc_type') or '')))
        card['note'].setText(' '.join(notes))
        card['note'].setVisible(bool(notes))

        st = self._funds.get(key) or {}
        info = st.get('info')
        fund_rows, has_data = acc.fund_rows(info, st.get('error') or '')
        ccy = acc.currency_of(info)
        card['hdr'].setText('  '.join(x for x in (
            t('ta_funds_title', lang),
            t('ta_currency', lang).format(ccy=ccy) if ccy else t('ta_currency_unknown', lang)) if x))
        # 本輪失敗但仍留著上次的好值 → 數值必須標為過期，不能只靠文字說明
        state = 'ok' if (has_data and not st.get('error') and not st.get('error_key')) else 'stale'
        for fk, (_label_key, text) in zip(ACCINFO_KEYS, fund_rows):
            card['funds'][fk]['val'].setText(text)
            set_prop(card['funds'][fk]['val'], 'state', state)

        if has_data and not st.get('error'):
            card['err'].setText('')
            card['err'].setVisible(False)
            return
        if not has_data and not st.get('error') and not st.get('error_key'):
            card['err'].setText(t('ta_funds_never', lang))     # 尚未取得，不是失敗
            card['err'].setVisible(True)
            return
        parts = [t(st.get('error_key') or 'ta_funds_fail', lang)]
        if st.get('error'):
            parts.append(str(st['error']))                      # 券商原文，不改寫、不翻譯
        if has_data:
            parts.append(t('ta_funds_stale', lang))
        if st.get('good_ts'):
            parts.append(t('ta_funds_age', lang).format(n=int(max(0, time.time() - st['good_ts']))))
        card['err'].setText('  '.join(parts))
        card['err'].setVisible(True)

    def _meta_value(self, row, field, lang):
        na = t('ta_field_na', lang)
        if field == 'broker':
            return acc.broker_label(row.get('broker', ''), lang)
        if field == 'trd_env':
            return acc.env_label(row.get('trd_env'), lang)
        if field == 'trdmarket_auth':
            return acc.markets_text(row) or na
        return str(row.get(field) or '').strip() or na

    def _render_updated(self):
        if self._fatal:
            self.ta_updated_lbl.setText(self._fatal)      # 異常原文屬事實，不改寫文案
            return
        if not self._last_update:
            self.ta_updated_lbl.setText(t('ta_updated_never', self._lang))
            return
        self.ta_updated_lbl.setText(t('ta_updated', self._lang).format(
            time=time.strftime('%H:%M:%S', time.localtime(self._last_update))))

    def _sync_refresh_btn(self):
        self.ta_refresh_btn.setText(t('ta_refreshing' if self._inflight else 'ta_refresh', self._lang))
        self.ta_refresh_btn.setEnabled(not self._inflight)

    # ── 別名（即時儲存；生成名永不入檔）──
    def _on_alias_text(self, key):
        """打字時只清掉上一次的拒絕訊息 —— 未 commit 前不寫檔，避免半截字串被當撞名。"""
        card = self._cards.get(key)
        if card is not None:
            card['msg'].setText('')
            card['msg'].setVisible(False)

    def _commit_alias(self, key):
        card = self._cards.get(key)
        if card is None:
            return
        text = card['edit'].text()
        ok, msg_key, clash = acc.set_alias(key, text)
        self._aliases = acc.load_aliases()
        self._render_card(key)                # 卡標題即時反映（成功即是最直接的回饋）
        if ok:
            return
        if msg_key == 'ta_alias_too_long':
            body = t(msg_key, self._lang).format(n=acc.ALIAS_MAX)
        else:
            other = next((r for r in self._rows if r['acc_key'] == clash), None)
            who = acc.display_name(other, self._lang, self._aliases) if other else str(clash)
            body = t('ta_alias_clash', self._lang).format(
                alias=str(text or '').strip(), key=who)
        card['msg'].setText(body)
        card['msg'].setVisible(True)

    def _reset_alias(self, key):
        acc.reset_alias(key)
        self._aliases = acc.load_aliases()
        card = self._cards.get(key)
        if card is not None:
            card['edit'].blockSignals(True)   # 清空屬程序行為，不應觸發 commit
            card['edit'].setText('')
            card['edit'].blockSignals(False)
            card['msg'].setText('')
            card['msg'].setVisible(False)
        self._render_card(key)

    # ── theme / i18n ──
    def _apply_theme_qss(self, name):
        pal = theme_mod.THEMES[name]
        self.setStyleSheet(string.Template(_PAGE_QSS).substitute(
            window=pal['window'], surface=pal['surface'], card=pal['card'],
            border=pal['border'], text=pal['text'], muted=pal['muted'],
            accent=pal['accent'], accent_pressed=pal['accent_pressed'],
            real=C_REAL, sim=C_SIM) + theme_mod.note_qss(name))

    def _on_theme_changed(self, name):
        self._apply_theme_qss(name)

    def _connect_signals(self):
        self.ta_refresh_btn.clicked.connect(lambda _c: self._on_refresh())

    def _retranslate_widgets(self):
        lang = self._lang
        apply_text(self, _TEXT, lang)
        self.ta_auto_lbl.setText(t('ta_auto_note', lang).format(n=FUNDS_REFRESH_MS // 1000))
        for name in BROKER_REGISTRY:
            box = getattr(self, f'ta_col_{name}', None)
            if box is not None:
                box.setTitle(t('ta_col_title_fmt', lang).format(broker=acc.broker_label(name, lang)))
        self._sync_refresh_btn()
        self._render_updated()
        if self._cols_synced:         # 欄上的「已屏蔽 N 個」與券商原文也要跟著轉
            self._sync_columns(self._col_errors, self._col_filtered)
        for key in self._cards:
            self._sync_card_captions(self._cards[key])
            self._render_card(key)

    def retranslate(self, lang):
        self._lang = lang
        self._retranslate_widgets()

    # ── 收工 ──
    def _on_app_quit(self):
        self._timer.stop()
        if self._thread is not None:
            try:
                self._thread.request_shutdown()
                self._thread.wait(3000)
            except Exception:
                pass


_PAGE_QSS = """
QWidget#accounts_page { background: ${window}; }
QLabel[og="taupd"] { color: ${muted}; }
QPushButton[og="tarefresh"] { background: ${accent}; color: ${window}; border: 1px solid ${accent};
    border-radius: 4px; padding: 6px 14px; font-weight: 600; }
QPushButton[og="tarefresh"]:hover { background: ${accent_pressed}; }
QPushButton[og="tarefresh"]:disabled { background: ${surface}; color: ${muted}; border-color: ${border}; }
QLabel[og="taerr"] { color: ${muted}; }
QScrollArea#ta_area { border: none; background: transparent; }
QGroupBox { border: 1px solid ${border}; border-radius: 6px; margin-top: 16px; background: ${surface}; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: ${muted};
    font-weight: 600; }

/* 帳戶卡：實盤 = 紅、模擬 = 藍。固定語義色 → 不入 palette，兩 theme 通用
   （政策見 futu_trade_page.py 的 buybtn/sellbtn；紅色與 gui_kline.C_UP 同源）*/
QFrame[og="accard"] { background: ${card}; border: 1px solid ${border}; border-radius: 6px; }
QFrame[og="accard"][env="REAL"] { border: 1px solid ${real}; border-left: 4px solid ${real}; }
QFrame[og="accard"][env="SIMULATE"] { border: 1px solid ${sim}; border-left: 4px solid ${sim}; }
QLabel[og="acctitle"] { color: ${text}; font-weight: 700; }
QLabel[og="acctitle"][env="REAL"] { color: ${real}; }
QLabel[og="acctitle"][env="SIMULATE"] { color: ${sim}; }
QLabel[og="accfldlbl"] { color: ${muted}; }
QLabel[og="accfldval"] { color: ${text}; }
QLabel[og="accfundlbl"] { color: ${muted}; }
QLabel[og="accfundval"] { color: ${text}; }
QLabel[og="accfundval"][state="stale"] { color: ${muted}; }
QLabel[og="accnote"] { color: ${muted}; }
QLabel[og="accmsg"] { color: ${real}; }
QLineEdit[og="accalias"] { background: ${surface}; color: ${text}; border: 1px solid ${border};
    border-radius: 4px; padding: 3px 6px; }
QLineEdit[og="accalias"]:focus { border-color: ${accent}; }
QPushButton[og="accaliasreset"] { background: ${surface}; color: ${muted};
    border: 1px solid ${border}; border-radius: 4px; padding: 3px 8px; }
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(AccountsPage, 'page_accounts_title')
