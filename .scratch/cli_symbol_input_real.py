"""真實 index + 真實頁 嘅模糊輸入 probe（用戶實測：行情 / 收藏 打 NVD 完全冇顯示）。

同 e2e 嘅分別：呢度用**真** symbol index（唔係 FakeDir），逐個標的欄位 dump：
model 有冇 item / popup 有冇彈出 / isPopupActive。
Run: QT_QPA_PLATFORM=offscreen PYTHONIOENCODING=utf-8 py -3.14 .scratch/cli_symbol_input_real.py
"""
import os
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)


def pump(n=30):
    for _ in range(n):
        app.processEvents()


def probe(what, edit, comp, q='NVD'):
    edit.show()
    edit.setFocus(Qt.OtherFocusReason)
    pump()
    edit.setText('')
    for ch in q:            # 逐個 key（同真人一樣觸發 textChanged / QLineEdit completer 路徑）
        edit.insert(ch)
        pump(3)
    t0 = time.time()
    while time.time() - t0 < 3.0:      # 等 debounce(200ms) + search
        pump(5)
        if comp.model().stringList():
            break
    items = comp.model().stringList()
    popup = comp.popup()
    print(f'{what:28} model={len(items)} 條 / popup visible={popup.isVisible()} '
          f'/ prefix={comp.completionPrefix()!r}')
    print(f'{"":28} 首三條: {items[:3]}')
    return bool(items) and popup.isVisible()


def main():
    from gateway.pages.favorites_page import FavoritesPage
    from gateway.pages.quotes_page import QuotesPage
    from gateway.pages.kline_page import KlinePage

    results = {}
    fav = FavoritesPage()
    fav.show()
    pump()
    results['收藏頁 add_edit'] = probe('收藏頁 add_edit', fav.add_edit, fav.completer)

    q = QuotesPage()
    q.show()
    pump()
    cell = q.cells[0]
    results['行情頁 cell.cell_symbol'] = probe('行情頁 cell.cell_symbol', cell.cell_symbol, cell.completer)

    k = KlinePage()
    k.show()
    pump()
    win = k._win   # K線頁係嵌入 gui_kline.MainWindow（takeCentralWidget 模式）
    results['K線頁 code_edit'] = probe('K線頁 code_edit', win.code_edit, win.completer)

    bad = [k for k, v in results.items() if not v]
    print('\n' + ('🎉 全部有 popup' if not bad else '❌ 冇結果/冇 popup: ' + ', '.join(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
