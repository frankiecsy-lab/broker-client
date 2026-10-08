# -*- coding: utf-8 -*-
"""P3 test：共用 OpenD 連線（shared ctx + 訂閱 refcount + 單 handler 按 (code,k_type) 分派）。

跑法：python .scratch/t_p3_shared_conn.py
A/B/C 完全 hermetic（StubCtx / mock SDK callback，唔打網絡）；D 先打真 OpenD（無開 → SKIP）。
"""
import asyncio
import os
import sys
import queue
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from futu import RET_OK, RET_ERROR  # noqa: E402
from modules import futu_client as fc  # noqa: E402

ok = []


def check(name, cond, detail=''):
    ok.append(cond)
    print(f'  {"✅" if cond else "❌"} {name}' + (f'  [{detail}]' if detail and not cond else ''))


def _df(n, code=None, ktype=None):
    """push 形狀嘅 fake K 線 df（帶 `code`/`k_type` 路由欄，同 futu push 一致）。"""
    d = {
        'time_key': pd.date_range('2026-10-08 09:30', periods=n, freq='1min').astype(str),
        'open': [100.0 + i for i in range(n)],
        'high': [101.0 + i for i in range(n)],
        'low': [99.0 + i for i in range(n)],
        'close': [100.5 + i for i in range(n)],
        'volume': [1000 + i for i in range(n)],
    }
    if code is not None:
        d['code'] = [code] * n
    if ktype is not None:
        d['k_type'] = [ktype] * n
    return pd.DataFrame(d)


class StubCtx:
    def __init__(self, sub_ret=RET_OK, sub_raises=False):
        self.closed = 0
        self.subscribed = 0
        self.unsubscribed = 0
        self.sub_ret = sub_ret
        self.sub_raises = sub_raises

    def set_handler(self, h):
        pass

    def subscribe(self, *a, **k):
        self.subscribed += 1
        if self.sub_raises:
            raise OSError('OpenD 無反應')
        return self.sub_ret, '模擬訂閱失敗'

    def unsubscribe(self, *a, **k):
        self.unsubscribed += 1
        return RET_OK, ''

    def close(self):
        self.closed += 1


class Shared(fc.FutuClient):
    """唔打 OpenD：`_open_ctx` 俾 StubCtx 並數住開咗幾次（= 真 handshake 數）。"""

    def __init__(self, sub_ret=RET_OK, sub_raises=False):
        super().__init__({'kline_num': 5, 'kline_num_first': 0})
        self.opened = 0
        self.ctxs = []
        self.sub_ret = sub_ret
        self.sub_raises = sub_raises
        self.fetch_calls = []

    def _open_ctx(self):
        self.opened += 1
        # sub_raises 只作用喺第一條 ctx → 先模擬到「連線斷咗 → 下次重開返好」
        ctx = StubCtx(sub_ret=self.sub_ret, sub_raises=self.sub_raises and self.opened == 1)
        self.ctxs.append(ctx)
        return ctx

    async def _fetch_kline(self, quote_ctx, code, ktype, kline_num):
        self.fetch_calls.append(int(kline_num))
        return RET_OK, _df(int(kline_num))


async def first(gen):
    """攞第一幀 baseline（generator 保持存活，唔 aclose）。"""
    return await gen.__anext__()


# ══════════ A：共享連線 + 訂閱 refcount ══════════
print('── A: shared ctx + refcount ──')


async def part_a():
    c = Shared()
    s1, g1, m1 = await c.stream_kline('HK.00700', 'K_1M', kline_num=5)
    check('stream#1 啟動成功', s1, str(m1))
    b1 = await first(g1)
    check('stream#1 baseline 5 根', len(b1) == 5, str(len(b1)))

    s2, g2, m2 = await c.stream_kline('HK.00005', 'K_1M', kline_num=5)
    await first(g2)
    check('stream#2（唔同標的）啟動成功', s2, str(m2))
    check('🔌 兩條 stream 只開**一條** OpenD 連線', c.opened == 1, f'opened={c.opened}')
    check('唔同 (code,ktype) 各自 subscribe 一次', c.ctxs[0].subscribed == 2, str(c.ctxs[0].subscribed))
    check('兩條 stream 攞到同一條 ctx', c._queues and all(
        len(v) == 1 for v in c._queues.values()))

    s3, g3, m3 = await c.stream_kline('HK.00700', 'K_1M', kline_num=5)
    await first(g3)
    check('同 (code,ktype) 第二條唔再 subscribe（refcount 2）',
          c.ctxs[0].subscribed == 2 and c._subs[('HK.00700', 'K_1M')] == 2,
          f'sub={c.ctxs[0].subscribed} ref={c._subs.get(("HK.00700", "K_1M"))}')
    check('同一 key 兩條 stream 各有一個**獨立** queue（唔會搶走對方嘅 push）',
          len(c._queues[('HK.00700', 'K_1M')]) == 2, str(len(c._queues[('HK.00700', 'K_1M')])))

    await g1.aclose()
    check('refcount 2→1：唔會 unsubscribe（其他 stream 仲用緊）',
          c.ctxs[0].unsubscribed == 0 and c._subs.get(('HK.00700', 'K_1M')) == 1,
          f'unsub={c.ctxs[0].unsubscribed}')
    await g3.aclose()
    check('refcount 1→0：先 unsubscribe 一次',
          c.ctxs[0].unsubscribed == 1 and ('HK.00700', 'K_1M') not in c._subs,
          f'unsub={c.ctxs[0].unsubscribed}')
    check('stream 結束**唔 close** 共享 ctx', c.ctxs[0].closed == 0, f'close={c.ctxs[0].closed}')

    await g2.aclose()
    check('全部 stream 結束都唔 close 共享 ctx', c.ctxs[0].closed == 0)

    await c.disconnect()
    check('disconnect() 先收線：close 一次 + 狀態清空',
          c.ctxs[0].closed == 1 and not c._subs and not c._queues and c._shared_ctx is None)
    check('disconnect 後再起 stream → 重開一條連線',
          (await c.stream_kline('HK.00700', 'K_1M', kline_num=5))[0] and c.opened == 2,
          f'opened={c.opened}')
    await c.disconnect()


asyncio.run(part_a())

# ══════════ B：失敗隔離（transport 異常 vs 訂閱 RET_ERROR）══════════
print('── B: 失敗隔離 ──')


async def part_b():
    c = Shared(sub_raises=True)
    s, g, m = await c.stream_kline('HK.00700', 'K_1M', kline_num=5)
    check('subscribe 擆異常 → 經 (False, None, msg) 如實回報', s is False and g is None and 'OpenD 無反應' in m, str(m))
    check('transport 異常 → 共享連線即刻清走（下次重開）',
          c._shared_ctx is None and not c._subs and not c._queues)

    s2, g2, m2 = await c.stream_kline('HK.00700', 'K_1M', kline_num=5)
    check('下次 stream 會重開一條新連線（自我修復）',
          s2 is True and c.opened == 2 and c.ctxs[1] is not c.ctxs[0], f'opened={c.opened} {m2}')
    check('斷咗嗰條 ctx 已 close（唔洩漏）', c.ctxs[0].closed == 1, f'close={c.ctxs[0].closed}')
    if s2:
        await g2.aclose()
    await c.disconnect()

    d = Shared(sub_ret=RET_ERROR)
    s3, g3, m3 = await d.stream_kline('HK.00700', 'K_1M', kline_num=5)
    check('訂閱 RET_ERROR（權限類）→ 如實回報失敗', s3 is False and '訂閱失敗' in m3, str(m3))
    check('RET_ERROR **唔會**拆走共享連線（唔連累其他 live stream）',
          d._shared_ctx is not None and d._shared_dead is False and d.opened == 1,
          f'opened={d.opened} dead={d._shared_dead}')
    s4, g4, m4 = await d.stream_kline('HK.00005', 'K_1M', kline_num=5)
    check('第二條都如實失敗、但唔會多開連線', s4 is False and d.opened == 1, f'opened={d.opened}')
    await d.disconnect()


asyncio.run(part_b())

# ══════════ C：KlineRouter 按 (code,k_type) 分派 ══════════
print('── C: KlineRouter 分派 ──')

_base_on_recv = fc.CurKlineHandlerBase.on_recv_rsp


def feed(router, df):
    """模擬 SDK callback：base `on_recv_rsp` 直接返我哋造嘅 df（跳過 protobuf 解析）。"""
    fc.CurKlineHandlerBase.on_recv_rsp = lambda self, rsp_pb: (RET_OK, df)
    try:
        router.on_recv_rsp(object())
    finally:
        fc.CurKlineHandlerBase.on_recv_rsp = _base_on_recv


def part_c():
    c = Shared()
    sinks = {}
    q1, q2, q3 = queue.Queue(), queue.Queue(), queue.Queue()
    sinks[('HK.00700', 'K_1M')] = [q1, q2]      # 兩格設同一標的同一週期
    sinks[('HK.00005', 'K_1M')] = [q3]
    r = fc.KlineRouter(normalize=c._normalize_kline, sinks=sinks)

    two = pd.concat([_df(2, 'HK.00700', 'K_1M'), _df(1, 'HK.00005', 'K_1M')], ignore_index=True)
    feed(r, two)
    check('一次 push 跨兩個標的 → 各 key 只收到自己嗰份', q3.qsize() == 1 and q1.qsize() == 1)
    got1, got2 = q1.get_nowait(), q2.get_nowait()
    check('同一 key 兩條 stream fan-out：兩條 queue 都收到', q2.qsize() == 0 and len(got1) == 1 and len(got2) == 1)
    check('派出去之前已 normalize（無 `code` 欄、time_key 係 datetime）',
          'code' not in got1.columns and hasattr(got1['time_key'].iloc[0], 'year'))
    check('每組只派最新嗰根（tail(1)）', len(got1) == 1 and str(got1['time_key'].iloc[0]).endswith('09:31:00'))

    while not q3.empty():
        q3.get_nowait()
    feed(r, _df(3))   # 冇 code/k_type 欄
    check('push 冇路由欄 → 如實唔派（唔會亂派錯格）', q1.empty() and q3.empty())

    feed(r, _df(1, 'HK.99999', 'K_1M'))
    check('而家冇人收嘅 key → 丟咗唔炸', q1.empty() and q3.empty())


part_c()

# ══════════ D：真 OpenD（連線數 → 1 + push 真的帶 code/k_type）══════════
print('── D: 真 OpenD ──')


async def part_d():
    real = fc.FutuClient({'kline_num': 20, 'kline_num_first': 0})
    opened = []
    orig = fc.FutuClient._open_ctx

    def counting(self):
        opened.append(1)
        return orig(self)

    fc.FutuClient._open_ctx = counting
    gens = []
    try:
        s1, g1, m1 = await real.stream_kline('HK.00700', 'K_1M', kline_num=20)
        if not s1:
            print(f'  ⚠️ SKIP：OpenD 打唔通（{m1}）')
            return
        gens.append(g1)
        await first(g1)
        s2, g2, m2 = await real.stream_kline('HK.00005', 'K_1M', kline_num=20)
        check('真 OpenD：第二條 stream 啟動成功', s2, str(m2))
        if s2:
            gens.append(g2)
            await first(g2)
        check('🔌 真 OpenD：兩條 stream 共用**一條**連線', len(opened) == 1, f'opened={len(opened)}')

        # push 帶唔帶路由欄 → 收市時段可能完全冇 push，呢項只能寬容
        try:
            tick = await asyncio.wait_for(g1.__anext__(), 15)
            check('真 push 經 router 落到 stream 自己嘅 queue（即係 push 真的帶 code/k_type）',
                  len(tick) >= 20, f'{len(tick)} 根')
        except asyncio.TimeoutError:
            print('  ⚠️ SKIP：15 秒內冇 push（收市 / 無成交時段），路由欄已由 C 覆蓋')
    finally:
        for g in gens:
            await g.aclose()
        await real.disconnect()
        fc.FutuClient._open_ctx = orig
        check('disconnect 後真 ctx 已收（無洩漏連線）', real._shared_ctx is None)


asyncio.run(part_d())

# ══════════ 結果 ══════════
print(f'\n{"所有功能測試成功 ✅" if all(ok) else "❌ 有失敗"} ({len(ok)} 項，{sum(ok)} 綠)')
sys.exit(0 if all(ok) else 1)
