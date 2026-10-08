# -*- coding: utf-8 -*-
"""P2 smoke test：K 線快取（hit/TTL/LRU）+ futu 兩段式取數契約 + K 線頁即切週期。

跑法：python .scratch/t_p2_cache_switch.py
Part A/B 完全 hermetic（冇打網絡）；Part C 用 fake BrokerClient（注入 gui_kline.BrokerClient）。
worker_ready race rule：等 thread.worker 之後 pump(0.5) 先至 signal slot connect 好。
"""
import asyncio
import os
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402
import pandas as pd  # noqa: E402

app = QApplication.instance() or QApplication([])

from futu import RET_OK  # noqa: E402
from modules import kline_cache  # noqa: E402
from modules import futu_client as fc  # noqa: E402
from gateway.pages import gui_kline as gk  # noqa: E402
from modules.symbol_search import get_directory as real_get_directory  # noqa: E402

ok = []


def check(name, cond, detail=''):
    ok.append(cond)
    print(f'  {"✅" if cond else "❌"} {name}' + (f'  [{detail}]' if detail and not cond else ''))


def _df(n):
    """KLINE_COLUMNS 形狀嘅 fake K 線 df（time_key 為 str，同 futu 回傳一致）。"""
    return pd.DataFrame({
        'time_key': pd.date_range('2026-10-08 09:30', periods=n, freq='1min').astype(str),
        'open': [100.0 + i for i in range(n)],
        'high': [101.0 + i for i in range(n)],
        'low': [99.0 + i for i in range(n)],
        'close': [100.5 + i for i in range(n)],
        'volume': [1000 + i for i in range(n)],
    })


# ══════════ Part A：kline_cache（hit / TTL / LRU / key 大細階 / 唔寫空）══════════
print('── A: kline_cache ──')
kline_cache.clear()
ROWS = [('2026-10-08 09:30:00', 1.0, 2.0, 0.5, 1.5, 10)]
kline_cache.put('hk.00700', 'K_5M', ROWS)
check('put 後 get 命中（key 唔理大細階）', kline_cache.get('HK.00700', 'K_5M') == ROWS)
check('唔同 ktype 唔會撞 key', kline_cache.get('HK.00700', 'K_1M') is None)
check('唔同 code 唔會撞 key', kline_cache.get('HK.00700', 'K_15M') is None)
check('entries() = 1', kline_cache.entries() == 1)

kline_cache.put('HK.00700', 'K_5M', [])
check('唔寫空 rows（唔會冴走現有快照）', kline_cache.get('HK.00700', 'K_5M') == ROWS)

old_ttl = dict(kline_cache._TTL)
kline_cache._TTL['K_5M'] = -1   # 即刻過期
kline_cache.put('HK.00700', 'K_5M', ROWS)
check('TTL 過期 → get 返 None', kline_cache.get('HK.00700', 'K_5M') is None)
check('過期條目會被清走', kline_cache.entries() == 0)
kline_cache._TTL.update(old_ttl)

kline_cache.clear()
kline_cache.MAX_ENTRIES = 3
for i in range(5):
    kline_cache.put(f'US.N{i:03d}', 'K_1M', [('t', i, i, i, i, i)])
check('LRU：超過上限只留最尾 3 條', kline_cache.entries() == 3
      and kline_cache.get('US.N000', 'K_1M') is None
      and kline_cache.get('US.N004', 'K_1M') is not None)
kline_cache.get('US.N002', 'K_1M')   # touch → 變最新
kline_cache.put('US.N005', 'K_1M', [('t', 5, 5, 5, 5, 5)])
check('LRU：get 過嘅先保留（最耐冇用嗰條先走）',
      kline_cache.get('US.N002', 'K_1M') is not None and kline_cache.get('US.N001', 'K_1M') is None)
kline_cache.MAX_ENTRIES = 32
kline_cache.clear()

# ══════════ Part B：futu 兩段式（baseline 短、第二次先係完整）══════════
print('── B: futu 兩段式取數 ──')


class StubCtx:
    def __init__(self):
        self.closed = 0
        self.subscribed = 0
        self.unsubscribed = 0

    def set_handler(self, h):
        pass

    def subscribe(self, *a, **k):
        self.subscribed += 1
        return RET_OK, ''

    def unsubscribe(self, *a, **k):
        self.unsubscribed += 1
        return RET_OK, ''

    def close(self):
        self.closed += 1


class TwoPhase(fc.FutuClient):
    """唔打 OpenD：_open_ctx 俾 stub、_fetch_kline 直接返 df。"""

    def __init__(self, first, full):
        super().__init__({'kline_num': full, 'kline_num_first': first})
        self.ctx = StubCtx()
        self.fetch_calls = []

    def _open_ctx(self):
        return self.ctx

    async def _fetch_kline(self, quote_ctx, code, ktype, kline_num):
        self.fetch_calls.append(int(kline_num))
        return RET_OK, _df(int(kline_num))


async def _two_phase():
    c = TwoPhase(first=300, full=1000)
    status, gen, msg = await c.stream_kline('HK.00700', 'K_1M', kline_num=1000)
    if not status:
        return None, None, c, msg
    sizes = []
    async for df in gen:
        sizes.append(len(df))
        if len(sizes) >= 2:
            break
    await gen.aclose()
    return sizes, c.fetch_calls, c, msg


sizes, fetch_calls, c, msg = asyncio.run(_two_phase())
check('stream_kline 啟動成功', sizes is not None, str(msg))
check('第一段 baseline 只帶 kline_num_first（300 根即刻上圖）', sizes and sizes[0] == 300, str(sizes))
check('第二次快照先係完整 kline_num（1000 根）', sizes and sizes[1] == 1000, str(sizes))
check('取數順序 = [300, 1000]（唔係一次過 1000）', fetch_calls == [300, 1000], str(fetch_calls))
check('兩段共用同一條連線（無多開 ctx）', c.ctx.subscribed == 1)
# 🔌 P3 契約：stream 結束只退訂閱，**唔 close 共享 ctx**（其他 stream 仲用緊；收線歸 disconnect()）
check('aclose 後退返訂閱、唔 close 共享 ctx', c.ctx.unsubscribed == 1 and c.ctx.closed == 0,
      f'unsub={c.ctx.unsubscribed} close={c.ctx.closed}')

async def _plain():
    c = TwoPhase(first=0, full=1000)
    status, gen, msg = await c.stream_kline('HK.00700', 'K_1M', kline_num=1000)
    sizes = []
    async for df in gen:
        sizes.append(len(df))
        break
    await gen.aclose()
    return sizes, c.fetch_calls


sizes2, calls2 = asyncio.run(_plain())
check('kline_num_first=0 → 關兩段式，一次過取完整', sizes2 == [1000] and calls2 == [1000],
      f'{sizes2} {calls2}')

# ══════════ Part C：K 線頁即切週期（真 MainWindow + fake client）══════════
print('── C: K 線頁即切 ──')


class FakeDir:
    """is_stale=False → startup 唔會 auto-fetch（唔碰 OpenD）；其餘 delegate 真 index。"""

    def __init__(self, real):
        self._real = real
        self.entries = real.entries
        self.fetched_at = real.fetched_at

    @property
    def is_stale(self):
        return False

    def search(self, q, limit=20, types=None):
        return self._real.search(q, limit) if types is None else self._real.search(q, limit, types)

    def display_name(self, code, lang='zh'):
        return self._real.display_name(code, lang)

    def has_code(self, c):
        return self._real.has_code(c)


class FakeClient:
    """BrokerClient.stream_kline 契約替身：yield baseline 後吊住等 cancel。
       ⚠️ 記錄放喺**類層面** — 注入經 factory，每次都會 new 一個實例，攞唔返我哋個 instance。"""

    calls = []
    entered = 0
    exited = 0

    async def __aenter__(self):
        type(self).entered += 1
        return self

    async def __aexit__(self, *a):
        type(self).exited += 1
        return None

    async def stream_kline(self, code, ktype, broker=None, kline_num=None):
        type(self).calls.append((str(code), str(ktype), kline_num))

        async def gen():
            yield _df(5)          # baseline（5 根，同快取 seed 明顯唔同）
            await asyncio.Event().wait()   # 吊住 — 等即切 / shutdown cancel

        return True, gen(), ''


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_until(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


real_dir = real_get_directory()
check('真 symbol index 可用（Part C hermetic 包裝用）', len(real_dir.entries) > 0,
      'run: python -m modules.symbol_search fetch')
gk.get_directory = lambda: FakeDir(real_dir)
# ⏳ P4 之後 `ensure_client` 喺 gateway/kline_stream（lazy import）→ patch `gk.BrokerClient` 已失效，
#    一律經 MainWindow(client_factory=...) 注入 fake → 唔打 OpenD
kline_cache.clear()
win = gk.MainWindow(client_factory=lambda: FakeClient())
win.show()
pump(0.2)
check('worker ready', win.thread.worker is not None)
pump(0.5)   # signal slot connect 需要呢段

for kt in ('K_5M', 'K_15M'):
    check(f'{kt} 喺 KTYPES 入面', kt in gk.KTYPES)

win.code_edit.setText('HK.00700')
win.ktype_combo.setCurrentText('K_1M')
pump(0.2)
num = win.num_spin.value()
win.test_btn.click()
check('撳 START → 疊層出現（加暗 + LOADING）', win.chart._overlay is not None
      and win.chart._overlay.isVisible())
check('baseline 到 → 疊層收返', wait_until(lambda: win.chart._overlay is not None
                                           and not win.chart._overlay.isVisible()))
check('fake 收到 (code, K_1M, num_spin)', FakeClient.calls
      == [('HK.00700', 'K_1M', num)], str(FakeClient.calls))
check('baseline 寫入快取', kline_cache.get('HK.00700', 'K_1M') is not None)

# 🔀 即切：跑緊都要改到，且唔改 button 文案 / _streaming
btn_before = win.test_btn.text()
check('串流期間 ktype_combo 照樣 enabled（即切前提）', win.ktype_combo.isEnabled())
win.ktype_combo.setCurrentText('K_5M')
check('即切 → 即刻收返加載態（同步 set_busy，唔使等 network）', win.chart._overlay.isVisible())
check('即切即刻再起一條 stream（唔使撳 STOP/START）',
      wait_until(lambda: len(FakeClient.calls) == 2)
      and FakeClient.calls[1][:2] == ('HK.00700', 'K_5M'), str(FakeClient.calls))
check('_streaming 保持 True、button 文案唔變',
      win._streaming and win.test_btn.text() == btn_before, win.test_btn.text())
check('即切後舊 run 嘅 done 唔會收復 GUI（冇 btn_start / 冇 ❌）',
      win.test_btn.text() == btn_before and '❌' not in win.status_label.text(),
      win.status_label.text())
check('K_5M baseline 到 → 疊層收返', wait_until(lambda: not win.chart._overlay.isVisible()))
check('K_5M 都寫入快取（兩條週期各自獨立）',
      kline_cache.get('HK.00700', 'K_5M') is not None
      and kline_cache.get('HK.00700', 'K_1M') is not None)

# ⏳ 快取命中 → 即刻上圖（未等 network 就有圖，唔係空白）
SEED = [('2026-01-01 09:30:00', 7.0, 8.0, 6.0, 7.5, 77), ('2026-01-01 09:31:00', 7.5, 9.0, 7.0, 8.5, 88)]
kline_cache.put('HK.00700', 'K_15M', SEED)
win.ktype_combo.setCurrentText('K_15M')
check('切返睇過嘅週期 → 即刻用快取上圖（零 network 等待）', win.chart._rows == SEED,
      str(win.chart._rows[:1]))
check('快取上圖期間照樣有 LOADING（真 baseline 會覆蓋）', win.chart._overlay.isVisible())
check('真 baseline 到 → 覆蓋快取圖', wait_until(lambda: len(win.chart._rows) == 5))

win.close()
pump(0.3)
check('收窗口 → fake __aexit__ 被 call（清理完整）', FakeClient.exited == 1, str(FakeClient.exited))

print('=' * 46)
print('所有功能測試成功 ✅' if all(ok) else f'❌ FAIL {ok.count(False)}/{len(ok)}: {ok}')
print(f'({len(ok)} 項)')
sys.exit(0 if all(ok) else 1)
