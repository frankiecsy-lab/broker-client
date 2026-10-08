"""全球市場脈搏 — 首頁指數卡的取數層（snapshot + 日K 走勢，純函數式、逐個如實）。

- fetch_pulse(codes, kline_num, progress_cb) → (ok_any, rows, msg)：
  短連線一次過：batch subscribe K_DAY（🤖 實測：唔 subscribe 直接 get_cur_kline 會拒）→
  逐個 get_cur_kline（sparkline 資料）+ get_market_snapshot（最新價/前收/高低/成交額）。
  🤖 實測：get_market_snapshot 一批入面有一隻不支援（如 US..SPX「暫不支援美股指數」）會拖爆
  成批 ret=-1 → 逐個 call，失敗只標記該行，其他照樣顯示。
- row = {code, ok, name, last, prev_close, open, high, low, volume, turnover, spark, err}。
- 美股指數呢個 OpenD 不支援 → 首页清單用 ETF 代理（SPY/QQQ/DIA…），代理與否由頁面向用戶如實講。

CLI 除錯：`python modules/market_pulse.py HK.800000 SH.000001`。
"""
import logging

log = logging.getLogger(__name__)


def fetch_pulse(codes, kline_num=60, progress_cb=None):
    """→ (ok_any, rows, msg)。rows 順序 = codes 順序；逐個失敗如實（err 欄）。"""
    import futu  # lazy：hermetic e2e 唔需要 futu

    from modules.symbol_search import _load_futu_config
    host, port = _load_futu_config()
    rows = []
    ctx = None
    errs = []
    try:
        ctx = futu.OpenQuoteContext(host=host, port=port)
        ret, msg = ctx.subscribe(list(codes), [futu.SubType.K_DAY], subscribe_push=False)
        if ret != 0:
            errs.append(f'subscribe: {msg}')   # 部分失敗唔擋住 — 繼續逐個試
        for i, code in enumerate(codes):
            if progress_cb:
                progress_cb(i + 1, len(codes), code)
            row = {'code': code, 'ok': False, 'name': '', 'last': None,
                   'prev_close': None, 'open': None, 'high': None, 'low': None,
                   'volume': None, 'turnover': None, 'spark': [], 'err': ''}
            ret2, kd = ctx.get_cur_kline(code, kline_num, futu.KLType.K_DAY,
                                         futu.AuType.QFQ)
            if ret2 == 0:
                row['spark'] = [float(c) for c in kd['close'].tolist()]
            ret3, sd = ctx.get_market_snapshot([code])
            if ret3 == 0 and len(sd):
                s = sd.iloc[0]
                row.update(ok=True, name=str(s.get('name', '')),
                           last=float(s['last_price']), prev_close=float(s['prev_close_price']),
                           open=float(s['open_price']), high=float(s['high_price']),
                           low=float(s['low_price']), volume=float(s['volume']),
                           turnover=float(s.get('turnover', 0) or 0))
            if not row['spark'] and not row['ok']:
                row['err'] = str(sd if ret3 != 0 else kd)[:80]
                errs.append(f'{code}: {row["err"]}')
            elif ret2 != 0:
                row['err'] = f'K線: {str(kd)[:60]}'   # snapshot 有就照顯示，走勢欠就如實
            rows.append(row)
        ok_any = any(r['ok'] or r['spark'] for r in rows)
        msg = f'{sum(1 for r in rows if r["ok"])}/{len(codes)}'
        if errs:
            msg += f'（{len(errs)} 個失敗：{errs[0]}）'
        return ok_any, rows, msg
    except Exception as e:   # 連唔到 OpenD 等 — 全部如實失敗
        log.warning('fetch_pulse failed: %s', e)
        rows = [dict(r, err=str(e)) for r in
                ({'code': c, 'ok': False, 'name': '', 'last': None, 'prev_close': None,
                  'open': None, 'high': None, 'low': None, 'volume': None, 'turnover': None,
                  'spark': [], 'err': ''} for c in codes)]
        return False, rows, f'❌ {e}'
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    codes = sys.argv[1:] or ['HK.800000', 'SH.000001', 'US.SPY']
    ok, rows, msg = fetch_pulse(codes)
    print('ok=', ok, '|', msg)
    for r in rows:
        print(f"{r['code']:10s} ok={r['ok']} last={r['last']} prev={r['prev_close']} "
              f"spark={len(r['spark'])} {r['err']}")
