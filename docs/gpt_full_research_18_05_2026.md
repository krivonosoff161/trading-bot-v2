# Full Research Report — 2026-05-18

## Scope

This report covers:

1. Block 1: main FAST/SWING scanner (`archive scanner` + `live ws_main_screener`)
2. Block 3: pump orchestrator (`2026-05-03` to `2026-05-18`, with current-risk focus on `2026-05-16` to `2026-05-18`)
3. Block 2: BB Fade, kept short by request

Production code under `scripts/ws/*.py` and `config.yaml` was not modified.

Research artifacts created:

- [main_block1_analysis.py](c:/Users/krivo/trading-bot-v2/scripts/analysis/research/main_block1_analysis.py)
- [pump_block3_analysis.py](c:/Users/krivo/trading-bot-v2/scripts/analysis/research/pump_block3_analysis.py)
- [bb_block2_analysis.py](c:/Users/krivo/trading-bot-v2/scripts/analysis/research/bb_block2_analysis.py)
- [main_block1_report.md](c:/Users/krivo/trading-bot-v2/scripts/analysis/research/output/main_block1_report.md)
- [pump_block3_report.md](c:/Users/krivo/trading-bot-v2/scripts/analysis/research/output/pump_block3_report.md)
- [bb_block2_report.md](c:/Users/krivo/trading-bot-v2/scripts/analysis/research/output/bb_block2_report.md)

---

## Executive Summary

- `FAST x DRIFT` is healthy and production-worthy on unified data: `n=71`, `WR=80.3%`, `avg_R=+0.09R`.
- `SWING x TRENDING` is the main problem bucket on unified data: `n=54`, `WR=68.5%`, `avg_R=-0.10R`, `PF=0.75`, `max_DD=-9.61R`.
- The `SWING x TRENDING` regression is real in live data, but the biggest driver is not only “filters got stricter”. The universe changed materially:
  - archive bucket = only `BTC/ETH/DOGE/XRP`
  - live bucket = `0` major-pair trades, dominated by alts (`MEW`, `HMSTR`, `TRUTH`, `KAT`, `PUMP`, `LINEA`, `BASED`, ...)
- Pump current window `2026-05-16..2026-05-18` is bad enough to justify risk action now:
  - baseline `n=121`, `WR=32.2%`, `net=-19.98%`
  - safe mitigation: daily/session ban after `2` consecutive SLs (`Sim7`, `+4.75pp`)
  - aggressive mitigation: hard blocks on no-tape drag pairs plus selected overrides (`Sim9`, `+24.81pp`, overfit risk high)
- BB Fade remains preliminary. Archive logged sample is positive, the new wick-rejection backtest is positive, but live `ws_bb_fade` has only `3` trades.

---

## Data Notes

- Block 1 unified dataset:
  - decisive rows: `185`
  - sources: `archive_scanner=170`, `live_main=82`
- Pump:
  - full joined sample: `757` trades from `2026-05-03` to `2026-05-18`
  - current-risk window: `121` trades from `2026-05-16` to `2026-05-18`
- Pump MFE/MAE coverage:
  - current window: `121/121` from live labels directly
  - full live set: `221/523` direct in labels, plus `151` extra via log backfill, `151` unmatched
- Ignored as requested:
  - `scripts/analysis/backtest_runs/signal_analysis_2026-05-18_18-52.md`

---

## Block 1 — Main Scanner

### Unified regime x style matrix

| Bucket | n | WR | avg_R | std_R | PF | max_DD | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| DRIFT x FAST | 71 | 80.3% | +0.09 | 0.71 | 1.35 | -5.78 | solid |
| DRIFT x FADE | 18 | 55.6% | +0.11 | 1.05 | 1.25 | -3.00 | preliminary |
| TRENDING x FAST | 14 | 71.4% | +0.34 | 0.87 | 2.19 | -2.00 | preliminary |
| TRENDING x SWING | 54 | 68.5% | -0.10 | 0.74 | 0.75 | -9.61 | solid |
| RANGING x FAST | 3 | 66.7% | +0.42 | 1.05 | 2.25 | -1.00 | N/A |
| RANGING x FADE | 24 | 62.5% | +0.37 | 1.14 | 1.98 | -4.00 | preliminary |

Main conclusion: the problem is not “kill SWING globally”. The problem is `SWING x TRENDING`.

### Worst pairs inside `TRENDING x SWING`

Current worst names are concentrated in the live alt bucket, not the old majors:

- `HMSTR-USDT`: `n=4`, `WR=75.0%`, `avg_R=-1.00R`
- `KAT-USDT`: `n=3`, `WR=0.0%`, `avg_R=-1.00R`
- `PUMP-USDT`: `n=2`, `WR=0.0%`, `avg_R=-1.00R`
- `LINEA-USDT`: `n=2`, `WR=100.0%`, `avg_R=-0.54R`
- `MEW-USDT`: `n=5`, `WR=40.0%`, `avg_R=-0.32R`

Interpretation: pair-level drag exists, but the larger issue is bucket composition drift. Archive and live are not the same instrument set.

### Live vs archive bias

| Bucket | live_n | live_WR | live_avg_R | archive_n | archive_WR | archive_avg_R | note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| DRIFT x FAST | 18 | 94.4% | +0.21 | 53 | 75.5% | +0.06 | live better |
| TRENDING x SWING | 30 | 63.3% | -0.25 | 24 | 75.0% | +0.09 | bias likely, treat live as current truth |

For production decisions, `live_main` is the only defensible truth.

---

## `TRENDING x SWING` Regression Analysis

### What changed

This is the most valuable finding in the whole report.

1. Universe shift is the largest change.
   - Old `ws_scanner` used fixed core pairs from [ws_feed.py](c:/Users/krivo/trading-bot-v2/src/data/ws_feed.py): `BTC/ETH/SOL/XRP/DOGE`.
   - New `ws_main_screener` builds a dynamic `top_n_pairs` universe by `volCcy24h`, then adds pinned pairs, in [ws_main_screener.py](c:/Users/krivo/trading-bot-v2/scripts/ws/ws_main_screener.py).
   - Archive `TRENDING x SWING` bucket = `24` decisive trades, all from `BTC/ETH/DOGE/XRP`.
   - Live `TRENDING x SWING` bucket = `30` decisive trades, `0` majors.

2. A new 15m prefilter exists in `ws_main_screener`.
   - In [ws_main_screener.py](c:/Users/krivo/trading-bot-v2/scripts/ws/ws_main_screener.py), `_on_15m_close()` exits early when both:
     - `vol_ratio < prefilter_vol_ratio_min`
     - `adx_approx < prefilter_adx_min`
   - Current config:
     - `prefilter_vol_ratio_min: 1.0`
     - `prefilter_adx_min: 10`
   - Old `ws_scanner` did not have this outer gate before `compute_signal()`.

3. A new hard TRENDING volume veto was added on `2026-05-15`.
   - Commit `2ea6a42`: `vol_ratio_sig < min_vol_ratio_trending(=1.5) -> NO_TRADE`
   - Code is in [signal_engine.py](c:/Users/krivo/trading-bot-v2/src/strategy/signal_engine.py).
   - On archive `TRENDING x SWING`, the subset with `vol_ratio < 1.5` was not bad:
     - `n=14`, `WR=78.6%`, `avg_R=+0.16R`
   - This means the new veto would have cut historically profitable archive setups.

4. A new TRENDING SWING short oversold veto was added on `2026-05-15`.
   - Commit `57ec2df`
   - It blocks `TRENDING x SWING` shorts when RSI is deeply oversold.
   - Archive bucket was mostly buys (`21 buy / 3 sell`), so this is secondary, not primary.

### What exactly was cut

The honest answer is:

- some historically good low-volume TRENDING SWING setups were cut by the new `min_vol_ratio_trending=1.5` veto
- but the larger regression comes from switching from majors to an alt-heavy dynamic universe

So the failure mode is not “one bad threshold”. It is:

`new universe + stricter outer screening + new trend-vol veto`

### Production implication

Do not disable `SWING` globally.

Current evidence supports this narrower claim:

- old major-pair `TRENDING x SWING` edge did not survive the move to the current live universe
- current live alt bucket is bad enough that any restore attempt must be validated on overlap data, not inferred from archive majors

### Recommendation

No production YAML change is justified yet for Block 1.

The next valid experiment is not “kill SWING” and not “remove the veto blindly”.
The right experiment is:

1. run `TRENDING x SWING` separately on majors vs alt universe
2. audit how many historical major winners would be cut by `min_vol_ratio_trending=1.5`
3. only then decide whether to relax the TRENDING volume veto, or split SWING policy by pair class

---

## Block 3 — Pump

### Baseline

- Current baseline `2026-05-16..2026-05-18`: `n=121`, `WR=32.2%`, `net=-19.98%`
- Expanded sample `2026-05-03..2026-05-18`: `n=757`, `WR=38.3%`, `net=+23.19%`

Current drag pairs:

- `APR-USDT-SWAP`: `n=5`, `net=-6.90%`
- `BSB-USDT-SWAP`: `n=9`, `net=-6.19%`
- `BABY-USDT-SWAP`: `n=5`, `net=-5.17%`
- `RIVER-USDT-SWAP`: `n=3`, `net=-4.23%`
- `LAB-USDT-SWAP`: `n=3`, `net=-3.47%`
- `BILL-USDT-SWAP`: `n=15`, `net=-2.89%`

### Tape coverage verdict

Current baseline tape availability:

| Pair | current_n | usable_tape | file_present_no_window | note |
| --- | ---: | ---: | ---: | --- |
| APR-USDT-SWAP | 5 | 0 | 0 | no tape files on disk |
| RIVER-USDT-SWAP | 3 | 0 | 0 | no tape files on disk |
| LAB-USDT-SWAP | 3 | 0 | 0 | no tape files on disk |
| BABY-USDT-SWAP | 5 | 2 | 1 | partial only |
| BSB-USDT-SWAP | 9 | 2 | 7 | partial only |
| BILL-USDT-SWAP | 15 | 14 | 1 | mostly covered |

Decision:

- `APR`, `RIVER`, `LAB`: no local tape basis for an entry filter. Decision must be pair override or hard block until Phase G.0 backfill.
- `BABY`: tape only proves a local pattern exists in principle. It is not enough for a production tape filter.

### BABY tape slice

Tested veto rule:

`pre_buy_ratio<0.50 && pre_cvd<0 && post_buy_ratio<0.40 && post_cvd<0`

Observed covered BABY losses:

- `2026-05-16 07:20 UTC` -> veto
- `2026-05-16 07:30 UTC` -> veto
- `2026-05-17 23:35 UTC` -> file exists, but no ticks around entry; recorder coverage gap

Conclusion: BABY tape has proof-of-concept value, not deployment-grade coverage.

### Sim0-Sim9

| Sim | Logic | net | delta_vs_base |
| --- | --- | ---: | ---: |
| Sim0 | current baseline | -19.98% | +0.00pp |
| Sim1 | BABY off | -14.81% | +5.17pp |
| Sim2 | BABY + RIVER off | -10.58% | +9.41pp |
| Sim3 | APR half | -16.53% | +3.45pp |
| Sim4 | BSB half | -16.89% | +3.10pp |
| Sim5 | BILL cap2 ban2 | -18.05% | +1.94pp |
| Sim6 | all overrides | -2.09% | +17.89pp |
| Sim7 | ban after 2 consecutive SLs on all pairs | -15.24% | +4.75pp |
| Sim8 | hard block APR/RIVER/LAB + BABY tape veto on covered trades | -3.71% | +16.27pp |
| Sim9 | hard block APR/RIVER/LAB/BABY + BSB half + BILL cap2 | +4.83% | +24.81pp |

### Safe vs aggressive

#### Safe

`Sim7` is the only clean immediate candidate.

Reason:

- no pair-specific overfitting
- no dependence on missing tape
- no dependency on ambiguous MFE reinterpretation
- still gives `+4.75pp`

Proposed YAML diff:

```yaml
pump_orchestrator:
  session_ban_sl_no_tp: 2
```

This maps best to the tested logic: ban a pair after `2` consecutive same-day SLs instead of `3`.

#### Aggressive

`Sim9` is the best deployable-risk candidate on the observed 3-day window, but overfit risk is high.

Reason:

- it explicitly handles no-tape drag pairs (`APR`, `RIVER`, `LAB`)
- it treats `BABY` as blocked until backfill exists
- it still keeps softer treatment for `BSB` and `BILL`

Proposed future YAML sketch:

```yaml
pump_orchestrator:
  session_ban_sl_no_tp: 2
  pair_risk_overrides:
    APR-USDT-SWAP:
      mode: block
    RIVER-USDT-SWAP:
      mode: block
    LAB-USDT-SWAP:
      mode: block
    BABY-USDT-SWAP:
      mode: block
    BSB-USDT-SWAP:
      size_mult: 0.5
    BILL-USDT-SWAP:
      max_trades_per_day: 2
      ban_after_sl_streak: 2
```

Important caveat: `pair_risk_overrides` is a research proposal here. This report does not claim the production code currently consumes that exact schema.

#### Breakeven interpretation

For current pump window, this branch is no longer blocked:

- `2026-05-16..2026-05-18` labels already carry `mfe_pct/mae_pct` for all `121/121` trades

What remains limited is historical live backfill before that point, and archive has no local orchestrator MFE log.

---

## Block 2 — BB Fade

### Archive logged sample

- `43` decisive archive `bb_fade` trades
- `WR=60.47%`
- `avg_R=+0.30`

By regime:

| Bucket | n | WR | avg_R |
| --- | ---: | ---: | ---: |
| DRIFT | 18 | 55.56% | 0.11 |
| RANGING | 24 | 62.50% | 0.37 |
| TRENDING | 1 | 100.00% | 2.28 |

Archive tape hypothesis could not be retested on local disk:

- local tape coverage for archive BB Fade sample = `0/47`

### Current wick-rejection backtest

Broad cache backtest of the new `ws_bb_fade` logic:

- total trades: `657`
- decisive trades: `344`
- `WR=70.64%`
- `avg_net=+0.48%`

By BB width:

| BW bucket | n | WR | avg_net |
| --- | ---: | ---: | ---: |
| 2-3% | 206 | 67.96% | +0.13% |
| 3-5% | 92 | 68.48% | +0.24% |
| 5%+ | 46 | 86.96% | +2.53% |

Worst symbols with `n>=5`:

- `SATS-USDT-SWAP`: `n=9`, `WR=33.3%`, `avg_net=-0.60%`
- `BONK-USDT-SWAP`: `n=5`, `WR=60.0%`, `avg_net=-0.19%`
- `PENGU-USDT-SWAP`: `n=19`, `WR=57.9%`, `avg_net=-0.08%`

### Live worker check

Current live `ws_bb_fade` sample:

- all trades: `3`
- decisive trades: `2`
- `WR=50.0%`
- total net: `-5.26%`

Trades:

- `2026-05-16 16:30 UTC` `TRUTH-USDT-SWAP` buy -> `SL`, `net=-5.87%`, `bw_pct=7.92`
- `2026-05-17 12:40 UTC` `CHZ-USDT-SWAP` sell -> `TIME`, `net=-0.68%`
- `2026-05-17 14:15 UTC` `OFC-USDT-SWAP` buy -> `TP`, `net=+1.28%`

Verdict:

- leave BB Fade config unchanged
- sample is too small for production retuning
- revisit after at least `20` decisive live trades

---

## Final Recommendations

1. Main scanner:
   - do not disable `SWING` globally
   - treat `TRENDING x SWING` as a live alt-universe regression problem, not a universal style failure
   - next research should isolate majors vs alt universe before any threshold rollback

2. Pump:
   - safe now: reduce same-day ban threshold from `3` SLs to `2`
   - aggressive optional path: hard-block `APR/RIVER/LAB/BABY`, half-size `BSB`, cap `BILL` at `2` trades/day

3. BB Fade:
   - no config changes yet
   - wait for live sample

---

## Audit Notes

- All conclusions above are based on saved research outputs, not on modified production code.
- Where overlap data is missing, the report states that directly:
  - no archive tape for BB Fade
  - no tape files for `APR/RIVER/LAB`
  - partial tape only for `BABY`
- The strongest claims in this report are:
  - `SWING x TRENDING` is negative on unified data
  - current live truth is worse than archive for that bucket
  - Pump current window justifies immediate risk mitigation

