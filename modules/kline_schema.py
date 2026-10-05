import pandas as pd

# 🤖 P3：K 線 DataFrame 契約（單一來源）— 所有 broker client 嘅 get_kline / stream_kline 輸出都必須符合呢個 schema。
#    IB / FUTU 原始格式唔同，所以每個 client 保留自己薄薄一層 _normalize_kline（「自家 raw 欄 → 標準欄」映射，
#    嗰部分故意留喺各 client）；但「標準 K 線長咩樣」— 欄名 / 順序 / dtype — 只定義喺呢度。改 schema 只改呢個檔。

KLINE_COLUMNS = ['time_key', 'open', 'high', 'low', 'close', 'volume']
OHLC_COLS = ('open', 'high', 'low', 'close')


def reorder_kline(df):
    """按契約順序排欄（各 client normalize 共用 — 唔使各自維護 standard_order list）。"""
    return df[KLINE_COLUMNS]


def validate_kline(df):
    """驗 df 符合 K 線契約；回傳 (ok, reason)。client 成功前 + contract test 都用呢個。"""
    if df is None:
        return False, "df is None"
    missing = [c for c in KLINE_COLUMNS if c not in df.columns]
    if missing:
        return False, f"缺欄 {missing}"
    extra = [c for c in df.columns if c not in KLINE_COLUMNS]
    if extra:
        return False, f"多餘欄 {extra}"
    if not pd.api.types.is_datetime64_any_dtype(df['time_key']):
        return False, "time_key 唔係 datetime 型"
    for col in OHLC_COLS:
        if not pd.api.types.is_numeric_dtype(df[col]):
            return False, f"{col} 唔係數值型"
    if not pd.api.types.is_integer_dtype(df['volume']):
        return False, "volume 唔係整數型"
    return True, ""
