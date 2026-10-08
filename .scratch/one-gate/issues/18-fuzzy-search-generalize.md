# 18 — 模糊搜尋完全泛用化（任何一段字母/中文都直達標的）

用戶：「模糊輸入希望能完全泛用化 — 打 `NVD` 或 `英偉` 都要找出 `US.NVDA`；`700` 要看到結果包括 `HK.00700`，打『訊』字都能見到，任何一個字母或中文字都能找出相對應的。」

根因唔喺 completer（#17 已經中央化），喺 **搜尋排序**：舊 `_score` 淨計 `ql in code` → 用戶打嘅係 **ticker**（唔帶 `US.` 前綴），`NVD` 食最低級 contains 分再按字母序排 → 俾幾千條淹沒；跳字（`NVA`）完全 match 唔到；`700` 比 `HK.87001` 排前面（`00700` 嘅 '700' 喺 position 2）。

1. [X] `modules/symbol_search.py`：`_S_*` 分層常量 + `_score()` rewrite — code/ticker exact 100 > **ticker prefix** 90 > code prefix 80 > name prefix 75 > contains 70/55 > **跳字 subsequence** 40/35；`_pos_bonus()`（match 越前越高分）；`_subseq_pos()`；HK 代碼零填充 → ticker 加去零變體（`700`/`0700`/`00700` 同一個）。
2. [X] `gateway/symbol_input.py`：`CAND_LIMIT` 20 → 50（用戶要「睇到」，20 太容易截走）。
3. [X] `.scratch/test_symbol_search_fuzzy.py`（新，打真實 index cache、唔使 OpenD/GUI）：用戶例子逐條斷言 + 單個字母/中文字都要有結果 + CORE_TYPES 零污染 → 全綠。五份 GUI e2e（favorites / symbol_list / quotes / p8 / futu_trade）全綠。
4. [X] README 檔案地圖 / CHANGELOG 同步。

🤖 教訓：
- **模糊度同排序永远喺 `symbol_search._score`**，唔好喺 completer 加 hack — completer 只負責顯示 hits。
- 用戶打嘅係 **ticker**，唔係 code：任何「code 包含 query」嘅計法都會將 ticker 匹配壓到最低級 → ticker 必須獨立計分。
- 零填充市場（HK 5 位）要正規化：`700` = `0700` = `00700`。
- ⚠️ 資料限制（未解）：OpenD `get_stock_basicinfo` 嘅 `name` 係單語言（實測港股/中概/美股大市值全部中文名）→ 打 `tencent`/`apple` 無資料可 match。要支持英文公司名必須引入第二份數據源。
