"""E2E GUI test — 連綫測試頁（UI 分離後嘅第一個試點）：.ui 載入 + stamp + config editor + probes + i18n + theme。

Run: python .scratch/e2e_gui_connection.py   (from project root; QT_QPA_PLATFORM=offscreen 自動設定。
     全 hermetic：CONFIG_PATH 指去 temp 副本（唔碰真 config.json）；probe 函數 monkey-patch，唔打 OpenD/Gateway)

Flow:
1. 構造：`.ui` 載入 → root objectName + 全部 objectName 都在 + 無嵌套 wrapper
2. stamp：QSS property 全部注入（pagecard / pagetitle / sectitle / formlabel / result / savebtn / actionbtn）
3. config 讀取：form 值 == config.json 現值
4. save：只改 futu/ib host+port + kline_num，其他欄位（kline_adj/source）原封不動
5. 非整數 → conn_int_err 且唔寫檔
6. probes：monkey-patch → 成功/ refused / timeout / other 四條路徑如實回報；運行中 disable、完咗 enable
7. i18n 三語：標題/按鈕跟隨，form 值唔變
8. theme：頁面級 QSS 跟隨外殼切換
9. app shell 註冊契约（起真 shell 會連 IB/OpenD，E2E 唔碰）

Exit code 0 = all pass; non-zero = at least one check failed.
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QWidget  # noqa: E402

import gateway.pages.connection_page as cp  # noqa: E402
from gateway.i18n import t  # noqa: E402
from gateway.pages.connection_page import ConnectionPage  # noqa: E402

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name)
    if not ok:
        FAILURES.append(name)


def pump(app, n=40):
    for _ in range(n):
        app.processEvents()


def wait_for(app, cond, what='?', timeout=10.0):
    t0 = time.time()
    while not cond():
        app.processEvents()
        if time.time() - t0 > timeout:
            check(f'TIMEOUT waiting: {what}', False)
            return False
    return True


REAL_CFG = Path(__file__).resolve().parents[1] / 'modules' / 'config.json'

WIDGETS = ['card_header', 'card_config', 'card_probe', 'title_lbl', 'body_lbl', 'cfg_title_lbl',
           'futu_group_lbl', 'host_lbl_a', 'port_lbl_a', 'ib_group_lbl', 'host_lbl_b', 'port_lbl_b',
           'kline_lbl', 'futu_host', 'futu_port', 'ib_host', 'ib_port', 'kline_num',
           'save_cfg_btn', 'cfg_status', 'probe_title_lbl', 'probe_futu_btn', 'probe_ib_btn',
           'futu_result', 'ib_result']

PROPS = {'card_header': ('og', 'pagecard'), 'card_config': ('og', 'pagecard'),
         'card_probe': ('og', 'pagecard'), 'title_lbl': ('role', 'pagetitle'),
         'body_lbl': ('role', 'pagebody'), 'cfg_title_lbl': ('role', 'sectitle'),
         'probe_title_lbl': ('role', 'sectitle'), 'futu_group_lbl': ('role', 'formlabel'),
         'kline_lbl': ('role', 'formlabel'), 'cfg_status': ('role', 'result'),
         'futu_result': ('role', 'result'), 'ib_result': ('role', 'result'),
         'save_cfg_btn': ('og', 'savebtn'), 'probe_futu_btn': ('og', 'actionbtn'),
         'probe_ib_btn': ('og', 'actionbtn')}


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    tmpdir = tempfile.mkdtemp(prefix='og_cfg_')
    cfg_path = Path(tmpdir) / 'config.json'
    shutil.copyfile(REAL_CFG, cfg_path)
    cp.CONFIG_PATH = cfg_path            # 所有 page 執行期都讀呢個 temp 副本
    original_probe = (cp._probe_futu, cp._probe_ib)

    try:
        page = ConnectionPage()
        page.resize(1100, 760)
        page.show()
        pump(app)

        # ── 1. 構造 ──
        check('root objectName 正確', page.objectName() == 'connection_page')
        check('無嵌套 wrapper（.ui root 注入咗現有 instance）',
              page.findChild(QWidget, 'connection_page') is None)
        missing = [n for n in WIDGETS if page.findChild(QWidget, n) is None]
        check(f'全部 widget 都在（{len(WIDGETS) - len(missing)}/{len(WIDGETS)}）', not missing)
        if missing:
            print('    缺少: ' + ', '.join(missing))
        check('widget 類型正確',
              isinstance(page.findChild(QLineEdit, 'futu_port'), QLineEdit)
              and isinstance(page.findChild(QPushButton, 'probe_ib_btn'), QPushButton)
              and isinstance(page.findChild(QLabel, 'ib_result'), QLabel))
        check('root 已 set WA_StyledBackground', page.testAttribute(Qt.WA_StyledBackground))

        # ── 2. stamp（QSS 契約）──
        bad = [n for n, (p, v) in PROPS.items() if page.findChild(QWidget, n).property(p) != v]
        check(f'QSS property 全部注入（{len(PROPS) - len(bad)}/{len(PROPS)}）', not bad)
        if bad:
            print('    property 唔啱: ' + ', '.join(bad))
        check('card 已 set WA_StyledBackground',
              page.findChild(QWidget, 'card_config').testAttribute(Qt.WA_StyledBackground))
        sectitle = page.findChild(QLabel, 'cfg_title_lbl')
        dark_text = cp.theme_mod.THEMES['dark']['text']
        check(f'QSS [role="sectitle"] 真係食到（{dark_text}）',
              sectitle.palette().color(sectitle.foregroundRole()).name().upper() == dark_text.upper())

        # ── 3. config 讀取 ──
        cfg0 = json.loads(cfg_path.read_text(encoding='utf-8'))
        check('form 值 == config.json 現值',
              page.futu_host.text() == str(cfg0['futu']['host'])
              and page.futu_port.text() == str(cfg0['futu']['port'])
              and page.ib_host.text() == str(cfg0['ib']['host'])
              and page.ib_port.text() == str(cfg0['ib']['port'])
              and page.kline_num.text() == str(cfg0['kline_num']))

        # ── 4. save：只改可編輯欄位，其他原封不動 ──
        page.futu_port.setText('33333')
        page.ib_host.setText('10.0.0.5')
        page.kline_num.setText('250')
        page.save_cfg_btn.click()
        pump(app)
        cfg1 = json.loads(cfg_path.read_text(encoding='utf-8'))
        check('save 改到目標欄位',
              cfg1['futu']['port'] == 33333 and cfg1['ib']['host'] == '10.0.0.5'
              and cfg1['kline_num'] == 250)
        check('其他欄位原封不動（kline_adj/source/futu.host）',
              cfg1['kline_adj'] == cfg0['kline_adj'] and cfg1['source'] == cfg0['source']
              and cfg1['futu']['host'] == cfg0['futu']['host']
              and cfg1['ib']['port'] == cfg0['ib']['port'])
        check('頂層 key 順序保留', list(cfg1.keys()) == list(cfg0.keys()))
        check('save 成功提示', page.cfg_status.text() == t('conn_saved_ok', 'zh_hk'))

        # ── 5. 非整數 → 唔寫檔 ──
        before = cfg_path.read_bytes()
        page.futu_port.setText('abc')
        page.save_cfg_btn.click()
        pump(app)
        check('非整數 → conn_int_err 且檔冇改',
              page.cfg_status.text() == t('conn_int_err', 'zh_hk')
              and cfg_path.read_bytes() == before)
        page.futu_port.setText('11111')

        # ── 6. probes（monkey-patch，唔打網絡）──
        CALLS = []

        def fake_futu(host, port):
            CALLS.append(('futu', host, port))
            return {'ok': True, 'connect_ms': 12.0, 'rtt_ms': 34.0}

        def fake_ib(host, port):
            CALLS.append(('ib', host, port))
            return {'ok': False, 'reason_code': 'refused', 'detail': 'ConnectionRefusedError'}

        cp._probe_futu, cp._probe_ib = fake_futu, fake_ib

        page.probe_futu_btn.click()
        check('futu probe：點擊後立即 disable + testing 提示',
              not page.probe_futu_btn.isEnabled()
              and page.futu_result.text() == t('conn_testing', 'zh_hk'))
        check('futu probe 行咗 QThread',
              wait_for(app, lambda: page.probe_futu_btn.isEnabled(), 'futu probe 完成'))
        check('futu probe 帶住 form 入面嘅 host/port',
              len(CALLS) == 1 and CALLS[0] == ('futu', '127.0.0.1', 11111))
        ok_text = t('conn_futu_ok', 'zh_hk').format(connect_ms='12', rtt_ms='34')
        check('futu probe 成功如實（含速度）', page.futu_result.text() == ok_text)

        page.probe_ib_btn.click()
        check('ib probe 行咗 QThread',
              wait_for(app, lambda: page.probe_ib_btn.isEnabled(), 'ib probe 完成'))
        fail_text = t('conn_ib_fail', 'zh_hk').format(reason=t('conn_reason_refused', 'zh_hk'))
        check('ib probe 失敗如實（refused → 人話原因，唔 fake success）',
              page.ib_result.text() == fail_text)

        def fake_timeout(host, port):
            return {'ok': False, 'reason_code': 'timeout', 'detail': 'handshake timeout (>15s)'}

        cp._probe_ib = fake_timeout
        page.probe_ib_btn.click()
        check('timeout 路徑如實',
              wait_for(app, lambda: page.probe_ib_btn.isEnabled(), 'ib timeout probe')
              and page.ib_result.text() == t('conn_ib_fail', 'zh_hk').format(
                  reason=t('conn_reason_timeout', 'zh_hk')))

        cp._probe_ib = lambda host, port: {'ok': False, 'reason_code': 'other', 'detail': 'boom'}
        page.probe_ib_btn.click()
        check('other 路徑直接顯示 detail',
              wait_for(app, lambda: page.probe_ib_btn.isEnabled(), 'ib other probe')
              and page.ib_result.text() == t('conn_ib_fail', 'zh_hk').format(reason='boom'))

        # 非整數 port → 唔起 worker
        n_before = len(CALLS)
        page.ib_port.setText('notaport')
        page.probe_ib_btn.click()
        pump(app)
        check('port 非整數 → 唔起 worker，直接提示',
              len(CALLS) == n_before and page.ib_result.text() == t('conn_int_err', 'zh_hk'))
        page.ib_port.setText('4001')

        # ── 7. i18n 三語（form 值唔變）──
        for lang in ('zh_hk', 'zh_cn', 'en'):
            page.retranslate(lang)
            pump(app)
            check(f'i18n {lang}：標題/按鈕跟隨',
                  page.title_lbl.text() == t('page_connection_title', lang)
                  and page.save_cfg_btn.text() == t('conn_save', lang)
                  and page.probe_ib_btn.text() == t('conn_probe_ib_btn', lang)
                  and page.host_lbl_a.text() == t('conn_host', lang))
            check(f'i18n {lang}：form 值唔變',
                  page.futu_host.text() == '127.0.0.1' and page.ib_port.text() == '4001')
            check(f'i18n {lang}：頁級使用說明跟隨語言',
                  page.body_lbl.text() == t('page_connection_body', lang))
        check('使用說明備注帶 role（缺少 role 時淡色提示不可見）且三語非空',
              page.body_lbl.property('role') == 'pagebody'
              and all(t('page_connection_body', l).strip()
                      for l in ('zh_hk', 'zh_cn', 'en')))

        # ── 8. theme 跟隨外殼 ──
        cp.theme_mod.apply_theme('light')
        pump(app)
        light_text = cp.theme_mod.THEMES['light']['text']
        check(f'theme 切換 → 頁面 QSS 跟隨（{light_text}）',
              sectitle.palette().color(sectitle.foregroundRole()).name().upper() == light_text.upper())
        check('本頁 stylesheet 已換 palette',
              cp.theme_mod.THEMES['light']['window'].lower() in page.styleSheet().lower())
        cp.theme_mod.apply_theme('dark')
        pump(app)

        # ── 9. app shell 註冊（起 OneGateWindow 會連真 IB/OpenD，E2E 唔碰 — 只核對註冊契约）──
        from gateway.app import _PAGE_CLASSES, PAGE_KEYS
        check('connection 喺 PAGE_KEYS 入面', 'connection' in PAGE_KEYS)
        check('shell 用無參 constructor（同 app.py 一樣起法）',
              _PAGE_CLASSES.get('connection') is ConnectionPage)

        page.close()
    finally:
        cp._probe_futu, cp._probe_ib = original_probe
        shutil.rmtree(tmpdir, ignore_errors=True)

    print('\nFAILURES: ' + (', '.join(FAILURES) if FAILURES else 'none'))
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
