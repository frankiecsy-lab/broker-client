# 07: Fulltest stream rows → 持續 live（用戶確認方向）

**What to build:** 用戶反映 fulltest_page HK.HSImain stream_kline「不會刷新」— 根因 = gui_fulltest `_bounded_consume` 係 5s bounded window by design（kline_page 先係無界連續）。用戶確認改持續 live：baseline 收到 → 即刻出 verdict（✅ PASS），之後 live tick 持續更新結果格 + 數據表，到 Stop / 重跑為止。

**Blocked by:** None

**Status:** done (2026-10-06) — offscreen smoke 12/12 PASS + 真 E2E（OpenD）全 PASS；CHANGELOG/README 已同步

- [X] gui_fulltest.py：`_bounded_consume` → `_start_live`（baseline wait 30s timeout + monotonic tick token + 重跑先 cancel 舊 task）；新 `stream_tick(idx, token, payload)` signal
- [X] GUI 端：`_on_stream_tick` slot（token guard 丟 stale tick + 數據表 throttle TICK_UI_INTERVAL=0.25s）；Stop/清理清 `_tick_gen`
- [X] offscreen smoke 驗證（fake stream generator）：baseline → PASS、live_updates 持續計上、重跑作廢舊 tick、Stop 清晒 + ctx cleanup — **12/12 PASS**
- [X] CHANGELOG/README 同步 + E2E 兼容確認 — 真 E2E `test/e2e_gui_fulltest.py`（OpenD）全 PASS，零改動 e2e script
