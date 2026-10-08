"""E2E GUI test — 策略管理頁（ticket #25）：Manager CRUD + 頁面 CRUD（分數制條件編輯）+ 持久化 + 三語。

Run: python .scratch/e2e_gui_strategies.py   (from project root; QT_QPA_PLATFORM=offscreen 自動設定。
     全 hermetic：tmp state 檔 + StubDir（唔打 broker、唔食真 symbol index）。

Flow:
1. StrategyManager：add（id/enum/vob 參數 default；#32 entry 冇 code/validity/created）、
   驗證（dup/空名/無條件）、update、#30 listener（add/update/remove 通知、失敗唔通知）、
   容忍 load（unknown type drop / clamp / 舊檔多餘欄忽略）、reset 後持久化重載
2. 頁面：空表提示 → 經 widget 加條件（draft label + ✕ 移除）→ 新增 → 表欄摘要（三欄）
   → JSON 檔結構（entry_price=market、qty=min_lot、score 原樣、冇 code/validity）
3. 揀行載入編輯 → 改名 save → manager 生效
4. 刪除所選 → 0 行；唔合法輸入 status；retranslate 三語（表頭/組標題/類型名）；shell 註冊；i18n 鍵全存在
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gateway.state_store as state_store  # noqa: E402

_TMPDIR = tempfile.mkdtemp()
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state.json'   # 🤖 hermetic：tmp state 檔

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QSpinBox  # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: E402

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if not ok:
        FAILURES.append(name)


def pump(n=40):
    for _ in range(n):
        app.processEvents()


# ══ Part 1：StrategyManager ════════════════════════════════════════════════
print('── Part 1: StrategyManager ──')
import gateway.strategies as st  # noqa: E402
from gateway.i18n import t  # noqa: E402

st.reset_manager_for_test()
mgr = st.get_manager()
EV = []   # #30：listener 事件記錄（fn(origin, kind)）
mgr.add_listener(lambda o, k: EV.append((o, k)))
BUY = [{'type': 'ma_cross', 'side': 'above', 'params': {'fast': 20, 'slow': 120}, 'score': 60},
       {'type': 'vob_break', 'side': 'above', 'params': {}, 'score': 40}]
SELL = [{'type': 'ma_cross', 'side': 'below', 'params': {'fast': 20, 'slow': 120}, 'score': 100}]
ok, msg, item = mgr.add('Golden Cross', BUY, SELL)
check('add → id s- 前綴 / entry_price=market / qty=min_lot（#32：entry 冇 code/validity/created）',
      ok and item['id'].startswith('s-') and item['entry_price'] == 'market'
      and item['qty'] == 'min_lot'
      and 'code' not in item and 'validity' not in item and 'created' not in item)
check('#29 add 冇傳 mark_buffer → 預設 10', item['mark_buffer'] == 10)
check('VOB 缺參數 → 全部填 default（period 14 / confirm 3）',
      item['buy'][1]['params']['period'] == 14 and item['buy'][1]['params']['confirm'] == 3)
check('大細階唔敏感 duplicate name → str_dup_name',
      mgr.add('golden cross', BUY, SELL) == (False, 'str_dup_name', None))
check('空名 → str_bad_name / 冇條件 → str_no_rules',
      mgr.add('', BUY, SELL)[1] == 'str_bad_name'
      and mgr.add('x', [], [{}])[1] == 'str_no_rules')
sid = item['id']
check('#29 update mark_buffer=5 → 改到；改其他欄 → 唔動；9999 → clamp 200',
      mgr.update(sid, mark_buffer=5)[0] and mgr.get(sid)['mark_buffer'] == 5
      and mgr.update(sid, name='Golden Cross 2c')[0] and mgr.get(sid)['mark_buffer'] == 5
      and mgr.update(sid, mark_buffer=9999)[0] and mgr.get(sid)['mark_buffer'] == 200)
ok2, _ = mgr.update(sid, name='Golden Cross 2', sell=[{'type': 'boll_cross', 'side': 'below',
                                                       'params': {'line': 'mid'}, 'score': 100}])
check('update 部分欄位 → name/sell 改咗、buy 唔動',
      ok2 and mgr.get(sid)['name'] == 'Golden Cross 2'
      and mgr.get(sid)['sell'][0]['type'] == 'boll_cross'
      and len(mgr.get(sid)['buy']) == 2)
check('update 未知 id → str_gone', mgr.update('s-nope', name='zz')[1] == 'str_gone')
check('#30 listener：成功 add/update 有通知（origin=strategies）、失敗（dup/bad/str_gone）冇通知',
      EV.count(('strategies', 'add')) == 1 and ('strategies', 'update') in EV and len(EV) == 5)

doc = json.loads(state_store.STATE_PATH.read_text(encoding='utf-8'))
doc['strategies']['items'].append(
    {'id': 's-bad', 'name': 'Junk', 'code': 'US.AAPL', 'validity': '9d', 'created': 'x',
     'buy': [{'type': 'nope', 'side': 'above', 'params': {}, 'score': 5},
             {'type': 'ma_cross', 'side': 'up', 'params': {'fast': 1}, 'score': 999}],
     'sell': []})
state_store.STATE_PATH.write_text(json.dumps(doc), encoding='utf-8')
st.reset_manager_for_test()
mgr2 = st.get_manager()
junk = mgr2.get('s-bad')
check('容忍 load：unknown type drop / side→above / fast clamp 1→2 / score 999→100 / 舊檔 code·validity·created 多餘欄忽略（#32）',
      len(junk['buy']) == 1 and junk['buy'][0]['side'] == 'above'
      and junk['buy'][0]['params']['fast'] == 2 and junk['buy'][0]['score'] == 100
      and 'code' not in junk and 'validity' not in junk and 'created' not in junk
      and junk['mark_buffer'] == 10)   # #29：舊檔冇呢欄 → 自動補預設（零遷移）
check('持久化：reset 後重載，第一條完整保留（2 buy + 1 sell）',
      mgr2.get(sid) is not None and len(mgr2.get(sid)['buy']) == 2
      and len(mgr2.get(sid)['sell']) == 1)
mgr2.add_listener(lambda o, k: EV.append((o, k)))   # #30：新 singleton → 重新掛先見到 remove
mgr2.remove([sid, 's-bad'])
check('remove 生效 + 即時寫檔', mgr2.items() == []
      and json.loads(state_store.STATE_PATH.read_text(encoding='utf-8'))['strategies']['items'] == [])
check('#30 listener：remove 有通知', ('strategies', 'remove') in EV)

# ══ Part 2：頁面 CRUD（經 widget）══════════════════════════════════════════
print('── Part 2: 策略頁 CRUD ──')
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state2.json'
st.reset_manager_for_test()


from gateway.pages.strategies_page import COLUMNS, StrategiesPage  # noqa: E402

_CI = {c: i for i, c in enumerate(COLUMNS)}
page = StrategiesPage()   # #32：冇標的輸入 → 唔再需要 StubDir
page.show()
pump()
check('空表 → 空提示 status + counts 0', page.model.rowCount() == 0
      and '暫時冇策略' in page.status_lbl.text())

page.name_edit.setText('T1')
check('objectName 齊：param spin / score / add_btn（E2E hook 契約）',
      isinstance(page.findChild(QSpinBox, 'str_buy_param_fast'), QSpinBox)
      and isinstance(page.findChild(QSpinBox, 'str_sell_score'), QSpinBox))
page.buy_editor._param_widgets['fast'].setValue(20)
page.buy_editor._param_widgets['slow'].setValue(120)
page.buy_editor.score_spin.setValue(60)
page.buy_editor.add_btn.click()
pump()
check('加條件 → draft label = 符號摘要 MA20>MA120 (+60)',
      'MA20>MA120 (+60)' in page.findChild(QLabel, 'str_buy_rule_0').text())
page.buy_editor.type_combo.setCurrentIndex(2)   # vob_break
page.buy_editor.score_spin.setValue(40)
page.buy_editor.add_btn.click()
pump()
check('轉 VOB → 參數欄換成 8 個 vob 參數 + 第二條 draft',
      set(page.buy_editor._param_widgets) == {'period', 'strength', 'confirm', 'sweep',
                                              'max_size', 'pen', 'supersede', 'max_zones'}
      and 'C>VOB (+40)' in page.findChild(QLabel, 'str_buy_rule_1').text())
page.findChild(QPushButton, 'str_buy_rule_0_del').click()   # 真 ✕ 掣
pump()
check('✕ 移除條件 → draft 重排（淨返 C>VOB 變 rule_0）',
      'C>VOB (+40)' in page.findChild(QLabel, 'str_buy_rule_0').text()
      and page.findChild(QLabel, 'str_buy_rule_1') is None)
page.buy_editor.type_combo.setCurrentIndex(0)   # 返 ma_cross 再補返第一條
page.buy_editor._param_widgets['fast'].setValue(20)
page.buy_editor._param_widgets['slow'].setValue(120)
page.buy_editor.score_spin.setValue(60)
page.buy_editor.add_btn.click()
pump()
page.sell_editor.side_combo.setCurrentIndex(1)   # below
page.sell_editor.score_spin.setValue(100)
page.sell_editor.add_btn.click()
pump()
check('#29 buffer spinbox 存在（0–200）預設 10',
      isinstance(page.findChild(QSpinBox, 'str_buffer_spin'), QSpinBox)
      and page.buffer_spin.value() == 10 and page.buffer_spin.maximum() == 200)
page.buffer_spin.setValue(5)
page.add_btn.click()
pump()
check('新增 → 1 行 + status 已新增', page.model.rowCount() == 1
      and '已新增' in page.status_lbl.text())
check('#32 表得返三欄（名稱/買/賣）；摘要照符號化',
      page.model.columnCount() == 3
      and page.model.data(page.model.index(0, _CI['buy'])) == 'C>VOB (+40) ＋ MA20>MA120 (+60)'
      and page.model.data(page.model.index(0, _CI['sell'])) == 'MA20<MA120 (+100)')
e0 = json.loads(state_store.STATE_PATH.read_text(encoding='utf-8'))['strategies']['items'][0]
check('JSON 檔：mode enum（market/min_lot）+ score 原樣 60/40/100 + id s- + #29 mark_buffer=5 + #32 冇 code/validity',
      e0['entry_price'] == 'market' and e0['qty'] == 'min_lot'
      and [r['score'] for r in e0['buy']] == [40, 60] and e0['id'].startswith('s-')
      and e0['mark_buffer'] == 5 and 'code' not in e0 and 'validity' not in e0)

page.table.selectRow(0)
pump()
check('揀行 → 載入表單（name/draft/sel_id + 編輯中 status）',
      page._sel_id == e0['id'] and page.name_edit.text() == 'T1'
      and len(page.buy_editor._draft) == 2 and '編輯中' in page.status_lbl.text())
check('#29 揀行 → buffer spin 回填 5', page.buffer_spin.value() == 5)
page.name_edit.setText('T1b')
page.buffer_spin.setValue(20)
page.save_btn.click()
pump()
check('套用修改 → manager 更新 + status（#29 mark_buffer 20 跟改）',
      st.get_manager().get(e0['id'])['name'] == 'T1b'
      and st.get_manager().get(e0['id'])['mark_buffer'] == 20
      and t('str_updated', 'zh_hk') in page.status_lbl.text())

page.table.selectRow(0)
pump()
page.remove_btn.click()
pump()
check('刪除所選 → 0 行 + status 已刪除 + 檔清空',
      page.model.rowCount() == 0 and '已刪除' in page.status_lbl.text()
      and json.loads(state_store.STATE_PATH.read_text(encoding='utf-8'))['strategies']['items'] == [])
page.name_edit.clear()   # 空名 → str_bad_name（#32：標的驗證已退役）
page.add_btn.click()
pump()
check('空名 → status 提示 + 冇新增', page.model.rowCount() == 0
      and t('str_bad_name', 'zh_hk') in page.status_lbl.text())
page.save_btn.click()
pump()
check('冇揀行 save → str_no_sel 提示', t('str_no_sel', 'zh_hk') in page.status_lbl.text())

# ══ Part 3：三語 + shell 註冊 + i18n 鍵齊 ═════════════════════════════════
print('── Part 3: 三語 / 註冊 / i18n ──')
page.name_edit.setText('T2')
page.buy_editor.add_btn.click()
page.sell_editor.add_btn.click()
page.add_btn.click()
pump()
page.retranslate('en')
pump()
check('retranslate EN → 表頭 Name / 組標題 / 類型名 MA Cross / nav 字串',
      page.model.headerData(_CI['name'], Qt.Horizontal) == 'Name'
      and 'Buy' in page.buy_editor.title()
      and page.buy_editor.type_combo.itemText(0) == 'MA Cross'
      and t('nav_strategies', 'en') == 'Strategies')
page.retranslate('zh_cn')
pump()
check('zh_cn 表頭简体', page.model.headerData(_CI['buy'], Qt.Horizontal) == '买入条件')
page.retranslate('zh_hk')
pump()

import gateway.pages.strategies_page as sp_mod  # noqa: E402
KEYS = (set(sp_mod.HEAD_KEYS.values())
        | set(sp_mod._TYPE_KEYS.values()) | set(sp_mod._SIDE_KEYS.values())
        | set(sp_mod._LINE_KEYS.values())
        | {'nav_strategies', 'page_strategies_title', 'str_name_lbl', 'str_name_ph',
           'str_buffer_lbl', 'str_price_lbl', 'str_qty_lbl',
           'str_buy_title', 'str_sell_title', 'str_score_lbl', 'str_add_rule', 'str_add_btn',
           'str_save_btn', 'str_remove_btn', 'str_clear_btn', 'str_count', 'str_empty',
           'str_bad_name', 'str_no_rules', 'str_dup_name',
           'str_added', 'str_updated', 'str_removed', 'str_gone', 'str_no_sel', 'str_sel_edit',
           'str_p_fast', 'str_p_slow', 'str_p_line',
           'str_desc_ma_cross', 'str_desc_boll_cross', 'str_desc_vob_break'}
        | {p.label_key for d in st.CONDITION_DEFS.values() for p in d.params})
missing = []
for k in sorted(KEYS):
    for lang in ('zh_hk', 'zh_cn', 'en'):
        try:
            t(k, lang)
        except Exception:
            missing.append(f'{k}@{lang}')
check(f'全部 i18n 鍵三語齊（{len(KEYS)} 鍵 × 3 語言）', not missing)
if missing:
    print('     ⚠️ ' + ', '.join(missing))

from gateway.app import NAV_DIRECT, PAGE_KEYS  # noqa: E402
check("'strategies' in PAGE_KEYS + NAV_DIRECT（頂層直按）",
      'strategies' in PAGE_KEYS and 'strategies' in NAV_DIRECT)

print('\n' + ('全部通過 ✅' if not FAILURES else f'失敗 {len(FAILURES)} 項：{FAILURES}'))
sys.exit(0 if not FAILURES else 1)
