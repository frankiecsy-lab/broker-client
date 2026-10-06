"""One Gate theme — 暗色（= gui_kline 原生配色）/ 淺色（專業 palette）。QSS 由呢度統一生成。

- **dark**：直接沿用 `test/gui_kline.py` 嘅 C_* 顏色常數 → 之後嵌入 gui_kline /
  gui_fulltest 時外殼同頁面零色差、原生感。
- **light**：新專業 palette（冷灰底 + 白 nav bar + 淺灰卡片，同一 accent 品牌色）。
- QSS 全部 scope 喺 `[og="..."]` property selector / objectName 之下 →
  嵌入頁自己 set 嘅顏色唔會俾 app-level stylesheet 蓋走。
"""
from string import Template

# ── dark = gui_kline 原生配色（test/gui_kline.py C_* constants，紅漲/綠跌港股慣例）──
DARK = {
    'window': '#1E1F22',        # C_WINDOW — app 底
    'surface': '#26282C',       # C_SURFACE — nav bar / raised surface
    'card': '#2E3136',          # C_CARD — 卡片 / hover
    'border': '#3A3D42',        # C_BORDER
    'text': '#E6E6E6',          # C_TEXT
    'muted': '#9AA0A6',         # C_MUTED
    'accent': '#F1553B',        # C_ACCENT（品牌橙紅）
    'accent_pressed': '#D6452C'  # C_ACCENT_PRESSED
}

# ── light = 新專業 palette ──
LIGHT = {
    'window': '#E8EAED',        # app 底 — 冷灰
    'surface': '#FFFFFF',       # nav bar — 白
    'card': '#F1F3F5',          # 卡片 / hover — 淺灰（同白 nav bar 有層次）
    'border': '#D0D4D9',
    'text': '#20262C',
    'muted': '#5F6B7A',
    'accent': '#E5492F',        # 同一品牌色，淺底加深一級保 contrast
    'accent_pressed': '#C93D26'
}

THEMES = {'dark': DARK, 'light': LIGHT}
DEFAULT_THEME = 'dark'
CURRENT = DEFAULT_THEME   # 當前已套用 theme name — 嵌入頁（kline_page / fulltest_page）經 add_listener 跟隨

# ── listener registry：PySide6 冇綁定 QApplication.styleSheetChanged，改由本 module 統一通知 ──
_LISTENERS = []


def add_listener(fn):
    """註冊 theme 切換 callback（簽名 `fn(name)`）。apply_theme 完成 setStyleSheet 後同步調用。"""
    if fn not in _LISTENERS:
        _LISTENERS.append(fn)

_QSS_TPL = Template("""\
/* ── One Gate shell chrome — 全部 scoped，嵌入頁保留自己顏色 ── */
QMainWindow { background-color: $window; }
QWidget#oneGateRoot { background-color: $window; }

QWidget[og="navbar"] { background-color: $surface; border-bottom: 1px solid $border; }
QWidget[og="navbar"] QLabel { color: $text; font-size: 13px; }
QLabel#brandLbl { color: $text; font-size: 16px; font-weight: bold; }

QPushButton[og="navbtn"] {
    background-color: transparent; color: $muted;
    border: none; border-radius: 6px; padding: 8px 18px; font-size: 14px; text-align: center;
}
QPushButton[og="navbtn"]:hover { background-color: $card; color: $text; }
QPushButton[og="navbtn"]:checked { background-color: $accent; color: #FFFFFF; font-weight: bold; }
QPushButton[og="navbtn"]:disabled { color: $muted; }

QPushButton#theme_btn, QPushButton#standalone_theme {
    background-color: $card; color: $text;
    border: 1px solid $border; border-radius: 6px; padding: 6px 14px; font-size: 13px;
}
QPushButton#theme_btn:hover, QPushButton#standalone_theme:hover { background-color: $border; }

QComboBox#lang_combo, QComboBox#standalone_lang {
    background-color: $card; color: $text;
    border: 1px solid $border; border-radius: 6px; padding: 5px 10px; font-size: 13px;
}

QLabel#standalone_title { color: $text; font-size: 15px; font-weight: bold; }

/* placeholder 頁卡片（ticket #02）— 之後嵌入真頁面時呢段保留做 fallback */
QWidget[og="pagecard"] { background-color: $card; border: 1px solid $border; border-radius: 10px; }
QWidget[og="pagecard"] QLabel[role="pagetitle"] { color: $text; font-size: 22px; font-weight: bold; }
QWidget[og="pagecard"] QLabel[role="pagebody"] { color: $muted; font-size: 14px; }
""")


def qss(name=DEFAULT_THEME):
    """生成指定 theme 嘅完整 QSS 字串。"""
    return _QSS_TPL.substitute(THEMES[name])


def apply_theme(name=DEFAULT_THEME):
    """套用到當前 QApplication（一鍵切換即時生效）。回傳 theme name。

    冇 QApplication instance 時靜默 no-op（方便 import 做純函數測試）。
    順序：CURRENT 先記錄 → setStyleSheet → 通知 listeners — listener 讀到嘅係新值，
    callback 直接收到 `name` 參數（唔使自己再讀 CURRENT，避免 stale import binding）。
    """
    from PySide6.QtWidgets import QApplication  # lazy — 本 module 可以無 Qt app 就 import
    if name not in THEMES:
        raise KeyError(f"unknown theme: {name}")
    global CURRENT
    CURRENT = name
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(qss(name))
    for fn in list(_LISTENERS):   # copy — listener 入面 add/remove 唔會炸 iteration
        fn(name)
    return name
