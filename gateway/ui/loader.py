"""One Gate `.ui` 載入器 — 執行期 QUiLoader，冇 `pyside6-uic` codegen（避免 stale `ui_*.py`）。

點解要自己 subclass QUiLoader：
- **root 注入**：root 永遠係第一個 `parent is None` 嘅 `createWidget` call，所以我哋可以將 widget 樹
  直接砌落已經存在嘅 page instance 身上（等價 `uic.loadUi`），唔使多一层 wrapper，
  `page.objectName()` 都照樣係 `.ui` 嗰個名。Designer 對 QMainWindow/QDialog form 會寫返
  `<widget class="QMainWindow">`（唔係我哋嘅 class），所以**唔可以靠 class_name 認 root**。
- **promoted widget factory**：Designer 入面 promote 咗嘅自繪 widget（`KlineChart` / `ChartCell` /
  `_IndexCard`）喺 `.ui` 得返 class name，靠 `_CUSTOM` registry 起返真 Python class。

契約：`.ui` 一律放 `gateway/ui/`，objectName 係唯一身份來源（見 `gateway/ui/bind.py`）。
"""
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtUiTools import QUiLoader

UI_DIR = Path(__file__).resolve().parent

# promoted class name → Python class（@register_custom 入面去）
_CUSTOM = {}


def register_custom(cls):
    """註冊 promoted widget（class name 必須同 Designer `<customwidget><class>` 一致）。"""
    _CUSTOM[cls.__name__] = cls
    return cls


class _Loader(QUiLoader):
    def __init__(self, root_cls, root_instance=None):
        super().__init__()
        self._root_cls = root_cls
        self._root_instance = root_instance
        self._root_done = False

    def createWidget(self, class_name, parent=None, name=''):
        # root = 第一個 parent is None 嘅 createWidget（ Designer 對 QMainWindow/QDialog form 會寫
        # <widget class="QMainWindow">，唔係我哋嘅 class name，所以唔可以靠 class_name 認 root）
        if parent is None and not self._root_done:
            self._root_done = True
            w = self._root_instance if self._root_instance is not None else self._root_cls()
            if name and not w.objectName():
                w.setObjectName(name)
            return w
        cls = _CUSTOM.get(class_name)
        if cls is None:
            return super().createWidget(class_name, parent, name)
        w = cls(parent)
        if name:
            w.setObjectName(name)
        return w


def _read(ui_name):
    """讀 `.ui` 入 QBuffer — 唔經 Qt 嘅 path 解析，Windows/Ubuntu/Docker 一致
    （AGENTS.md：跨平台、唔 hardcode 斜線）。"""
    path = UI_DIR / f'{ui_name}.ui'
    buf = QBuffer()
    buf.setData(path.read_bytes())
    if not buf.open(QIODevice.ReadOnly):
        raise RuntimeError(f'load_ui: 開唔到 {path}')
    return path, buf


def _load(ui_name, root_cls, root_instance):
    path, buf = _read(ui_name)
    loader = _Loader(root_cls, root_instance)
    try:
        w = loader.load(buf)
    except RuntimeError as e:
        # .ui 唔合法時 QUiLoader 只抛一句「Unable to open/read ui device」，冇行號 — 補返路徑俾人追
        raise RuntimeError(f'{e} — 檢查 {path}（pyside6-uic 嗰個檔可以報行號）') from None
    if w is None:
        raise RuntimeError(f'load_ui 失敗：{path} — {loader.errorString()}')
    return w


def apply_ui(page, ui_name):
    """將 `gateway/ui/<ui_name>.ui` 直接砌落 `page` 身上（等價 `uic.loadUi`，冇嵌套 wrapper）。"""
    return _load(ui_name, type(page), page)


def load_ui(ui_name, root_cls, parent=None):
    """自己起一個 `root_cls` instance 並砌入 `.ui`。返填好 layout 嘅 instance。"""
    w = _load(ui_name, root_cls, None)
    if parent is not None and w.parent() is None:
        w.setParent(parent)
    return w
