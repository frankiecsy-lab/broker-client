# 28 — MA 四條線個別顯示 + 行情頁指標逐個揀

需求（用戶）：「MA 四個週期每個加CHECKBOX 可以切換是否顯示，行中顯示指標要個別選擇」。

設計（單一事實來源，唔新增狀態層）：
- MA 逐線可見性 = instance params 新增 `show1..show4`（bool ParamSpec，預設 1）→ 管理頁參數欄自動出 CHECKBOX；`compute_ma` 對隱藏線 **唔輸出 key** → 唔畫、唔入 Y-fit、唔計 panel 值；`_plot_ma` 用 `sl.get`。改參數 → config_version bump → cache 自動失效 = 串流照同步。
- 行情頁頂欄加「指標選項」menu（QToolButton+QMenu，逐個指標 checkable = `set_enabled`；MA 有 sub-menu 四條線 = `update(params)`）。總開關 ind_toggle 保留（頁面級 all-off，唔碰全局）；逐個揀直接食 manager 嘅 enabled — 同 K線頁掣列／管理頁 checkbox 天然雙向同步（現有 listener 鏈）。
- `_params_summary` / 摘要 skip bool → 摘要照舊「5/10/20/60」。

1. [X] indicators.py：ParamSpec.is_bool + clamp + summary skip + MA show1..4 + compute 跳過 + _plot_ma .get
2. [X] indicators_page：bool 參數 → QCheckBox（_editor_params 跟食）
3. [X] quotes_page：指標選項 menu（逐指標 + MA sub-menu）+ i18n + QSS
4. [X] 測試：test_indicators_common 加 bool/show 斷言 + e2e_gui_quotes Part 6.6 改行 menu
5. [X] README / CHANGELOG 同步
