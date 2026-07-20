import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402


def main() -> None:
    load_runtime_dotenv(ROOT)
    from scripts.build_journal import _fmt_dt, _load_real_trades

    trades = _load_real_trades()
    trades.sort(key=lambda item: int(item.get("cTime") or 0))
    total_net = 0.0
    for trade in trades:
        net = float(trade.get("realizedPnl") or 0) + float(trade.get("fundingFee") or 0)
        total_net += net
        instrument = trade.get("instId", "")
        direction = (trade.get("direction") or "?").upper()
        timestamp = _fmt_dt(int(trade.get("cTime", 0)))
        print(f"{timestamp} | {instrument:<22} | {direction:<5} | net={net:+.4f}")
    print(f"\nИТОГО NET: {total_net:+.4f} USDT  ({len(trades)} позиций)")


if __name__ == "__main__":
    main()
