# -*- coding: utf-8 -*-
"""量：同一條 OpenQuoteContext 上並發取歷史 K 線，係咪實際串行（futu SDK 一條 socket + 內部 lock）。

呢個決定 P3「共用連線」值唔值：連線 13 → 1，但六格取數 717 → 1549 ms。
跑法：python .scratch/bench_futu_concurrency.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from futu import RET_OK, KLType, Session, AuType  # noqa: E402
from modules import futu_client as fc  # noqa: E402

CODES = ['HK.00700', 'HK.00005', 'HK.00001', 'HK.03988', 'HK.09988', 'HK.3690']
NUM = 300
KT = KLType.K_5M


def one(ctx, code):
    t = time.perf_counter()
    ret, data = ctx.get_cur_kline(code, NUM, KT, AuType.QFQ)
    return (time.perf_counter() - t) * 1000, ret == RET_OK, len(data) if ret == RET_OK else data


async def main():
    c = fc.FutuClient({'kline_num': NUM, 'kline_num_first': 0})

    # ── A：一條 ctx 串行 6 次（同一條 socket，冇 pool）──
    ctx = c._open_ctx()
    ctx.subscribe(CODES, [KT], subscribe_push=False, session=Session.ALL)
    t0 = time.perf_counter()
    for code in CODES:
        one(ctx, code)
    serial = (time.perf_counter() - t0) * 1000

    # ── B：一條 ctx，6 個 thread 同時 call（SDK 內部 lock → 預期仍然串行）──
    t0 = time.perf_counter()
    res = await asyncio.gather(*[asyncio.to_thread(one, ctx, code) for code in CODES])
    shared_conc = (time.perf_counter() - t0) * 1000
    print(f'  各次用時(ms) {[round(r[0]) for r in res]}')

    # ── C：6 條 ctx，各取一個（P2 舊設計：真並發）──
    ctxs = [c._open_ctx() for _ in CODES]
    for x, code in zip(ctxs, CODES):
        x.subscribe([code], [KT], subscribe_push=False, session=Session.ALL)
    t0 = time.perf_counter()
    await asyncio.gather(*[asyncio.to_thread(one, x, code) for x, code in zip(ctxs, CODES)])
    multi_conc = (time.perf_counter() - t0) * 1000
    for x in ctxs:
        x.close()
    ctx.close()

    print(f'\n  A 一條 ctx 串行 6 次        {serial:8.0f} ms')
    print(f'  B 一條 ctx 開 6 個 thread   {shared_conc:8.0f} ms   ← 共用連線嘅真實成本')
    print(f'  C 六條 ctx 各取一個         {multi_conc:8.0f} ms   ← P2 舊設計')
    print(f'\n  結論：{"B ≈ A → SDK 內部串行，共用連線必然排隊" if shared_conc > 0.8 * serial else "B < A → 真係並發得"}')


asyncio.run(main())
