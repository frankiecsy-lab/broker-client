import logging

# 🤖 P5：app-level log config — modules 由 print 轉用 logging；root 預設 WARNING，唔設 INFO 就全部睇唔到
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

from .broker import BrokerClient
from .ib_client import IBClient
from .futu_client import FutuClient
from .registry import BROKERS   # 🤖 P2：券商名單一來源（GUI / 測試讀呢度）
from .kline_schema import KLINE_COLUMNS, validate_kline   # 🤖 P3：K 線 schema 契約 + 驗證
