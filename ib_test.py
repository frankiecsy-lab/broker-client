# =====================================================================
# IB API K線參數合法值清單 (区分大小写 / Case-Sensitive)
# =====================================================================

# 1. durationStr (歷史長度格式: '[數字] [單位]')
# 单位支持:
#   'S' (秒)  -> 例: '30 S'
#   'D' (天)  -> 例: '1 D', '5 D'  (1~5min線常用)
#   'W' (週)  -> 例: '1 W'
#   'M' (月)  -> 例: '1 M', '3 M'  (10~30min線常用)
#   'Y' (年)  -> 例: '1 Y', '5 Y'  (日線常用)

# 2. barSizeSetting (K線週期)
# 周期支持:
#   秒級:   '1 sec', '5 secs', '15 secs', '30 secs'
#   分鐘級: '1 min', '2 mins', '3 mins', '5 mins', '10 mins', '15 mins', '20 mins', '30 mins'
#   小時級: '1 hour', '2 hours', '3 hours', '4 hours', '8 hours'
#   日以上: '1 day', '1 week', '1 month'

# 3. 官方搭配限制 (防止 TWS 報錯拒絕)
#   - 1 min / 2 mins 線   -> durationStr 最大限制 '1 D' 到 '2 D'
#   - 3 min / 5 min / 10 min 線 -> durationStr 最大限制 '1 M' (約30天)
#   - 15 min / 30 min 線  -> durationStr 最大限制 '1 M' 到 '2 M'
#   - 1 hour 線以上        -> durationStr 最大可達 '1 Y'
#   - 1 day (日線)         -> durationStr 最大可達 '10 Y'
# =====================================================================

import pandas as pd
import asyncio
from ib_client import *

pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
pd.set_option('display.width', 1000)
pd.set_option('display.float_format', lambda x: '%.2f' % x)

class TickList:
    def __init__(self, max_ticks=100):
        self.max_ticks = max_ticks
        self.df = None

    def update(self, df_incoming):
        if self.df is None:
            self.df = df_incoming.copy()
            self.df.set_index('time', inplace=True)
        else:
            df_incoming_indexed = df_incoming.set_index('time')
            self.df = pd.concat([self.df, df_incoming_indexed])
            if len(self.df) > self.max_ticks:
                self.df = self.df.iloc[-self.max_ticks:]
        return self.df



class KlineChart:
    """專門負責在外部「長大」與管理 K 線圖的工具"""
    def __init__(self, max_bars=1000):
        self.max_bars = max_bars
        self.df = None

    def update(self, df_incoming):
        """傳入新 Bar，自動完成合併、去重、限長，並回傳最完整的 K 線圖"""
        if self.df is None:
            self.df = df_incoming.copy()
        else:
            self.df = pd.concat([self.df, df_incoming])
            self.df = self.df[~self.df.index.duplicated(keep='last')]
            self.df.sort_index(inplace=True)
            if len(self.df) > self.max_bars:
                self.df = self.df.iloc[-self.max_bars:]
        return self.df


async def get_kline(*,security_type, symbol,durationStr='1 M',barSizeSetting='1 day'):

    # 🔥 使用 async with 呼叫我們改寫好的 IBClient
    async with IBClient(host='127.0.0.1', port=4001, client_id=200) as client:
        print("====== 成功透過 ib_async 入口連線 ======")
        # 💡 呼叫範例 1：拉取股票 (STK) 的 5 分鐘 K 線
        stock = await client.get_kline(symbol=symbol, security_type=security_type, durationStr=durationStr, barSizeSetting=barSizeSetting)
        if stock['status'] == 'OK':
            data = stock['data']
            print(f"✅ 成功獲取 DataFrame (筆數: {len(data)}):\n")
            print(data.tail())
        else:
            print(f"❌ 獲取失敗！原因: {stock['message']}")


async def get_kline_live(*,security_type, symbol,durationStr='1 M',barSizeSetting='1 day'):

    # 🔥 使用 async with 呼叫我們改寫好的 IBClient
    async with IBClient(host='127.0.0.1', port=4001, client_id=200) as ib:
        stream = ib.get_kline_live(symbol, security_type=security_type, durationStr=durationStr, barSizeSetting=barSizeSetting,
                                          only_new_bar=True)

        chart = KlineChart(max_bars=500)

        async for df_incoming in stream:
            # 一行代碼搞定拼接，拿到永遠最新的完整 K 線圖！
            full_df = chart.update(df_incoming)

            print(f"📈 完整 K 線圖已更新，目前長度: {len(full_df)}")
            print(full_df.tail(20))

        '''is_first = True
        async for df in stream:
            if is_first:
                print("\n📦 【第一步：成功獲取初始歷史數據】")
                print(df.tail(5))  # 列印最後 5 條歷史記錄
                is_first = False
            else:
                print("\n🔔 【第二步：收到全新自動更新的 K 線】")
                print(df)'''


async def run_ticks_monitoring(*, security_type, symbol, max_records=20):
    async with IBClient(host='127.0.0.1', port=4001, client_id=200) as ib:
        # 呼叫 Class 內剛剛寫好的逐筆成交方法
        stream = ib.get_ticks_live(symbol, security_type=security_type)

        tick_table = TickList(max_ticks=max_records)

        async for df_incoming in stream:
            # 外部一行代碼完成明細拼接
            full_tick_df = tick_table.update(df_incoming)

            print(f"\n⚡ [Time & Sales - {symbol}] 收到即時成交成交明細！")
            print(full_tick_df.tail(10))  # 列印最新的 10 筆原始 Tick 記錄


class OrderBook:
    """專門負責在外部展示與解析實時訂單流（五檔盤口）的工具"""

    def update(self, depth_dict):
        """將底層推來的數據轉換成直觀的五檔對比 DataFrame"""
        df_bids = pd.DataFrame(depth_dict['bids'])
        df_asks = pd.DataFrame(depth_dict['asks'])

        # 將買盤與賣盤橫向合併，做成標準的盤口看板
        order_book_df = pd.concat([df_bids, df_asks], axis=1)
        order_book_df.index = range(1, len(order_book_df) + 1)
        order_book_df.index.name = '檔位'
        return order_book_df


# 📌 外層封裝函數：無 self，使用 *, 強制關鍵字參數，位置完全自由對調
async def run_order_flow_monitoring(*, security_type, symbol, rows=5):
    async with IBClient(host='127.0.0.1', port=4001, client_id=200) as ib:
        stream = ib.get_depth_live(symbol, security_type=security_type, num_rows=rows)
        book_tool = OrderBook()

        async for depth_data in stream:
            # 實時更新五檔
            current_book = book_tool.update(depth_data)

            print("\033[c", end="")  # 🚀 核心視覺優化：刷新控制台畫面（清屏），讓五檔盤口看起來像看盤軟體一樣在原地閃爍
            print(f"=============================================")
            print(f"🔥 [Order Flow 實時訂單流] 商品: {symbol} | 時間: {depth_data['time'].strftime('%H:%M:%S.%f')[:-3]}")
            print(f"=============================================")
            print(current_book)
            print(f"=============================================")


# 啟動非同步主程式（相容 Python 3.12+ 的標準寫法）
if __name__ == '__main__':
    #has permission
    #asyncio.run(get_kline(symbol='MNQ', security_type='FUT',durationStr='1 D',barSizeSetting='15 mins'))
    #asyncio.run(get_kline(symbol='NVDA', security_type='STK',durationStr='1 D',barSizeSetting='15 mins'))
    #asyncio.run(get_kline(symbol='USDJPY', security_type='CASH', durationStr='1 D', barSizeSetting='15 mins'))

    #asyncio.run(run_ticks_monitoring(security_type='CASH', symbol='USDJPY', max_records=50))

    #asyncio.run(run_order_flow_monitoring(security_type='CRYPTO', symbol='BTC', rows=5))
    #asyncio.run(run_order_flow_monitoring(security_type='STK', symbol='NVDA', rows=5))
    asyncio.run(run_order_flow_monitoring(security_type='FUT', symbol='MNQ', rows=5))

    #asyncio.run(get_kline_live(symbol='USDJPY', security_type='CASH', durationStr='1 D', barSizeSetting='15 mins'))
    #asyncio.run(get_kline_live(symbol='MNQ', security_type='FUT', durationStr='1 D', barSizeSetting='15 mins'))
    #asyncio.run(get_kline_live(symbol='NVDA', security_type='STK', durationStr='1 D', barSizeSetting='15 mins'))


    #no persmission
    #asyncio.run(get_kline(symbol='BTC', security_type='CRYPTO', durationStr='1 D', barSizeSetting='15 mins'))

    #asyncio.run(run_ticks_monitoring(security_type='STK', symbol='NVDA', max_records=50))
    #asyncio.run(run_ticks_monitoring(security_type='FUT', symbol='MNQ', max_records=50))
    #asyncio.run(run_ticks_monitoring(security_type='CRYPTO', symbol='BTC', max_records=50))

    #asyncio.run(run_order_flow_monitoring(security_type='CASH', symbol='USDJPY', rows=5))
    #asyncio.run(get_kline_live(symbol='BTC', security_type='CRYPTO', durationStr='1 M', barSizeSetting='1 day'))







