"""i18n 文案入 JSON 嘅長期回歸：代碼零文案 + 取字行為唔變 + fail-fast 守門有效。

（遷移時「文案零改動」嘅逐 key 對比已喺 CHANGELOG 低咗 record，呢檔淨低永遠有效嘅守門。）
行法：python -u .scratch/t_i18n_json.py
"""
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAIN_JSON = ROOT / 'gateway' / 'i18n_strings.json'
KLINE_JSON = ROOT / 'gateway' / 'pages' / 'gui_kline_strings.json'

FAILURES = []
PASSED = 0


def check(name, ok, detail=''):
    global PASSED
    print(('  ✅ ' if ok else '  ❌ ') + name + (f'  [{detail}]' if not ok and detail else ''))
    if ok:
        PASSED += 1
    else:
        FAILURES.append(name)


import gateway.i18n as I  # noqa: E402
from gateway.pages import gui_kline as gk  # noqa: E402

print('── 1. 文案真係唔喺代碼入面 ──')
for rel in ('gateway/i18n.py', 'gateway/pages/gui_kline.py'):
    src = (ROOT / rel).read_text(encoding='utf-8')
    check(f'{rel} 冇硬編碼文案表', 'STRINGS = {' not in src and '_s(' not in src)
check('兩份 JSON 喺位且可 parse', all(p.is_file() for p in (MAIN_JSON, KLINE_JSON)))
check('JSON 用 pathlib 相對本檔（冇 hardcode 路徑斜線）',
      "pathlib.Path(__file__).with_name('i18n_strings.json')" in (ROOT / 'gateway' / 'i18n.py').read_text(encoding='utf-8')
      and "pathlib.Path(__file__).with_name('gui_kline_strings.json')" in (ROOT / 'gateway' / 'pages' / 'gui_kline.py').read_text(encoding='utf-8'))

print('── 2. 兩份表結構齊全（載入時已核對，呢度再獨立睇一次）──')
raw = json.loads(MAIN_JSON.read_text(encoding='utf-8'))
check(f'三語表 {len(raw["strings"])} 鍵全部三語齊全、冇多餘語言欄',
      all(set(v) == set(I.LANGS) and all(v[l] for l in I.LANGS) for v in raw['strings'].values()))
rawk = json.loads(KLINE_JSON.read_text(encoding='utf-8'))
check(f'K 線測試頁兩語表 {len(rawk)} 鍵全部 zh/en 齊全',
      all(set(v) == {'zh', 'en'} and all(v[l] for l in ('zh', 'en')) for v in rawk.values()))
check('代碼入面嘅表 == JSON（冇人偷偷喺 load 之後改）',
      I.STRINGS == raw['strings'] and gk.STRINGS == rawk)
check('LANG_LABELS / LANG_SHORT 照樣由 JSON 嚟（endonym 唔跟 UI 語言）',
      I.LANG_LABELS == raw['lang_labels'] and I.LANG_SHORT == raw['lang_short'])

print('── 3. 取字行為唔變（t() / 模板 format / fail-fast）──')
check('t() 三語照樣取到', I.t('chart_loading', 'en') == 'Loading…'
      and I.t('nav_quotes', 'zh_cn') == '行情')
try:
    I.t('唔存在嘅key')
    check('未知 key 即刻 KeyError', False)
except KeyError:
    check('未知 key 即刻 KeyError', True)
try:
    got = gk.t('en', 'subscribing', code='HK.00700', ktype='K_5M', num=1000, broker='futu')
    check('兩語模板 {kw} format 照樣 work', got == 'Subscribing HK.00700 K_5M x1000 (futu)...', got)
except Exception as e:  # noqa: BLE001
    check('兩語模板 {kw} format 照樣 work', False, repr(e))
check('theme_toggle_text 照樣經 JSON 取字',
      I.theme_toggle_text('dark', 'en') == I.STRINGS['theme_to_light']['en'])

print('── 4. 缺語言 → 載入即刻炸（外部資料守門）──')
tmp = ROOT / '.scratch' / 'i18n_broken'
tmp.mkdir(exist_ok=True)
(tmp / 'i18n.py').write_text((ROOT / 'gateway' / 'i18n.py').read_text(encoding='utf-8'), encoding='utf-8')
broken = json.loads(MAIN_JSON.read_text(encoding='utf-8'))
del broken['strings']['nav_quotes']['en']          # 故意缺 EN
broken['strings']['nav_quotes'].pop('zh_cn', None)
(tmp / 'i18n_strings.json').write_text(json.dumps(broken, ensure_ascii=False), encoding='utf-8')
r = subprocess.run([sys.executable, '-B', '-c', 'import i18n'], cwd=str(tmp),
                   capture_output=True, text=True)
check('缺語言 → ValueError 並列明邊啲 key',
      r.returncode != 0 and 'nav_quotes' in (r.stderr or ''), (r.stderr or '').strip()[-200:])
shutil.rmtree(tmp)

print('\n' + (f'所有功能測試成功 ✅ ({PASSED} 項)' if not FAILURES
               else f'❌ 有 {len(FAILURES)} 項失敗：{FAILURES}'))
sys.exit(0 if not FAILURES else 1)
