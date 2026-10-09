# -*- coding: utf-8 -*-
"""帳戶領域層 — 跨券商帳戶清單、ACTIVE 規則、別名與顯示名、資金明細格式化（ticket #36）。

## 為什麼需要這一層
三個消費端（交易帳戶頁、量化交易頁、富途交易頁）都要同一批帳戶知識：哪些帳戶存在、
哪些應被屏蔽、如何命名、資金欄位的名稱。若各頁各寫一份，規則會各自漂移 ——
「非 ACTIVE 屏蔽」在兩頁寫成兩個 if，遲早一個過濾一個不過濾。故本檔是這些規則的
**唯一定義處**；頁面只負責排版與排程。

## 邊界
- 純計算部分（`normalise_rows` / `active_only` / `display_name` / `fund_rows`）可用一般
  dict 直接測，不需要 Qt，也不需要 broker。
- 非同步部分（`collect_accounts` / `refresh_funds`）把 client 當**參數**，不 import
  `modules.broker`：頁面已持有 client（真或測試替身），本層只負責「逐券商取 → 標記 →
  合併 → 過濾」這段會漂移的迴圈；排程（token、重入、關閉）留在頁面的 worker。
- 欄位定義仍屬 `modules/trade_base.py`（供應商契約）。本檔只在其上疊加 `broker` /
  `acc_key` 兩個**衍生**欄，不修改 `ACC_COLS`。

## 顯示名（用戶：「以後全域可以用別名顯示來選擇的帳戶，比如富途實盤(4654894524)」）
生成名由 i18n + 行欄位**即時解析，永不寫入儲存**；儲存裡只有用戶自己寫的別名。
理由與 `gateway/favorites.py` 對符號顯示名的立場相同：把生成文字存下來，切換語言後
就會留下舊語言的錯文字。連帶要求：任何顯示帳戶清單的選單必須在 `retranslate()` 時重填。

## 誠實的空值
資金缺失、`'N/A'`、非數字一律顯示 `—`，**不顯示 0**：0 與「未知」是兩件不同的事。
券商回傳的錯誤原文原樣保留（不 i18n、不改寫），由頁面決定如何顯示。
"""
import re
import time

from modules.trade_base import ACC_COLS, ACCINFO_KEYS, clamp_env
from gateway.i18n import DEFAULT_LANG, t

SECTION = 'accounts'      # state_store section 名（名屬領域層，不放頁面）

# 合併後的行形狀 = 供應商契約 + 兩個衍生欄
ROW_KEYS = ACC_COLS + ('broker', 'acc_key')

DASH = '—'
ALIAS_MAX = 40

# 資金欄 → 標籤 key（欄位定義屬契約，標籤文字屬 i18n；這裡只連接兩者）
FUND_LABEL_KEYS = {
    'total_assets': 'fund_total_assets',
    'cash': 'fund_cash',
    'market_val': 'fund_market_val',
    'power': 'fund_power',
    'available_funds': 'fund_available_funds',
    'avl_withdrawal_cash': 'fund_avl_withdrawal_cash',
}

_BROKER_LABEL_KEYS = {'futu': 'name_broker_futu', 'ib': 'name_broker_ib'}
_ENV_LABEL_KEYS = {'REAL': 'trade_env_real', 'SIMULATE': 'trade_env_sim'}
_ALIAS_DROP = re.compile(r'[\x00-\x1f\x7f]')


def acc_key(broker, acc_id):
    """帳戶唯一 key。跨券商不碰撞（futu:4654894524 / ib:DU12345）。"""
    return f'{broker}:{acc_id}'


def normalise_rows(rows, broker):
    """供應商行 → 合併行（補 broker / acc_key；`acc_id` 一律轉 str）。

    ⚠️ `acc_id` 必須歸一為 str：富途回 int、IB 回 'DU12345'。混用會令 dict key 與
    combo userData 靜默不再相符 —— 選了 A 帳戶卻下單到 B 是會動真錢的錯。
    沒有 acc_id 的行無法識別，如實略過（不編造 id）。
    """
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        raw = r.get('acc_id')
        acc = '' if raw is None else str(raw).strip()
        if not acc:
            continue
        row = {c: r.get(c, '') for c in ACC_COLS}
        row['acc_id'] = acc
        row['broker'] = str(broker)
        row['acc_key'] = acc_key(broker, acc)
        out.append(row)
    return out


def active_only(rows):
    """→ (保留的行, 被屏蔽數量)。用戶：「富途將 STATUS 不是 ACTIVE 帳號的屏蔽」。

    這是全app 唯一一份 ACTIVE 規則（富途交易頁與交易帳戶頁都呼叫這裡）。
    ⚠️ `acc_status == 'N/A'` 是「未知」不是「已關閉」，因此必須計數並由頁面回報，
    否則被屏蔽的帳戶會無聲消失。
    """
    kept = [r for r in rows if str(r.get('acc_status') or '').strip().upper() == 'ACTIVE']
    return kept, len(rows) - len(kept)


def broker_label(broker, lang=DEFAULT_LANG):
    key = _BROKER_LABEL_KEYS.get(str(broker or '').lower())
    return t(key, lang) if key else str(broker or '')


def env_label(trd_env, lang=DEFAULT_LANG):
    """環境標籤。未知值經 `clamp_env` 退回模擬 —— 與全app 一致：未知時不假裝有真錢。"""
    return t(_ENV_LABEL_KEYS[clamp_env(trd_env)], lang)


def display_name(row, lang=DEFAULT_LANG, aliases=None):
    """帳戶顯示名：`富途實盤(4654894524)`；設了別名則 `主帳戶(4654894524)`。

    別名取代生成的 label，但 **acc_id 永遠保留在括號內**：真錢在一步之外，
    隱藏帳號數字有風險；同時兩種情況共用一個格式函式。
    措辭與分隔一律由 i18n 決定（`ta_label_fmt` 決定「富途」與「實盤」之間要不要空格、
    `ta_name_fmt` 決定括號），不在程式碼內寫字面量：中文直接相接，英文相接會變成
    "FutuREAL"。
    """
    key = row.get('acc_key') or acc_key(row.get('broker', ''), row.get('acc_id', ''))
    alias = str((aliases if aliases is not None else load_aliases()).get(key) or '').strip()
    label = alias or t('ta_label_fmt', lang).format(
        broker=broker_label(row.get('broker', ''), lang),
        env=env_label(row.get('trd_env'), lang))
    return t('ta_name_fmt', lang).format(label=label, acc_id=str(row.get('acc_id') or ''))


# ── 別名儲存（{'aliases': {acc_key: text}}）──
def load_aliases():
    from gateway import state_store
    al = state_store.load_section(SECTION, {}).get('aliases')
    return dict(al) if isinstance(al, dict) else {}


def set_alias(key, text):
    """→ (ok, msg_key, data)。msg_key ∈ {'','ta_alias_too_long','ta_alias_clash'}；
       data = 撞名時的另一個 acc_key（頁面用它把衝突講清楚）。

    - 空白 → 刪除該筆（回落生成名），視為成功。
    - 撞名 → 擋下並回報衝突，**不靜默覆蓋**：兩個帳戶同名，與 #34 那個
      「REAL 永遠變 SIMULATE（會錯錢）」同屬一類錯誤。
    - 不轉錄、不截斷：截斷後兩個別名在畫面上會完全相同。
    """
    from gateway import state_store
    clean = _ALIAS_DROP.sub('', str(text or '')).strip()
    if not clean:
        reset_alias(key)
        return True, '', None
    if len(clean) > ALIAS_MAX:
        return False, 'ta_alias_too_long', None
    st = state_store.load_section(SECTION, {})
    aliases = st.get('aliases') if isinstance(st.get('aliases'), dict) else {}
    clash = next((k for k, v in aliases.items()
                  if k != key and str(v).strip() == clean), None)
    if clash:
        return False, 'ta_alias_clash', clash
    aliases[key] = clean
    st['aliases'] = aliases
    state_store.save_section(SECTION, st)
    return True, '', None


def reset_alias(key):
    from gateway import state_store
    st = state_store.load_section(SECTION, {})
    aliases = st.get('aliases') if isinstance(st.get('aliases'), dict) else {}
    if aliases.pop(key, None) is not None:
        st['aliases'] = aliases
        state_store.save_section(SECTION, st)


# ── 資金明細 ──
def _num_text(v):
    """數值 → 顯示字串；缺失／'N/A'／非數字 → DASH（0 是有效值，照實顯示）。"""
    if v is None:
        return DASH
    s = str(v).strip()
    if not s or s.upper() == 'N/A':
        return DASH
    try:
        n = float(s.replace(',', ''))
    except ValueError:
        return DASH
    return f'{n:,.2f}'


def fund_rows(info, error=''):
    """→ ([(label_key, 顯示值)], 是否有可用資料)。

    缺失一律 `—`，不填 0。`error` 為券商原文（由頁面原樣顯示，不改寫）。
    """
    info = info if isinstance(info, dict) else {}
    rows = [(FUND_LABEL_KEYS[k], _num_text(info.get(k))) for k in ACCINFO_KEYS]
    return rows, any(v != DASH for _, v in rows)


def currency_of(info):
    """資金幣種。富途從不傳 currency（實際以 HKD 回傳）、IB 的 per-tag currency 由
    `IBClient.account_info` 帶回 —— 兩者都可能是空字串，頁面就實顯示「未標明」。"""
    return str((info or {}).get('currency') or '').strip()


def markets_text(row):
    """`trdmarket_auth` 在富途是 list（['HK','US']）→ 渲染成 'HK・US'，不可出現 Python repr。"""
    v = row.get('trdmarket_auth')
    items = v if isinstance(v, (list, tuple)) else ([v] if v else [])
    return '・'.join(str(x).strip() for x in items if str(x).strip())


def env_inferred(row):
    """IB 的 REAL/SIMULATE 是推斷（DU 前綴慣例），不是 API 欄位 → 頁面要標明。"""
    return str(row.get('broker') or '').lower() == 'ib'


def env_conflict(row):
    """推斷出的 trd_env 與券商上報的 AccountType 明顯矛盾時為 True。

    只認「上報 PAPER 但推斷為 REAL」這一種無歧義的情況：IB 的 `AccountType` 取值
    並不保證包含 PAPER，其餘組合不能據以斷言矛盾。發現矛盾只標記，不靜默「修正」
    —— 改 trd_env 會改變量化頁實際下單的帳戶。
    """
    if not env_inferred(row):
        return False
    return 'PAPER' in str(row.get('acc_type') or '').upper() and \
        str(row.get('trd_env') or '').upper() == 'REAL'


# ── 非同步：逐券商取得與合併 ──
async def collect_accounts(client, brokers):
    """→ {'rows': [...], 'errors': {broker: 原文}, 'filtered': {broker: n}}。

    逐券商各一次呼叫（`BrokerClient` 的交易分派每次只針對一個 broker）。某家失敗只記
    原文、不影響另一家 —— 兩條取得互相獨立。
    """
    rows, errors, filtered = [], {}, {}
    for name in brokers:
        try:
            status, data, msg = await client.trade_accounts(broker=name)
        except Exception as e:            # 任何異常都如實歸因到該券商
            status, data, msg = False, None, str(e)
        if not status:
            errors[name] = str(msg or '')
            continue
        kept, n = active_only(normalise_rows(data or [], name))
        rows.extend(kept)
        filtered[name] = n
    return {'rows': rows, 'errors': errors, 'filtered': filtered}


async def refresh_funds(client, rows):
    """逐帳戶**循序**取得資金 → async generator，每筆 {'acc_key','info','error',
    'error_key','ts'}。

    不用 `asyncio.gather`：富途全部交易呼叫已在 `_trd_lock` + `to_thread` 串行、
    `accinfo_query` 有請求頻率限制；IB 共用一條 TWS session（clientId=99）。
    循序不損失吞吐，且錯誤可歸因到單一帳戶。逐筆 yield 讓卡片逐步填好，不必等全部。
    ⚠️ 帳戶為空時不得呼叫 `account_info`：`IBClient.account_info` 的
    `acct = str(account or '')` 會令 `ib.accountValues('')` 合併所有帳戶，會顯示錯帳戶的錢。
    """
    for row in rows:
        key = row.get('acc_key') or acc_key(row.get('broker', ''), row.get('acc_id', ''))
        acc = str(row.get('acc_id') or '').strip()
        if not acc:
            yield {'acc_key': key, 'info': None, 'error': '',
                   'error_key': 'ta_funds_no_account', 'ts': time.time()}
            continue
        try:
            status, info, msg = await client.account_info(
                account=acc, env=row.get('trd_env'), broker=row.get('broker'))
        except Exception as e:
            status, info, msg = False, None, str(e)
        yield {'acc_key': key,
               'info': info if status and isinstance(info, dict) else None,
               'error': '' if status else str(msg or ''),
               'error_key': '', 'ts': time.time()}
