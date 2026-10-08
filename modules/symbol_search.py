# 🤖 P8: 本地 symbol 目錄 + fuzzy search（GUI 標的欄 autocomplete / CLI fetch）。
#
# 數據源 = Futu OpenD get_stock_basicinfo(market, stock_type) 枚舉 — probe 實測（test/probe_symbol_names.py）：
#   - 本 SDK 版本（futu-api 10.05.6508）冇 search_quote / get_stock_screen → 本地 index 係唯一搜尋路；
#     miss 嘅輸入照原樣 pass-through（autocomplete 永遠唔 block 輸入）。
#   - name 欄單語言：美股 = 英文、港股/期貨 = 中文 → 分存 name_zh / name_en，顯示缺邊個語言 fallback 返原生名。
#     OpenD 回傳嘅中文名繁簡視版本/locale 而定（實測有繁體 cache）→ **load/fetch 時經 opencc 繁→簡統一正規化**：
#     search 將 query 正規化做簡體先 match；EN fallback 顯示原生（簡體）；zh 顯示時 s2t 轉返繁體。
#
# Scope：股票 / ETF / 指數 / 期貨 main contract / 窩輪（HK+US 實測 ~15.7k，delisting 跳過）。
#   窩輪只俾標的列表頁（search 預設 types=CORE_TYPES 排除 → 交易/行情頁 autocomplete 照舊淨返核心）。
#   期權：OpenD get_stock_basicinfo 唔支援枚舉（DRVT interface not supported）、IB 唔可以無標的枚舉
#   → 入唔到 index（用戶打準確 code 一樣 pass-through 行得通）。牛熊證（BWRT）呢個 OpenD 回 0。
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
    _T2S = OpenCC('t2s')   # 繁 → 簡（canonical 正規化：index name + search query）
    _S2T = OpenCC('s2t')   # 簡 → 繁（zh 顯示）
except ImportError:        # opencc 唔係硬依賴 — 冇就跳過繁簡轉換（match 變 script-sensitive）
    _T2S = _S2T = None


from futu import OpenQuoteContext, RET_OK, Market, SecurityType

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'symbol_index.json')
STALE_HOURS = 24           # startup auto-fetch 閾值（cache 唔存在 / 超過呢個時數 → 重新 fetch）

# 🤖 每個 plan key → (Market enum, SecurityType) 對。HK_FUTURE 係獨立 market enum（probe 實測）。
#   WARRANT 只入 index 俾列表頁（search 預設 CORE_TYPES 排除 — 見 search() types 參數）。
MARKET_PLAN = {
    'US': [(Market.US, SecurityType.STOCK), (Market.US, SecurityType.ETF),
           (Market.US, SecurityType.IDX), (Market.US, SecurityType.FUTURE),
           (Market.US, SecurityType.WARRANT)],
    'HK': [(Market.HK, SecurityType.STOCK), (Market.HK, SecurityType.ETF),
           (Market.HK, SecurityType.IDX), (Market.HK_FUTURE, SecurityType.FUTURE),
           (Market.HK, SecurityType.WARRANT)],
}
# search() 預設種類（autocomplete 唔想被 ~15.7k 窩輪污染）；列表頁傳 types=None 攞全量。
CORE_TYPES = ('STOCK', 'ETF', 'IDX', 'FUTURE')

_MONTH_SUFFIX = re.compile(r'\s*\(\d{4}\)$')          # "恒指期货主连 (2610)" → 去尾綴
_FUT_CODE = re.compile(r'^(.*?)(\d{4})$')             # HK.HSI2610 → prefix 'HK.HSI'

# search() 分層分（高者先；0 = 唔 match）。🤖 ticker（'US.NVDA' 嘅 'NVDA'）必須獨立於成串 code 計 —
#   用戶打嘅係 ticker、唔帶市場前綴；淨計 `ql in code` 會令 NVDA 食最低級 contains 分，打 'N' 即刻俾幾千條淹沒。
_S_CODE_EXACT = 100      # 'US.NVDA' / 'NVDA'
_S_TICKER_PREFIX = 90    # 'NVD' → NVDA
_S_CODE_PREFIX = 80      # 'HK.007' → HK.00700
_S_NAME_PREFIX = 75      # '英偉' → 英伟达
_S_SUBSTR = 70           # ticker/code contains（+ position bonus）
_S_NAME_SUBSTR = 55      # name contains（+ position bonus）
_S_TICKER_SUBSEQ = 40    # 跳字：'NVA' → NVDA
_S_NAME_SUBSEQ = 35      # 跳字：'訊控' → 腾讯控股 呢類（非連續都中）


def _has_cjk(s):
    return any('一' <= ch <= '鿿' for ch in s)


def _pos_bonus(pos):
    """match 位置越前 → 加分越多（最多 +9）：打 '700' 要 HK.00700 排喺 HK.02700 前面。"""
    return max(0, 9 - pos) if pos >= 0 else 0


def _subseq_pos(hay, needle):
    """needle 逐字**順序**出現喺 hay（唔使連續）→ 首字位置；冇 → -1。
       🤖 泛用化：打 'NVA'（少咗個 D）都要搵到 NVDA。"""
    if not needle:
        return -1
    p = hay.find(needle[0])
    if p < 0:
        return -1
    first = p
    for ch in needle[1:]:
        p = hay.find(ch, p + 1)
        if p < 0:
            return -1
    return first


def _normalize_cjk(entry):
    """entry 嘅 CJK name 欄統一正規化做簡體（in-place）。OpenD 回傳繁/簡視版本/locale 而定 —
       canonical 存簡體先至 search t2s match / EN fallback / zh s2t 顯示三條路全部成立。
       opencc 缺席時 no-op（保持原行為）；純英文 entry 唔 touch。"""
    if _T2S is None:
        return entry
    for k in ('name', 'name_zh', 'name_en'):
        v = entry.get(k, '')
        if v and _has_cjk(v):
            entry[k] = _T2S.convert(v)
    return entry


def _load_futu_config():
    """同 broker.py 同一個 config.json（modules/ 目錄）— futu host/port 一來源。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cfg = (json.load(f).get('futu') or {})
    except (OSError, ValueError):
        cfg = {}
    return str(cfg.get('host', '127.0.0.1')), int(cfg.get('port', 11111))


def to_simplified(s):
    """任意字串 → 簡體（opencc 缺席 / 無 CJK → no-op）。列表頁下鑽 substring 過濾用：
       🤖 實測 s2t 將「汇丰」顯示做「滙豐」（港式）— 用戶打「匯豐」兩邊都 t2s 先至 match 到。"""
    return _T2S.convert(s) if (_T2S is not None and s and _has_cjk(s)) else s


def display_for(entry, lang='zh'):
    """entry → 指定語言嘅顯示名：該語言欄有值就用，冇就 fallback 原生 name（單語言市場唔會兩邊都空）。
       🤖 lang 直接食 GUI 語言碼：'zh_hk' → name_zh 再 s2t 轉繁體（港式）；'zh_cn' → 簡體原樣（唔轉）；
       'en' → name_en fallback 原生；legacy 'zh' 當 'zh_hk'（舊调用保兼容）。
       GUI completer / sym_label / 列表頁 / 收藏頁共用呢把尺 — 唔准喺呼叫端自己 collapse 語言。"""
    if lang == 'en':
        return entry.get('name_en') or entry.get('name', '')
    name = entry.get('name_zh') or entry.get('name', '')
    if lang != 'zh_cn' and _S2T is not None and _has_cjk(name):
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
            # 舊 cache 可能存繁體（OpenD locale 差異）— load 時統一正規化做簡體（~0.2s / 25k entries）
            self.entries = [_normalize_cjk(e) for e in obj.get('entries', [])]
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
                        if not code or bool(row.get('delisting')):   # 已退市唔入 index（窩輪特别多）
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
                        _normalize_cjk(entry)   # canonical 簡體 — cache 統一 script（OpenD 回繁/簡都一樣）
                        if stype == SecurityType.WARRANT:
                            # 窩輪下鑽資料（列表頁三級導航）：所屬標的 + 類別 + 行使價/到期日。
                            # 🤖 實測：HK stock_owner 100% 有值、stock_child_type=CALL/PUT/BULL/BEAR；
                            #    US 窩輪兩者皆 'N/A.'（無所屬數據 → 列表頁如實 fallback 平鋪）。
                            owner = str(row.get('stock_owner') or '').strip()
                            entry['owner'] = '' if owner.upper().rstrip('.') in ('', 'N/A') else owner
                            entry['wtype'] = str(row.get('stock_child_type') or '').strip().upper()
                            entry['expiry'] = str(row.get('strike_time') or '')
                            entry['strike'] = row.get('strike_price')
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
    def _score(self, e, ql, q_cjk):
        """entry → 分層分（見 _S_* 常量；0 = 唔 match）。
           ticker 同 name 兩條路都要 position bonus — 同樣 contains，match 越靠前越似用戶想要嘅嗰隻。"""
        code = e['code'].upper()
        ticker = code.split('.', 1)[1] if '.' in code else code
        # 🤖 HK 代碼 5 位零填充 — 用戶打 '700'/'0700' 都係指 00700 → 去零版本一齊計（先至排得入第一）
        variants = [ticker]
        if ticker[:1].isdigit():
            variants.append(ticker.lstrip('0'))
        if code == ql or any(v == ql for v in variants):
            return _S_CODE_EXACT
        if any(v.startswith(ql) for v in variants):
            return _S_TICKER_PREFIX
        if code.startswith(ql):
            return _S_CODE_PREFIX
        ps = [v.find(ql) for v in variants + [code]]
        ps = [p for p in ps if p >= 0]
        if ps:
            return _S_SUBSTR + _pos_bonus(min(ps))
        nz, ne = e.get('name_zh', ''), e.get('name_en', '')
        if q_cjk:
            p = nz.find(q_cjk)
            if p == 0:
                return _S_NAME_PREFIX
            if p > 0:
                return _S_NAME_SUBSTR + _pos_bonus(p)
        if ne:
            p = ne.lower().find(ql.lower())
            if p == 0:
                return _S_NAME_PREFIX
            if p > 0:
                return _S_NAME_SUBSTR + _pos_bonus(p)
        p = _subseq_pos(ticker, ql)
        if p >= 0:
            return _S_TICKER_SUBSEQ + _pos_bonus(p)
        if q_cjk:
            p = _subseq_pos(nz, q_cjk)
            if p >= 0:
                return _S_NAME_SUBSEQ + _pos_bonus(p)
        if ne:
            p = _subseq_pos(ne.lower(), ql.lower())
            if p >= 0:
                return _S_NAME_SUBSEQ + _pos_bonus(p)
        return 0

    def search(self, query, limit=20, types=CORE_TYPES):
        """fuzzy match，分層排序（高者先）：code/ticker exact > **ticker prefix** > code prefix
           > name prefix > ticker/code contains > name contains > **跳字 subsequence**（'NVA' → NVDA）。
           🤖 完全泛用化（用戶要求）：打任何一段字母/中文都要返對應標的 — 所以 ticker 獨立計分
           （用戶打 'NVD' 唔會帶 'US.' 前綴）＋ position bonus（'700' 先 HK.00700 後 HK.02700）
           ＋ 跳字兜底。CJK query 先經 t2s 正規化（打繁體都 match 到簡體 name）。
           types：只 match 呢啲 SecurityType（預設 CORE_TYPES — autocomplete 唔被窩輪污染）；None = 全量（列表頁）。
           成本：全表 ~40k 條含跳字 ≈ 20ms（實測）→ 經 GUI debounce，唔會卡。"""
        q = str(query).strip()
        if not q or not self.entries:
            return []
        ql = q.upper()
        # CJK：query 轉簡體做 match key；英文部分照舊 upper
        q_cjk = _T2S.convert(q) if (_T2S is not None and _has_cjk(q)) else q
        scored = []
        for e in self.entries:
            if types is not None and e.get('type') not in types:
                continue
            s = self._score(e, ql, q_cjk)
            if s:
                scored.append((s, e))
        scored.sort(key=lambda t: (-t[0], t[1]['code']))
        return [e for _, e in scored[:limit]]

    def get(self, code):
        """O(1) 精確 code lookup（大細階唔敏感）→ entry（canonical 大細階，期貨主連 HK.HSImain
        細階 main 靠呢度還原）or None。"""
        return self._by_code.get(str(code).strip().upper())

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


# ── 期權鏈（列表頁二級下鑽）────────────────────────────────────────
# 🤖 probe 實測：冇任何接口可以「邊個有期權」一次枚舉 → 只能逐標的即時查。
#   get_option_expiration_date 日期喺 strike_time 欄；get_option_chain start/end 跨度 ≤30 日
#   → 逐到期日各 call 一次。🤖 配額實測：get_option_chain 每 30 秒最多 10 次（超咗 RET_ERROR
#   「頻率太高」，連 NVDA 26 個到期日都要 3 個窗口 ~60 秒）→ 模組級滑動視窗節流（跨 call 共用，
#   連續查兩個標的都唔會食底食配額），失敗如實入 msg（全失敗先 ok=False）。
_CHAIN_STAMPS = []   # 滑動 30 秒視窗內嘅 get_option_chain call 時間（配額 10 次/30s，全程序共用）


def _chain_throttle():
    now = time.monotonic()
    _CHAIN_STAMPS[:] = [t for t in _CHAIN_STAMPS if now - t < 30.0]
    if len(_CHAIN_STAMPS) >= 10:
        time.sleep(30.0 - (now - _CHAIN_STAMPS[0]) + 0.2)
    _CHAIN_STAMPS.append(time.monotonic())


def fetch_option_chain(code, progress_cb=None, max_dates=30):
    """(ok, rows, msg) — 即時攞指定標的全部到期日嘅期權鏈（10 次/30 秒節流）。
       rows = [{code,name,otype,strike,expiry,lot_size,owner}]（otype = CALL/PUT）。
       progress_cb(i, total, date) 俾 GUI 顯示進度；max_dates 上限防超長鏈拖死 UI。"""
    host, port = _load_futu_config()
    ctx = OpenQuoteContext(host=host, port=port)
    try:
        ret, data = ctx.get_option_expiration_date(str(code).strip().upper())
        if ret != RET_OK or data is None or len(data) == 0:
            return False, [], str(data if data is not None else 'empty')[:120]
        dates = [str(d) for d in data['strike_time']][:max_dates]
        rows = []
        errs = []
        for i, d in enumerate(dates):
            if progress_cb:
                progress_cb(i + 1, len(dates), d)
            _chain_throttle()
            ret, chain = ctx.get_option_chain(str(code).strip().upper(),
                                              start=d, end=d, option_type='ALL')
            if ret != RET_OK or chain is None:
                errs.append(str(chain))
                continue   # 個別到期日失敗唔殺全鏈 — 跳過，msg 如實 count
            for r in chain.to_dict('records'):
                rows.append({'code': str(r.get('code', '')), 'name': str(r.get('name', '')),
                             'otype': str(r.get('option_type', '')), 'strike': r.get('strike_price'),
                             'expiry': str(r.get('strike_time', '')), 'lot_size': r.get('lot_size'),
                             'owner': str(r.get('stock_owner', ''))})
        msg = f'{len(dates)} 個到期日 / {len(rows)} 條'
        if errs:
            msg += f'（{len(errs)} 個失敗：{errs[0][:60]}）'
        return (bool(rows) or not errs), rows, msg   # 全部失敗 → ok=False 如實報
    finally:
        ctx.close()


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
