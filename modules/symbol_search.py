# 🤖 P8: 本地 symbol 目錄 + fuzzy search（GUI 標的欄 autocomplete / CLI fetch）。
#
# 數據源 = Futu OpenD get_stock_basicinfo(market, stock_type) 枚舉 — probe 實測（test/probe_symbol_names.py）：
#   - 本 SDK 版本（futu-api 10.05.6508）冇 search_quote / get_stock_screen → 本地 index 係唯一搜尋路；
#     miss 嘅輸入照原樣 pass-through（autocomplete 永遠唔 block 輸入）。
#   - name 欄單語言：美股 = 英文、港股/期貨 = 中文（簡體）→ 分存 name_zh / name_en，顯示缺邊個語言 fallback 返原生名。
#     opencc 處理繁簡雙向：search 將 query 正規化做簡體先 match；zh 顯示時轉返繁體。
#
# Scope（用戶確認）：股票 / ETF / 指數 / 期貨 main contract。窩輪/牛熊證/期權太動態太海量，唔入 index
#   （用戶打準確 code 一樣 pass-through 行得通）。
#
# 期貨：只留 main_contract=True 嘅 row + 由月份 code 合成 canonical 'XXmain'（HK.HSI2610 → HK.HSImain —
#   Futu naming convention，probe 實測 HSImain/NQmain 係有效 code）；name 去掉 " (2610)" 月份尾綴。
#
# 用法：
#   CLI: python -m modules.symbol_search fetch [--markets US,HK]
#        python -m modules.symbol_search search <query>
#   GUI: get_directory().search(q) / .fetch(markets, progress_cb=...)

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

try:
    from opencc import OpenCC
    _T2S = OpenCC('t2s')   # 繁 → 簡（search query 正規化 — HK name 存簡體）
    _S2T = OpenCC('s2t')   # 簡 → 繁（zh 顯示）
except ImportError:        # opencc 唔係硬依賴 — 冇就跳過繁簡轉換（match 變 script-sensitive）
    _T2S = _S2T = None

from futu import OpenQuoteContext, RET_OK, Market, SecurityType

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'symbol_index.json')
STALE_HOURS = 24           # startup auto-fetch 閾值（cache 唔存在 / 超過呢個時數 → 重新 fetch）

# 🤖 每個 plan key → (Market enum, SecurityType) 對。HK_FUTURE 係獨立 market enum（probe 實測）。
MARKET_PLAN = {
    'US': [(Market.US, SecurityType.STOCK), (Market.US, SecurityType.ETF),
           (Market.US, SecurityType.IDX), (Market.US, SecurityType.FUTURE)],
    'HK': [(Market.HK, SecurityType.STOCK), (Market.HK, SecurityType.ETF),
           (Market.HK, SecurityType.IDX), (Market.HK_FUTURE, SecurityType.FUTURE)],
}

_MONTH_SUFFIX = re.compile(r'\s*\(\d{4}\)$')          # "恒指期货主连 (2610)" → 去尾綴
_FUT_CODE = re.compile(r'^(.*?)(\d{4})$')             # HK.HSI2610 → prefix 'HK.HSI'


def _has_cjk(s):
    return any('一' <= ch <= '鿿' for ch in s)


def _load_futu_config():
    """同 broker.py 同一個 config.json（modules/ 目錄）— futu host/port 一來源。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cfg = (json.load(f).get('futu') or {})
    except (OSError, ValueError):
        cfg = {}
    return str(cfg.get('host', '127.0.0.1')), int(cfg.get('port', 11111))


def display_for(entry, lang='zh'):
    """entry → 指定語言嘅顯示名：該語言欄有值就用，冇就 fallback 原生 name（單語言市場唔會兩邊都空）；
       zh 模式將簡體轉返繁體（opencc）。GUI completer / sym_label 共用呢把尺。"""
    pref = entry.get('name_zh') if lang == 'zh' else entry.get('name_en')
    name = pref or entry.get('name', '')
    if lang == 'zh' and _S2T is not None and _has_cjk(name):
        return _S2T.convert(name)
    return name


class SymbolDirectory:
    """本地 symbol index：fetch() 從 OpenD 枚舉建 cache，search() 純本地 fuzzy match（唔打網絡）。"""

    def __init__(self, cache_path=CACHE_PATH):
        self.cache_path = cache_path
        self.entries = []       # [{code, name, market, type, name_zh, name_en}, ...]
        self.fetched_at = None  # ISO string / None（cache 從未建過）
        self._by_code = {}      # code.upper() → entry（O(1) display_name — GUI 每次 keypress 都 call）
        self._load()

    def _reindex(self):
        self._by_code = {e['code'].upper(): e for e in self.entries}

    def has_code(self, code):
        return str(code).strip().upper() in self._by_code

    # ── cache I/O ────────────────────────────────────────────────
    def _load(self):
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                obj = json.load(f)
            self.entries = obj.get('entries', [])
            self.fetched_at = obj.get('fetched_at')
        except (OSError, ValueError):
            pass   # 首次運行 / cache 壞 — 當空 index（fetch 會重建）
        self._reindex()

    def _save(self):
        tmp = self.cache_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'fetched_at': self.fetched_at, 'entries': self.entries}, f, ensure_ascii=False)
        os.replace(tmp, self.cache_path)   # atomic — 唔會留半份 cache

    @property
    def is_stale(self):
        if not self.fetched_at:
            return True
        try:
            # fetched_at 存咗 tz offset（astimezone().isoformat）— 比較用 aware now，唔好混 naive/aware
            return datetime.now().astimezone() - timedelta(hours=STALE_HOURS) > datetime.fromisoformat(self.fetched_at)
        except ValueError:
            return True

    # ── fetch（blocking — GUI 喺 QThread 入面 call）──────────────
    def fetch(self, markets=('US', 'HK'), progress_cb=None):
        """枚舉指定 market 嘅全部 code，合併返 index（其他 market 舊 entry 保留）。回傳 (ok, message)。"""
        host, port = _load_futu_config()
        ctx = OpenQuoteContext(host=host, port=port)
        try:
            fresh = []
            seen = set()
            for m in markets:
                plan = MARKET_PLAN.get(str(m).upper())
                if plan is None:
                    return False, f"unknown market [{m}]，有效：{sorted(MARKET_PLAN)}"
                for market_enum, stype in plan:
                    label = f"{m}/{stype}"   # 🤖 SDK 嘅 SecurityType 係 class-attr string（唔係 enum）— 直接用值
                    ret, data = ctx.get_stock_basicinfo(market=market_enum, stock_type=stype)
                    if ret != RET_OK or data is None:
                        return False, f"{label}: {str(data)[:120]}"
                    kept = 0
                    for row in data.to_dict('records'):
                        code = str(row.get('code', '')).strip()
                        name = _MONTH_SUFFIX.sub('', str(row.get('name', '')).strip())
                        if not code:
                            continue
                        if stype == SecurityType.FUTURE:
                            # 期貨只留 main contract + 合成 canonical 'XXmain'（唔 hardcode 月份 — 由 live data 推）
                            if not bool(row.get('main_contract')):
                                continue
                            mm = _FUT_CODE.match(code)
                            code = f"{mm.group(1)}main" if mm else code
                        if code in seen:
                            continue
                        seen.add(code)
                        entry = {'code': code, 'name': name, 'market': str(m).upper(),
                                 'type': str(stype), 'name_zh': '', 'name_en': ''}
                        if _has_cjk(name):
                            entry['name_zh'] = name
                        else:
                            entry['name_en'] = name
                        fresh.append(entry)
                        kept += 1
                    if progress_cb:
                        progress_cb(label, kept)
            # merge：fetch 咗嘅 market 用新 data，其他 market 保留舊 cache（partial refresh 安全）
            fetched_keys = {str(m).upper() for m in markets}
            self.entries = [e for e in self.entries if e.get('market') not in fetched_keys] + fresh
            self.fetched_at = datetime.now().astimezone().isoformat(timespec='seconds')
            self._reindex()
            self._save()
            return True, f"{len(self.entries)} symbols（{', '.join(sorted(fetched_keys))} 更新）"
        finally:
            ctx.close()

    # ── search（純本地，唔打網絡）────────────────────────────────
    def search(self, query, limit=20):
        """fuzzy match：code exact > code prefix > name startswith > code substring ≈ name substring。
           CJK query 先經 t2s 正規化（用戶打繁體都 match 到簡體 name）。回傳 entry list（已排序、截斷 limit）。"""
        q = str(query).strip()
        if not q or not self.entries:
            return []
        ql = q.upper()
        # CJK：query 轉簡體做 match key；英文部分照舊 upper
        q_cjk = _T2S.convert(q) if (_T2S is not None and _has_cjk(q)) else q
        scored = []
        for e in self.entries:
            cl = e['code'].upper()
            score = 0
            if cl == ql:
                score = 100
            elif cl.startswith(ql):
                score = 80
            elif ql in cl:
                score = 60
            else:
                nz, ne = e.get('name_zh', ''), e.get('name_en', '')
                if q_cjk and q_cjk in nz:
                    score = 70 if nz.startswith(q_cjk) else 50
                elif ne and ql.lower() in ne.lower():
                    score = 70 if ne.lower().startswith(ql.lower()) else 50
            if score:
                scored.append((score, e))
        scored.sort(key=lambda t: (-t[0], t[1]['code']))
        return [e for _, e in scored[:limit]]

    def display_name(self, code, lang='zh'):
        """按語言返顯示名（O(1) — GUI 每次 keypress / sym_label update 都 call）。"""
        entry = self._by_code.get(str(code).strip().upper())
        return display_for(entry, lang) if entry else ''


_dir = None

def get_directory():
    """module-level singleton — CLI / GUI 共用同一份 index。"""
    global _dir
    if _dir is None:
        _dir = SymbolDirectory()
    return _dir


# ── CLI entry：python -m modules.symbol_search fetch [--markets US,HK] / search <query> ──
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    d = get_directory()
    cmd = argv[0] if argv else 'fetch'

    if cmd == 'fetch':
        markets = ('US', 'HK')
        if '--markets' in argv:
            i = argv.index('--markets')
            markets = tuple(x.strip().upper() for x in argv[i + 1].split(',') if x.strip()) or ('US', 'HK')
        t0 = time.time()

        def cb(label, count):
            print(f"  {label}: {count} rows", flush=True)

        ok, msg = d.fetch(markets=markets, progress_cb=cb)
        print(("OK   " if ok else "FAIL ") + f"{msg} ({time.time() - t0:.1f}s)", flush=True)
        return 0 if ok else 1

    if cmd == 'search':
        q = argv[1] if len(argv) > 1 else ''
        hits = d.search(q)
        for e in hits:
            print(f"{e['code']:<24} {e['name']}")
        print(f"({len(hits)} hits)", flush=True)
        return 0

    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main())
