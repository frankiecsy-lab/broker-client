"""One Gate Page 3 — 連綫測試（config.json editor + FUTU OpenD / IB Gateway probes）。

ticket #05 實裝：
- **Config editor**：載入 `modules/config.json` 現值入 form；save 時只改 loaded dict 入面嘅
  futu.host/port、ib.host/port、kline_num，再 dump 返（indent=4 + ensure_ascii=False）—
  source / kline_adj / ib.symbol_aliases 等其他欄位原封不動（json.load 保留插入順序）。
- **Futu probe**：行 QThread 唔 block UI — `OpenQuoteContext(host, port)` constructor 即刻連線
  （量度連線時間），再 `get_global_state()` RTT；失敗時如實講原因（port 冇開 / timeout），
  唔 fake success。futu SDK lazy import（import 要幾秒，唔好拖慢 UI 啟動）。
- **IB probe**：用 ib_async 嘅 `IB` class 直接開獨立連線，**clientId=98**（絕非 99 — app 主
  session 用 99，兩邊同時在線唔衝突）；量度 connectAsync handshake timing + reqCurrentTime
  server-time RTT；行完即刻 disconnect。QThread 入面開新 event loop 跑 async probe。

Theme 傳播：頁面級 QSS template（palette 由 gateway.theme.THEMES 注入，經 listener registry
跟隨外殼切換）— 同 fulltest_page 同一 pattern。

單獨運行：`python gateway/pages/connection_page.py`（standalone window，帶語言/theme 控制）。
"""
import json
import os
import sys
from pathlib import Path
from string import Template

# ── standalone bootstrap：直接跑呢個檔時將 project root 放落 sys.path（package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QThread, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

import gateway.theme as theme_mod  # noqa: E402 — module 引用（唔係 from-import，避免 stale value binding）
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402

# config.json 路徑 — pathlib 跨平台（AGENTS.md：禁 hardcode 斜線）
CONFIG_PATH = Path(__file__).resolve().parents[2] / 'modules' / 'config.json'


def _classify_error(e):
    """將 probe exception 分類成人話 reason_code（refused / timeout / other）— 如實回報，唔 fake success。"""
    import socket
    msg = str(e) or e.__class__.__name__
    low = msg.lower()
    if isinstance(e, (ConnectionRefusedError, ConnectionResetError)) or 'refuse' in low:
        code = 'refused'
    elif isinstance(e, (TimeoutError, socket.timeout)) or 'timeout' in low or 'timed out' in low:
        code = 'timeout'
    else:
        code = 'other'
    return {'ok': False, 'reason_code': code, 'detail': msg}


def _probe_futu(host, port):
    """Futu OpenD probe — 喺 QThread 入面跑。回 result dict（見 module docstring）。

    `OpenQuoteContext` constructor 即刻連線 → 量度連線時間；再 get_global_state() RTT。
    futu SDK lazy import（import 要幾秒）→ 唔拖慢 UI 啟動。
    """
    import time
    from futu import OpenQuoteContext, RET_OK

    t0 = time.monotonic()
    try:
        ctx = OpenQuoteContext(host=host, port=int(port))
    except Exception as e:
        return _classify_error(e)
    connect_ms = (time.monotonic() - t0) * 1000.0
    try:
        t1 = time.monotonic()
        ret_code, data = ctx.get_global_state()
        rtt_ms = (time.monotonic() - t1) * 1000.0
        if ret_code != RET_OK:
            return {'ok': False, 'reason_code': 'other',
                    'detail': f'get_global_state returned non-OK code {ret_code}'}
        return {'ok': True, 'connect_ms': connect_ms, 'rtt_ms': rtt_ms}
    except Exception as e:
        return _classify_error(e)
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def _probe_ib(host, port):
    """IB Gateway probe — 獨立 clientId=98（絕非 99；app 主 session 用 99，兩邊同時在線唔衝突）。

    直接用 ib_async `IB` class（唔係 modules.ib_client — 嗰個 hardcode 99）；
    QThread 入面開新 event loop 跑 async probe，15s watchdog timeout，行完即刻 disconnect。
    """
    import asyncio
    import time

    async def _run():
        from ib_async import IB
        ib = IB()
        try:
            t0 = time.monotonic()
            await ib.connectAsync(host, int(port), clientId=98)
            connect_ms = (time.monotonic() - t0) * 1000.0
            t1 = time.monotonic()
            await ib.reqCurrentTime()
            rtt_ms = (time.monotonic() - t1) * 1000.0
            return {'ok': True, 'connect_ms': connect_ms, 'rtt_ms': rtt_ms}
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass

    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(asyncio.wait_for(_run(), timeout=15))
        finally:
            loop.close()
    except (asyncio.TimeoutError, TimeoutError):
        return {'ok': False, 'reason_code': 'timeout', 'detail': 'handshake timeout (>15s)'}
    except Exception as e:
        return _classify_error(e)


class _ProbeWorker(QThread):
    """背景跑單個 probe 唔 block UI；完成時 emit (probe_name, result_dict)。"""

    finished_probe = Signal(str, dict)

    def __init__(self, probe_name, host, port, parent=None):
        super().__init__(parent)
        self.probe_name = probe_name
        self.host = host
        self.port = int(port)

    def run(self):
        if self.probe_name == 'futu':
            result = _probe_futu(self.host, self.port)
        else:
            result = _probe_ib(self.host, self.port)
        self.finished_probe.emit(self.probe_name, result)


# ── 頁面級 QSS template：只 style 本頁自己嘅 role / widget（pagetitle/pagebody 由 app-level [og="pagecard"] 規則處理）──
_QSS_TPL = Template('''
QWidget#connection_page { background-color: $window; }

QLabel[role="sectitle"] { color: $text; font-size: 16px; font-weight: bold; }
QLabel[role="formlabel"] { color: $muted; font-size: 13px; }
QLabel[role="result"] { color: $text; font-size: 13px; }

QLineEdit {
    background-color: $surface; color: $text;
    border: 1px solid $border; border-radius: 5px; padding: 4px 8px; font-size: 13px;
}
QLineEdit:focus { border: 1px solid $accent; }

QPushButton[og="actionbtn"] {
    background-color: $card; color: $text;
    border: 1px solid $border; border-radius: 6px; padding: 7px 14px; font-size: 13px;
}
QPushButton[og="actionbtn"]:hover { background-color: $border; }
QPushButton[og="actionbtn"]:disabled { background-color: $surface; color: $muted; }

QPushButton[og="savebtn"] {
    background-color: $accent; color: #FFFFFF; font-weight: bold;
    border: none; border-radius: 6px; padding: 7px 18px; font-size: 13px;
}
QPushButton[og="savebtn"]:hover { background-color: $accent_pressed; }
''')


class ConnectionPage(QWidget):
    """連綫測試頁 — config.json editor（保留結構）+ FUTU OpenD / IB Gateway probes。

    - Config save 只改 loaded dict 入面嘅 futu/ib host/port + kline_num → 其他欄位原封不動。
    - Probes 行 QThread；失敗如實回報 reason_code（refused/timeout/other），唔 fake success。
    - IB probe 用獨立 clientId=98 → 同 app 主 session（clientId=99）同時在線唔衝突。
    """

    def __init__(self):
        super().__init__()
        self.setObjectName('connection_page')
        self.setAttribute(Qt.WA_StyledBackground, True)   # bare QWidget 要呢個先會畫頁面級 QSS background
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)

        # ── Header card（標題 + 說明；pagetitle/pagebody 由 app-level [og="pagecard"] 規則 style）──
        header = self._make_card()
        hv = QVBoxLayout(header)
        hv.setContentsMargins(32, 24, 32, 24)
        self.title_lbl = QLabel()
        self.title_lbl.setProperty('role', 'pagetitle')
        self.body_lbl = QLabel()
        self.body_lbl.setProperty('role', 'pagebody')
        self.body_lbl.setWordWrap(True)
        hv.addWidget(self.title_lbl)
        hv.addSpacing(10)
        hv.addWidget(self.body_lbl)
        v.addWidget(header)

        # ── Config editor card ──
        cfg_card = self._make_card()
        cv = QVBoxLayout(cfg_card)
        cv.setContentsMargins(32, 24, 32, 24)
        self.cfg_title_lbl = QLabel()
        self.cfg_title_lbl.setProperty('role', 'sectitle')
        cv.addWidget(self.cfg_title_lbl)
        cv.addSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        def _form_label(key):
            lbl = QLabel(t(key, DEFAULT_LANG))
            lbl.setProperty('role', 'formlabel')
            return lbl

        self.futu_group_lbl = _form_label('conn_futu_group')
        self.host_lbl_a = _form_label('conn_host')
        self.port_lbl_a = _form_label('conn_port')
        self.ib_group_lbl = _form_label('conn_ib_group')
        self.host_lbl_b = _form_label('conn_host')
        self.port_lbl_b = _form_label('conn_port')
        self.kline_lbl = _form_label('conn_kline_num')

        def _edit(name):
            e = QLineEdit()
            e.setObjectName(name)
            return e

        self.futu_host_edit = _edit('futu_host')
        self.futu_port_edit = _edit('futu_port')
        self.ib_host_edit = _edit('ib_host')
        self.ib_port_edit = _edit('ib_port')
        self.kline_num_edit = _edit('kline_num')

        grid.addWidget(self.futu_group_lbl, 0, 0)
        grid.addWidget(self.host_lbl_a, 0, 1)
        grid.addWidget(self.futu_host_edit, 0, 2)
        grid.addWidget(self.port_lbl_a, 0, 3)
        grid.addWidget(self.futu_port_edit, 0, 4)
        grid.addWidget(self.ib_group_lbl, 1, 0)
        grid.addWidget(self.host_lbl_b, 1, 1)
        grid.addWidget(self.ib_host_edit, 1, 2)
        grid.addWidget(self.port_lbl_b, 1, 3)
        grid.addWidget(self.ib_port_edit, 1, 4)
        grid.addWidget(self.kline_lbl, 2, 1)
        grid.addWidget(self.kline_num_edit, 2, 2)
        cv.addLayout(grid)

        save_row = QHBoxLayout()
        self.save_btn = QPushButton(t('conn_save', DEFAULT_LANG))
        self.save_btn.setObjectName('save_cfg_btn')
        self.save_btn.setProperty('og', 'savebtn')
        self.save_btn.clicked.connect(self._on_save_config)
        self.status_lbl = QLabel('')
        self.status_lbl.setObjectName('cfg_status')
        self.status_lbl.setProperty('role', 'result')
        save_row.addWidget(self.save_btn)
        save_row.addSpacing(16)
        save_row.addWidget(self.status_lbl, 1)
        cv.addLayout(save_row)
        v.addWidget(cfg_card)

        # ── Probe card ──
        probe_card = self._make_card()
        pv = QVBoxLayout(probe_card)
        pv.setContentsMargins(32, 24, 32, 24)
        self.probe_title_lbl = QLabel()
        self.probe_title_lbl.setProperty('role', 'sectitle')
        pv.addWidget(self.probe_title_lbl)
        pv.addSpacing(12)

        btn_row = QHBoxLayout()
        self.futu_btn = QPushButton(t('conn_probe_futu_btn', DEFAULT_LANG))
        self.futu_btn.setObjectName('probe_futu_btn')
        self.futu_btn.setProperty('og', 'actionbtn')
        self.futu_btn.clicked.connect(lambda: self._on_probe('futu'))
        self.ib_btn = QPushButton(t('conn_probe_ib_btn', DEFAULT_LANG))
        self.ib_btn.setObjectName('probe_ib_btn')
        self.ib_btn.setProperty('og', 'actionbtn')
        self.ib_btn.clicked.connect(lambda: self._on_probe('ib'))
        btn_row.addWidget(self.futu_btn)
        btn_row.addSpacing(12)
        btn_row.addWidget(self.ib_btn)
        pv.addLayout(btn_row)
        pv.addSpacing(14)

        self.futu_result_lbl = QLabel('')
        self.futu_result_lbl.setObjectName('futu_result')
        self.futu_result_lbl.setProperty('role', 'result')
        self.futu_result_lbl.setWordWrap(True)
        self.ib_result_lbl = QLabel('')
        self.ib_result_lbl.setObjectName('ib_result')
        self.ib_result_lbl.setProperty('role', 'result')
        self.ib_result_lbl.setWordWrap(True)
        pv.addWidget(self.futu_result_lbl)
        pv.addSpacing(10)
        pv.addWidget(self.ib_result_lbl)
        v.addWidget(probe_card)

        v.addStretch(1)

        # ── state ──
        self._lang = DEFAULT_LANG
        self._cfg = None            # loaded config dict（save 時只改可編輯欄位）
        self._futu_worker = None    # QThread refs — 防 GC + 防重入
        self._ib_worker = None

        self._load_config()
        theme_mod.add_listener(self._on_theme_changed)   # apply_theme 完成後同步通知（PySide6 冇 styleSheetChanged）
        self._apply_embedded_theme(theme_mod.CURRENT)    # 初始 theme（讀 live module attr，避免 stale import binding）

    @staticmethod
    def _make_card():
        card = QWidget()
        card.setProperty('og', 'pagecard')
        card.setAttribute(Qt.WA_StyledBackground, True)
        return card

    # ── config editor ─────────────────────────────────────────────
    def _load_config(self):
        """載入 modules/config.json 現值入 form（保留原 dict 引用俾 save 用）。"""
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            self._cfg = json.load(f)   # dict 插入順序保留 → save 時其他欄位原封不動
        self.futu_host_edit.setText(str(self._cfg.get('futu', {}).get('host', '')))
        self.futu_port_edit.setText(str(self._cfg.get('futu', {}).get('port', '')))
        self.ib_host_edit.setText(str(self._cfg.get('ib', {}).get('host', '')))
        self.ib_port_edit.setText(str(self._cfg.get('ib', {}).get('port', '')))
        self.kline_num_edit.setText(str(self._cfg.get('kline_num', '')))

    def _on_save_config(self):
        """保存：只改 loaded dict 入面嘅可編輯欄位，dump 返（indent=4）— 其他欄位原封不動。"""
        try:
            futu_port = int(self.futu_port_edit.text().strip())
            ib_port = int(self.ib_port_edit.text().strip())
            kline_num = int(self.kline_num_edit.text().strip())
        except ValueError:
            self.status_lbl.setText(t('conn_int_err', self._lang))
            return
        cfg = self._cfg if isinstance(self._cfg, dict) else {}
        cfg.setdefault('futu', {})['host'] = self.futu_host_edit.text().strip() or '127.0.0.1'
        cfg['futu']['port'] = futu_port
        cfg.setdefault('ib', {})['host'] = self.ib_host_edit.text().strip() or '127.0.0.1'
        cfg['ib']['port'] = ib_port
        cfg['kline_num'] = kline_num
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)
                f.write('\n')
        except OSError as e:
            self.status_lbl.setText(t('conn_save_err', self._lang).format(err=e))
            return
        self.status_lbl.setText(t('conn_saved_ok', self._lang))

    # ── probes ────────────────────────────────────────────────────
    def _on_probe(self, name):
        """撳 probe 按鈕 → QThread 背景跑（唔 block UI）；運行中 disable 防重入。"""
        if name == 'futu':
            btn, result_lbl = self.futu_btn, self.futu_result_lbl
            worker_attr = '_futu_worker'
            host = self.futu_host_edit.text().strip() or '127.0.0.1'
            port_text = self.futu_port_edit.text().strip()
        else:
            btn, result_lbl = self.ib_btn, self.ib_result_lbl
            worker_attr = '_ib_worker'
            host = self.ib_host_edit.text().strip() or '127.0.0.1'
            port_text = self.ib_port_edit.text().strip()
        try:
            port = int(port_text)
        except ValueError:
            result_lbl.setText(t('conn_int_err', self._lang))
            return
        if getattr(self, worker_attr) is not None and getattr(self, worker_attr).isRunning():
            return   # 同一 probe 運行中 → 唔重入
        btn.setEnabled(False)
        result_lbl.setText(t('conn_testing', self._lang))
        worker = _ProbeWorker(name, host, port, parent=self)
        setattr(self, worker_attr, worker)
        worker.finished_probe.connect(self._on_probe_done)
        worker.start()

    def _on_probe_done(self, name, result):
        """probe 完成（QThread signal）→ 人話顯示狀態同速度；失敗如實講原因。"""
        btn = self.futu_btn if name == 'futu' else self.ib_btn
        lbl = self.futu_result_lbl if name == 'futu' else self.ib_result_lbl
        btn.setEnabled(True)
        if result.get('ok'):
            key = 'conn_futu_ok' if name == 'futu' else 'conn_ib_ok'
            text = t(key, self._lang).format(
                connect_ms=f"{result['connect_ms']:.0f}", rtt_ms=f"{result['rtt_ms']:.0f}")
        elif result.get('reason_code') in ('refused', 'timeout'):
            reason_key = f"conn_reason_{result['reason_code']}"
            text = t(f'conn_{"futu" if name == "futu" else "ib"}_fail', self._lang).format(
                reason=t(reason_key, self._lang))
        else:
            text = t(f'conn_{"futu" if name == "futu" else "ib"}_fail', self._lang).format(
                reason=result.get('detail', '?'))
        lbl.setText(text)

    # ── theme 傳播 ────────────────────────────────────────────────
    def _apply_embedded_theme(self, name: str):
        """套用頁面級 QSS（palette 值由 THEMES[name] 注入）— cascade 入本頁子 widget。"""
        pal = theme_mod.THEMES[name]
        self.setStyleSheet(_QSS_TPL.substitute(pal))

    def _on_theme_changed(self, name: str):
        """外殼 / standalone window theme 切換（apply_theme listener）→ 本頁跟住換。"""
        self._apply_embedded_theme(name)

    # ── i18n ──────────────────────────────────────────────────────
    def retranslate(self, lang: str):
        """外殼 / standalone window 語言切換時調用 — 全部文字跟隨（form 值唔變）。"""
        self._lang = lang
        self.title_lbl.setText(t('page_connection_title', lang))
        self.body_lbl.setText(t('page_connection_body', lang))
        self.cfg_title_lbl.setText(t('conn_cfg_title', lang))
        self.futu_group_lbl.setText(t('conn_futu_group', lang))
        self.ib_group_lbl.setText(t('conn_ib_group', lang))
        # Host/Port 三語同字 — 建檔時已 set，唔使重設
        self.kline_lbl.setText(t('conn_kline_num', lang))
        self.save_btn.setText(t('conn_save', lang))
        self.probe_title_lbl.setText(t('conn_probe_title', lang))
        self.futu_btn.setText(t('conn_probe_futu_btn', lang))
        self.ib_btn.setText(t('conn_probe_ib_btn', lang))


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(ConnectionPage, 'page_connection_title')
