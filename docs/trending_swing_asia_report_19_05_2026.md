# TRENDING x SWING + Asia WS-Only Analysis

Scope: WS-only signals. Outcomes come from `logs/signals/main_signals_labels.jsonl`; trade metadata/R comes from `logs/signals/main_signals.jsonl`; full context comes from `logs/signals/signal_snapshot.jsonl` when present. Archive REST data is not used.

Session buckets are non-overlapping: Asia `00-05`, EU `06-12`, US `13-20`, Late `21-23` UTC.

## Coverage

| metric | value |
| --- | ---: |
| labels joined to WS signals | 85 |
| decisive TP/SL | 60 |
| joined ws_main snapshots | 59 |
| TRENDING x SWING decisive, all WS metadata | 30 |
| TRENDING x SWING decisive with snapshot context | 23 |

R note: SL is `-1R`; TP uses price-based R where valid, with fallback `TP1=+0.5R`, `TP2=+1.0R`.

## TRENDING x SWING R Decomposition

| scope | n | WR | avg_R | avg_TP_R | avg_SL_R |
| --- | ---: | ---: | ---: | ---: | ---: |
| all decisive metadata | 30 | 63.3% | 0.02 | 0.61 | -1.00 |
| snapshot context subset | 23 | 69.6% | 0.13 | 0.62 | -1.00 |

## TRENDING x SWING Splitters

| feature | with | n | WR | avg_R | without | n | WR | avg_R | WR gap | missing | recommendation |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| adx_4h_rising | true | 17 | 82.4% | 0.32 | false | 6 | 33.3% | -0.43 | 49.0% | 0 | filter candidate |
| bb_expanding | true | 20 | 75.0% | 0.20 | false | 3 | 33.3% | -0.36 | 41.7% | 0 | need more data |
| adx_4h 40-55 | 40<=adx<55 | 7 | 42.9% | -0.39 | other | 16 | 81.2% | 0.35 | -38.4% | 0 | filter candidate |
| session EU/US | EU/US | 18 | 77.8% | 0.25 | Asia | 5 | 40.0% | -0.31 | 37.8% | 0 | filter candidate |
| rsi_15m<60 | <60 | 19 | 63.2% | 0.05 | >=60 | 4 | 100.0% | 0.51 | -36.8% | 0 | need more data |
| adx_4h 25-40 | 25<=adx<40 | 10 | 80.0% | 0.41 | other | 13 | 61.5% | -0.09 | 18.5% | 0 | watch |
| bb_pct_b_15m<70 | <70 | 18 | 72.2% | 0.21 | >=70 | 5 | 60.0% | -0.16 | 12.2% | 0 | do not filter yet |
| day_position<0.7 | <0.7 | 9 | 66.7% | 0.01 | >=0.7 | 4 | 75.0% | 0.16 | -8.3% | 10 | need more data |
| daily_range_pct>5 | >5 | 9 | 66.7% | -0.01 | <=5 | 14 | 71.4% | 0.21 | -4.8% | 0 | do not filter yet |
| adx_4h 55+ | adx>=55 | 3 | 66.7% | -0.08 | other | 20 | 70.0% | 0.16 | -3.3% | 0 | need more data |
| abs(slope_1h)>=30 | abs>=30 | 23 | 69.6% | 0.13 | abs<30 | 0 | n/a | n/a | n/a | 0 | need more data |

## Top Combined Keep Conditions

| condition | n | WR | avg_R | avg_TP_R |
| --- | ---: | ---: | ---: | ---: |
| adx_4h_rising=true AND bb_pct_b_15m<70 | 13 | 84.6% | 0.41 | 0.66 |
| adx_4h_rising=true AND abs(slope_1h)>=30 AND bb_pct_b_15m<70 | 13 | 84.6% | 0.41 | 0.66 |
| adx_4h_rising=true AND bb_pct_b_15m<70 AND adx_4h<55 | 13 | 84.6% | 0.41 | 0.66 |
| bb_pct_b_15m<70 AND session=EU_US | 13 | 84.6% | 0.40 | 0.66 |
| bb_expanding=true AND bb_pct_b_15m<70 AND session=EU_US | 13 | 84.6% | 0.40 | 0.66 |

## Session x Regime x Style

| session | regime | style | n | WR | avg_R | TP | SL |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Asia | DRIFT | FAST | 4 | 75.0% | 0.12 | 3 | 1 |
| Asia | TRENDING | FAST | 3 | 33.3% | -0.40 | 1 | 2 |
| Asia | TRENDING | SWING | 6 | 33.3% | -0.42 | 2 | 4 |
| EU | DRIFT | FAST | 2 | 100.0% | 0.50 | 2 | 0 |
| EU | RANGING | FAST | 2 | 100.0% | 1.12 | 2 | 0 |
| EU | TRENDING | FAST | 3 | 100.0% | 1.03 | 3 | 0 |
| EU | TRENDING | SWING | 10 | 70.0% | 0.08 | 7 | 3 |
| Late | RANGING | FAST | 1 | 0.0% | -1.00 | 0 | 1 |
| US | DRIFT | FAST | 15 | 93.3% | 0.78 | 14 | 1 |
| US | TRENDING | SWING | 14 | 71.4% | 0.17 | 10 | 4 |

## Asia vs EU/US Snapshot Context

| feature | Asia n | Asia avg | EU/US n | EU/US avg | Asia-EU/US |
| --- | ---: | ---: | ---: | ---: | ---: |
| adx_4h | 15 | 32.84 | 42 | 36.90 | -4.06 |
| vol_ratio_sig | 15 | 2.11 | 42 | 2.12 | -0.01 |
| daily_range_pct | 15 | 1.92 | 42 | 7.65 | -5.73 |
| day_position | 8 | 0.65 | 33 | 0.49 | 0.16 |
| rsi_15m | 15 | 48.62 | 42 | 49.55 | -0.93 |
| bb_pct_b_15m | 15 | 41.76 | 42 | 48.92 | -7.16 |
| slope_1h | 15 | -7.89 | 42 | 1.63 | -9.53 |
| bb_expanding_share | 15 | 66.67% | 42 | 92.86% | -26.19% |

## Interaction: TRENDING x SWING In Asia

| bucket | n | WR | avg_R | TP | SL |
| --- | ---: | ---: | ---: | ---: | ---: |
| TRENDING x SWING Asia | 6 | 33.3% | -0.42 | 2 | 4 |
| TRENDING x SWING EU/US | 24 | 70.8% | 0.13 | 17 | 7 |

## Candidate Skip Filters

| filter | removed decisive | removed WR | kept n | kept WR | kept avg_R |
| --- | ---: | ---: | ---: | ---: | ---: |
| IF regime=TRENDING AND style=SWING AND adx_4h_rising=false -> skip | 6 | 33.3% | 24 | 70.8% | 0.13 |
| IF regime=TRENDING AND style=SWING AND 40<=adx_4h<55 -> skip | 7 | 42.9% | 23 | 69.6% | 0.15 |
| IF regime=TRENDING AND style=SWING AND bb_expanding=false -> skip | 3 | 33.3% | 27 | 66.7% | 0.06 |
| IF session=Asia AND regime=TRENDING -> skip | 9 | 33.3% | 51 | 80.4% | 0.41 |
| IF session=Asia AND regime=TRENDING AND style=SWING -> skip | 6 | 33.3% | 54 | 77.8% | 0.36 |
| IF session=Asia AND daily_range_pct<=5 -> skip | 11 | 54.5% | 49 | 77.6% | 0.37 |

## Caveats

- Context splitters use only the 59 joined `ws_main_screener` snapshots; missing context is not assumed random.
- Buckets with `n < 10` are preliminary; use them as guardrail hypotheses, not as proven production filters.
- `main_signals_labels.jsonl` currently has no `mfe_r`, `mae_r`, or `elapsed_m`; R is reconstructed from WS signal metadata and label exit price.
