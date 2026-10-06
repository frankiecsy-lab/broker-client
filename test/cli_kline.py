import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules import *
import asyncio


# print(client.get_config())
async def stream_kline(code,ktype,broker,kline_num=None):
    async with BrokerClient() as client:
        # 🤖 同 get_kline 形狀：(status, data, message)；成功時 data 係 async generator
        status, gen, message = await client.stream_kline(code=code, ktype=ktype, broker=broker, kline_num=kline_num)
        if not status or gen is None:
            print(f'❌ stream_kline 失敗：{message}')   # 🤖 失敗時印出真正原因（權限/代號解析/訂閱），唔會靜默結束
            return
        async for kline in gen:
            print(kline)

async def get_kline(code,ktype,broker,kline_num=None):
    async with BrokerClient() as client:
        status, data, message = await client.get_kline(code=code, ktype=ktype, broker=broker, kline_num=kline_num)
        if not status or data is None:
            print(f'❌ get_kline 失敗：{message}')   # 🤖 之前只 print(data) → 失敗時只見到 None，真正原因喺 message
        else:
            print(data)


# ═══════════ Contract test — P1-P5 每部份嘅驗證入口 ═══════════
# 🤖 用法：python test/cli_kline.py          → 對每個 broker 跑 get + stream 契約斷言（exit code 0=PASS / 1=FAIL）
#    python test/cli_kline.py stream <code> <ktype> <broker>   → 舊式手動長串流
from modules.registry import BROKERS as _REGISTRY
BROKER_LIST = list(_REGISTRY.keys())   # 🤖 P2：讀 registry（加新 client 自動覆蓋，唔使改呢度）
CONTRACT_CASES = {             # 每個 broker 嘅測試 case (code, ktype) — 美股 RTH 有 live tick；港股收市都取得 baseline
    'futu': ('US.NVDA', 'K_1M'),
    'ib':   ('US.NVDA', 'K_1M'),
}
STREAM_SECONDS = 20            # stream 觀察窗口（短過 IB 120s watchdog，長到可抓到幾筆 live tick）
MAPPING_CASES = {              # 🤖 P7: canonical Futu code → per-broker 期望 (code, expect_success)
    'futu': ('HK.HSImain', True),   # Futu native — front-month futures 必須解到（OpenD 原生支援 main contract）
    'ib':   ('HK.HSImain', False),  # 本 account 無 HK derivatives permission（probe 實測全 venue fail）— 必須 honest fail「無法解析」
}

def _check_shape(df):
    """🤖 P3：驗 K 線 df 符合共享契約（kline_schema.validate_kline — 同 client 端用同一把尺）"""
    from modules.kline_schema import validate_kline
    return validate_kline(df)

async def _bounded_consume(gen, seconds):
    """🤖 消費 stream `seconds` 秒後 cancel — 驗 consumer 可以乾淨取消（generator finally 拔線）"""
    snapshots = 0
    first_df = last_df = None
    async def consume():
        nonlocal snapshots, first_df, last_df
        async for df in gen:
            snapshots += 1
            if first_df is None:
                first_df = df
            last_df = df
    task = asyncio.create_task(consume())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass   # 🤖 預期取消 — generator finally 若冇清理乾淨，呢度會 hang / 噴錯
    return {'snapshots': snapshots, 'first': first_df, 'last': last_df}

async def _contract_one(broker):
    code, ktype = CONTRACT_CASES.get(broker, ('US.NVDA', 'K_1M'))
    print(f"\n=== contract [{broker}] {code} {ktype} ===", flush=True)
    async with BrokerClient() as client:
        # 1️⃣ get_kline：(status, df, message) + 形狀
        status, data, message = await client.get_kline(code=code, ktype=ktype, broker=broker, kline_num=None)
        if not status or data is None:
            print(f"RESULT FAIL [{broker}] get_kline: {message}", flush=True)
            return False
        ok, why = _check_shape(data)
        if not ok:
            print(f"RESULT FAIL [{broker}] get_kline 形狀: {why}", flush=True)
            return False
        print(f"get OK rows={len(data)} last_tk={data['time_key'].iloc[-1]}", flush=True)

        # 2️⃣ stream_kline：(status, gen, message) + baseline + 乾淨取消
        status, gen, message = await client.stream_kline(code=code, ktype=ktype, broker=broker, kline_num=None)
        if not status or gen is None:
            print(f"RESULT FAIL [{broker}] stream setup: {message}", flush=True)
            return False
        info = await _bounded_consume(gen, STREAM_SECONDS)
        if info['first'] is None:
            print(f"RESULT FAIL [{broker}] stream: 冇 baseline snapshot", flush=True)
            return False
        ok, why = _check_shape(info['first'])
        if not ok:
            print(f"RESULT FAIL [{broker}] stream baseline 形狀: {why}", flush=True)
            return False
        live = info['snapshots'] - 1   # 第一份係 baseline
        print(f"stream OK snapshots={info['snapshots']} live_updates={live} "
              f"(0 updates 可能係無成交時段 / IB 無 RTUS — 唔計 FAIL)", flush=True)

        # 3️⃣ P4：失敗路徑 — 壞代號必須回 (False, None, message)，唔好噴 exception（統一 triple 契約）
        bad_code = 'US.NONEXIST123'
        status, data, msg_get = await client.get_kline(code=bad_code, ktype=ktype, broker=broker, kline_num=None)
        if status or data is not None:
            print(f"RESULT FAIL [{broker}] get_kline 失敗路徑應回 (False, None, msg)", flush=True)
            return False
        if not msg_get:
            print(f"RESULT FAIL [{broker}] get_kline 失敗路徑冇 message", flush=True)
            return False
        status, gen, msg_stream = await client.stream_kline(code=bad_code, ktype=ktype, broker=broker, kline_num=None)
        if status or gen is not None:
            print(f"RESULT FAIL [{broker}] stream 失敗路徑應回 (False, None, msg)", flush=True)
            return False
        if not msg_stream:
            print(f"RESULT FAIL [{broker}] stream 失敗路徑冇 message", flush=True)
            return False
        # 🤖 P6：error honesty — IB 無標的必須明講「無法解析」（唔係 generic timeout/error）
        if broker == 'ib':
            if '無法解析' not in msg_get:
                print(f"RESULT FAIL [{broker}] get_kline 無標的 message 應含 無法解析: {msg_get}", flush=True)
                return False
            if '無法解析' not in msg_stream:
                print(f"RESULT FAIL [{broker}] stream 無標的 message 應含 無法解析: {msg_stream}", flush=True)
                return False
        print(f"fail-path OK get=[{msg_get[:60]}] stream=[{msg_stream[:60]}]", flush=True)

        # 4️⃣ P7: symbol mapping — canonical Futu code per-broker 期望（futu native 成功 / ib honest fail）
        m_code, expect_ok = MAPPING_CASES.get(broker, ('HK.HSImain', True))
        status, data, msg_map = await client.get_kline(code=m_code, ktype=ktype, broker=broker, kline_num=None)
        if expect_ok:
            if not status or data is None:
                print(f"RESULT FAIL [{broker}] mapping {m_code} 應成功: {msg_map}", flush=True)
                return False
            ok, why = _check_shape(data)
            if not ok:
                print(f"RESULT FAIL [{broker}] mapping {m_code} 形狀: {why}", flush=True)
                return False
            print(f"mapping OK [{m_code}] rows={len(data)}", flush=True)
        else:
            if status or data is not None:
                print(f"RESULT FAIL [{broker}] mapping {m_code} 應 honest fail (False, None, msg)", flush=True)
                return False
            if '無法解析' not in (msg_map or ''):
                print(f"RESULT FAIL [{broker}] mapping {m_code} message 應含 無法解析: {msg_map}", flush=True)
                return False
            print(f"mapping honest-fail OK [{m_code}] msg=[{msg_map[:60]}]", flush=True)
    return True

async def contract_test(brokers):
    results = {}
    for b in brokers:   # 🤖 順序跑：IB clientId=99 同一時間只可以有一條連線
        results[b] = await _contract_one(b)
    print(f"\n{'=' * 50}\nCONTRACT " + " ".join(f"{b}={'PASS' if ok else 'FAIL'}" for b, ok in results.items()), flush=True)
    return all(results.values())


if __name__ == "__main__":

    # ── 手動模式（舊用法）：python test/cli_kline.py stream <code> <ktype> <broker> ──
    if len(sys.argv) > 1 and sys.argv[1] == 'stream':
        code, ktype, broker = sys.argv[2], sys.argv[3], sys.argv[4]
        asyncio.run(stream_kline(code=code, ktype=ktype, broker=broker))
        sys.exit(0)

    # ── 預設：contract test（P1-P5 每部份改完都跑呢度驗證）──
    ok = asyncio.run(contract_test(BROKER_LIST))
    sys.exit(0 if ok else 1)


    #asyncio.run(get_kline(code='HK.HSImain', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='HK.HSImain',ktype='K_1M',broker='futu',kline_num=None))

    #asyncio.run(get_kline(code='HK.00700', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='HK.00700', ktype='K_1M', broker='futu', kline_num=None))

    #asyncio.run(get_kline(code='US.NQmain', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='US.NQmain', ktype='K_1M', broker='futu', kline_num=None))

    #asyncio.run(get_kline(code='US.NVDA', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='US.NVDA', ktype='K_1M', broker='futu', kline_num=None))

    #asyncio.run(get_kline(code='HK.HSImain', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='HK.HSImain',ktype='K_1M',broker='ib',kline_num=None))

    #asyncio.run(get_kline(code='HK.00700', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='HK.00700', ktype='K_1M', broker='ib', kline_num=None))

    #asyncio.run(get_kline(code='US.NQmain', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='US.NQmain', ktype='K_1M', broker='ib', kline_num=None))

    #asyncio.run(get_kline(code='US.NVDA', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='US.NVDA', ktype='K_1M', broker='ib', kline_num=None))
