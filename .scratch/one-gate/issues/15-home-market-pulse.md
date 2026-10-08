# 15 — 首頁「全球市場脈搏」：指數卡 dashboard（預設首頁）

用戶：設計一個首頁（第一個進入嘅 PAGE）— 所有市場重要指數 + 仔細資料，密集資訊、UI 可以花巧（能量圖）、專業直觀。先設計，用戶再改。

探測結論（真 OpenD）：HK/A股指數 snapshot+日K 全食到（HK.800000/800100/800700、SH.000001/000300/000016/000688、SZ.399001/399006）；**美股指數呢個 OpenD 不支援**（US..SPX 等 snapshot/K線都 ❌）→ 用 ETF 代理（SPY/QQQ/DIA/IWM/VIXY 全 OK）；JP/KS/TW/EU 代碼不認識。日K 要先 subscribe K_DAY 先 get_cur_kline 得。

設計（用戶可改清單 = home_page.py 頂部 HOME_INDICES 一個常量）：
- 三區：**港股**（恒指/國企/恒生科技）、**A股**（上證/深成指/滬深300/創業板/科創50）、**美股（ETF 代理，tooltip 如實講）**（SPY/QQQ/DIA/IWM/VIXY）。
- 每張卡：名（三語自設）+ code、大字價格（紅漲綠跌）、變動 + 變動%、**30-60 日走勢 sparkline（漸變面積，花巧位）**、**當日區間能量條**（low→high，marker 喺最新價位置）、footer 開/高/低/前收/成交額。
- 預設首頁：PAGE_KEYS[0]='home'（quotes 退第二位）；手動刷新 + 60 秒自動刷新 + 更新時間；逐卡失敗如實。

1. [X] `modules/market_pulse.py`：fetch_pulse（🤖 snapshot 一批有一隻不支援會拖爆成批 → 逐個 call，失敗只標該行）
2. [X] i18n：nav_home + 13 個指數名三語 + 分區/代理 tooltip
3. [X] `gateway/pages/home_page.py`：_IndexCard 全自畫（sparkline 漸變面積 + 區間能量條 + 紅漲綠跌跟 gk.C_UP/C_DOWN）+ 分區 grid + ScrollArea + worker/60s 自動刷新 + theme palette 跟隨
4. [X] app.py：PAGE_KEYS[0]='home' + NAV_DIRECT 最前；e2e_gui_quotes/futu_trade 斷言同步
5. [X] e2e `.scratch/e2e_gui_home.py` 全綠 15 checks + live 13/13 張卡（60 日 sparkline）+ 截圖 `.scratch/home_shot.png`（offscreen 無 CJK font 所以 □，結構如圖）
6. [X] README / CHANGELOG 同步；regression quotes/futu_trade e2e 全綠
