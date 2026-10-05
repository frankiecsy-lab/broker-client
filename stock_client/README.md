# Tomato Broker Server

Professional PySide6 desktop GUI that aggregates the two brokers from
`D:\coding\broker_client` (Interactive Brokers + Futu) behind one interface.
First feature: a **Broker Test Tool** that exercises `get_kline` and
`stream_kline` across every code × ktype × broker × function combination, with
per-row results, one-click Test All, live stream charts, and full parameter
control.

## Prerequisites

- Python 3.12 (PySide6 is the only Qt binding used)
- The broker library at `D:\coding\broker_client` (path configurable in settings;
  default is that folder). It must contain `broker.py` + `config.json`.
- For live testing: **Futu OpenD** running on `127.0.0.1:11111` and
  **IB Gateway / TWS** with the API port open (`127.0.0.1:4001`).

```bat
pip install -r requirements.txt
python main.py
```

## Using the Test Tool

- **Codes** — Futu-style canonical format (`US.AAPL`, `HK.00700`); add, edit
  (double-click), or remove entries. The list persists between runs.
- **Filters** — ktype / broker / function checkboxes rebuild the visible matrix
  on change (default: 2 codes × 6 ktypes × 2 brokers × 2 funcs = 48 rows).
- **Params** — `kline_num` (history depth), stream duration in seconds, and two
  toggles:
  - *Strict pass* — ON: a stream with baseline but zero live ticks is reported
    as **OK-no-ticks** (amber; normal outside market hours) instead of Pass.
  - *Use mock broker* — runs the whole pipeline against an offline
    `MockBrokerClient` (sine-wave OHLCV, 5 simulated ticks per stream). No
    OpenD / IB Gateway needed and no broker SDK is even imported — useful for
    UI development and CI. The toggle persists.
- **Per row** — Run button, status pill (Pending/Running/Pass/Fail/OK-no-ticks/
  Aborted), expandable body: get_kline shows a ≤20-row preview table + elapsed
  ms; stream rows show baseline/tick stats, first/last timestamps and a live
  close-price sparkline (~3 Hz). Failures auto-expand with the error text.
- **Test All** — runs every visible row (get_klines first, then streams, max 4
  concurrent) with progress + running pass/fail counts; Abort cancels in-flight
  tests and lets broker cleanup finish before reporting.

Pass criteria: baseline + ≥1 live tick = **Pass**; baseline + 0 ticks =
**OK-no-ticks**; no baseline = **Fail**.

## Internationalization (3 languages)

Translations live in top-level JSON files, one per language — edit them freely:

| File | Language |
|---|---|
| `i18n/zh_TW.json` | Traditional Chinese (default) |
| `i18n/zh_CN.json` | Simplified Chinese |
| `i18n/en_US.json` | English |

Keys are nested by area (`app`, `menu`, `nav`, `status`, `testtool`). All three
files must keep the same key set — a startup check warns on drift, and a missing
key renders as the raw key string (visible bug signal). Switch languages live
from the menu; the choice persists.

## Themes

Dark / light, switchable from the menu (persisted):

- `app/theme/qss/dark.qss`, `app/theme/qss/light.qss` — hand-editable stylesheets
- `app/theme/palette.py` — semantic colors used in code (status dots, result
  pills, sparkline stroke); keep in sync with the QSS when recoloring

## Settings

Persisted via QSettings under `%APPDATA%` (registry key
`HKCU\Software\Tomato Broker Server\TomatoBrokerServer`): language, theme,
broker directory, and last-used test-tool parameters.

## Smoke tests (offscreen, no display needed)

```bat
set QT_QPA_PLATFORM=offscreen
python _smoke_l0.py   :: app shell + i18n parity + themes + real bridge probe
python _smoke_m3.py   :: test-matrix state machine with a fake bridge (no brokers)
python _smoke_l2.py   :: full pipeline through the REAL worker, offline via mock broker
python _smoke_l3.py   :: LIVE e2e — needs OpenD + IB Gateway running; run alone (clientId 99)
```

Each prints `SMOKE_*_OK` on success and exits non-zero otherwise. `_smoke_l0`
works whether or not the gateways are running — its probe results must match
the actual port state. `_smoke_l3` prints a SKIP message (exit 2) if either
gateway is down; it proves Check Connection double-green, `get_kline` via both
brokers with real rows, a short stream window (Pass in market hours /
OK-no-ticks outside — both acceptable), and that a clean shutdown releases IB
clientId 99 (a fresh bridge re-probes green immediately). Run it alone: IB
allows one session per clientId. For a manual live check instead: run the app,
use File → Check Connection (both dots green), then Test All during market
hours (streams should pass) or outside them (OK-no-ticks is expected, not a
failure).
