"""標的收藏 — 統一本地 JSON 儲存（state_store section 'favorites'）。

- entry = {code, market, type, added}：code 為 key（原樣保留 — canonical 大細階由頁經 index 還原；
  dedupe 大細階唔敏感）；market/type 喺
  **新增時**由本地 index snapshot（index 冇 → market 由 code prefix 兜底、type 'UNKNOWN'）。
  name 唔存 — 顯示名永遠跟 UI 語言由 index 即時解析（index 冇就如實 '—'）。
- 其他功能（行情 watchlist / K線捷徑 / 交易捷徑…）以後直接 `load_items()` 就得 —
  呢度係收藏嘅唯一事實來源，唔另開檔（AGENTS：統一一個文件儲存）。

格式：{'items': [...], 'market': 'ALL', 'type': 'ALL'}（market/type = 收藏頁 filter 記憶）。
"""
import datetime
import re

SECTION = 'favorites'


def load_items():
    """→ list[dict]（copy）；保序（加入順序）。"""
    from gateway import state_store
    items = state_store.load_section(SECTION, {}).get('items')
    return [dict(e) for e in items] if isinstance(items, list) else []


def load_filter():
    from gateway import state_store
    st = state_store.load_section(SECTION, {})
    return st.get('market'), st.get('type')


def save_filter(market, type_):
    from gateway import state_store
    st = state_store.load_section(SECTION, {})
    st['market'], st['type'] = market, type_
    state_store.save_section(SECTION, st)


def _market_from_code(code):
    m = re.match(r'^(HK|US)\.', code)
    return m.group(1) if m else ''


def add(code, entry=None):
    """新增收藏。entry = 本地 index 嘅 entry（有就 snapshot market/type）；None → prefix 兜底。
    🤖 code 原樣保留（唔准 upper）— canonical 大細階（期貨主連 HK.HSImain）由頁經 index 還原，
    store 再 upper 會整爛。dedupe 先係大細階唔敏感。
    → (ok, msg_key, data)：msg_key ∈ {'fav_added','fav_dup'}（i18n key，由頁 t()）。"""
    from gateway import state_store
    code = str(code).strip()
    st = state_store.load_section(SECTION, {})
    items = st.get('items') if isinstance(st.get('items'), list) else []
    if any(str(e.get('code', '')).upper() == code.upper() for e in items):
        return False, 'fav_dup', None
    item = {'code': code,
            'market': (entry or {}).get('market') or _market_from_code(code),
            'type': (entry or {}).get('type') or 'UNKNOWN',
            'added': datetime.date.today().isoformat()}
    items.append(item)
    st['items'] = items
    state_store.save_section(SECTION, st)
    return True, 'fav_added', item


def remove(codes):
    """刪除一批 code（大細階唔敏感）→ 剩低嘅 items。"""
    from gateway import state_store
    drop = {str(c).strip().upper() for c in codes}
    st = state_store.load_section(SECTION, {})
    items = st.get('items') if isinstance(st.get('items'), list) else []
    items = [e for e in items if str(e.get('code', '')).upper() not in drop]
    st['items'] = items
    state_store.save_section(SECTION, st)
    return items
