# -*- coding: utf-8 -*-
"""持倉模型（長倉／短倉／雙向）— 回測頁同量化交易頁共用嘅**單一事實來源**。

純計算、冇 Qt、冇 broker：食「訊號方向（B/S）+ 目前持倉方向 + 模式」→ 講應該做乜
（`open` / `close` / `flip` / `ignore`）。回測同即時落單都靠呢個狀態機決定持倉，所以
「同一個策略喺回測同即時嘅持倉行為一致」係由**結構**保證，唔靠兩份 if/else 對口徑。

## 三種模式（用戶：「長倉，短倉，雙向，BACKTEST 都可以選」）
* `long`：只做多。空倉遇 B 開倉；持倉遇 S 全平。持倉中 B / 空倉 S → 忽略並如實計數。
* `short`：只做空（沽空）。空倉遇 S 開空倉；持空倉遇 B 全平。持空中 S / 空倉 B → 忽略。
* `both`：長短雙向。訊號方向同持倉相反 → **反手 = 兩筆**（先平後開，成本四邊如實計）；
  同向 → 忽略（單倉模型，唔加倉）。

⚠️ 反手喺**同一根**完成（平舊倉 + 開新倉都用訊號根收盤）— 同 `strategies.trade_marks`
「成交價 = 訊號根收盤、冇未來函數」嘅語義一致。
⚠️ 價格有效性（價 ≤ 0 → 開唔到倉）屬**資料**問題，留喺調用方；呢度得方向狀態。
⚠️ `step()` **唔改**入參 `state`，只回新狀態 → 調用方若唔接納呢個訊號（例如價唔啱）就
唔好 commit 新狀態，否則持倉方向會同實際成交脫節。
"""

MODE_LONG, MODE_SHORT, MODE_BOTH = 'long', 'short', 'both'
MODES = (MODE_LONG, MODE_SHORT, MODE_BOTH)
MODE_DEFAULT = MODE_LONG

DIR_LONG, DIR_SHORT = 'long', 'short'
SIDE_B, SIDE_S = 'B', 'S'

ACT_OPEN, ACT_CLOSE, ACT_FLIP, ACT_IGNORE = 'open', 'close', 'flip', 'ignore'

# 忽略原因 key（i18n 三語；同回測被忽略表共用 — 兩頁要講同一件事）
IG_INPOS, IG_FLAT = 'bt_ig_inpos', 'bt_ig_flat'
IG_BADPRICE = 'bt_ig_badprice'   # 由調用方舉（價 ≤ 0 屬資料問題，`step()` 唔食價）

# 邊一側可以喺呢個模式開倉（另一側只可能係平倉／反手）
_ENTRY_SIDE = {MODE_LONG: (SIDE_B,), MODE_SHORT: (SIDE_S,), MODE_BOTH: (SIDE_B, SIDE_S)}


def clamp_mode(v):
    """任何輸入 → 合法 mode（唔識就退回預設；頁/設定檔俾乜都唔會炸）。"""
    s = str(v or '').strip().lower()
    return s if s in MODES else MODE_DEFAULT


def new_state():
    """空倉狀態。"""
    return {'dir': None}


def dir_of_side(side):
    """訊號方向 → 倉位方向（B → 多、S → 空）。"""
    return DIR_LONG if side == SIDE_B else DIR_SHORT


def sign(direction):
    """倉位方向 → 報酬符號（多 +1 / 空 −1 / 無倉 0）。"""
    if direction == DIR_LONG:
        return 1
    if direction == DIR_SHORT:
        return -1
    return 0


def step(state, side, mode):
    """(目前持倉, 訊號 B/S, 模式) → (action, 新狀態, 忽略原因 key 或 None)。

    只決定**方向狀態**；成交價、成本、結算全部由調用方（回測引擎／執行層）負責。
    """
    mode = clamp_mode(mode)
    cur = (state or new_state()).get('dir')
    want = dir_of_side(side)
    if side not in _ENTRY_SIDE[mode]:   # 呢個模式唔會由呢邊開倉 → 只可能平倉／反手
        want = None
    if cur is None:
        if want is None:
            return ACT_IGNORE, new_state(), IG_FLAT
        return ACT_OPEN, {'dir': want}, None
    if cur == want:
        return ACT_IGNORE, {'dir': cur}, IG_INPOS
    if want is None:
        return ACT_CLOSE, new_state(), None
    return ACT_FLIP, {'dir': want}, None
