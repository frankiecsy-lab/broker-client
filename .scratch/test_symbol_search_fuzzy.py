"""模糊搜尋「完全泛用化」斷言 — 直接打真實 index cache（唔使 OpenD、唔使 GUI）。

用戶要求（2026-10-08）：「我想打 NVD 或英偉 都能找出 US.NVDA；700 要看到結果包括 HK.00700，
打訊字就也能看到，任何一個字母或中文字都能找出相對應的。」

所以斷言分兩級：
- **rank 1**：明顯只有一個答案嘅 query（英偉 / 0070 / HK.007 / 騰 / 訊控）→ 必須排第一。
- **喺候選入面**：本質有歧義嘅 query（NVD 有 US.NVD、700 有 HK.87001、訊 有一堆「訊」頭公司）
  → 目標必須出現喺前 N 個候選（用戶要「睇到」，唔係要唯一）。
跑法：PYTHONIOENCODING=utf-8 py -3.14 .scratch/test_symbol_search_fuzzy.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.symbol_search import SymbolDirectory, CORE_TYPES  # noqa: E402

TOP = 50   # = symbol_input.CAND_LIMIT：dropdown 實際會顯示嘅候選數

# (query, 期望 code, 級別) — 級別 'top' = 必須 rank 1；'in' = 必須喺前 TOP 個
CASES = [
    # 用戶原句
    ('NVD', 'US.NVDA', 'in'),
    ('英偉', 'US.NVDA', 'top'),
    ('700', 'HK.00700', 'top'),
    ('訊', 'HK.00700', 'in'),
    # 零填充 / 大小寫 / 繁簡
    ('0700', 'HK.00700', 'top'),
    ('0070', 'HK.00700', 'top'),
    ('HK.007', 'HK.00700', 'top'),
    ('nvda', 'US.NVDA', 'in'),
    ('騰', 'HK.00700', 'top'),
    ('訊控', 'HK.00700', 'top'),
    ('英偉達', 'US.NVDA', 'top'),
    # 跳字（subsequence 兜底）
    ('NVA', 'US.NVDA', 'in'),      # 跳字兜底（'NDA' 呢類同時係別人嘅實 substring → 排唔入前 50 係正確）
    # 非股票：指數 / 期貨主連
    ('HSImain', 'HK.HSImain', 'top'),
    ('HSI', 'HK.HSImain', 'in'),
    # 英文名（index 得部分美股有 name_en — 見下面 LIMITS 說明）
    ('broadband', 'US.AABB', 'top'),
]

# 已知的**資料**限制（唔算 FAIL）：OpenD get_stock_basicinfo 回傳嘅 name 係單語言
# （實測：港股 / 中概 / 美股大市值一律中文名，name_en 淨返部分冷門美股）→ 打
# 'tencent' 呢類英文公司名**根本無資料可 match**，唔係排序問題。要支持就要第二份數據源。
LIMITS = [
    ('tencent', 'HK.00700'),
    ('apple', 'US.AAPL'),
]

FAILS = []


def rank_of(hits, code):
    for i, e in enumerate(hits):
        if e['code'].upper() == code.upper():
            return i + 1
    return 0


def main():
    d = SymbolDirectory()
    if not d.entries:
        print('❌ index cache 空 — 先喺有 OpenD 嘅環境跑一次 fetch 建 cache')
        return 1
    print(f'index: {len(d.entries)} entries（fetched_at={d.fetched_at}）')
    for q, want, level in CASES:
        t0 = time.perf_counter()
        hits = d.search(q, limit=TOP, types=None)   # types=None：斷言唔受種類過濾掩蓋
        ms = (time.perf_counter() - t0) * 1000
        r = rank_of(hits, want)
        if level == 'top':
            ok = r == 1
        else:
            ok = 1 <= r <= TOP
        got = hits[0]['code'] if hits else '（冇候選）'
        mark = '✅' if ok else '❌'
        print(f"{mark} '{q}' → {want} rank={r or '—'} / 共 {len(hits)} 候選 / 首位 {got} / {ms:.0f}ms")
        if not ok:
            FAILS.append(f"'{q}' 期望 {want} {level}，實際 rank={r} 首位={got}")
            print('    候選:', ' '.join(e['code'] for e in hits[:10]))

    # 單個字母 / 單個中文字都要有結果（用戶：任何一個字母或中文字都能找出相對應的）
    for q in ('A', 'K', '訊', '股'):
        hits = d.search(q, limit=TOP, types=None)
        ok = bool(hits)
        print(f"{'✅' if ok else '❌'} 單字 '{q}' → {len(hits)} 個候選（首位 {hits[0]['code'] if hits else '—'}）")
        if not ok:
            FAILS.append(f"單字 '{q}' 冇任何候選")

    for q, want in LIMITS:
        r = rank_of(d.search(q, limit=TOP, types=None), want)
        print(f"⚠️  資料限制 '{q}' → {want} rank={r or '—'}（index 冇英文名，非排序問題）")

    # CORE_TYPES 預設唔應該被窩輪污染（autocomplete 路徑）
    hits = d.search('HSI', limit=TOP)
    bad = [e['code'] for e in hits if e.get('type') not in CORE_TYPES]
    print(f"{'✅' if not bad else '❌'} CORE_TYPES 過濾：污染 {len(bad)} 條")
    if bad:
        FAILS.append(f'CORE_TYPES 泄漏: {bad[:5]}')

    print('\n' + ('🎉 全部通過' if not FAILS else '❌ %d FAIL:\n  ' % len(FAILS) + '\n  '.join(FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
