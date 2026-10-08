"""One Gate 統一本地狀態儲存 — 一個 JSON 檔記住所有頁的 UI 狀態（以後記其他嘢加 section 就得）。

- Section dict：`load_section('quotes') / save_section('quotes', {...})` — 唔再另開檔。
- 寫法照 modules/symbol_search._save()：tmp + os.replace atomic 替換（斷線當機都唔會寫半截）。
- 讀取容忍 missing / corrupt → 返 default（狀態丟失唔應該炸 UI，如實靜默）。

檔：gateway/ui_state.json（gitignored — runtime state，唔係 source code）。
"""
import json
import os
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent / 'ui_state.json'


def _read_all():
    try:
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_section(name, default=None):
    """讀一個 section（dict）；冇 / 檔壞 → 返 default copy。"""
    v = _read_all().get(name)
    if isinstance(v, dict):
        return v
    return dict(default or {})


def save_section(name, value):
    """整檔 read-modify-write：改一個 section，其他 section 原樣保留；atomic 寫回。
    寫入失敗（disk/perm）靜默 — UI 狀態記憶係 best-effort，唔該打斷用戶操作。"""
    data = _read_all()
    data[name] = value
    tmp = str(STATE_PATH) + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass
