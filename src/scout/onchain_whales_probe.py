"""
SCOUT #2 — on-chain whale / capital-flow feasibility probe (KEYLESS ONLY).

Goal: can we detect large on-chain capital movements (whale flows, stablecoin
mint/burn, exchange-flow proxies, BTC large-tx activity) WITHOUT paid keys, and
is there any LEAD to price = potential edge?

Prior is skeptical (27.05 research: 1m BTC->alt lead-lag absent; volume/CVD do not
lead pumps). On-chain is a DIFFERENT angle (harder for retail), so we test it, but
we do NOT manufacture an edge.

Sources used (all keyless, read-only, no .env / no auth):
  - DeFiLlama stablecoins.llama.fi  : stablecoin total mcap + per-coin history (mint/burn = fresh capital)
  - DeFiLlama api.llama.fi          : chain TVL history, DEX volume history
  - mempool.space                   : BTC mempool size, recent blocks, fee pressure (live only)
  - blockchain.info/charts          : BTC tx count, estimated USD tx volume (daily history)
  - CoinGecko                       : BTC/ETH price history (lead-lag target)

Blocked / paid / key-required (honestly excluded):
  - Whale Alert            : API key + paid subscription (no usable free large-tx feed)
  - DeFiLlama bridges      : HTTP 402 Payment Required (Pro-only)
  - Coinglass v4           : API key required
  - CryptoQuant / Glassnode: paid (exchange netflow / reserves)
  - CMC keyless reserves   : current snapshot only, no historical netflow series

Lead-lag: stablecoin total-mcap DAILY history is the only deep keyless "flow" series
with enough length. We test whether stablecoin net-minting (capital entering crypto)
LEADS BTC forward returns. Honest n / overlap / no OOS caveats are printed.

Usage:
  python onchain_whales_probe.py            # live bundle + lead-lag on history
  python onchain_whales_probe.py --no-llt   # skip lead-lag (bundle only)
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (research-probe; keyless)"}
TIMEOUT = 30


def _get(url, label, retries=2):
    """GET json keyless. Returns (data_or_None, status_str)."""
    last = ""
    for attempt in range(retries + 1):
        try:
            t = time.time()
            req = urllib.request.Request(url, headers=UA)
            r = urllib.request.urlopen(req, timeout=TIMEOUT)
            raw = r.read()
            dt = time.time() - t
            try:
                data = json.loads(raw)
            except Exception:
                data = raw
            print(f"  [OK {r.status} {dt:0.1f}s {len(raw)//1024}KB] {label}")
            return data, "ok"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (402, 401, 403):
                print(f"  [BLOCKED {e.code}] {label} (paid/key-required)")
                return None, f"blocked_{e.code}"
        except Exception as e:
            last = type(e).__name__
        time.sleep(1.5)
    print(f"  [FAIL {last}] {label}")
    return None, "fail"


def _ts_to_day(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# 1. LIVE BUNDLE
# --------------------------------------------------------------------------- #
def live_bundle():
    print("=" * 70)
    print("KEYLESS ON-CHAIN BUNDLE (live)")
    print("=" * 70)
    bundle = {}

    # --- Stablecoins: total mcap + top movers ---
    print("\n[DeFiLlama stablecoins]")
    d, st = _get("https://stablecoins.llama.fi/stablecoins?includePrices=true",
                 "stablecoins overview")
    if d:
        pegged = d.get("peggedAssets", [])
        total = sum(a.get("circulating", {}).get("peggedUSD", 0) or 0 for a in pegged)
        top = sorted(pegged, key=lambda a: a.get("circulating", {}).get("peggedUSD", 0) or 0,
                     reverse=True)[:5]
        bundle["stablecoin_total_mcap_usd"] = total
        print(f"   total stablecoin mcap: ${total/1e9:0.1f}B  ({len(pegged)} assets)")
        for a in top:
            circ = a.get("circulating", {}).get("peggedUSD", 0) or 0
            prev = a.get("circulatingPrevDay", {}).get("peggedUSD", 0) or 0
            d1 = circ - prev
            print(f"     {a.get('symbol'):>6}  ${circ/1e9:7.2f}B   24h d_mint ${d1/1e6:+8.1f}M")

    # --- Chain TVL (capital parked in DeFi) ---
    print("\n[DeFiLlama chain TVL]")
    d, st = _get("https://api.llama.fi/v2/historicalChainTvl",
                 "all-chain TVL history")
    if isinstance(d, list) and len(d) > 1:
        last, prev = d[-1], d[-2]
        bundle["defi_tvl_usd"] = last["tvl"]
        print(f"   total DeFi TVL: ${last['tvl']/1e9:0.1f}B  "
              f"24h d ${(last['tvl']-prev['tvl'])/1e6:+0.0f}M")

    # --- DEX volume (on-chain trading activity) ---
    print("\n[DeFiLlama DEX volume]")
    d, st = _get("https://api.llama.fi/overview/dexs"
                 "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true",
                 "DEX volume 24h")
    if isinstance(d, dict):
        bundle["dex_vol_24h_usd"] = d.get("total24h")
        print(f"   DEX 24h volume: ${ (d.get('total24h') or 0)/1e9:0.1f}B")

    # --- BTC mempool / blocks (live activity pressure) ---
    print("\n[mempool.space BTC]")
    d, st = _get("https://mempool.space/api/mempool", "mempool size")
    if isinstance(d, dict):
        bundle["btc_mempool_count"] = d.get("count")
        print(f"   mempool unconfirmed tx: {d.get('count'):,}  "
              f"vsize {d.get('vsize',0)/1e6:0.1f} MvB")
    d, st = _get("https://mempool.space/api/v1/fees/recommended", "fees")
    if isinstance(d, dict):
        print(f"   fastest fee: {d.get('fastestFee')} sat/vB (pressure proxy)")
    d, st = _get("https://mempool.space/api/blocks", "recent blocks")
    if isinstance(d, list) and d:
        b = d[0]
        print(f"   tip block {b.get('height')}: {b.get('tx_count')} tx")

    # --- blockchain.info BTC on-chain $ volume (whale-ish settlement) ---
    print("\n[blockchain.info BTC]")
    d, st = _get("https://api.blockchain.info/charts/estimated-transaction-volume-usd"
                 "?timespan=7days&format=json&cors=true", "est. tx volume USD 7d")
    if isinstance(d, dict):
        vals = d.get("values", [])
        if vals:
            bundle["btc_onchain_usd_vol_latest"] = vals[-1]["y"]
            print(f"   latest daily on-chain USD volume: ${vals[-1]['y']/1e9:0.1f}B")

    print("\n[bundle keys]", list(bundle.keys()))
    return bundle


# --------------------------------------------------------------------------- #
# 2. HISTORY FETCH (for lead-lag)
# --------------------------------------------------------------------------- #
def fetch_stablecoin_mcap_history():
    """Daily total stablecoin mcap (USD). Returns {day: mcap}."""
    d, st = _get("https://stablecoins.llama.fi/stablecoincharts/all",
                 "stablecoin mcap history")
    out = {}
    if isinstance(d, list):
        for p in d:
            usd = p.get("totalCirculatingUSD", {})
            v = usd.get("peggedUSD") if isinstance(usd, dict) else None
            if v:
                out[_ts_to_day(p["date"])] = float(v)
    return out


def fetch_btc_price_history(days=365):
    """Daily BTC price from CoinGecko. Returns {day: price}."""
    d, st = _get(f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
                 f"?vs_currency=usd&days={days}&interval=daily", f"BTC price {days}d")
    out = {}
    if isinstance(d, dict):
        for ts_ms, price in d.get("prices", []):
            out[_ts_to_day(ts_ms / 1000)] = float(price)
    return out


# --------------------------------------------------------------------------- #
# 3. LEAD-LAG TEST  (stablecoin net-mint  ->  BTC forward return)
# --------------------------------------------------------------------------- #
def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0, 0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0, n
    return cov / (vx * vy) ** 0.5, n


def lead_lag():
    print("\n" + "=" * 70)
    print("LEAD-LAG TEST: stablecoin net-mint  ->  BTC forward return")
    print("Hypothesis: fresh stablecoin supply (capital entering crypto) leads BTC.")
    print("=" * 70)

    mcap = fetch_stablecoin_mcap_history()
    # CoinGecko keyless caps daily history ~365d; use that window for the overlap.
    price = fetch_btc_price_history(days=365)
    if not mcap or not price:
        print("  Could not fetch both series; aborting lead-lag.")
        return None

    days = sorted(set(mcap) & set(price))
    print(f"  overlap days: {len(days)}  ({days[0]} .. {days[-1]})")
    if len(days) < 40:
        print("  Too few overlapping days for a meaningful test.")
        return None

    # Daily series aligned
    m = [mcap[d] for d in days]
    p = [price[d] for d in days]

    # signal: 1-day stablecoin net-mint growth (%), i.e. capital inflow rate
    # target: BTC forward return over horizon h (days)
    results = []
    for h in (1, 2, 3, 5, 7):
        sig, fwd = [], []
        for i in range(1, len(days) - h):
            mint_g = (m[i] - m[i - 1]) / m[i - 1] if m[i - 1] else 0.0
            btc_fwd = (p[i + h] - p[i]) / p[i] if p[i] else 0.0
            sig.append(mint_g)
            fwd.append(btc_fwd)
        r, n = _pearson(sig, fwd)
        # rough t-stat (NOT corrected for h-day overlap autocorrelation)
        t = r * ((n - 2) / max(1e-9, 1 - r * r)) ** 0.5 if n > 2 else 0.0
        results.append((h, r, t, n))
        print(f"  h={h}d  corr(net_mint -> BTC fwd ret) = {r:+0.3f}  "
              f"t~{t:+0.2f}  n={n}")

    # Top/bottom decile event study: does a big mint-day precede higher BTC return?
    print("\n  Event study (mint-day deciles, horizon 3d):")
    h = 3
    rows = []
    for i in range(1, len(days) - h):
        mint_g = (m[i] - m[i - 1]) / m[i - 1] if m[i - 1] else 0.0
        btc_fwd = (p[i + h] - p[i]) / p[i] if p[i] else 0.0
        rows.append((mint_g, btc_fwd))
    rows.sort(key=lambda x: x[0])
    k = max(1, len(rows) // 10)
    low = rows[:k]
    high = rows[-k:]
    def avg(items):
        return sum(item[1] for item in items) / len(items)
    print(f"    top-decile mint days (n={k}):    BTC fwd 3d avg = {avg(high)*100:+0.2f}%")
    print(f"    bottom-decile mint days (n={k}): BTC fwd 3d avg = {avg(low)*100:+0.2f}%")
    print(f"    spread (high-low) = {(avg(high)-avg(low))*100:+0.2f}%")

    print("\n  CAVEATS (honest):")
    print("   - n is small (~1yr daily); CoinGecko keyless caps history at 365d.")
    print("   - h-day forward returns OVERLAP -> t-stats inflated (no Newey-West here).")
    print("   - single regime, NO out-of-sample split -> this is a PEEK, not proof.")
    print("   - mcap is daily-snapshot; cannot see intraday whale timing keyless.")
    return results


def main():
    do_llt = "--no-llt" not in sys.argv
    print(f"# onchain_whales_probe.py  run {datetime.now(timezone.utc).isoformat()}")
    live_bundle()
    if do_llt:
        lead_lag()
    print("\nDone. Read-only, keyless. No edge implied without forward OOS.")


if __name__ == "__main__":
    main()
