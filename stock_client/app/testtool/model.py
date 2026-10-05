"""Test matrix model — pure data, no Qt imports.

The matrix is codes × ktypes × brokers × funcs (2×6×2×2 = 48 rows by default).
Code format follows Futu's canonical `MARKET.SYMBOL` (US.AAPL / HK.00700);
the broker library accepts the same string for both IB and Futu.
"""

from dataclasses import dataclass, field

DEFAULT_CODES = ["US.AAPL", "HK.00700"]
KTYPES = ["K_1M", "K_5M", "K_15M", "K_60M", "K_DAY", "K_WEEK"]
BROKERS = ["ib", "futu"]
FUNCS = ["get_kline", "stream_kline"]


@dataclass(frozen=True)
class TestSpec:
    """One row of the matrix — a single (code, ktype, broker, func) combination."""

    code: str
    ktype: str
    broker: str
    func: str  # "get_kline" | "stream_kline"

    @property
    def test_id(self) -> str:
        return f"{self.code}|{self.ktype}|{self.broker}|{self.func}"


@dataclass
class MatrixConfig:
    """What the user selected in the parameter panel. Empty list = no rows."""

    codes: list[str] = field(default_factory=lambda: list(DEFAULT_CODES))
    ktypes: list[str] = field(default_factory=lambda: list(KTYPES))
    brokers: list[str] = field(default_factory=lambda: list(BROKERS))
    funcs: list[str] = field(default_factory=lambda: list(FUNCS))
    kline_num: int = 100
    duration_s: int = 30
    strict_pass: bool = True


def build_matrix(cfg: MatrixConfig) -> list[TestSpec]:
    """Expand the config into an ordered spec list.

    get_kline rows come first (fast, no long-lived subscriptions), then stream
    rows — with the worker's concurrency semaphore this naturally forms two
    phases and never jumps the queue.
    """
    specs = [
        TestSpec(c, k, b, f)
        for c in cfg.codes
        for k in cfg.ktypes
        for b in cfg.brokers
        for f in cfg.funcs
    ]
    return sorted(specs, key=lambda s: 0 if s.func == "get_kline" else 1)
