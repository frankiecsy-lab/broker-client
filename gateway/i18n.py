"""One Gate i18n — 單一入口：文案喺 `gateway/i18n_strings.json`，呢檔淨低載入 + t()。

三語（zh_hk / zh_cn / en）全部係**明確字串**，唔係 opencc 轉換 —
每種語言嘅文案由人手寫低喺 JSON，避免自動轉換出錯嘅專業術語。
改文案 = 改 JSON，唔使郁代碼；JSON 入面 key 順序照功能頁面分組，同原本代碼表一致。
"""
import json
import pathlib

LANGS = ('zh_hk', 'zh_cn', 'en')
DEFAULT_LANG = 'zh_hk'

_data = json.loads(pathlib.Path(__file__).with_name('i18n_strings.json')
                   .read_text(encoding='utf-8'))

STRINGS = _data['strings']
# 各語言自稱（endonym）— combo 顯示用固定字串，唔跟隨 UI 語言切換
LANG_LABELS = _data['lang_labels']
LANG_SHORT = _data['lang_short']

# 文案變咗外部資料 → 載入時一次過核對三語齊全（缺語言即刻炸，唔等到上咗 UI 先發現）
_incomplete = [k for k, v in STRINGS.items() if any(not v.get(l) for l in LANGS)]
if _incomplete:
    raise ValueError(f'i18n_strings.json 三語唔齊全：{_incomplete}')


def t(key, lang=DEFAULT_LANG):
    """取字串：STRINGS[key][lang]。未知 key 即刻炸（開發期 fail-fast）。"""
    entry = STRINGS.get(key)
    if entry is None:
        raise KeyError(f"i18n key not found: {key}")
    return entry.get(lang, entry[DEFAULT_LANG])


def theme_toggle_text(current_theme, lang=DEFAULT_LANG):
    """Theme toggle 按鈕文字 — 顯示目標模式（dark → 「淺色模式」）。"""
    key = 'theme_to_light' if current_theme == 'dark' else 'theme_to_dark'
    return t(key, lang)
