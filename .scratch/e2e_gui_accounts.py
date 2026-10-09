# -*- coding: utf-8 -*-
"""E2E GUI 測試 — 交易帳戶頁（ticket #36c / #36d）。

執行：python .scratch/e2e_gui_accounts.py（由專案根目錄；自動設定 offscreen）
全部 hermetic：fake 交易 broker（注入 client_factory）+ 暫存 state 檔 —
不打真 broker，不受開市時段影響。

分節：
1  `.ui` 骨架：宣告名全部可取回、無 `qt_` 前綴、宣告的 ta_grid_* == registry.BROKERS
2  語義色：紅 = 全app 唯一紅色、藍為新增；頁級 QSS 含 [env=…] 且已 append note_qss
3  排程常數：10 秒、顯示啟動 / 隱藏停止、重載不重複取清單
4  卡片：每帳戶一張、放進所屬券商欄、env property、用戶要求的顯示名、
   市場渲染而非 Python repr、IB 缺的欄位以「不適用」呈現、推斷/矛盾如實標記、
   非 ACTIVE 被屏蔽並如實計數
5  資金：千分位、0 照實 0.00、缺失一律 —、幣種逐卡標明；失敗保留好值並標過期
6  排程行為：tick 只取資金、手動刷新、重入只一輪在飛且按鈕不會卡住、過期 token 作廢
7  單券商失敗只影響該欄，另一欄不受影響；恢復後卡片回來
8  別名：即時儲存、撞名如實擋、超長如實擋、重設回生成名、生成名永不入檔、跨實例重載
9  三語跟隨 + 本頁可引用的 i18n 鍵三語齊全 + 外殼註冊
10 收工：worker thread 與 broker 連線一齊釋放
"""
import asyncio
import os
import re
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TMPDIR = tempfile.mkdtemp()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame  # noqa: E402

from gateway import i18n, state_store, theme as theme_mod  # noqa: E402
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state.json'   # 🤖 hermetic：tmp state 檔

from gateway.i18n import LANGS, t  # noqa: E402
from gateway.ui.loader import UI_DIR  # noqa: E402
from modules.registry import BROKERS  # noqa: E402
import gateway.accounts as acc  # noqa: E402
import gateway.app as appmod  # noqa: E402
import gateway.pages.accounts_page as bp  # noqa: E402
from gateway.pages import gui_kline as gk  # noqa: E402

FAILURES, TOTAL = [], 0


def check(name, ok, detail=''):
    global TOTAL
    TOTAL += 1
    if ok:
        print(f'  PASS  {name}')
    else:
        print(f'  FAIL  {name}  {detail}')
        FAILURES.append(name)


def pump(app, n=40, delay=0.005):
    for _ in range(n):
        app.processEvents()
        time.sleep(delay)


def wait_for(app, cond, what, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    check(f'等待{what}', False, f'{timeout}s timeout')
    return False


# ── fixture：兩家券商、含一個非 ACTIVE（必須被屏蔽並計數）與一個 PAPER/REAL 矛盾 ──
FUTU_ROWS = [
    {'acc_id': 4654894524, 'trd_env': 'REAL', 'acc_type': 'MARGIN',
     'trdmarket_auth': ['HK', 'US'], 'acc_status': 'ACTIVE'},
    {'acc_id': '5002', 'trd_env': 'SIMULATE', 'acc_type': 'SIMULATE',
     'trdmarket_auth': ['HK'], 'acc_status': 'ACTIVE'},
    {'acc_id': '5003', 'trd_env': 'REAL', 'acc_type': 'MARGIN',
     'trdmarket_auth': [], 'acc_status': 'DISABLED'},
]
IB_ROWS = [
    {'acc_id': 'DU12345', 'trd_env': 'SIMULATE', 'acc_type': '',
     'trdmarket_auth': [], 'acc_status': 'ACTIVE'},
    {'acc_id': 'U999', 'trd_env': 'REAL', 'acc_type': 'PAPER',
     'trdmarket_auth': [], 'acc_status': 'ACTIVE'},
]
# 0 是有效值（必須顯示 0.00）；None / 'N/A' / '' 是未知（必須顯示 —）
FUNDS = {
    '4654894524': {'total_assets': 1234567.891, 'cash': 100000, 'market_val': 1134567.89,
                   'power': 250000.5, 'available_funds': 0, 'avl_withdrawal_cash': None,
                   'currency': 'HKD'},
    '5002': {'total_assets': '88000.5', 'cash': 'N/A', 'market_val': '', 'power': 1000,
             'available_funds': 0, 'avl_withdrawal_cash': 0, 'currency': ''},
    'U999': {'total_assets': 54321.0, 'cash': 1234.5, 'market_val': 53086.5,
             'power': 20000, 'available_funds': 1234.5, 'avl_withdrawal_cash': 1000,
             'currency': 'USD'},
}
BROKER_KEYS = {'futu': 'name_broker_futu', 'ib': 'name_broker_ib'}
ENV_KEYS = {'REAL': 'trade_env_real', 'SIMULATE': 'trade_env_sim'}
NEEDED = ('accounts_page', 'ta_header_card', 'ta_title_lbl', 'ta_updated_lbl', 'ta_auto_lbl',
          'ta_refresh_btn', 'accounts_page_note', 'ta_usage', 'ta_area_wrap', 'ta_area', 'ta_host',
          'ta_col_futu', 'ta_col_futu_err', 'ta_col_ib', 'ta_col_ib_err')


def expect_name(broker, env, acc_id, lang):
    """由 i18n 直接構造期望的顯示名（不经 `display_name`，否則斷言會變成自我循環）。"""
    return t('ta_name_fmt', lang).format(
        label=t('ta_label_fmt', lang).format(
            broker=t(BROKER_KEYS[broker], lang), env=t(ENV_KEYS[env], lang)),
        acc_id=acc_id)


class FakeTradeClient:
    """duck-typed 交易 client（契約見 modules/trade_base.py）。

    ⚠️ 屬性名不可用 `positions` / `open_orders`：會遮蔽契約裡的 async 方法。
    """

    def __init__(self):
        self.rows = {'futu': [dict(r) for r in FUTU_ROWS], 'ib': [dict(r) for r in IB_ROWS]}
        self.funds = {k: dict(v) for k, v in FUNDS.items()}
        self.accounts_calls, self.info_calls = [], []
        self.accounts_fail, self.funds_fail = {}, {}
        self.list_delay = 0.0
        self.entered = self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *a):
        self.exited += 1
        return False

    def trade_supported(self, broker=None):
        return True

    async def trade_accounts(self, *, broker=None):
        self.accounts_calls.append(broker)
        if self.list_delay:
            await asyncio.sleep(self.list_delay)
        if broker in self.accounts_fail:
            return False, None, self.accounts_fail[broker]
        return True, self.rows.get(broker, []), ''

    async def account_info(self, *, account=None, env=None, broker=None):
        key = str(account or '')
        self.info_calls.append((broker, key, env))
        if key in self.funds_fail:
            return False, None, self.funds_fail[key]
        info = self.funds.get(key)
        if info is None:
            return False, None, f'沒有帳戶 {key} 的資金資料'
        return True, dict(info), ''


app = QApplication.instance() or QApplication(sys.argv)
fake = FakeTradeClient()
page = bp.AccountsPage(client_factory=lambda: fake)

print('\n[1] `.ui` 骨架與 objectName 契約')
ui_text = (UI_DIR / 'accounts_page.ui').read_text(encoding='utf-8')
declared = set(re.findall(r'<widget class="[^"]*" name="([^"]+)"', ui_text))
layouts = set(re.findall(r'<layout class="[^"]*" name="([^"]+)"', ui_text))
check('`.ui` 宣告的控件全部在場', set(NEEDED) <= declared, f'缺少 {set(NEEDED) - declared}')
unreachable = [n for n in declared if n != 'accounts_page' and getattr(page, n, None) is None]
check('宣告的控件全部可經頁面屬性取回（QUiLoader 掛載契約）', not unreachable, f'{unreachable}')
check('無 objectName 以 `qt_` 開頭（QUiLoader 不會掛載該前綴）',
      not [n for n in declared | layouts if n.startswith('qt_')])
grids = {n[len('ta_grid_'):] for n in layouts if n.startswith('ta_grid_')}
check('宣告的 ta_grid_* == registry.BROKERS（「按 BROKER 數量加欄」由結構保證）',
      grids == set(BROKERS), f'{grids} vs {list(BROKERS)}')
try:
    page._grid('nope')
    check('缺 ta_grid_* 時 raise RuntimeError', False)
except RuntimeError:
    check('缺 ta_grid_* 時 raise RuntimeError', True)

print('\n[2] 主色與頁級 QSS')
check('實盤主色 = 全app 唯一紅色（與漲跌同源，不再第二份紅）', bp.C_REAL == gk.C_UP,
      f'{bp.C_REAL} vs {gk.C_UP}')
check('模擬主色為藍，且不是把 C_DOWN（青綠）當藍', bp.C_SIM == '#2C7BE5' and bp.C_SIM != gk.C_DOWN)
ss = page.styleSheet()
check('頁級 QSS 含 [env="REAL"] / [env="SIMULATE"] 兩條语义 selector',
      '[og="accard"][env="REAL"]' in ss and '[og="accard"][env="SIMULATE"]' in ss)
check('頁級 setStyleSheet 已 append note_qss（否則頁內 role 文字靜默無樣式）',
      'QLabel[role="pagebody"]' in ss and 'QLabel[role="usagehint"]' in ss)
check('root 已補 WA_StyledBackground（頁級 QSS 才生效）',
      page.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
check('標題/備注的 role 已 stamp',
      page.ta_title_lbl.property('role') == 'pagetitle'
      and page.accounts_page_note.property('role') == 'pagebody'
      and page.ta_usage.property('role') == 'usagehint')
light = theme_mod.THEMES['light']
theme_mod.apply_theme('light')
pump(app, 6)
ss2 = page.styleSheet()
check('theme 切換後頁級 QSS 重新代入新 palette', light['window'].lower() in ss2.lower(),
      f'{light["window"]} 不在頁級 QSS')
check('theme 切換後語義色與 note_qss 仍在（兩 theme 通用）',
      '[og="accard"][env="REAL"]' in ss2 and 'QLabel[role="pagebody"]' in ss2)
theme_mod.apply_theme('dark')
pump(app, 4)

print('\n[3] 10 秒週期（用戶明確要求）')
check('FUNDS_REFRESH_MS == 10_000', bp.FUNDS_REFRESH_MS == 10_000, str(bp.FUNDS_REFRESH_MS))
check('QTimer interval 與常數一致', page._timer.interval() == bp.FUNDS_REFRESH_MS)
check('未顯示前定時器未啟動', not page._timer.isActive())

print('\n[4] 開頁即取清單：每帳戶一卡、每券商一欄')
page.show()
check('worker 就緒後補發首次取得（開頁早於執行緒就緒不能靜默無資料）',
      wait_for(app, lambda: len(page._cards) == 4, '四張卡片'))
check('富途欄 2 張、IB 欄 2 張（水平排列，欄永不隱藏）',
      page.ta_grid_futu.count() == 2 and page.ta_grid_ib.count() == 2,
      f'futu={page.ta_grid_futu.count()} ib={page.ta_grid_ib.count()}')
frame = page.findChild(QFrame, 'ta_card_futu_4654894524')
check('卡片 objectName 可預測（acc_key 安全化）', frame is not None)
check('卡片 env property = REAL / SIMULATE（主色由 QSS 決定，建立時一次）',
      frame.property('env') == 'REAL'
      and page._cards['futu:5002']['frame'].property('env') == 'SIMULATE'
      and page._cards['ib:U999']['frame'].property('env') == 'REAL')
check('卡片放進所屬券商欄（不跨欄）',
      all(str(page.ta_grid_futu.itemAt(i).widget().objectName()).startswith('ta_card_futu_')
          for i in range(page.ta_grid_futu.count()))
      and all(str(page.ta_grid_ib.itemAt(i).widget().objectName()).startswith('ta_card_ib_')
              for i in range(page.ta_grid_ib.count())))
title = page._cards['futu:4654894524']['title'].text()
check('顯示名 = 用戶要求的格式：富途實盤(4654894524)', title == '富途實盤(4654894524)', repr(title))
check('IB 顯示名按推斷環境生成',
      page._cards['ib:DU12345']['title'].text() == expect_name('ib', 'SIMULATE', 'DU12345', 'zh_hk'),
      repr(page._cards['ib:DU12345']['title'].text()))
card = page._cards['futu:4654894524']
check('市場渲染為 HK・US，不是 Python repr',
      card['fields']['trdmarket_auth']['val'].text() == 'HK・US',
      repr(card['fields']['trdmarket_auth']['val'].text()))
ib_card = page._cards['ib:DU12345']
na = t('ta_field_na', 'zh_hk')
check('IB 沒有的欄位以「不適用」呈現（空白會被讀成「無」）',
      ib_card['fields']['acc_type']['val'].text() == na
      and ib_card['fields']['trdmarket_auth']['val'].text() == na)
# 卡片於執行期建立（頁面已顯示）→ 不會被父控件的 show() 帶出來。
# 只斷言文字會完全漏掉這一類「頁面一片空白」的失敗。
check('四張卡片全部實際可見（執行期建的控件要自己 show）',
      all(c['frame'].isVisible() for c in page._cards.values()) and len(page._cards) == 4,
      repr([(k, c['frame'].isVisible(), c['frame'].isHidden()) for k, c in page._cards.items()]))
check('IB 的 REAL/SIMULATE 標明為推斷（不是 API 欄位）',
      t('ta_env_inferred_note', 'zh_hk') in ib_card['note'].text()
      and ib_card['note'].isVisible(),
      f'text={ib_card["note"].text()!r} visible={ib_card["note"].isVisible()}')
check('新建卡片即刻已有欄位／資金／別名標籤文字（不能等下次切換語言才出現）',
      card['fields']['acc_type']['cap'].text() == t('ta_field_type', 'zh_hk')
      and card['funds']['power']['cap'].text() == t('fund_power', 'zh_hk')
      and card['albl'].text() == t('ta_alias', 'zh_hk')
      and card['edit'].placeholderText() == t('ta_alias_ph', 'zh_hk')
      and card['reset'].text() == t('ta_alias_reset', 'zh_hk'))
conflict = page._cards['ib:U999']
check('推斷與上報 AccountType 矛盾時如實標記（不靜默「修正」）',
      t('ta_env_conflict', 'zh_hk').format(type='PAPER') in conflict['note'].text())
check('富途卡不出現推斷/矛盾標記（該兩家資料來源不同）',
      not card['note'].text() and not page._cards['futu:5002']['note'].text())
check('非 ACTIVE 帳戶被屏蔽，且如實計數',
      'futu:5003' not in page._cards
      and page.ta_col_futu_err.text() == t('ta_filtered_note', 'zh_hk').format(n=1),
      repr(page.ta_col_futu_err.text()))
check('無可說事的欄錯誤標籤保持隱藏（不留空標籤佔位）', not page.ta_col_ib_err.isVisible())
check('欄標題按券商生成',
      page.ta_col_futu.title() == t('ta_col_title_fmt', 'zh_hk').format(broker=t('name_broker_futu', 'zh_hk')))
check('最後更新時間已顯示', t('ta_updated', 'zh_hk').split('{time}')[0] in page.ta_updated_lbl.text())
check('自動刷新說明含 10 秒（與常數同源）',
      t('ta_auto_note', 'zh_hk').format(n=bp.FUNDS_REFRESH_MS // 1000) == page.ta_auto_lbl.text())
check('取得完成後按鈕回到可用狀態',
      page.ta_refresh_btn.text() == t('ta_refresh', 'zh_hk') and page.ta_refresh_btn.isEnabled())
# 其後一律手動驅動：10 秒定時器若在多步斷言中途觸發，會令 token 過期、斷言變成擷取時機問題
page._timer.stop()

print('\n[5] 資金明細：誠實的數值與空值')
check('資金已填入', wait_for(app, lambda: (page._funds.get('futu:4654894524') or {}).get('info'), '資金填入'))
fv = page._cards['futu:4654894524']['funds']
check('千分位與兩位小數', fv['total_assets']['val'].text() == '1,234,567.89',
      repr(fv['total_assets']['val'].text()))
check('0 是有效值，照實顯示 0.00（不與「未知」混為一談）', fv['available_funds']['val'].text() == '0.00',
      repr(fv['available_funds']['val'].text()))
check('None → —（不是 0）', fv['avl_withdrawal_cash']['val'].text() == acc.DASH)
f2 = page._cards['futu:5002']['funds']
check("'N/A' 與空字串一律 —（不填 0）",
      f2['cash']['val'].text() == acc.DASH and f2['market_val']['val'].text() == acc.DASH)
check('字串數字同樣正確', f2['total_assets']['val'].text() == '88,000.50',
      repr(f2['total_assets']['val'].text()))
check('幣種逐卡標明（富途傳 HKD）',
      t('ta_currency', 'zh_hk').format(ccy='HKD') in page._cards['futu:4654894524']['hdr'].text())
check('券商未標明幣種時如實說明（不做跨券商合計）',
      t('ta_currency_unknown', 'zh_hk') in page._cards['futu:5002']['hdr'].text())
check('資金標籤文字來自 i18n（與契約欄位一一对應）',
      f2['total_assets']['cap'].text() == t('fund_total_assets', 'zh_hk')
      and f2['power']['cap'].text() == t('fund_power', 'zh_hk'))
check('資金成功時卡上不出現錯誤行',
      not page._cards['futu:4654894524']['err'].text())
fake.funds_fail['DU12345'] = 'OpenD 拒絕：模擬帳戶配額不足'
page._on_refresh()
check('單帳戶資金失敗：顯示 — 並保留券商原文',
      wait_for(app, lambda: 'OpenD 拒絕' in page._cards['ib:DU12345']['err'].text(), '資金失敗訊息'))
d = page._cards['ib:DU12345']
check('失敗帳戶全部為 —，不出現 0',
      all(d['funds'][k]['val'].text() == acc.DASH for k in d['funds'])
      and not any(d['funds'][k]['val'].text().startswith('0') for k in d['funds']))
check('其他帳戶的資金不受影響', fv['total_assets']['val'].text() == '1,234,567.89')
page._funds['ib:U999']['good_ts'] = time.time() - 37
fake.funds_fail['U999'] = 'TWS 連線中斷'
page._on_refresh()
age_tpl = t('ta_funds_age', 'zh_hk')
age_re = re.compile(re.escape(age_tpl.split('{n}')[0]) + r'\d+' + re.escape(age_tpl.split('{n}')[1]))
err_txt = ''
check('資金失敗時保留上次好值並標為過期',
      wait_for(app, lambda: t('ta_funds_stale', 'zh_hk') in
               (err_txt := page._cards['ib:U999']['err'].text()), '過期標記'))
check('過期時仍顯示上次成功的數值（清空會丟掉使用者需要的資訊）',
      page._cards['ib:U999']['funds']['total_assets']['val'].text() == '54,321.00')
check('如實回報最後成功時間', bool(age_re.search(page._cards['ib:U999']['err'].text())),
      repr(page._cards['ib:U999']['err'].text()))
check('過期數值以 state="stale" 表達（QSS 可見），不是只靠文字',
      page._cards['ib:U999']['funds']['total_assets']['val'].property('state') == 'stale')
del fake.funds_fail['DU12345'], fake.funds_fail['U999']
page._on_refresh()
check('恢復後 state 回到 ok',
      wait_for(app, lambda: page._cards['ib:U999']['funds']['total_assets']['val'].property('state') == 'ok',
               'state 恢復 ok'))

print('\n[6] 排程行為：tick 只取資金、重入、過期 token')
page._timer.start()
a_pre = len(fake.accounts_calls)
page.hide()
check('隱藏即停止定時器（不在本頁就不再向券商發問）', not page._timer.isActive())
page.show()
check('重新顯示即重新啟動定時器', page._timer.isActive())
check('重新顯示不重複取帳戶清單（只恢复資金週期）', len(fake.accounts_calls) == a_pre)
page._timer.stop()
a0, i0 = len(fake.accounts_calls), len(fake.info_calls)
page._on_tick()
check('定時器 tick 會取資金', wait_for(app, lambda: len(fake.info_calls) > i0 and not page._inflight, 'tick 取資金'))
check('10 秒週期只取資金，不重新取帳戶清單', len(fake.accounts_calls) == a0,
      f'清單呼叫 {a0} → {len(fake.accounts_calls)}')
a1 = len(fake.accounts_calls)
page.ta_refresh_btn.click()
check('手動刷新會重新取清單', wait_for(app, lambda: len(fake.accounts_calls) >= a1 + 2 and not page._inflight,
                                     '手動刷新'))
fake.list_delay = 0.6
a2 = len(fake.accounts_calls)
page._on_refresh()
pump(app, 8)
check('請求進行中按鈕顯示「刷新中…」並停用（不給使用者重複點擊的錯覺）',
      page.ta_refresh_btn.text() == t('ta_refreshing', 'zh_hk') and not page.ta_refresh_btn.isEnabled())
page._on_refresh()   # 第二次請求：第一家仍未返回 → 應被 worker 擋下
check('重入：慢回應期間只有一輪清單在飛（兩家各一次，不是兩輪）',
      wait_for(app, lambda: len(fake.accounts_calls) == a2 + 2, '一輪完整的兩家取得'),
      f'{a2} → {len(fake.accounts_calls)}')
check('被重入擋掉的請求仍會 settle（按鈕不會永久卡在「刷新中」）',
      wait_for(app, lambda: not page._inflight, '第二次請求 settle')
      and page.ta_refresh_btn.isEnabled(),
      f'inflight={page._inflight} text={page.ta_refresh_btn.text()!r}')
check('慢回應結束後 worker 的重入旗標釋放（下一輪可正常取得）',
      wait_for(app, lambda: page._worker._listing is False, 'worker 取得旗標釋放'),
      f'listing={page._worker._listing}')
fake.list_delay = 0.0
n_cards = len(page._cards)
page._on_accounts({'token': page._token + 99, 'rows': []})
page._on_funds({'token': page._token + 99, 'acc_key': 'futu:4654894524',
                'info': {'total_assets': 1}})
check('過期 token 的 payload 一律作廢（遲到的結果不能蓋過當前）',
      len(page._cards) == n_cards and fv['total_assets']['val'].text() == '1,234,567.89')
ts_before = page._last_update
page._on_refresh()
# 只數卡片會假過：上一輪被擋掉時卡片也不會少。要等的是「本輪真的落地」
check('正常刷新會落地並保持四張卡片',
      wait_for(app, lambda: page._last_update > ts_before and len(page._cards) == 4, '本輪取得落地'))

print('\n[7] 單券商失敗只影響該欄')
fake.accounts_fail['ib'] = 'IB 未連線（TWS 未啟動）'
page._on_refresh()
check('失敗券商的欄如實顯示原文',
      wait_for(app, lambda: page.ta_col_ib_err.text() == 'IB 未連線（TWS 未啟動）', 'IB 欄錯誤訊息'))
check('已宣告的欄永不隱藏', page.ta_col_ib.isVisible() and page.ta_col_ib_err.isVisible())
check('該欄的卡片被移除', page.ta_grid_ib.count() == 0)
check('另一家券商完全不受影響（兩次取得互相獨立）',
      page.ta_grid_futu.count() == 2 and page.ta_col_futu_err.text() == t('ta_filtered_note', 'zh_hk').format(n=1))
del fake.accounts_fail['ib']
page._on_refresh()
check('恢復後卡片回來（差異增刪，不是整頁重建）',
      wait_for(app, lambda: page.ta_grid_ib.count() == 2 and len(page._cards) == 4, 'IB 卡片恢復'))

print('\n[8] 別名（#36d）')
c1 = page._cards['futu:4654894524']
c1['edit'].setText('主帳戶')
c1['edit'].editingFinished.emit()
pump(app, 6)
check('別名即時生效：主帳戶(4654894524)',
      c1['title'].text() == '主帳戶(4654894524)', repr(c1['title'].text()))
check('別名已寫入儲存', acc.load_aliases().get('futu:4654894524') == '主帳戶')
check('acc_id 永遠保留在括號內（真錢在一步之外，不能只靠別名識別）',
      c1['title'].text().endswith('(4654894524)'))
raw = Path(state_store.STATE_PATH).read_text(encoding='utf-8')
check('生成名永不入檔（否則切換語言會殘留舊文字）',
      '富途實盤' not in raw and 'Futu REAL' not in raw)
page.retranslate('en')
pump(app, 4)
check('設了別名後切換語言：別名原樣保留，未設別名的帳戶改用該語言的生成名',
      c1['title'].text() == t('ta_name_fmt', 'en').format(label='主帳戶', acc_id='4654894524')
      and page._cards['ib:DU12345']['title'].text() == expect_name('ib', 'SIMULATE', 'DU12345', 'en'),
      repr((c1['title'].text(), page._cards['ib:DU12345']['title'].text())))
page.retranslate('zh_hk')
pump(app, 4)
c2 = page._cards['ib:U999']
c2['edit'].setText('主帳戶')
c2['edit'].editingFinished.emit()
pump(app, 6)
check('撞名如實擋下，不靜默覆蓋',
      c2['title'].text() == 'IB實盤(U999)' and acc.load_aliases().get('ib:U999') is None,
      repr(c2['title'].text()))
check('衝突訊息把對方講清楚（用對方的顯示名，不是內部 key）',
      c2['msg'].text() == t('ta_alias_clash', 'zh_hk').format(alias='主帳戶', key='主帳戶(4654894524)'),
      repr(c2['msg'].text()))
c2['edit'].setText('長' * (acc.ALIAS_MAX + 1))
c2['edit'].editingFinished.emit()
pump(app, 6)
check('超長別名如實擋下（不截斷：截斷後兩個別名在畫面上會完全相同）',
      c2['msg'].text() == t('ta_alias_too_long', 'zh_hk').format(n=acc.ALIAS_MAX)
      and acc.load_aliases().get('ib:U999') is None, repr(c2['msg'].text()))
c2['reset'].click()
pump(app, 6)
check('重設回生成名', c2['title'].text() == 'IB實盤(U999)' and c2['edit'].text() == '')
c1['reset'].click()
pump(app, 6)
check('重設後儲存該筆已刪除，顯示名回落生成名',
      acc.load_aliases().get('futu:4654894524') is None
      and c1['title'].text() == '富途實盤(4654894524)')

print('\n[9] 三語跟隨 + i18n 完整性 + 外殼註冊')
for lang in LANGS:
    page.retranslate(lang)
    pump(app, 4)
    want = [
        ('帳戶顯示名', page._cards['futu:4654894524']['title'].text(),
         expect_name('futu', 'REAL', '4654894524', lang)),
        ('頁標題', page.ta_title_lbl.text(), t('page_accounts_title', lang)),
        ('券商欄標題', page.ta_col_ib.title(),
         t('ta_col_title_fmt', lang).format(broker=t('name_broker_ib', lang))),
        ('欄位標籤', page._cards['futu:4654894524']['fields']['acc_type']['cap'].text(),
         t('ta_field_type', lang)),
        ('資金標籤', page._cards['futu:4654894524']['funds']['power']['cap'].text(),
         t('fund_power', lang)),
        ('自動刷新說明', page.ta_auto_lbl.text(),
         t('ta_auto_note', lang).format(n=bp.FUNDS_REFRESH_MS // 1000)),
        ('屏蔽計數', page.ta_col_futu_err.text(), t('ta_filtered_note', lang).format(n=1)),
        ('刷新按鈕', page.ta_refresh_btn.text(), t('ta_refresh', lang)),
        ('別名標籤', page._cards['futu:4654894524']['albl'].text(), t('ta_alias', lang)),
        ('IB 缺欄「不適用」', page._cards['ib:DU12345']['fields']['acc_type']['val'].text(),
         t('ta_field_na', lang)),
        ('推斷標記', t('ta_env_inferred_note', lang) in page._cards['ib:DU12345']['note'].text(), True),
        ('未標明幣種', t('ta_currency_unknown', lang) in page._cards['futu:5002']['hdr'].text(), True),
    ]
    bad = [f'{n}: {got!r} ≠ {exp!r}' for n, got, exp in want if got != exp]
    check(f'切換 {lang}：標題、欄標題、欄位標籤、卡片、狀態訊息全部跟隨', not bad, ' | '.join(bad))
KEYS = ['page_accounts_title', 'accounts_page_note', 'ta_usage', 'ta_refresh', 'ta_refreshing',
        'ta_updated', 'ta_updated_never', 'ta_auto_note', 'ta_col_title_fmt', 'ta_no_accounts',
        'ta_filtered_note', 'ta_field_broker', 'ta_field_env', 'ta_field_type', 'ta_field_markets',
        'ta_field_status', 'ta_field_na', 'ta_env_inferred_note', 'ta_env_conflict', 'ta_currency',
        'ta_currency_unknown', 'ta_funds_title', 'ta_funds_fail', 'ta_funds_stale', 'ta_funds_age',
        'ta_funds_never', 'ta_funds_no_account', 'ta_alias', 'ta_alias_ph', 'ta_alias_reset',
        'ta_alias_clash', 'ta_alias_too_long', 'ta_name_fmt', 'ta_label_fmt', 'name_broker_futu',
        'name_broker_ib', 'trade_env_real', 'trade_env_sim', 'nav_accounts', 'col_display_name']
KEYS += list(acc.FUND_LABEL_KEYS.values())
# `i18n` 匯入時已核對「三語齊全」；這裡要守的是**本頁引用的鍵是否存在**（缺鍵會 KeyError）
missing = [k for k in KEYS if not isinstance(i18n.STRINGS.get(k), dict)
           or any(not str(i18n.STRINGS[k].get(l) or '').strip() for l in LANGS)]
check(f'本頁引用的 {len(KEYS)} 個鍵全部存在且三語非空', not missing, f'{missing}')
check('外殼已註冊 accounts（PAGE_KEYS / _PAGE_CLASSES / NAV_DIRECT）',
      'accounts' in appmod.PAGE_KEYS and 'accounts' in appmod._PAGE_CLASSES
      and 'accounts' in appmod.NAV_DIRECT)
check('accounts 排在 futu_trade 之後，且 PAGE_KEYS[0]/[1] 不變（返回頁重插位置正確）',
      appmod.PAGE_KEYS.index('accounts') == appmod.PAGE_KEYS.index('futu_trade') + 1
      and appmod.PAGE_KEYS[0] == 'home' and appmod.PAGE_KEYS[1] == 'quotes')
check('註冊的是本頁類別', appmod._PAGE_CLASSES['accounts'] is bp.AccountsPage)

print('\n[10] 別名跨實例 + 收工')
c1['edit'].setText('主帳戶')
c1['edit'].editingFinished.emit()
pump(app, 6)
page._on_app_quit()
pump(app, 6)
check('收工後 worker thread 已結束', page._thread.isFinished())
check('收工後 broker 連線已釋放', fake.exited == 1, f'exited={fake.exited}')
check('收工後定時器已停止', not page._timer.isActive())
fake2 = FakeTradeClient()
page2 = bp.AccountsPage(client_factory=lambda: fake2)
page2.show()
check('第二個頁面實例可正常載入', wait_for(app, lambda: len(page2._cards) == 4, '第二實例的四張卡片'))
check('別名從儲存重載，跨實例仍成立',
      page2._cards['futu:4654894524']['title'].text() == '主帳戶(4654894524)',
      repr(page2._cards['futu:4654894524']['title'].text()))
page2._on_app_quit()
pump(app, 6)
check('第二個實例的執行緒與連線一齊釋放（外殼級清理依賴此契約）',
      page2._thread.isFinished() and fake2.exited == 1)

print(f'\n{"=" * 62}\n總數 {TOTAL}，失敗 {len(FAILURES)}')
if FAILURES:
    for f in FAILURES:
        print(f'  ❌ {f}')
    sys.exit(1)
print('✅ e2e_gui_accounts 全部通過')
sys.exit(0)
