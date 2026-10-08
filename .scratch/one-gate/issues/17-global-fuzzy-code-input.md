# 17 — 全域標的代碼輸入：模糊輸入集中化（GLOBAL）

用戶：收藏嘅代碼輸入要有模糊輸入，**記低** — 全域代碼輸入都要有，呢個功能 GLOBAL 使用、集中處理。

現況（三份各自砌，行為唔齊）：
- `gui_kline.FuzzyCompleter` — K線頁 + 行情格共用（item =「CODE  名稱」、`filterAcceptsRow` 全放行）→ 有效。
- `futu_trade_page` — 自己砌 QCompleter + MatchContains，model 由 worker `stock_search` 餵（item 都係「CODE  名稱」）→ 有效但重複。
- `favorites_page` — 砌咗個**壞嘅**：model 淨返 code + `MatchContains` → QCompleter 用輸入去 filter 個 model，打中文名（例：「騰訊」）model 入面根本冇中文 → popup 永遠空。即用戶所講「要模糊輸入」。

設計：新檔 `gateway/symbol_input.py` = 標的代碼輸入單一事實來源。
- `FuzzyCompleter`：QCompleter 子類。item =「CODE  名稱（跟語言，display_for 同一把尺）」；`filterAcceptsRow` 全放行（排序交返 search）；冇 hit / 輸入已係準確 code → 空 model 唔彈窗（pass-through）；唔係用戶緊輸入（無 focus）唔彈窗。
- `make_search(directory, types, limit)`：本地 index 標準搜尋 fn（含準確 code 短接）。
- `attach_symbol_input(edit, search_fn=...)`：一次裝好 debounce + completer + activated（揀咗淨返乾淨 CODE 入欄，`singleShot(0)` 蓋返 QCompleter 寫入嘅 item 文字）。
- 異步來源（交易頁 worker）→ 照樣 `set_hits(entries)`，共用同一個 item 格式 / popup 規則。
- 各頁只留「邊度 search、揀完做咩」；唔准再自己砌 QCompleter。

1. [X] `gateway/symbol_input.py`（FuzzyCompleter / make_search / attach_symbol_input / apply_item / display_name）
2. [X] gui_kline、quotes_page、favorites_page、futu_trade_page 全部改經呢度（刪走各自版本 + 重複 `_display_name`；行情頁補返統一 debounce 200ms、retranslate 統一 `completer.lang` — 🤖 之前 zh_cn 會被轉做繁體名）
3. [X] e2e 全綠：favorites 30 checks（新增中文名 → 有候選 + item 連名 / 揀完淨返 CODE / 準確 code 清空）、futu_trade 全綠（Part 5 唔使改）、quotes + p8 補 debounce 等待後全綠
4. [X] README / CHANGELOG / FILE_MAP 同步

🤖 教訓：QCompleter 嘅 model 係俾佢自己 filter 嘅 — 自己砌嗰陣如果 model 淨返 code、輸入係中文名，`MatchContains` 永遠 match 唔到 → popup 空。正路係 model 只放 search hits（item 連名）+ `filterAcceptsRow` 全放行。
