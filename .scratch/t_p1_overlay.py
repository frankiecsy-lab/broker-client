# -*- coding: utf-8 -*-
"""P1 加載態 smoke test：`.ui` 疊層砌得成、set_busy 收放、theme/i18n 跟到。
跑法：python .scratch/t_p1_overlay.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication.instance() or QApplication([])

from gateway.i18n import STRINGS  # noqa: E402
from gateway.pages import gui_kline as gk  # noqa: E402

ok = []


def check(name, cond):
    ok.append(cond)
    print(f'  {"✅" if cond else "❌"} {name}')


# ① i18n 三語齊
for k in ('chart_loading', 'chart_loading_slow'):
    check(f'i18n {k} 三語齊', len(STRINGS.get(k, {})) == 3 and all(STRINGS[k].values()))

# ② 疊層惰性砌 + 可見性 + 文案
ch = gk.KlineChart()
ch.resize(600, 400)
ch.show()
app.processEvents()
check('未 set_busy → 冇疊層（惰性）', ch._overlay is None)
ch.set_busy(True)
app.processEvents()
ov = ch._overlay
check('set_busy(True) → 疊層存在且 visible', ov is not None and ov.isVisible())
check('objectName = kline_overlay / kline_loading',
      ov.objectName() == 'kline_overlay' and ch._loading_lbl.objectName() == 'kline_loading')
check('文案 = chart_loading(zh_hk)', ov.findChild(QLabel, 'kline_loading').text()
      == STRINGS['chart_loading']['zh_hk'])
check('疊層 geometry 貼住 canvas', ov.geometry() == ch.canvas.geometry())
check('滑鼠穿透（加載期間照樣 pan/zoom）', bool(ov.testAttribute(Qt.WA_TransparentForMouseEvents)))
check('WA_StyledBackground（QSS background 先食到）', bool(ov.testAttribute(Qt.WA_StyledBackground)))
check('watchdog 排緊 8s', ch._slow_timer.isActive() and ch._slow_timer.interval() == gk.KlineChart.SLOW_MS)

# ③ 窗口郁動 → geometry 跟
ch.resize(700, 460)
app.processEvents()
check('resize 後疊層跟住 canvas', ov.geometry() == ch.canvas.geometry())

# ④ theme 切換 → scrim 跟 palette（唔炸、有 rgba）
gk.C_SURFACE = '#FFFFFF'
ch._restyle_overlay()
ss = ov.styleSheet()
check('light palette → rgba(255,255,255,.72)', 'rgba(255,255,255,0.72)' in ss)
gk.C_SURFACE = '#26282C'
ch._restyle_overlay()
check('返 dark palette → rgba(38,40,44,.72)', 'rgba(38,40,44,0.72)' in ov.styleSheet())

# ⑤ 語言切換（兩邊語言碼都歸一）
for lang, want in (('en', 'Loading…'), ('zh_cn', STRINGS['chart_loading']['zh_cn']),
                   ('zh', STRINGS['chart_loading']['zh_hk'])):
    ch.set_lang(lang)
    got = ch._loading_lbl.text()
    check(f'set_lang({lang}) → 文案跟語言', want in got or got == want)

# ⑥ watchdog 觸發 → 轉「比較慢」文案
ch._on_slow_loading()
check('watchdog → chart_loading_slow', ch._loading_lbl.text() == STRINGS['chart_loading_slow'][ch._lang])

# ⑦ 收埋
ch.set_busy(False)
app.processEvents()
check('set_busy(False) → 疊層唔 visible 且 watchdog 停',
      not ov.isVisible() and not ch._slow_timer.isActive())
ch.set_busy(False)   # 冪等
check('再收一次唔炸（冪等）', True)

# ⑧ 子类（IndicatorKlineChart = 行情格 6 格用嘅類）自動繼承
from gateway import indicators  # noqa: E402
ich = indicators.IndicatorKlineChart()
ich.resize(300, 200)
ich.show()
app.processEvents()
ich.set_busy(True)
app.processEvents()
check('IndicatorKlineChart 同樣有加載態', ich._overlay is not None and ich._overlay.isVisible())

print('\n' + ('所有功能測試成功 ✅ (' + str(len(ok)) + ' 項)' if all(ok) else '❌ 有失敗：' + str(ok)))
sys.exit(0 if all(ok) else 1)
