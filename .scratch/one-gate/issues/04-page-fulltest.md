# 04: 第二頁 全功能測試（embed gui_fulltest）

**What to build:** One Gate 第二頁 = 完整可用嘅綜合測試矩陣：嵌入 #01 增強後嘅 gui_fulltest central widget（四項增強自動帶入），Run All 全並發 / Stop cancel+清理 / 成功行數據拆疊全部照常用；主題切換傳播到嵌入頁；頁面可單獨開視窗運行 Debug。

**Blocked by:**
- #01 Fulltest 頁四項增強（embed 嗰個就係改完嘅 gui_fulltest，避免重複驗證）
- #02 One Gate 骨架（外殼 + i18n + theme）

**Status:** done (2026-10-06) — in-process smoke 39/39 PASS + 2 入口點 offscreen sanity EXIT=124 無 traceback

- [X] 同 K綫頁同一嵌入模式：保留原 MainWindow 引用 alive + closeEvent 清理鏈（request_shutdown → wait(3000)）
- [X] theme 傳播到嵌入 fulltest 頁（淺色模式下表格/結果色仍清晰可讀）
- [X] `if __name__ == '__main__':` 單獨開視窗顯示完整測試矩陣（Run All / Stop 可用）
- [X] E2E：One Gate 主窗口入面嵌入頁嘅 objectNames 存在；Run All wiring 喺 One Gate 內生效
