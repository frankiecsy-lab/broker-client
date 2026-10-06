# 02: One Gate 骨架（外殼 + i18n + theme）

**What to build:** 「One Gate」主入口：一個匯合 FUTU / IB 等多個 broker gateway 功能嘅 PySide6 外殼。最上方多頁導航欄（按鈕配搭 QStackedWidget），三個頁面槽位 — K綫測試 / 全功能測試 / 連綫測試（本 ticket 先放 placeholder，其餘頁面預留位置以後再加）。工具列有暗/淺色模式切換 + 繁中/簡中/EN 三語語言切換。組件化硬性要求：每個頁面係獨立組件、底層都有 `if __name__ == '__main__':` 確保可以單獨開視窗運行 Debug，平時又能合體成一個大系統。

**Blocked by:** None (can start immediately)

**Status:** done (2026-10-06) — in-process smoke 87/87 PASS + 4 入口點 offscreen sanity EXIT=124 無 traceback

- [x] `python gateway.py` 開啟 One Gate 主窗口：頂部導航按鈕 + QStackedWidget 切換三頁（placeholder），其餘頁面預留位置
- [x] i18n 單一入口（STRINGS dict + t()）支援繁中/簡中/EN，外殼全部文字（nav、toolbar、window title）跟隨切換；三語係明確字串唔係 opencc 轉換
- [x] theme 模組：暗色 = gui_kline 現有配色（嵌入頁原生感）、淺色 = 新專業 palette；QSS 由 theme 統一生成，一鍵切換即時生效
- [x] 每個 page 組件檔底層有 `if __name__ == '__main__':`，單獨開視窗運行（帶基本語言/主題控制）
- [x] 純新增：唔改 test/ 同 modules/ 任何現有檔案
