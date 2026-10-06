# 06: One Gate 整合驗證 + 文件

**What to build:** 完成版 One Gate 嘅端到端驗證同專案文件同步：in-process E2E 覆蓋三頁結構、theme 傳播、三語切換、config round-trip；對真實 OpenD/TWS 做 live probe check；建立 FILE_MAP.md（AGENTS.md 硬性規定嘅獨立目錄索引，覆蓋所有檔案包括新 gateway 檔）；CHANGELOG/README 最終 pass；清理所有臨時測試檔。

**Blocked by:**
- #03 第一頁 K綫測試（embed gui_kline）
- #04 第二頁 全功能測試（embed gui_fulltest）
- #05 第三頁 連綫測試（config editor + probe）

**Status:** ready-for-agent

- [ ] One Gate E2E 全數 PASS EXIT=0：三頁存在、theme toggle 真係重新指派咗 gui_kline 常數、繁中/簡中/EN 切換外殼文字全部生效、config save round-trip + restore、嵌入頁 objectNames 齊
- [ ] live probe check：Futu OpenD（127.0.0.1:11111）+ IB TWS/Gateway（127.0.0.1:4001）真連測試，回報合理 timing 或如實失敗原因
- [ ] FILE_MAP.md 建立，覆蓋專案所有檔案（含 gateway/ 新檔），格式跟 AGENTS.md：`[檔案路徑/名稱]` - **[一句話核心功能]**：詳細職責描述與依賴關係
- [ ] CHANGELOG.md 有 One Gate 完整 entry；README.md 運行表加入 `python gateway.py` + 核心功能更新
- [ ] repo 無任何臨時/測試 scratch 檔殘留（保持環境乾淨）
