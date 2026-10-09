# -*- coding: utf-8 -*-
"""純計算測試 — gateway/accounts.py（跨帳戶合併、ACTIVE 規則、別名與顯示名、資金格式化）（ticket #36a）。

Run: python .scratch/test_accounts.py   （from project root）
無 Qt、無網絡：以 dict 直接餵供應商行，非同步部分用假 client（記錄呼叫）。
斷言：acc_key 跨券商唯一／acc_id int-str 歸一／active_only 同時丟 DISABLED 與 'N/A' 並計數／
IB 硬編碼 ACTIVE 行保留／合併順序確定／一券商失敗不影響另一券商／
display_name 三語 byte-exact／別名優先於生成名且跨語言仍正確／
別名驗證（strip、長度、空→刪除、撞名如實擋）／生成名永不入檔／
FUND_LABEL_KEYS 與 ACCINFO_KEYS 同步（契約漂移守門）／資金缺失不產 0／
refresh_funds 絕不以空帳戶呼叫 account_info。
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gateway import accounts as ac  # noqa: E402
from gateway import state_store  # noqa: E402
from modules.trade_base import ACCINFO_KEYS  # noqa: E402

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if not ok:
        FAILURES.append(name)


# ── fixture：兩家券商的原始行（futu 回 int acc_id、IB 回 'DU…'）──
FUTU_RAW = [
    {'acc_id': 4654894524, 'trd_env': 'REAL', 'acc_type': '', 'trdmarket_auth': ['HK', 'US'],
     'acc_status': 'ACTIVE'},
    {'acc_id': 4654894525, 'trd_env': 'REAL', 'acc_type': '', 'trdmarket_auth': ['HK'],
     'acc_status': 'DISABLED'},
    {'acc_id': 4654894526, 'trd_env': 'SIMULATE', 'acc_type': '', 'trdmarket_auth': ['HK'],
     'acc_status': 'N/A'},
    {'acc_id': 4654894527, 'trd_env': 'SIMULATE', 'acc_type': '', 'trdmarket_auth': [],
     'acc_status': 'ACTIVE'},
]
IB_RAW = [
    {'acc_id': 'DU12345', 'trd_env': 'SIMULATE', 'acc_type': '', 'trdmarket_auth': '',
     'acc_status': 'ACTIVE'},
    {'acc_id': '123456', 'trd_env': 'REAL', 'acc_type': '', 'trdmarket_auth': '',
     'acc_status': 'ACTIVE'},
]


class FakeClient:
    """只記錄呼叫；回傳形狀照 (status, data, message) 契約。"""

    def __init__(self, per_broker=None, fail=(), raise_on=(), funds=None, fund_fail='權限不足'):
        self.per_broker = per_broker or {'futu': FUTU_RAW, 'ib': IB_RAW}
        self.fail = set(fail)
        self.raise_on = set(raise_on)
        self.funds = funds or {}
        self.fund_fail = fund_fail
        self.acc_calls = []
        self.fund_calls = []

    async def trade_accounts(self, broker=None):
        self.acc_calls.append(broker)
        if broker in self.raise_on:
            raise RuntimeError(f'{broker} 連線中斷')
        if broker in self.fail:
            return False, None, f'{broker} 未解鎖'
        return True, self.per_broker.get(broker, []), ''

    async def account_info(self, account=None, env=None, broker=None):
        self.fund_calls.append((broker, account, env))
        if not account:
            return True, {'total_assets': 999}, ''      # 會合併所有帳戶的錯誤來源
        if account in self.funds:
            return True, self.funds[account], ''
        return False, None, self.fund_fail


def main():
    tmp = Path(tempfile.mkdtemp(prefix='acct_state_'))
    state_store.STATE_PATH = tmp / 'ui_state.json'

    print('\n[1] acc_key 與行正規化', flush=True)
    check('跨券商 acc_key 不碰撞',
          ac.acc_key('futu', 4654894524) != ac.acc_key('ib', 4654894524))
    rows = ac.normalise_rows(FUTU_RAW, 'futu')
    check('acc_id 一律轉 str（int 4654894524 → "4654894524"）',
          rows[0]['acc_id'] == '4654894524' and isinstance(rows[0]['acc_id'], str))
    check('行欄位 == ROW_KEYS（契約 + 兩個衍生欄，無多餘欄）',
          all(set(r) == set(ac.ROW_KEYS) for r in rows))
    check('來源券商由呼叫標記（不推測）', all(r['broker'] == 'futu' for r in rows))
    check('缺欄以空字串補齊（不 KeyError、不編造）',
          ac.normalise_rows([{'acc_id': 'X'}], 'ib')[0]['acc_status'] == '')
    check('無 acc_id 的行如實略過（不編造 id）',
          len(ac.normalise_rows([{'acc_status': 'ACTIVE'}, {'acc_id': '  '}, 'junk'], 'ib')) == 0)

    print('\n[2] ACTIVE 規則（全app 唯一定義處）', flush=True)
    kept, n = ac.active_only(rows)
    check('只保留 ACTIVE，DISABLED 與 N/A 一併屏蔽',
          [r['acc_id'] for r in kept] == ['4654894524', '4654894527'])
    check('被屏蔽數量如實回報（N/A 是未知，不是可靜默消失）', n == 2)
    ib_rows = ac.normalise_rows(IB_RAW, 'ib')
    check('IB 行（acc_status 硬編碼 ACTIVE）全部保留',
          ac.active_only(ib_rows)[1] == 0 and len(ac.active_only(ib_rows)[0]) == 2)
    check('大小寫與空白不影響判斷',
          ac.active_only([{'acc_status': ' active '}, {'acc_status': ''}])[0] ==
          [{'acc_status': ' active '}])

    print('\n[3] collect_accounts（逐券商、順序確定、失敗互相獨立）', flush=True)
    fc = FakeClient()
    r = asyncio.run(ac.collect_accounts(fc, ('futu', 'ib')))
    check('每家券商各呼叫一次、順序即傳入順序', fc.acc_calls == ['futu', 'ib'])
    check('合併順序 = 券商順序 → 該券商行序',
          [(x['broker'], x['acc_id']) for x in r['rows']] ==
          [('futu', '4654894524'), ('futu', '4654894527'), ('ib', 'DU12345'), ('ib', '123456')])
    check('逐券商被過濾計數', r['filtered'] == {'futu': 2, 'ib': 0})
    check('無失敗時 errors 為空', r['errors'] == {})
    r2 = asyncio.run(ac.collect_accounts(FakeClient(fail=('ib',)), ('futu', 'ib')))
    check('一券商失敗：原文保留、另一券商行完整（不連帶清空）',
          r2['errors'] == {'ib': 'ib 未解鎖'} and len(r2['rows']) == 2)
    r3 = asyncio.run(ac.collect_accounts(FakeClient(raise_on=('futu',)), ('futu', 'ib')))
    check('異常歸因到該券商並轉為原文（不炸整頁）',
          r3['errors'] == {'futu': 'futu 連線中斷'} and len(r3['rows']) == 2)

    print('\n[4] 顯示名（三語、生成名跟語言）', flush=True)
    futu_real = r['rows'][0]
    ib_sim = r['rows'][2]
    check('zh_hk 生成名 = 富途實盤(4654894524)',
          ac.display_name(futu_real, 'zh_hk', {}) == '富途實盤(4654894524)')
    check('zh_cn 生成名跟語言（富途实盘）',
          ac.display_name(futu_real, 'zh_cn', {}) == '富途实盘(4654894524)')
    check('en 生成名與間隔由 ta_name_fmt 決定（環境標籤沿用全app 同一份）',
          ac.display_name(futu_real, 'en', {}) == 'Futu REAL (4654894524)')
    check('IB 模擬盤生成名', ac.display_name(ib_sim, 'zh_hk', {}) == 'IB模擬盤(DU12345)')
    check('未知 trd_env 退回模擬（與全app clamp_env 一致）',
          ac.display_name({'broker': 'futu', 'acc_id': '1', 'trd_env': 'MAYBE'}, 'zh_hk', {})
          == '富途模擬盤(1)')
    check('未知券商名原樣顯示（不 KeyError、不假裝認識）',
          ac.display_name({'broker': 'xyz', 'acc_id': '1', 'trd_env': 'REAL'}, 'zh_hk', {})
          == 'xyz實盤(1)')

    print('\n[5] 別名（儲存、驗證、撞名、跨語言）', flush=True)
    ok, msg, data = ac.set_alias('futu:4654894524', '  主帳戶  ')
    check('設定成功並 strip', ok and msg == '' and ac.load_aliases() == {'futu:4654894524': '主帳戶'})
    check('別名取代生成 label，但 acc_id 仍保留在括號內',
          ac.display_name(futu_real, 'zh_hk') == '主帳戶(4654894524)')
    check('換語言後別名照樣正確（生成部分即時解析，別名不變）',
          ac.display_name(futu_real, 'en') == '主帳戶 (4654894524)')
    check('別名只存在儲存，生成名永不入檔',
          json.loads(state_store.STATE_PATH.read_text(encoding='utf-8'))['accounts']['aliases']
          == {'futu:4654894524': '主帳戶'})
    ok, msg, data = ac.set_alias('ib:DU12345', '主帳戶')
    check('撞名如實擋下（不靜默覆蓋）', ok is False and msg == 'ta_alias_clash')
    check('撞名回報衝突的 acc_key（頁面能把衝突講清楚）', data == 'futu:4654894524')
    check('撞名後兩筆各自保持原值（未寫入、未覆蓋）',
          ac.load_aliases() == {'futu:4654894524': '主帳戶'})
    ok, msg, _ = ac.set_alias('ib:DU12345', 'x' * (ac.ALIAS_MAX + 1))
    check('超長如實擋下（不截斷：截斷後兩個別名會完全相同）',
          ok is False and msg == 'ta_alias_too_long')
    check('上限內可設定', ac.set_alias('ib:DU12345', 'x' * ac.ALIAS_MAX)[0] is True)
    ok, _, _ = ac.set_alias('ib:DU12345', '   ')
    check('空白 = 刪除該筆並回落生成名',
          ok and 'ib:DU12345' not in ac.load_aliases()
          and ac.display_name(ib_sim, 'zh_hk') == 'IB模擬盤(DU12345)')
    ac.set_alias('futu:4654894527', '對帳用')
    ac.reset_alias('futu:4654894527')
    check('reset_alias 回落生成名', 'futu:4654894527' not in ac.load_aliases())
    check('控制字元被移除（不可存入無法顯示的名字）',
          ac.set_alias('futu:4654894527', 'a\tb\x00c')[1] == ''
          and ac.load_aliases()['futu:4654894527'] == 'abc')
    check('帳戶暫時不在清單時別名不丟失（富途模擬 id 會輪替）',
          'futu:4654894527' in ac.load_aliases())
    check('其他 section 不受影響（read-modify-write）',
          state_store.save_section('quant', {'a': 1}) is None
          and state_store.load_section('quant', {}).get('a') == 1
          and 'futu:4654894524' in ac.load_aliases())

    print('\n[6] 資金明細（缺失不產 0）', flush=True)
    check('FUND_LABEL_KEYS 與 ACCINFO_KEYS 完全同步（契約漂移守門）',
          set(ac.FUND_LABEL_KEYS) == set(ACCINFO_KEYS)
          and all(isinstance(v, str) and v for v in ac.FUND_LABEL_KEYS.values()))
    info = {'total_assets': 1234567.891, 'cash': 0, 'market_val': 'N/A',
            'power': '1,000', 'available_funds': 'abc', 'currency': 'HKD'}
    frows, has = ac.fund_rows(info)
    got = dict(frows)
    check('欄序 = ACCINFO_KEYS（與契約同序）', [k for k, _ in frows] ==
          [ac.FUND_LABEL_KEYS[k] for k in ACCINFO_KEYS])
    check('千分位與兩位小數', got['fund_total_assets'] == '1,234,567.89')
    check('0 是有效值，照實顯示 0.00', got['fund_cash'] == '0.00')
    check("'N/A' / 非數字 / 缺失 → —（0 與未知是兩件事）",
          got['fund_market_val'] == ac.DASH and got['fund_available_funds'] == ac.DASH
          and got['fund_power'] == '1,000.00'
          and dict(ac.fund_rows({})[0])[ac.FUND_LABEL_KEYS['avl_withdrawal_cash']] == ac.DASH)
    check('有無可用資料旗標', has is True and ac.fund_rows({})[1] is False)
    check('currency 帶回（IB 由 per-tag 帶回、富途從不傳 → 空字串）',
          ac.currency_of(info) == 'HKD' and ac.currency_of({}) == '' and ac.currency_of(None) == '')

    print('\n[7] 市場 / 環境推斷標記', flush=True)
    check('trdmarket_auth list → HK・US（不可出現 Python repr）',
          ac.markets_text({'trdmarket_auth': ['HK', 'US']}) == 'HK・US')
    check('字串照原樣、空值 → 空字串',
          ac.markets_text({'trdmarket_auth': 'HK'}) == 'HK' and ac.markets_text({}) == '')
    check('IB 的 REAL/SIMULATE 標明為推斷（不是 API 欄位）',
          ac.env_inferred({'broker': 'ib'}) and not ac.env_inferred({'broker': 'futu'}))
    check('上報 PAPER 但推斷 REAL → 矛盾（只標記，不靜默改 trd_env）',
          ac.env_conflict({'broker': 'ib', 'trd_env': 'REAL', 'acc_type': 'PAPER'}))
    check('其餘組合不斷言矛盾（AccountType 取值不保證含 PAPER）',
          not ac.env_conflict({'broker': 'ib', 'trd_env': 'REAL', 'acc_type': 'INDIVIDUAL'})
          and not ac.env_conflict({'broker': 'ib', 'trd_env': 'SIMULATE', 'acc_type': 'PAPER'})
          and not ac.env_conflict({'broker': 'futu', 'trd_env': 'REAL', 'acc_type': 'PAPER'}))

    print('\n[8] refresh_funds（循序、逐筆、錯誤歸因到帳戶）', flush=True)
    funds = {'4654894524': {'total_assets': 100.0, 'currency': 'HKD'}}
    fc = FakeClient(funds=funds)
    out = asyncio.run(_drain(ac.refresh_funds(fc, r['rows'])))
    check('逐筆 yield，順序 = 行序', [o['acc_key'] for o in out] ==
          ['futu:4654894524', 'futu:4654894527', 'ib:DU12345', 'ib:123456'])
    check('成功筆帶回 info、失敗筆 info=None + 原文',
          out[0]['info'] == funds['4654894524'] and out[1]['info'] is None
          and out[1]['error'] == '權限不足')
    check('每筆帶時間戳（頁面才能顯示「最後成功：N 秒前」）',
          all(isinstance(o['ts'], float) for o in out))
    check('呼叫帶 broker / env（資金要打到正確的帳戶與環境）',
          fc.fund_calls == [('futu', '4654894524', 'REAL'), ('futu', '4654894527', 'SIMULATE'),
                            ('ib', 'DU12345', 'SIMULATE'), ('ib', '123456', 'REAL')])
    fc2 = FakeClient(funds=funds)
    out2 = asyncio.run(_drain(ac.refresh_funds(fc2, [{'broker': 'futu', 'acc_id': '',
                                                      'acc_key': 'futu:'}])))
    check('空帳戶絕不呼叫 account_info（IB 會合併所有帳戶 → 會顯示錯帳戶的錢）',
          fc2.fund_calls == [] and out2[0]['error_key'] == 'ta_funds_no_account')
    out3 = asyncio.run(_drain(ac.refresh_funds(_RaisingClient(), r['rows'][:1])))
    check('擲出異常亦轉為該帳戶的原文（不中斷其餘帳戶）',
          out3[0]['info'] is None and out3[0]['error'] == 'reqTimeout')


async def _drain(agen):
    return [x async for x in agen]


class _RaisingClient:
    async def account_info(self, account=None, env=None, broker=None):
        raise TimeoutError('reqTimeout')


if __name__ == '__main__':
    main()
    print('\n' + ('❌ 失敗 %d 項：\n   ' % len(FAILURES) + '\n   '.join(FAILURES)
                  if FAILURES else '✅ test_accounts 全部通過'))
    sys.exit(1 if FAILURES else 0)
