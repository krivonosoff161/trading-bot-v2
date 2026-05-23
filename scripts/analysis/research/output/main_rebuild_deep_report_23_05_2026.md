# Main Rebuild Deep Research - 23.05.2026

Research-only run. Production files were read for audit only; outputs are in `scripts/analysis/research/output`.

## Coverage

- replay candles: `2026-05-04T19:00:00Z` -> `2026-05-14T19:00:00Z`, universe `29`
- tick root: `E:\trading-data\ticks`
- tick symbols with any data: `29` / `29`
- tick symbols overlapping replay dates `2026-05-04..2026-05-14`: `24` / `29`
- honest caveat: replay GO window starts 04.05, local tick coverage starts mostly 11.05, so the exact 04-10.05 part cannot be tick-replayed without external historical trades.

## P0-1 MFE/MAE And Geometry

| cell | entry | tier | n | MFE p25 | p50 | p75 | p90 | MAE p50 | net |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRENDING_IMPULSE | mid | all | 48 | 0.57% | 0.84% | 1.42% | 2.30% | -0.74% | 0.39% |
| TRENDING_IMPULSE | mid | high_vol_alt | 8 | 1.56% | 2.25% | 2.73% | 4.18% | -1.96% | 1.47% |
| TRENDING_IMPULSE | mid | low_vol_alt | 2 | 0.57% | 0.89% | 1.20% | 1.39% | -0.51% | -0.27% |
| TRENDING_IMPULSE | mid | major | 2 | 0.29% | 0.40% | 0.51% | 0.58% | -0.55% | -0.31% |
| TRENDING_IMPULSE | mid | mid_vol_alt | 36 | 0.56% | 0.76% | 1.20% | 1.80% | -0.73% | 0.23% |
| DRIFT | open | all | 25 | 0.37% | 0.49% | 0.64% | 0.82% | -0.16% | 0.07% |
| DRIFT | open | low_vol_alt | 1 | 0.82% | 0.82% | 0.82% | 0.82% | -0.26% | 0.15% |
| DRIFT | open | major | 8 | 0.32% | 0.39% | 0.51% | 0.59% | -0.17% | 0.00% |
| DRIFT | open | mid_vol_alt | 16 | 0.38% | 0.52% | 0.79% | 0.97% | -0.16% | 0.10% |
| RANGING | close | all | 29 | 0.42% | 1.06% | 1.79% | 3.22% | -0.42% | 0.37% |
| RANGING | close | high_vol_alt | 2 | 0.84% | 1.12% | 1.39% | 1.56% | -0.66% | -0.30% |
| RANGING | close | low_vol_alt | 5 | 0.21% | 1.05% | 1.40% | 1.50% | -0.26% | 0.11% |
| RANGING | close | major | 5 | 0.31% | 1.42% | 1.50% | 1.75% | -0.26% | 0.36% |
| RANGING | close | mid_vol_alt | 17 | 0.50% | 1.06% | 2.84% | 4.71% | -0.50% | 0.52% |

### Current vs Derived Geometry

| cell | formula | n | current net | derived net | delta | long net | short net | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRENDING_IMPULSE | entry=tick/mid proxy; SL=impulse low/high +0.10%; TP=1.0x impulse body or structure_k2 ride | 48 | 0.10% | 0.39% | 0.30% | 0.56% | 0.27% | GO |
| DRIFT | NO-GO; best measured rescue is open+0.75x body TP, but sample/sides fail | 25 | -0.59% | 0.07% | 0.66% | 0.12% | 0.02% | NO-GO: split |
| RANGING | entry=boundary close; SL=range/BB structure; exit=structure_k2 or BB middle/opposite fade | 29 | -0.11% | 0.37% | 0.47% | 0.65% | 0.19% | NO-GO: split |

Derived code shape:

```python
if regime == 'TRENDING_IMPULSE':
    entry = tick_trigger('>=0.30% within 10-20s')  # mid proxy in replay
    stop = impulse_extreme +/- 0.10%
    exit = scaled_tp(1.0 * impulse_body) or structure_k2 ride
elif regime == 'RANGING':
    entry = bb_boundary_touch_close
    stop = range_extreme +/- buffer
    exit = bb_middle / opposite_band / structure_k2
else:  # DRIFT
    no_trade_until_subcondition_has_n20_both_sides
```

## P0-2 Real Tick Entry And Falsification

| test | n | net | WR/positive | capture |
| --- | ---: | ---: | ---: | ---: |
| all | 233 | 3.02% | 83.26% | 48.58% |
| drop top pair BSB-USDT-SWAP | 157 | 2.24% | 81.53% | 48.03% |
| opposite same entry | 233 | -0.26% | 1.29% | 86.36% |
| shuffle side p50 | 300 | 1.35% | 100.00% | n/a |

Side split:
| side | n | net | WR |
| --- | ---: | ---: | ---: |
| long | 108 | 3.72% | 81.48% |
| short | 125 | 2.42% | 84.80% |

Date split:
| period | n | net | WR |
| --- | ---: | ---: | ---: |
| early | 34 | 1.69% | 85.29% |
| late | 199 | 3.25% | 82.91% |

Verdict: tick-entry edge survives real tick measurement on the available 17-23.05 tape if the fixed top config stays positive after top-pair removal and fails on opposite/shuffled labels. It is still not the exact 04-14 replay sample.

## P1-1 DRIFT

| group | n | net | WR | long n | long net | short n | short net | n20 both+ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all | 25 | 0.07% | 84.00% | 13.00 | 0.12% | 12.00 | 0.02% | no |
| tier=mid_vol_alt | 16 | 0.10% | 87.50% | 6.00 | 0.17% | 10.00 | 0.06% | no |
| period=early | 14 | 0.06% | 85.71% | 8.00 | 0.09% | 6.00 | 0.03% | no |
| session=us | 12 | 0.09% | 83.33% | 6.00 | 0.17% | 6.00 | 0.00% | no |
| period=late | 11 | 0.08% | 81.82% | 5.00 | 0.17% | 6.00 | 0.01% | no |
| session=eu | 8 | 0.09% | 87.50% | 4.00 | 0.02% | 4.00 | 0.15% | no |
| tier=major | 8 | 0.00% | 75.00% | 6.00 | 0.07% | 2.00 | -0.19% | no |
| session=asia | 5 | 0.01% | 80.00% | 3.00 | 0.15% | 2.00 | -0.19% | no |
| tier=low_vol_alt | 1 | 0.15% | 100.00% | 1.00 | 0.15% | 0.00 | n/a | no |

Verdict: drop DRIFT as a new rebuild branch until a real subcondition has n>=20 on both sides and net>0. Current best rescue is still thin and does not pass side/sample gates.

## P1-2 RANGING Fade

| tol | adx | bb max | target | n | net | WR | hold |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.05 | 22.0 | 3.5 | opposite | 10 | -0.05% | 30.00% | 49.50m |
| 0.05 | 22.0 | 5.0 | opposite | 10 | -0.05% | 30.00% | 49.50m |
| 0.05 | 26.0 | 3.5 | opposite | 10 | -0.05% | 30.00% | 49.50m |
| 0.05 | 26.0 | 5.0 | opposite | 10 | -0.05% | 30.00% | 49.50m |
| 0.05 | 30.0 | 3.5 | opposite | 10 | -0.05% | 30.00% | 49.50m |
| 0.05 | 30.0 | 5.0 | opposite | 10 | -0.05% | 30.00% | 49.50m |
| 0.1 | 22.0 | 3.5 | opposite | 12 | -0.08% | 25.00% | 53.75m |
| 0.1 | 22.0 | 5.0 | opposite | 12 | -0.08% | 25.00% | 53.75m |

Verdict: no GO. Best fade rows are small and mostly negative; short side and high-vol tiers are the only hints, not a tradable rule.

## P2-1 Collision Audit

| place | collision | 3-invariant guard |
| --- | ---: | ---: |
| src/strategy/signal_engine.py:145 | Feature calculation and classification are coupled inside compute_signal; analyzers would recalc or reinterpret the same inputs if split naively. | One FeatureSnapshot per pair/bar; analyzers receive immutable snapshot; snapshot hash logged with signal. |
| src/strategy/signal_engine.py:991 | Regime is detected, then TRENDING can be rewritten to RANGING on 4H conflict. | Classifier is the only owner of regime; rewrites must emit classifier_reason; analyzers cannot mutate regime. |
| src/strategy/signal_engine.py:1000 | TRENDING chooses SWING first, then FAST fallback, so style axis controls entry and stop policy inside regime logic. | Remove style arbitration from analyzer outputs; one analyzer owns one regime contract and max_hold. |
| src/strategy/signal_engine.py:1039 | RANGING has both base and recovery branches, but shares output contract with trend-like entries. | Ranging analyzer emits only fade contracts; recovery/expansion routes to trend/impulse or NO_TRADE. |
| src/strategy/signal_engine.py:1124 | VWAP veto runs after side selection and can nullify DRIFT/RANGING entries outside the analyzer that chose the side. | Analyzer owns entry filters; orchestrator can only reject for global risk, not market thesis. |
| src/strategy/signal_engine.py:1143 | SL/TP geometry lives in production signal computation, not in regime-specific contracts. | SignalContract must carry entry/stop/exit_rule/max_hold; no downstream component recomputes geometry. |
| src/strategy/signal_engine.py:1181 | max_hold is still derived from FAST/SWING style and night session, not regime personality. | max_hold comes from analyzer namespace; session logic is an explicit analyzer parameter or global risk veto. |
| src/strategy/signal_engine.py:1202 | Final entry_signal gate merges style, geometry, funding, OI, VWAP and RR into one boolean. | Separate analyzer rejection reasons from orchestrator risk rejections; never collapse owners into one flag. |
| scripts/ws/ws_main_screener.py:250 | 15m close handler computes main signal and stores last regime used by the 5m fade handler. | 5m fade cannot inherit a mutable last regime; it must route from the same immutable classifier snapshot. |
| scripts/ws/ws_main_screener.py:328 | Cooldown key uses symbol/regime/side but not analyzer ownership, while BB_FADE is special-cased. | One pair -> one analyzer lock until expiry/close; cooldown namespace includes analyzer_id. |
| config.yaml:73 | Global breakeven_trail can override analyzer-specific exit semantics. | Orchestrator may execute only follow rules embedded in SignalContract; global BE is disabled unless contract opts in. |
| src/data/snapshot_writer.py:63 | Snapshot stores generic entry/sl/tp fields, so different regime contracts lose exit-rule semantics. | Persist full signal contract, including exit_rule and follow_rule, with schema_version. |

## P2-2 Separation Skeleton

- Added research-only `scripts/analysis/research/regime_contract_skeleton_23_05_2026.py`.
- It keeps classifier ownership, analyzer ownership and position ownership separate.
- It stores exit/follow rules inside `SignalContract`, so the orchestrator can execute but not invent strategy.

## Hypotheses / Honesty

- Edge exists clearly in real tick impulse rows, but the sample is a different tape window than 04-14.05 and still needs forward confirmation.
- TRENDING_IMPULSE replay GO is credible because close->mid->open is monotonic and both sides are positive at mid, but exact tick validation for 04-10.05 is missing locally.
- DRIFT is not rescued: net is near zero and no n>=20 both-side subgroup appears.
- RANGING fade is plausible structurally, but current measured rows are too thin/negative for GO.
- All 10-day replay conclusions remain thin-sample research, not production config.

## Artifacts

- summary: `C:\Users\krivo\trading-bot-v2\scripts\analysis\research\output\main_rebuild_deep_summary_23_05_2026.json`
- plots: `C:\Users\krivo\trading-bot-v2\scripts\analysis\research\output\main_rebuild_deep_cases_23_05_2026`
- skeleton: `C:\Users\krivo\trading-bot-v2\scripts\analysis\research\regime_contract_skeleton_23_05_2026.py`
