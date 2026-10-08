# 10 — 標的列表頁：全 index 表格 + 市場/種類 FILTER + 模糊輸入 + 一鍵更新 + 底部數字

設計決定：窩輪入 shared index（HK 14,929 + US 828 實測可得）但 `search()` 預設排除 WARRANT（其他頁 autocomplete 唔受污染）；期權老實講 0（OpenD 唔支援枚舉、IB 唔可以無標的枚舉）。

1. [X] symbol_search：WARRANT 入 plan（跳 delisting）+ search(types=) 預設 CORE_TYPES（p8 無 regression）
2. [X] symbol_list_page：表格/FILTER/模糊/一鍵更新/計數/記憶/i18n/theme（修咗 1 個：done 訊息要喺 refresh 之後先 set，否則被 status 蓋住）
3. [X] shell 註冊 + i18n + e2e hermetic 全綠
4. [X] live：fetch 40,585（窩輪 15,757）、search 預設 0 窩輪 / types=None 744、shell 六頁 smoke ✅
5. [X] README / CHANGELOG 同步
