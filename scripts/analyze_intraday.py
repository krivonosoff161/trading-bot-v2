"""
Intraday 15m movement analysis for main 5 pairs.
Shows: open, high, low, close per candle + cumulative move from session open.
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from scripts.mcp_okx_server import get_candles  # noqa: E402


def parse_candles(raw: str) -> list[dict]:
    # Format: "  HH:MM  O=xxx  H=xxx  L=xxx  C=xxx  V=xxx"
    candles = []
    idx = 0
    for line in raw.strip().split('\n'):
        line = line.strip()
        if not line or '|' in line or 'O=' not in line:
            continue
        try:
            parts = line.split()
            time_str = parts[0]  # HH:MM
            o = float(parts[1].split('=')[1])
            h = float(parts[2].split('=')[1])
            lo = float(parts[3].split('=')[1])
            c = float(parts[4].split('=')[1])
            candles.append({'idx': idx, 'time': time_str, 'o': o, 'h': h, 'l': lo, 'c': c})
            idx += 1
        except (ValueError, IndexError):
            continue
    # Data comes newest-first from OKX, reverse to chronological
    return list(reversed(candles))


def analyze_pair(symbol: str, limit: int = 64):
    raw = get_candles(symbol, '15m', limit)
    candles = parse_candles(raw)
    if not candles:
        print(f"{symbol}: no data")
        return

    session_open = candles[0]['o']
    session_high = max(c['h'] for c in candles)
    session_low = min(c['l'] for c in candles)
    last_close = candles[-1]['c']

    range_pct = (session_high - session_low) / session_low * 100
    move_pct = (last_close - session_open) / session_open * 100

    print(f"\n{'='*60}")
    print(f"  {symbol}  [{candles[0]['time']} - {candles[-1]['time']} UTC]")
    print(f"  Range: {range_pct:.2f}%  |  Net move: {move_pct:+.2f}%")
    print(f"  Open: {session_open}  High: {session_high}  Low: {session_low}  Close: {last_close}")
    print(f"{'='*60}")

    # Find the biggest sustained move (rolling 8-candle = 2h window)
    window = 8
    best_bull = {'pct': 0}
    best_bear = {'pct': 0}

    for i in range(len(candles) - window):
        start = candles[i]
        end = candles[i + window]
        sub = candles[i:i + window + 1]
        sub_high = max(c['h'] for c in sub)
        sub_low = min(c['l'] for c in sub)

        bull_move = (sub_high - start['l']) / start['l'] * 100
        bear_move = (start['h'] - sub_low) / start['h'] * 100

        if bull_move > best_bull['pct']:
            best_bull = {
                'pct': bull_move,
                'start': start['time'],
                'end': end['time'],
                'from': start['l'],
                'to': sub_high,
            }
        if bear_move > best_bear['pct']:
            best_bear = {
                'pct': bear_move,
                'start': start['time'],
                'end': end['time'],
                'from': start['h'],
                'to': sub_low,
            }

    if best_bull['pct'] > 0:
        print(f"  Best 2h BULL: {best_bull['start']}->{best_bull['end']}  "
              f"+{best_bull['pct']:.2f}%  ({best_bull['from']} -> {best_bull['to']})")
    if best_bear['pct'] > 0:
        print(f"  Best 2h BEAR: {best_bear['start']}->{best_bear['end']}  "
              f"-{best_bear['pct']:.2f}%  ({best_bear['from']} -> {best_bear['to']})")

    # Print candle table
    print(f"\n  {'Time':>5}  {'Open':>10}  {'High':>10}  {'Low':>10}  {'Close':>10}  {'15m%':>6}  {'CumMv%':>7}")
    for c in candles:
        chg_15m = (c['c'] - c['o']) / c['o'] * 100
        cum_mv = (c['c'] - session_open) / session_open * 100
        marker = ' <--' if abs(chg_15m) >= 0.5 else ''
        print(f"  {c['time']:>5}  {c['o']:>10.4f}  {c['h']:>10.4f}  {c['l']:>10.4f}  {c['c']:>10.4f}  {chg_15m:>+6.2f}%  {cum_mv:>+7.2f}%{marker}")


def main():
    pairs = [
        'BTC-USDT-SWAP',
        'ETH-USDT-SWAP',
        'SOL-USDT-SWAP',
        'XRP-USDT-SWAP',
        'DOGE-USDT-SWAP',
    ]
    print("Intraday 15m analysis — last 16h (64 bars)")
    for sym in pairs:
        analyze_pair(sym, limit=64)
        time.sleep(0.3)


main()
