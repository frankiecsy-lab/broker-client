"""批量測試 runner：跑 futu_get_kline_index.py 入面全部 get_kline / stream_kline case，逐個報告成功/失敗。

STREAM case 觀察固定窗口（window），每次 yield 比較「最後一行 signature」（row數/time_key/close/volume）
— 有變先計一次 live update；初始 keepUpToDate 批次補發（同舊數據重送）唔會誤報。
用法：python test/_run_all_tests.py   （約 8-10 分鐘，背景跑）
"""
import os
import sys
import asyncio
import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules import *   # noqa


def now():
    return datetime.datetime.now().strftime('%H:%M:%S')


def sig(df):
    """K 線表最後一行嘅緊湊 signature — 用於偵測「有冇新數據」"""
    if df is None or len(df) == 0:
        return ('empty',)
    r = df.iloc[-1]
    return (len(df), str(r['time_key']), round(float(r['close']), 4), int(r['volume']))


async def run_get(code, broker, timeout=90):
    print(f"\n=== [{now()}] get_kline {code} ({broker}) ===", flush=True)
    async with BrokerClient() as client:
        try:
            status, data, message = await asyncio.wait_for(
                client.get_kline(code=code, ktype='K_1M', broker=broker, kline_num=None), timeout)
        except asyncio.TimeoutError:
            print(f"RESULT FAIL (timeout {timeout}s)", flush=True)
            return False
        except Exception as e:
            print(f"RESULT FAIL ({type(e).__name__}: {e})", flush=True)
            return False
    if not status or data is None:
        print(f"RESULT FAIL ({message})", flush=True)
        return False
    print(f"RESULT OK rows={len(data)} first_tk={data['time_key'].iloc[0]} last_tk={data['time_key'].iloc[-1]}", flush=True)
    return True


async def run_stream(code, broker, window):
    print(f"\n=== [{now()}] stream_kline {code} ({broker}) observe {window}s ===", flush=True)
    state = {'snapshots': 0, 'updates': 0, 'first': None, 'last': None, 'ended': '?'}

    async with BrokerClient() as client:
        gen = client.stream_kline(code=code, ktype='K_1M', broker=broker, kline_num=None)

        async def feed():
            prev = None
            async for df in gen:
                s = sig(df)
                state['snapshots'] += 1
                if state['first'] is None:
                    state['first'] = s
                state['last'] = s
                if prev is not None and s != prev:
                    state['updates'] += 1
                    print(f"    UPDATE #{state['updates']} rows={s[0]} last_tk={s[1]} close={s[2]} vol={s[3]}", flush=True)
                prev = s
            state['ended'] = 'generator-returned'

        task = asyncio.ensure_future(feed())
        await asyncio.sleep(window)
        if not task.done():
            state['ended'] = f'timeout-{window}s-cancelled'
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"    feed exception: {type(e).__name__}: {e}", flush=True)

    f, l = state['first'], state['last']
    ok = f is not None   # 收到初始快照先算 stream 成功啟動
    live = state['updates'] > 0
    print(f"RESULT {'OK' if ok else 'FAIL'} snapshots={state['snapshots']} live_updates={state['updates']} "
          f"ended={state['ended']} first_tk={f[1] if f and len(f) > 1 else '-'} last_tk={l[1] if l and len(l) > 1 else '-'}", flush=True)
    return ok, live


# 🤖 同 futu_get_kline_index.py 一樣嘅順序：每個 code 先 get 後 stream，futu 全部先、ib 全部後
CASES = [
    ('HK.HSImain', 'futu'), ('HK.00700', 'futu'), ('US.NQmain', 'futu'), ('US.NVDA', 'futu'),
    ('HK.HSImain', 'ib'),   ('HK.00700', 'ib'),   ('US.NQmain', 'ib'),   ('US.NVDA', 'ib'),
]

# 🤖 觀察窗口：港股已收市（30s 足夠確認靜默）；NQ 期貨 Globex 24h session 而家有成交；NVDA 盤後稀疏俾多啲時間
STREAM_WINDOWS = {'HK.HSImain': 30, 'HK.00700': 30, 'US.NQmain': 60, 'US.NVDA': 90}


async def main():
    print(f"### batch test start {datetime.datetime.now()} (local) ###", flush=True)
    summary = []
    for code, broker in CASES:
        ok_get = await run_get(code, broker)
        summary.append((f'get_kline {code} ({broker})', 'OK' if ok_get else 'FAIL'))
        ok_stream, live = await run_stream(code, broker, STREAM_WINDOWS[code])
        tag = 'OK+LIVE' if (ok_stream and live) else ('OK(no-live)' if ok_stream else 'FAIL')
        summary.append((f'stream_kline {code} ({broker})', tag))

    print("\n### SUMMARY ###", flush=True)
    for name, tag in summary:
        print(f"  [{tag:>10}] {name}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
