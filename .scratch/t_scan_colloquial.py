"""掃描 i18n 文案中的粵語口語殘留與字形殘留（ticket #35 T5）：只讀、不改檔。

高置信標記 = 只會在口語出現的字／詞；低置信標記 = 可能屬正常書面語，需人工判斷。
字形殘留 = zh_cn 殘留繁體、zh_hk 殘留簡體（兩個方向都查）。
退出碼：高置信粵語或任一字形殘留 → 1；僅低置信 → 0（只供人工判斷）。
Run: python .scratch/t_scan_colloquial.py [關鍵詞過濾]
"""
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRINGS = os.path.join(_ROOT, 'gateway', 'i18n_strings.json')

HIGH = ['呢度', '呢個', '呢啲', '呢位', '呢邊', '嗰度', '嗰個', '嗰啲', '唔', '嘅', '佢', '哂',
        '咗', '嘢', '嘥', '冇', '俾', '畀', '揀', '攞', '喺', '搵', '睇', '啲', '咁', '咪', '吖',
        '嚟', '嗱', '乜', '點樣', '點解', '幾時', '邊個', '邊度', '邊啲', '晒', '嗮', '唔該',
        '同埋', '之類', '咁多', '咁樣', '無咗', '俾返', '還返', '畀返']
LOW = ['啦', '囉', '呀', '嘛', '添', '走咗', '先會', '先至', '過晒', '返去', '起身',
       '落去', '入去', '出去']


def main():
    filt = sys.argv[1] if len(sys.argv) > 1 else ''
    table = json.load(open(STRINGS, encoding='utf-8'))['strings']
    hi_pat = re.compile('|'.join(re.escape(m) for m in HIGH))
    lo_pat = re.compile('|'.join(re.escape(m) for m in LOW))
    hits_hi, hits_lo = [], []
    for key, vals in table.items():
        if filt and filt not in key:
            continue
        for lang in ('zh_hk', 'zh_cn'):
            v = str(vals.get(lang, ''))
            for pat, bucket in ((hi_pat, hits_hi), (lo_pat, hits_lo)):
                m = pat.findall(v)
                if m:
                    bucket.append((key, lang, sorted(set(m)), v))
    print(f'高置信粵語殘留：{len(hits_hi)} 條（key×lang）')
    for key, lang, marks, v in hits_hi:
        print(f'  [{key} · {lang}] {",".join(marks)}\n      {v[:160]}')
    print(f'\n低置信（需人工判斷）：{len(hits_lo)} 條')
    for key, lang, marks, v in hits_lo:
        print(f'  [{key} · {lang}] {",".join(marks)}\n      {v[:160]}')

    # 字形殘留：zh_cn 不應殘留繁體、zh_hk 不應殘留簡體 —— 兩個方向都要查，
    # 只查一個方向會漏掉「寫 zh_hk 時混入簡體」這類完全無聲的錯誤。
    # 字形表與 t_apply_i18n.py 共用同一份（兩處各寫一份遲早不一致）。
    from t_apply_i18n import SIMPL_ONLY, TRAD_ONLY
    hits_trad = [(k, sorted({c for c in v['zh_cn'] if c in TRAD_ONLY}), v['zh_cn'])
                 for k, v in table.items() if any(c in TRAD_ONLY for c in v['zh_cn'])]
    print(f'\nzh_cn 繁體殘留：{len(hits_trad)} 條')
    for key, marks, v in hits_trad:
        print(f'  [{key}] {",".join(marks)}\n      {v[:160]}')
    hits_simp = [(k, sorted({c for c in v['zh_hk'] if c in SIMPL_ONLY}), v['zh_hk'])
                 for k, v in table.items() if any(c in SIMPL_ONLY for c in v['zh_hk'])]
    print(f'\nzh_hk 簡體殘留：{len(hits_simp)} 條')
    for key, marks, v in hits_simp:
        print(f'  [{key}] {",".join(marks)}\n      {v[:160]}')
    # 低置信標記只報告、不影響退出碼：其中多數屬正常書面語。
    return 1 if (hits_hi or hits_trad or hits_simp) else 0


if __name__ == '__main__':
    sys.exit(main())
