# -*- coding: utf-8 -*-
"""拆開量六格同時起 stream 時，時間花喺邊度：`_acquire`（訂閱）vs `_fetch_kline`（取數）vs `_release`（退訂閱）。

背景：P3 後六格同時切 717 → 1549 ms，但 `get_cur_kline` 實測只需 ~1 ms → 成本唔喺取數。
跑法：python .scratch/bench_futu_setup_path.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import futu_client as fc  # noqa: E402

CODES = ['HK.00700', 'HK.00005', 'HK.00001', 'HK.03988', 'HK.09988', 'HK.3690']
KT = 'K_5M'
NUM = 300

LOG = []
_orig_acquire = fc.FutuClient._acquire
_orig_release = fc.FutuClient._release


def timed_acquire(self, code, ktype):
    t = time.perf_counter()
    r = _orig_acquire(self, code, ktype)
    LOG.append(('acquire', code, (time.perf_counter() - t) * 1000))
    return r


def timed_release(self, code, ktype, q):
    t = time.perf_counter()
    r = _orig_release(self, code, ktype, q)
    LOG.append(('release', code, (time.perf_counter() - t) * 1000))
    return r


fc.FutuClient._acquire = timed_acquire
fc.FutuClient._release = timed_release

_orig_fetch = fc.FutuClient._fetch_kline


async def timed_fetch(self, quote_ctx, code, ktype, kline_num):
    t = time.perf_counter()
    r = await _orig_fetch(self, quote_ctx, code, ktype, kline_num)
    LOG.append(('fetch', code, (time.perf_counter() - t) * 1000))
    return r


fc.FutuClient._fetch_kline = timed_fetch


async def main():
    c = fc.FutuClient({'kline_num': NUM, 'kline_num_first': 0})

    # 先起 6 條 K_1M（模仿六格而家嘅狀態），再一次過切去 K_5M
    def start(ktype):
        return [c.stream_kline(code, ktype, kline_num=NUM) for code in CODES]

    t0 = time.perf_counter()
    results = await asyncio.gather(*start('K_1M'))
    print(f'  起 6 條 K_1M：{(time.perf_counter() - t0) * 1000:8.0f} ms')
    gens = [r[1] for r in results if r[0]]
    for g in gens:
        await g.__anext__()

    LOG.clear()
    t0 = time.perf_counter()
    # 逐格 cancel 舊 + 起新（照行情頁即切嘅順序，唔係 gather 先至貼近實際）
    old = list(gens)
    gens = []
    for g in old:
        await g.aclose()
    results = await asyncio.gather(*start(KT))
    total = (time.perf_counter() - t0) * 1000
    for r in results:
        if r[0]:
            gens.append(r[1])
            await r[1].__anext__()
    print(f'  六格同時切 {KT}：{total:8.0f} ms')

    for g in gens:
        await g.aclose()
    await c.disconnect()

    for tag in ('acquire', 'fetch', 'release'):
        rows = [(code, ms) for kind, code, ms in LOG if kind == tag]
        print(f'  {tag:<8} n={len(rows)}  ' + ' '.join(f'{ms:.0f}' for _, ms in rows))


asyncio.run(main())
