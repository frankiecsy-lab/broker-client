"""Translator — loads the active language JSON from i18n/ and serves t(key).

Design notes:
- Paths resolved via __file__ (CWD-independent): `python D:\\coding\\stock_client\\main.py`
  works no matter where it is launched from.
- Missing key returns the key itself — a visible bug signal in the UI, logged once.
- Live switch: set_language() reloads JSON and emits language_changed(str);
  MainWindow walks every built widget calling retranslateUi().
"""

import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal

# stock_client/i18n  (translator.py -> app/i18n/ -> parents[2] = stock_client)
I18N_DIR = Path(__file__).resolve().parents[2] / "i18n"

SUPPORTED_LANGUAGES = ["zh_TW", "zh_CN", "en_US"]
DEFAULT_LANGUAGE = "zh_TW"


class Translator(QObject):
    language_changed = Signal(str)

    def __init__(self, initial: str = DEFAULT_LANGUAGE):
        super().__init__()
        self._lang = None
        self._table = {}
        self._missing_logged = set()
        self.set_language(initial, emit=False)

    @property
    def language(self) -> str:
        return self._lang

    def set_language(self, lang: str, emit: bool = True):
        if lang not in SUPPORTED_LANGUAGES:
            logging.warning("Unknown language [%s], falling back to %s", lang, DEFAULT_LANGUAGE)
            lang = DEFAULT_LANGUAGE
        path = I18N_DIR / f"{lang}.json"
        with open(path, "r", encoding="utf-8") as f:
            self._table = json.load(f)
        changed = (self._lang != lang)
        self._lang = lang
        if emit and changed:
            self.language_changed.emit(lang)

    def t(self, key: str, **fmt):
        """Nested lookup: t('testtool.run') -> table['testtool']['run'].

        Supports {placeholder} formatting via kwargs. A missing key returns the
        key itself (visible in the UI) and logs a single warning.
        """
        node = self._table
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                if key not in self._missing_logged:
                    logging.warning("i18n missing key [%s] (language %s)", key, self._lang)
                    self._missing_logged.add(key)
                return key
        text = str(node)
        if fmt:
            try:
                text = text.format(**fmt)
            except (KeyError, IndexError):
                pass  # leave the raw template rather than crash a label
        return text

    def check_key_parity(self):
        """Startup helper — diff key sets across all three JSON files and warn."""
        tables = {}
        for lang in SUPPORTED_LANGUAGES:
            with open(I18N_DIR / f"{lang}.json", "r", encoding="utf-8") as f:
                tables[lang] = json.load(f)

        def flatten(d, prefix=""):
            out = set()
            for k, v in d.items():
                full = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    out |= flatten(v, full)
                else:
                    out.add(full)
            return out

        base = flatten(tables[DEFAULT_LANGUAGE])
        for lang in SUPPORTED_LANGUAGES:
            keys = flatten(tables[lang])
            missing, extra = base - keys, keys - base
            if missing or extra:
                logging.warning(
                    "i18n key mismatch [%s]: missing=%s extra=%s",
                    lang, sorted(missing), sorted(extra),
                )
