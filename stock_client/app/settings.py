"""AppSettings — QSettings INI wrapper (lands under %APPDATA%/Tomato Broker Server)."""

from PySide6.QtCore import QSettings


class AppSettings:
    """Thin typed wrapper over QSettings. All persistence goes through here."""

    def __init__(self):
        self._s = QSettings("Tomato Broker Server", "TomatoBrokerServer")

    # --- generic get/set ---
    def get(self, key: str, default=None):
        return self._s.value(key, default)

    def set(self, key: str, value):
        self._s.setValue(key, value)
        self._s.sync()

    # --- app-level prefs ---
    @property
    def language(self) -> str:
        return self.get("language", "zh_TW")

    @language.setter
    def language(self, v: str):
        self.set("language", v)

    @property
    def theme(self) -> str:
        return self.get("theme", "dark")

    @theme.setter
    def theme(self, v: str):
        self.set("theme", v)

    @property
    def broker_dir(self) -> str:
        return self.get("broker_dir", r"D:\coding\broker_client")

    @broker_dir.setter
    def broker_dir(self, v: str):
        self.set("broker_dir", v)

    # --- test tool defaults (persisted so the page restores last-used params) ---
    @property
    def tt_codes(self) -> list:
        return self.get("tt.codes", ["US.AAPL", "HK.00700"])

    @tt_codes.setter
    def tt_codes(self, v):
        self.set("tt.codes", list(v))

    @property
    def tt_kline_num(self) -> int:
        return int(self.get("tt.kline_num", 100))

    @tt_kline_num.setter
    def tt_kline_num(self, v):
        self.set("tt.kline_num", int(v))

    @property
    def tt_duration(self) -> int:
        return int(self.get("tt.duration", 30))

    @tt_duration.setter
    def tt_duration(self, v):
        self.set("tt.duration", int(v))

    # --- bool persistence ---------------------------------------------------------
    # PySide6's QSettings stores Python bools as REG_SZ "true"/"false" on Windows —
    # and bool('false') is True! So persist bools as 0/1 (REG_DWORD) and parse both
    # shapes when reading (older writes left string values behind).

    def _read_bool(self, key: str, default: bool) -> bool:
        raw = self.get(key, int(default))
        if isinstance(raw, str):  # legacy REG_SZ value from an older write
            return raw.strip().lower() in ("1", "true")
        return bool(int(raw))

    @property
    def tt_strict(self) -> bool:
        return self._read_bool("tt.strict", True)

    @tt_strict.setter
    def tt_strict(self, v):
        self.set("tt.strict", int(bool(v)))  # DWORD — see _read_bool note

    @property
    def tt_mock(self) -> bool:
        return self._read_bool("tt.mock", False)

    @tt_mock.setter
    def tt_mock(self, v):
        self.set("tt.mock", int(bool(v)))
