# 🤖 P2：券商名單一來源（registry）— GUI combo / 測試 / config 全部讀呢度，唔再散落 magic string。
#    加新 client：繼承 BrokerBase + 聲明 NAME + 喺下面 tuple 加一行 → dispatch / GUI / contract test 自動覆蓋。
from .futu_client import FutuClient
from .ib_client import IBClient

BROKERS = {cls.NAME: cls for cls in (FutuClient, IBClient)}


def get_broker_class(name):
    """按名查 client class；打錯字即刻炸（fail-fast）並列出有效名。"""
    try:
        return BROKERS[str(name).lower()]
    except KeyError:
        raise ValueError(f"❌ 不支援的券商類型 [{name}]，有效：{sorted(BROKERS)}") from None
