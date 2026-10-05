"""MockBrokerClient — offline stand-in for broker_client.BrokerClient.

Same call surface as the real client (get_kline / stream_kline) but generates
synthetic sine-wave OHLCV locally, so the full test pipeline can be exercised
without OpenD or IB Gateway:

  get_kline(code, ktype, broker=None, kline_num=None) -> (True, DataFrame, "ok")
  stream_kline(...) -> async generator of FULL snapshot DataFrames; the first
    yield is the history baseline, then `tick_count` live ticks.

`fail_brokers` simulates a connection failure for specific brokers so the fail
path can be exercised offline too. time_key is a string (like Futu's) — the
worker normalizes it with pd.to_datetime exactly as it does for real data.
"""

import asyncio
import math
from datetime import datetime, timedelta

import pandas as pd

_KTYPE_STEP_S = {
    "K_1M": 60, "K_5M": 300, "K_15M": 900, "K_60M": 3600,
    "K_DAY": 86400, "K_WEEK": 7 * 86400,
}


class MockBrokerClient:
    def __init__(self, tick_count: int = 5, fail_brokers=()):
        self.tick_count = max(0, int(tick_count))
        self.fail_brokers = set(fail_brokers)

    # --- synthetic data ---------------------------------------------------------
    def _bars(self, code: str, ktype: str, n: int) -> pd.DataFrame:
        step_s = _KTYPE_STEP_S.get(ktype, 60)
        end = datetime(2026, 10, 5, 9, 30)
        rows = []
        for i in range(n):
            t = end - timedelta(seconds=step_s * (n - 1 - i))
            phase = i / max(n // 8, 1)
            close = 100.0 + 5.0 * math.sin(phase) + 0.01 * i
            rows.append({
                "time_key": t.strftime("%Y-%m-%d %H:%M:%S"),
                "open": round(close - 0.2, 4),
                "high": round(close + 0.5, 4),
                "low": round(close - 0.5, 4),
                "close": round(close, 4),
                "volume": int(1000 + 500 * math.sin(phase + 1)),
            })
        return pd.DataFrame(rows)

    # --- same surface as BrokerClient ---------------------------------------------
    async def get_kline(self, code, ktype, broker=None, kline_num=None):
        if broker in self.fail_brokers:
            return False, None, f"mock connection refused ({broker})"
        await asyncio.sleep(0.05)  # simulate network latency
        n = int(kline_num or 100)
        return True, self._bars(code, ktype, n), "ok"

    async def stream_kline(self, code, ktype, broker=None, kline_num=None):
        if broker in self.fail_brokers:
            raise ConnectionError(f"mock connection refused ({broker})")
        await asyncio.sleep(0.1)  # history fetch latency
        base = self._bars(code, ktype, int(kline_num or 100))
        yield base  # first yield = history baseline (matches the real client)
        for i in range(self.tick_count):
            await asyncio.sleep(0.2)  # ~5 ticks/sec — faster than a live feed so tests stay quick
            df = base.copy()
            idx = df.index[-1]
            new_close = round(float(df["close"].iloc[idx]) + 0.1 * math.sin(i), 4)
            df.loc[idx, "close"] = new_close
            df.loc[idx, "high"] = round(max(float(df["high"].iloc[idx]), new_close), 4)
            df.loc[idx, "volume"] = int(df["volume"].iloc[idx]) + 10
            yield df

    # --- lifecycle (no-ops; keeps the worker's __aenter__/__aexit__ calls valid) ----
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None
