"""E2E GUI test — 首頁「全球市場脈搏」：註冊/預設頁 + 指數卡 + 刷新 + 如實 + i18n。

Run: python .scratch/e2e_gui_home.py   (from project root; QT_QPA_PLATFORM=offscreen 自動設定。
     全 hermetic：fake pulse fetcher（注入 constructor）— 唔打 OpenD/網絡)

Flow:
1. shell 註冊：PAGE_KEYS[0] == 'home'（預設頁）+ NAV_DIRECT 最前
2. 構造：13 張卡 × 三分區；fake worker → 逐卡 set_row
3. 數字格式：_fmt_num/_fmt_big（zh 萬/億、en K/M/B）
4. 逐個失敗如實：err 入卡、唔炸其他卡
5. 刷新按鈕 → 再 fetch；worker 運行中唔重入
6. i18n 三語（標題/分區 header/代理 tooltip）

Exit code 0 = all pass; non-zero = at least one check failed.
"""
import os
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from gateway.app import NAV_DIRECT, PAGE_KEYS  # noqa: E402
from gateway.pages.home_page import (HOME_INDICES, HomePage,  # noqa: E402
                                     _fmt_big, _fmt_num)

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


CALLS = []


def blank_row(code):
    return {'code': code, 'ok': False, 'name': '', 'last': None, 'prev_close': None,
            'open': None, 'high': None, 'low': None, 'volume': None, 'turnover': None,
            'spark': [], 'err': ''}


def fake_pulse(codes, kline_num):
    """market_pulse.fetch_pulse 契約：(ok_any, rows, msg)。"""
    CALLS.append(list(codes))
    rows = []
    for c in codes:
        if c == 'US.VIXY':   # 模擬逐個失敗（如實，唔拖爆其他）
            rows.append(dict(blank_row(c), err='暫不支援美股指數'))
        else:
            rows.append(dict(blank_row(c), ok=True, name=c, last=100.0, prev_close=90.0,
                             open=95.0, high=105.0, low=92.0, volume=1.0,
                             turnover=94699652358.0, spark=[90.0 + i for i in range(12)]))
    return True, rows, f'{len(codes) - 1}/{len(codes)}（1 個失敗：US.VIXY）'


def main():
    app = QApplication(sys.argv)

    # ── 1. shell 註冊 ──
    print('── Part 1: shell 註冊 ──')
    check("PAGE_KEYS[0] == 'home'（預設頁）", PAGE_KEYS[0] == 'home')
    check('NAV_DIRECT[0] == home（主菜單最前）', NAV_DIRECT[0] == 'home')

    page = HomePage(pulse_fetcher=fake_pulse)
    page.show()
    wait_for(app, lambda: all(c._row is not None for c in page._cards.values()),
             'cards populated')

    # ── 2. 卡 + 分區 ──
    print('── Part 2: 指數卡 ──')
    check(f'{len(HOME_INDICES)} 張卡全部建咗', len(page._cards) == len(HOME_INDICES) == 13)
    check('三分區 header 齊', set(page._grp_lbls) == {'HK', 'CN', 'US'})
    check('worker 收齊全部 code', CALLS and set(CALLS[0]) == {e['code'] for e in HOME_INDICES})
    ok_card = page._cards['HK.800000']
    check('成功卡：row 已入（last/prev/spark）',
          ok_card._row and ok_card._row['last'] == 100.0 and len(ok_card._row['spark']) == 12)
    check('更新時間 label 已設', '更新於' in page.updated_lbl.text())

    # ── 3. 數字格式 ──
    print('── Part 3: 數字格式 ──')
    check('_fmt_num 千分位', _fmt_num(24130.5) == '24,130.50' and _fmt_num(None) == '—')
    check('_fmt_big zh 億/萬', _fmt_big(94699652358, 'zh_hk') == '947.00億'
          and _fmt_big(12345, 'zh_hk') == '1.23萬')
    check('_fmt_big en B/M/K', _fmt_big(94699652358, 'en') == '94.70B'
          and _fmt_big(1500000, 'en') == '1.50M')

    # ── 4. 逐個失敗如實 ──
    print('── Part 4: 如實失敗 ──')
    bad = page._cards['US.VIXY']
    check('失敗卡：err 入卡（其他卡唔受影響）',
          bad._row and '暫不支援' in bad._row['err'] and ok_card._row['ok'])

    # ── 5. 刷新 ──
    print('── Part 5: 刷新 ──')
    n0 = len(CALLS)
    page.refresh_btn.click()
    wait_for(app, lambda: len(CALLS) > n0, 'second fetch')
    check('刷新按鈕 → 再 fetch', len(CALLS) == n0 + 1)

    # ── 6. i18n ──
    print('── Part 6: i18n ──')
    page.retranslate('en')
    pump(app)
    check('EN：標題/分區跟語言',
          page.title_lbl.text() == 'Global Market Pulse'
          and page._grp_lbls['US'].text() == '🇺🇸 US (ETF proxies)'
          and 'SPY≈S&P 500' in page._grp_lbls['US'].toolTip())
    page.retranslate('zh_cn')
    pump(app)
    check('zh_cn：简体標題 + 指数名 key 有值',
          page.title_lbl.text() == '全球市场脉搏'
          and page._grp_lbls['CN'].text() == '🇨🇳 A股')

    print()
    if FAILURES:
        print(f'❌ E2E FAILED — {len(FAILURES)} checks: {FAILURES}')
        sys.exit(1)
    print('✅ E2E PASSED — all checks green')
    sys.exit(0)


if __name__ == '__main__':
    main()
