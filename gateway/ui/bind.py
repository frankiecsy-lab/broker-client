"""objectName → 行為綁定（QSS property 注入 + i18n 文字套用）。

點解要呢一层：Qt Designer 嘅 property editor 只識 set 已宣告嘅 Q_PROPERTY，**自訂 dynamic
property（`og` / `role`）喺 Designer save 時會被 drop**。而本 project 成個 theme 系統都係靠
`[og="pagecard"]` / `[role="pagetitle"]` 呢啲 property selector（`gateway/theme.py`）。
所以 `.ui` 只負責帶 objectName（Designer 一定 preserve），property 由呢度喺 load 後統一還原
—— QSS 一行都唔使改。

兩張表都係 pure data，objectName 做 key：
- `stamp(root, registry)`   → `{objectName: {'og': 'actionbtn'}}`
- `apply_text(root, table, lang)` → `{objectName: i18n_key}`
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QWidget

from gateway.i18n import t


def _find(root, obj_name):
    if root.objectName() == obj_name:
        return root
    return root.findChild(QWidget, obj_name)


def stamp(root, registry):
    """按 registry 喺已載入嘅 widget 樹上注入 dynamic property（QSS selector 契約）。

    返未搵到嘅 objectName list —— `.ui` 改咗名但 registry 跟唔切係好常見嘅 silent bug，
    所以宁可如實回報，唔好靜默。
    已 polish 過嘅 widget（例如運行期先加嘅動態列）要 unpolish/polish 先會食到新 property。
    """
    missing = []
    for obj_name, props in registry.items():
        w = _find(root, obj_name)
        if w is None:
            missing.append(obj_name)
            continue
        for prop, value in props.items():
            w.setProperty(prop, value)
        if type(w).paintEvent is QWidget.paintEvent:
            # 冇自畫 paintEvent 嘅容器（bare QWidget、或頁面 root 呢類純 QWidget subclass）先需要
            # WA_StyledBackground，QSS 先會畫佢嘅 background；QGroupBox/QLabel/QFrame 自己畫 → 唔好郁
            w.setAttribute(Qt.WA_StyledBackground, True)
        w.style().unpolish(w)
        w.style().polish(w)
    return missing


def set_prop(w, name, value):
    """運行期改 QSS property（值未變即返回）→ unpolish/polish 後才會套用新值。

    `stamp` 只在 `.ui` 載入後跑一次；會變的狀態（帳戶環境、數值是否過期）必須經此處改，
    否則 property selector 永遠停在初始值。
    """
    if w.property(name) == value:
        return
    w.setProperty(name, value)
    w.style().unpolish(w)
    w.style().polish(w)


def apply_text(root, table, lang):
    """table-driven retranslate：`{objectName: i18n_key}` → `setText`。取代各頁手寫 retranslate body。
    QGroupBox 嘅標題冇 `setText`，一律用 `setTitle`（等 GroupBox 標題都入到同一張表）。"""
    missing = []
    for obj_name, key in table.items():
        w = _find(root, obj_name)
        if w is None:
            missing.append(obj_name)
            continue
        (w.setTitle if isinstance(w, QGroupBox) else w.setText)(t(key, lang))
    return missing
