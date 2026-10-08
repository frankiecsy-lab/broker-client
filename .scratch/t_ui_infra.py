"""Phase 0 煙霧測試 — load_ui（root class 注入）+ stamp（objectName → QSS property）行唔行得通。

行法：python .scratch/t_ui_infra.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QLineEdit, QWidget

from gateway.ui.bind import stamp
from gateway.ui.loader import apply_ui, load_ui

FAILS = []


def check(name, cond):
    print(('  OK   ' if cond else '  FAIL ') + name)
    if not cond:
        FAILS.append(name)


class ConnectionPage(QWidget):
    """煙霧用 stub — class name 必須同 .ui root class 一致。"""


class AppliedPage(QWidget):
    """apply_ui 用法 stub — class name 同 .ui root class 唔同，證明 root 係靠位置唔係 class name 認。"""

    def __init__(self):
        super().__init__()
        apply_ui(self, 'connection_page')


REGISTRY = {
    'connection_page': {'og': 'shell'},
    'card_header': {'og': 'pagecard'},
    'card_config': {'og': 'pagecard'},
    'card_probe': {'og': 'pagecard'},
    'title_lbl': {'role': 'pagetitle'},
    'body_lbl': {'role': 'pagebody'},
    'cfg_title_lbl': {'role': 'sectitle'},
    'futu_group_lbl': {'role': 'formlabel'},
    'save_cfg_btn': {'og': 'savebtn'},
    'cfg_status': {'role': 'result'},
    'probe_futu_btn': {'og': 'actionbtn'},
    'probe_ib_btn': {'og': 'actionbtn'},
    'futu_result': {'role': 'result'},
    'ib_result': {'role': 'result'},
}

NEEDED = ['card_header', 'card_config', 'card_probe', 'title_lbl', 'body_lbl', 'cfg_title_lbl',
          'futu_group_lbl', 'host_lbl_a', 'port_lbl_a', 'ib_group_lbl', 'host_lbl_b', 'port_lbl_b',
          'kline_lbl', 'futu_host', 'futu_port', 'ib_host', 'ib_port', 'kline_num',
          'save_cfg_btn', 'cfg_status', 'probe_title_lbl', 'probe_futu_btn', 'probe_ib_btn',
          'futu_result', 'ib_result']


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    page = load_ui('connection_page', ConnectionPage)
    check('load_ui 返 ConnectionPage instance', isinstance(page, ConnectionPage))
    check('root objectName 保留', page.objectName() == 'connection_page')

    found = [n for n in NEEDED if page.findChild(QWidget, n) is not None]
    check(f'全部 widget 搵到（{len(found)}/{len(NEEDED)}）', len(found) == len(NEEDED))
    missing = [n for n in NEEDED if page.findChild(QWidget, n) is None]
    if missing:
        print('    缺少: ' + ', '.join(missing))

    check('widget 類型正確',
          isinstance(page.findChild(QLineEdit, 'futu_host'), QLineEdit)
          and isinstance(page.findChild(QPushButton, 'probe_ib_btn'), QPushButton)
          and isinstance(page.findChild(QLabel, 'cfg_status'), QLabel))

    check('root layout 係 QVBoxLayout', page.layout() is not None
          and page.layout().metaObject().className() == 'QVBoxLayout')

    bad = stamp(page, REGISTRY)
    check('stamp 無 missing', not bad)
    if bad:
        print('    stamp missing: ' + ', '.join(bad))
    check('property 已注入（pagecard）',
          page.findChild(QWidget, 'card_config').property('og') == 'pagecard')
    check('property 已注入（result）',
          page.findChild(QLabel, 'cfg_status').property('role') == 'result')
    check('root property 已注入', page.property('og') == 'shell')
    check('bare QWidget 已 set WA_StyledBackground',
          page.findChild(QWidget, 'card_config').testAttribute(Qt.WA_StyledBackground))

    # QSS 契約真係食到 property selector
    page.setStyleSheet('QLabel[role="sectitle"] { color: #FF0000; }')
    page.resize(900, 600)
    page.show()
    app.processEvents()
    pal = page.findChild(QLabel, 'cfg_title_lbl').palette().color(page.foregroundRole())
    check('QSS [role=...] selector 生效', pal.name().upper() == '#FF0000')

    # apply_ui：直接砌落現有 instance（page 嘅正常用法），唔准出現嵌套 wrapper
    p2 = AppliedPage()
    check('apply_ui 砌落現有 instance',
          p2.objectName() == 'connection_page'
          and isinstance(p2.findChild(QLineEdit, 'futu_host'), QLineEdit))
    check('apply_ui 冇嵌套 wrapper', p2.findChild(QWidget, 'connection_page') is None)
    check('apply_ui 有 layout', p2.layout() is not None
          and p2.layout().metaObject().className() == 'QVBoxLayout')

    print('\nFAILS: ' + (', '.join(FAILS) if FAILS else 'none'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
