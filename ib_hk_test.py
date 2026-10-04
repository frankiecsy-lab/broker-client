# =====================================================================
# ib_hk_test.py — 港股 / 港期 行情權限獨立測試（自訂交易所到 HK）
#
# 目的：驗證本 IB 帳號能否查到 HKG 股票、HKFUT 期貨數據，
#       並定位卡在哪一層：
#         A/B = 港股（0700 @HKG）：合約解析 vs. 全規格合約直接拉歷史
#         C/D = 港期（HSI @HKFUT）：合約明細 vs. 全規格合約直接拉歷史
#         E   = 對照組（MNQ @CME，已知有權限），確認連線本身正常
#
# 背景：IB 的 qualifyContracts / reqContractDetails 會受「交易權限」過濾，
#       可能回 Error 200；但 reqHistoricalData 用全規格合約（明確交易所+幣別）
#       只要「行情訂閱」在就能拿到數據。所以 B/D 才是決定性測試。
#
# 用法：python ib_hk_test.py   （需 IB Gateway 跑在 127.0.0.1:4001）
# =====================================================================

import asyncio
from ib_async import *

HOST, PORT = '127.0.0.1', 4001
CLIENT_ID = 230   # 📌 獨立 clientId，避免與主測試套件（200）衝突


async def run():
    ib = IB()
    await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=15)

    # 把 IB 推來的錯誤全部印出來（Error 200/354... 是診斷關鍵）
    def on_error(reqId, code, msg, contract):
        print(f"  ⚠️ [IB Error {code}] reqId={reqId} {contract}")
        print(f"     {msg[:200]}")
    ib.errorEvent += on_error

    try:
        # ---------- 0. 這個 session 登入的是哪個帳號（對照訂閱清單用）----------
        print("=" * 62)
        print("0. 本 Gateway session 的帳號編號")
        try:
            async with asyncio.timeout(20):
                vals = await ib.accountSummaryAsync('')   # 空字串 = Gateway 自動填目前登入帳號
            accounts = {v.account for v in vals}
            print(f"  -> {sorted(accounts)}")
        except TimeoutError:
            print("  ❌ API 取帳號資訊超時 → 請直接看 IB Gateway 視窗左上角的帳號編號")

        # ---------- A. 港股：合約解析（明確 HKG 交易所）----------
        print("=" * 62)
        print("A. 港股 0700 @HKG — qualifyContracts（合約解析）")
        c = Stock('0700', 'HKG', 'HKD')
        try:
            async with asyncio.timeout(25):
                q = await ib.qualifyContractsAsync(c)
            print(f"  -> conId={c.conId} exchange={c.exchange} {'✅ 解析成功' if c.conId else '❌ 解析失敗'}")
        except TimeoutError:
            print("  ❌ 25s 超時（IB 未回應）")

        # ---------- B. 港股：全規格合約直接拉歷史（繞過解析）----------
        print("=" * 62)
        print("B. 港股 0700 @HKG — reqHistoricalData（全規格合約直拉，決定性測試）")
        c = Stock('0700', 'HKG', 'HKD')
        try:
            async with asyncio.timeout(30):
                bars = await ib.reqHistoricalDataAsync(c, endDateTime='', durationStr='5 D',
                                                       barSizeSetting='1 day', whatToShow='TRADES',
                                                       useRTH=False, formatDate=1)
            if bars:
                df = util.df(bars)
                print(f"  ✅ 拿到 {len(df)} 根日線:")
                print(df.tail())
            else:
                print("  ❌ 回傳空（無數據 / 無權限）")
        except TimeoutError:
            print("  ❌ 30s 超時")

        # ---------- C. 港期：合約明細（HKFUT）----------
        print("=" * 62)
        print("C. 港期 HSI @HKFUT — reqContractDetails（合約明細）")
        try:
            async with asyncio.timeout(25):
                cds = await ib.reqContractDetailsAsync(Future(symbol='HSI', exchange='HKFUT', currency='HKD'))
            if cds:
                for cd in cds[:4]:
                    ct = cd.contract
                    print(f"  ✅ {ct.symbol} localSymbol={ct.localSymbol} lastTradeDate={ct.lastTradeDateOrContractMonth}")
            else:
                print("  ❌ 回傳空（無合約 / 無權限）")
        except TimeoutError:
            print("  ❌ 25s 超時")

        # ---------- D. 港期：全規格合約直接拉歷史（含到期月，決定性測試）----------
        print("=" * 62)
        print("D. 港期 HSI @HKFUT — reqHistoricalData（全規格合約直拉，試鄰近月份）")
        for month in ('202611', '202612'):   # 2026-10 → 最近月 11 月、次季 12 月
            # ⚠️ 此版 ib_async 的 Future 位置參數順序是 (symbol, lastTradeDateOrContractMonth, exchange, ...)，
            #    第二個位置不是交易所！一律用關鍵字參數才不會放錯欄位。
            c = Future(symbol='HSI', exchange='HKFUT', currency='HKD', lastTradeDateOrContractMonth=month)
            try:
                async with asyncio.timeout(30):
                    bars = await ib.reqHistoricalDataAsync(c, endDateTime='', durationStr='5 D',
                                                           barSizeSetting='1 day', whatToShow='TRADES',
                                                           useRTH=False, formatDate=1)
                if bars:
                    df = util.df(bars)
                    print(f"  ✅ {month}: 拿到 {len(df)} 根日線:")
                    print(df.tail())
                    break
                else:
                    print(f"  ❌ {month}: 回傳空")
            except TimeoutError:
                print(f"  ❌ {month}: 30s 超時")

        # ---------- E. 對照組：MNQ @CME（已知有權限）----------
        print("=" * 62)
        print("E. 對照組 MNQ @CME — reqHistoricalData（確認連線正常）")
        try:
            async with asyncio.timeout(30):
                cds = await ib.reqContractDetailsAsync(Future(symbol='MNQ', exchange='CME'))
            if cds:
                ct = sorted((cd.contract for cd in cds), key=lambda x: x.lastTradeDateOrContractMonth)[0]
                bars = await ib.reqHistoricalDataAsync(ct, endDateTime='', durationStr='5 D',
                                                       barSizeSetting='1 day', whatToShow='TRADES',
                                                       useRTH=False, formatDate=1)
                print(f"  {'✅' if bars else '❌'} MNQ {ct.lastTradeDateOrContractMonth}: {len(bars) if bars else 0} 根日線")
            else:
                print("  ❌ 對照組合約明細為空（連線有問題？）")
        except TimeoutError:
            print("  ❌ 30s 超時")

    finally:
        ib.disconnect()


if __name__ == '__main__':
    asyncio.run(run())
