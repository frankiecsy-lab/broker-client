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

from PySide6.QtCore import QMargins, Qt  # noqa: E402
from PySide6.QtWidgets import (QApplication, QLabel, QPushButton, QGroupBox,  # noqa: E402
                               QSpinBox)

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
# ── Part 2b：`.ui` 骨架（排版喺 gateway/ui/strategies_page.ui；條件/參數數量屬資料 → 填進 slot）──
lay = page.layout()
check('`.ui` root objectName + Designer margin/spacing 照載入',
      page.objectName() == 'strategies_page' and lay is not None
      and lay.contentsMargins() == QMargins(10, 8, 10, 8) and lay.spacing() == 6)
check('靜態控件全部由 `.ui` 建出（objectName 即身份契約）',
      all(getattr(page, n, None) is not None for n in
          ('str_table', 'str_name_edit', 'str_buffer_spin', 'str_price_lbl', 'str_qty_lbl',
           'str_add_btn', 'str_save_btn', 'str_remove_btn', 'str_clear_btn',
           'str_counts', 'str_status')))
check('買／賣 GroupBox 同兩個空 slot 由 `.ui` 建出（str_<side>_group / _paramSlot / _rulesSlot）',
      all(page.findChild(QGroupBox, f'str_{s}_group') is not None
          and getattr(page, f'str_{s}_paramSlot') is not None
          and getattr(page, f'str_{s}_rulesSlot') is not None for s in ('buy', 'sell')))
check('og / WA_StyledBackground 由 _STAMP 補返（Designer 帶唔住 dynamic property）',
      page.str_add_btn.property('og') == 'strbtn'
      and page.str_price_lbl.property('og') == 'strfixed'
      and page.testAttribute(Qt.WA_StyledBackground))
_ed = page.buy_editor
_pd = _ed._cur_def()
check('參數欄按 CONDITION_DEFS 生成並填進 paramSlot（加條件類型唔使改 `.ui`）',
      _ed.param_slot.count() == 2 * len(_pd.params) + 1
      and all(page.findChild(QSpinBox, f'str_buy_param_{p.key}') is not None
              for p in _pd.params if p.is_int))
check('頁面 QSS 有根（objectName → QSS cascade）', 'QWidget#strategies_page' in page.styleSheet())

check('空表 → 空提示 status + counts 0', page.model.rowCount() == 0
      and '暫無策略' in page.str_status.text())

page.str_name_edit.setText('T1')
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
check('已加條件逐條填進 rulesSlot（每條 = 一個 strategy_rule_row.ui：label + ✕）',
      page.buy_editor.rules_slot.count() == len(page.buy_editor._draft) == 2
      and page.buy_editor.rules_slot.itemAt(0).widget().objectName() == 'str_buy_rule_row_0'
      and page.buy_editor.rules_slot.itemAt(0).widget().findChild(QLabel, 'rule_lbl') is None
      and page.buy_editor.rules_slot.itemAt(0).widget().layout().count() == 2
      and page.findChild(QLabel, 'str_buy_rule_0').property('og') == 'strrule'
      and page.findChild(QPushButton, 'str_buy_rule_1_del').property('og') == 'strrule')
page.sell_editor.side_combo.setCurrentIndex(1)   # below
page.sell_editor.score_spin.setValue(100)
page.sell_editor.add_btn.click()
pump()
check('#29 buffer spinbox 存在（0–200）預設 10',
      isinstance(page.findChild(QSpinBox, 'str_buffer_spin'), QSpinBox)
      and page.str_buffer_spin.value() == 10 and page.str_buffer_spin.maximum() == 200)
page.str_buffer_spin.setValue(5)
page.str_add_btn.click()
pump()
check('新增 → 1 行 + status 已新增', page.model.rowCount() == 1
      and '已新增' in page.str_status.text())
check('#32 表得返三欄（名稱/買/賣）；摘要照符號化',
      page.model.columnCount() == 3
      and page.model.data(page.model.index(0, _CI['buy'])) == 'C>VOB (+40) ＋ MA20>MA120 (+60)'
      and page.model.data(page.model.index(0, _CI['sell'])) == 'MA20<MA120 (+100)')
e0 = json.loads(state_store.STATE_PATH.read_text(encoding='utf-8'))['strategies']['items'][0]
check('JSON 檔：mode enum（market/min_lot）+ score 原樣 60/40/100 + id s- + #29 mark_buffer=5 + #32 冇 code/validity',
      e0['entry_price'] == 'market' and e0['qty'] == 'min_lot'
      and [r['score'] for r in e0['buy']] == [40, 60] and e0['id'].startswith('s-')
      and e0['mark_buffer'] == 5 and 'code' not in e0 and 'validity' not in e0)

page.str_table.selectRow(0)
pump()
check('揀行 → 載入表單（name/draft/sel_id + 編輯中 status）',
      page._sel_id == e0['id'] and page.str_name_edit.text() == 'T1'
      and len(page.buy_editor._draft) == 2 and '編輯中' in page.str_status.text())
check('#29 揀行 → buffer spin 回填 5', page.str_buffer_spin.value() == 5)
page.str_name_edit.setText('T1b')
page.str_buffer_spin.setValue(20)
page.str_save_btn.click()
pump()
check('套用修改 → manager 更新 + status（#29 mark_buffer 20 跟改）',
      st.get_manager().get(e0['id'])['name'] == 'T1b'
      and st.get_manager().get(e0['id'])['mark_buffer'] == 20
      and t('str_updated', 'zh_hk') in page.str_status.text())

page.str_table.selectRow(0)
pump()
page.str_remove_btn.click()
pump()
check('刪除所選 → 0 行 + status 已刪除 + 檔清空',
      page.model.rowCount() == 0 and '已刪除' in page.str_status.text()
      and json.loads(state_store.STATE_PATH.read_text(encoding='utf-8'))['strategies']['items'] == [])
page.str_name_edit.clear()   # 空名 → str_bad_name（#32：標的驗證已退役）
page.str_add_btn.click()
pump()
check('空名 → status 提示 + 冇新增', page.model.rowCount() == 0
      and t('str_bad_name', 'zh_hk') in page.str_status.text())
page.str_save_btn.click()
pump()
check('冇揀行 save → str_no_sel 提示', t('str_no_sel', 'zh_hk') in page.str_status.text())

# ══ Part 3：三語 + shell 註冊 + i18n 鍵齊 ═════════════════════════════════
print('── Part 3: 三語 / 註冊 / i18n ──')
page.str_name_edit.setText('T2')
page.buy_editor.add_btn.click()
page.sell_editor.add_btn.click()
page.str_add_btn.click()
pump()
page.retranslate('en')
pump()
check('retranslate EN → 表頭 Name / 組標題 / 類型名 MA Cross / nav 字串',
      page.model.headerData(_CI['name'], Qt.Horizontal) == 'Name'
      and 'Buy' in page.str_buy_group.title()
      and page.buy_editor.type_combo.itemText(0) == 'MA Cross'
      and t('nav_strategies', 'en') == 'Strategies')
# 兩邊 GroupBox 標題 / 分數 label / 加條件掣一律由 `_TEXT` 表-driven（code 冇逐邊 setText）
check('_TEXT 覆蓋兩邊 GroupBox 文案（apply_text 連 QGroupBox.setTitle 都處理）',
      page.str_buy_group.title() == t('str_buy_title', 'en')
      and page.str_sell_group.title() == t('str_sell_title', 'en')
      and page.str_buy_scorelbl.text() == page.str_sell_scorelbl.text() == t('str_score_lbl', 'en')
      and page.str_buy_add_btn.text() == page.str_sell_add_btn.text() == t('str_add_rule', 'en'))
page.retranslate('zh_cn')
pump()
check('zh_cn 表頭简体', page.model.headerData(_CI['buy'], Qt.Horizontal) == '买入条件')
page.retranslate('zh_hk')
pump()

# ── 使用說明備注：文案屬 i18n、樣式屬 theme（role）。缺 role → QSS 無聲失效，用戶睇唔到 ──
NOTES = {'str_page_note': ('str_page_note', 'pagebody'),
         'str_score_note': ('str_score_note', 'usagehint')}


def _has_text(k, lang):
    try:
        return bool(t(k, lang).strip())
    except Exception:
        return False


check(f'使用說明備注（{len(NOTES)} 條）三語齊全、無空白',
      all(_has_text(k, lang) for _w, (k, _r) in NOTES.items()
          for lang in ('zh_hk', 'zh_cn', 'en')))
check('備注已套用文案並帶 role（無 role → 淡色提示睇唔到）',
      all(getattr(page, w).text() == t(k, 'zh_hk')
          and getattr(page, w).property('role') == r
          for w, (k, r) in NOTES.items()))
page.retranslate('en')
pump()
check('切 EN：使用說明照跟語言（唔係寫死母語）',
      all(getattr(page, w).text() == t(k, 'en') for w, (k, _r) in NOTES.items()))
page.retranslate('zh_cn')
pump()
check('切 zh_cn：使用說明轉简体',
      all(getattr(page, w).text() == t(k, 'zh_cn') for w, (k, _r) in NOTES.items()))
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
           'str_desc_ma_cross', 'str_desc_boll_cross', 'str_desc_vob_break',
           'str_page_note', 'str_score_note'}
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
