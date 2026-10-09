"""按 key 覆寫 i18n 文案（ticket #35 T5）：保持「一行一 key」格式，只改指定 key 的指定語言。

不用 json.dump 重寫整個檔案（會破壞既有排版）。改完即時重讀驗證：可解析、三語齊全、
zh_cn 無繁體殘留、zh_hk 無簡體殘留（逐條人手改寫時，這類錯誤完全無聲）。
Run: python .scratch/t_apply_i18n.py .scratch/_patch_t5_bt.json [...]
"""
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRINGS = os.path.join(_ROOT, 'gateway', 'i18n_strings.json')
LINE = re.compile(r'^(\s*)"([^"]+)":\s*(\{.*\})(,?)$')
# 只存在繁體字形的字：zh_cn 出現即代表仍是繁體（簡體另有字形）。
TRAD_ONLY = set('線執選標與擊獲敗檢權週數據盤價訊號區間該無條倉單蓋場時運產業個們這說話語譯關閉開顯'
                '隱載錄應當傳輸連綫續斷結構樣類統計劃規則義釋補齊刪復備註冊欄內進從於為將經過'
                '麼後來對歲歷盡聽寫廳點發變讓給買賣債資購虧損風險額餘換匯兌轉動啟試驗證錯許須會夠長'
                '體詞彙記擇碼淨監處辦項組屬準佔軸範圍庫徑態稱視圖畫戶嚴謹認識確維護維棄丟漲緣導')
# 對稱守門：只存在簡體字形的字 → zh_hk 出現即代表寫成簡體。
# 逐條人手寫 zh_hk 時，簡體殘留完全無聲（且 zh_hk 與 zh_cn 常常只差幾個字）。
# 刻意不含兩式通用的字形（周/划/后/于/内/注/册/占/强/范/余/干/台/里/面/系），
# 收進來會產生誤報 —— 誤報的守門會被無視，等於沒有守門。
SIMPL_ONLY = set('账显资币种环类别历从为与会说语这个们关闭开时无条单盖场运产业数据盘价讯号区该仓图'
                 '画户严谨认识确维护弃丢涨缘导体词汇记择码净监处办项组属准轴围库径态称视计规则义释'
                 '补齐删复备栏进将经过么来对岁尽听写厅点发变让给买卖债购亏损风险换汇兑转动启试验证'
                 '错许须够长执选标击获败检权结构样统隐载录应当传输连续断线'
                 '费钱货总层页顶顺预领储摊赚赔头参调议优编询边负财质务势达遗门问难仅随频题国际级'
                 '约纳终觉评读订设诉请归签网继没')


def main():
    patches = {}
    for p in sys.argv[1:]:
        patches.update(json.load(open(p, encoding='utf-8')))
    lines = open(STRINGS, encoding='utf-8').read().split('\n')
    done, out = set(), []
    for line in lines:
        m = LINE.match(line)
        if m and m.group(2) in patches:
            key = m.group(2)
            obj = json.loads(m.group(3))
            obj.update(patches[key])
            out.append(f'{m.group(1)}"{key}": {json.dumps(obj, ensure_ascii=False)}{m.group(4)}')
            done.add(key)
        else:
            out.append(line)
    missing = sorted(set(patches) - done)
    if missing:
        print('❌ 未找到 key（未修改）：' + ', '.join(missing))
        return 1
    open(STRINGS, 'w', encoding='utf-8').write('\n'.join(out))

    table = json.load(open(STRINGS, encoding='utf-8'))['strings']
    bad = [k for k, v in table.items() if not all(str(v.get(l, '')).strip()
                                                 for l in ('zh_hk', 'zh_cn', 'en'))]
    trad = {k: sorted({c for c in table[k]['zh_cn'] if c in TRAD_ONLY})
            for k in done if any(c in TRAD_ONLY for c in table[k]['zh_cn'])}
    simp = {k: sorted({c for c in table[k]['zh_hk'] if c in SIMPL_ONLY})
            for k in done if any(c in SIMPL_ONLY for c in table[k]['zh_hk'])}
    print(f'✅ 已改 {len(done)} 個 key；總 key {len(table)}；三語齊全：{not bad}；'
          f'zh_cn 無繁體殘留：{not trad}；zh_hk 無簡體殘留：{not simp}')
    for k in sorted(done):
        print(f'  [{k}]\n      zh_hk: {table[k]["zh_hk"]}\n      zh_cn: {table[k]["zh_cn"]}')
    if bad:
        print('❌ 三語缺空：' + ', '.join(bad))
    for k, cs in trad.items():
        print(f'❌ zh_cn 繁體殘留 [{k}]：' + ' '.join(cs))
    for k, cs in simp.items():
        print(f'❌ zh_hk 簡體殘留 [{k}]：' + ' '.join(cs))
    return 1 if (bad or trad or simp) else 0


if __name__ == '__main__':
    sys.exit(main())
