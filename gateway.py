"""One Gate 主入口（thin launcher）— 真身喺 `gateway/` package。

用法：`python gateway.py`

呢個檔同同名嘅 `gateway/` directory 可以安全共存 — CPython FileFinder
會先檢查目錄（有 __init__.py 嘅 package）先至查同名 module；而呢個 script
本身以 `__main__` 運行，永遠唔會俾 import 做 'gateway'。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gateway.app import main  # noqa: E402

if __name__ == '__main__':
    main()
