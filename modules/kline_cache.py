# -*- coding: utf-8 -*-
"""K 線取數快取（LRU + TTL）— 換週期 / 換返睇過嘅標的時即刻有圖，唔使由零再取一次歷史。

⚠️ 只喺 GUI thread 讀寫（`quotes_page` / `gui_kline`）→ 唔使 lock：worker thread 淨係 send df，
   轉 row tuples 已經喺 GUI thread 做緊。
value 直接係 `KlineChart.set_bars` 食嘅 tuples（舊→新），唔 store DataFrame → 唔食 pandas 記憶體。
key 唔包 kline_num：同一 (code,ktype) 邊取邊覆寫，最長嗰次就係最完整嗰次；而 chart 一次本來
   就只畫 `KlineChart.MAX_DRAW` 根，少咗嘅只係 zoom-out 深度。
"""
import time
from collections import OrderedDict

MAX_ENTRIES = 32   # 每條 = 一個 (code,ktype) 完整快照；1000 根 ≈ 200KB → 上限 ~6MB

# TTL 按週期：越短嘅 bar 越快被新 bar 蓋過，太長會俾用戶睇到過時嘅圖
_TTL = {'K_1M': 30, 'K_3M': 30, 'K_5M': 30,
        'K_15M': 120, 'K_30M': 120, 'K_60M': 120}
_TTL_DEFAULT = 600   # 日 / 週 / 月

_cache = OrderedDict()   # key -> (expire_ts, rows)


def _key(code, ktype):
    return (str(code).upper(), str(ktype))


def get(code, ktype):
    """命中 → row tuples（照 `set_bars` 嘅形狀，直接可上圖）；過期 / 冇 → None。"""
    hit = _cache.get(_key(code, ktype))
    if hit is None:
        return None
    expire, rows = hit
    if time.monotonic() >= expire:
        _cache.pop(_key(code, ktype), None)
        return None
    _cache.move_to_end(_key(code, ktype))
    return rows


def put(code, ktype, rows):
    """寫入 / 覆寫並續期（live tick 都走呢度 → 快取永遠係最新見到嘅快照）。"""
    if not rows:
        return
    _cache[_key(code, ktype)] = (time.monotonic() + _TTL.get(str(ktype), _TTL_DEFAULT), rows)
    _cache.move_to_end(_key(code, ktype))
    while len(_cache) > MAX_ENTRIES:   # LRU  eviction：最耐冇用嗰條先走
        _cache.popitem(last=False)


def clear():
    _cache.clear()


def entries():
    """量測 / 測試用：而家存咗幾多條。"""
    return len(_cache)
