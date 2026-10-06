# 03: 第一頁 K綫測試（embed gui_kline）

**What to build:** One Gate 第一頁 = 完整可用嘅 K綫測試：嵌入現有主 GUI（gui_kline）嘅 central widget，模糊搜尋標的 / get+stream K綫圖表 / i18n / FETCH 全部照常用；One Gate 嘅主題切換要傳播到嵌入頁（暗/淺色都正確渲染）；頁面可單獨開視窗運行 Debug。

**Blocked by:** #02 One Gate 骨架（外殼 + i18n + theme）

**Status:** done (2026-10-06) — in-process smoke 31/31 PASS + 2 入口點 offscreen sanity EXIT=124 無 traceback

- [x] takeCentralWidget() 嵌入模式：保留原 MainWindow Python 引用 alive（thread lifecycle 綁定），One Gate 退出時觸發原 closeEvent 清理鏈（fetch wait → request_shutdown → thread.wait(3000)）
- [x] theme 傳播 = 運行時重新指派 gui_kline 模組級顏色常數 + chart redraw；**零改動 gui_kline.py 本身**
- [x] `if __name__ == '__main__':` 單獨開視窗顯示完整 K綫頁（模糊搜尋、get/stream 可用）
- [x] E2E：One Gate 主窗口入面嵌入頁嘅 objectNames 存在
