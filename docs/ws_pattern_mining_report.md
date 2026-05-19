# WS Pattern Mining Report

Scope: `signal_snapshot.jsonl` joined with `main_signals_labels.jsonl` by `signal_id`.
Only matching `source=ws_main_screener` snapshots are used. This is partial coverage, not the full 85-signal truth set.

- snapshot rows joined to labels: 59
- decisive TP/SL snapshot rows: 43

## High-WR Buckets

| regime | style | adx_4h_bucket | vol_ratio_bucket | hour_bucket | n | WR | avg_R |
| --- | --- | --- | --- | --- | ---: | ---: | ---: |
| TRENDING | FAST | 40+ | 1.2-2 | asia_00_06 | 3 | 100.0% | 0.80 |
| TRENDING | SWING | 25-40 | 1.2-2 | asia_00_06 | 4 | 75.0% | 0.37 |

## Low-WR Buckets

| regime | style | adx_4h_bucket | vol_ratio_bucket | hour_bucket | n | WR | avg_R |
| --- | --- | --- | --- | --- | ---: | ---: | ---: |
| none | - | - | - | - | 0 | n/a | n/a |

## Single-Feature Separation

| Feature | yes_n | yes_WR | yes_avg_R | no_n | no_WR | no_avg_R | WR gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bb_expanding = true | 35 | 77.1% | 0.29 | 8 | 50.0% | 0.00 | 27.14pp |
| abs(slope_1h) >= 30 | 39 | 74.4% | 0.25 | 4 | 50.0% | 0.07 | 24.36pp |
| EU/US session | 23 | 82.6% | 0.45 | 20 | 60.0% | -0.01 | 22.61pp |
| adx_4h >= 40 | 17 | 58.8% | -0.00 | 26 | 80.8% | 0.39 | -21.95pp |
| vol_ratio_sig >= 2 | 13 | 69.2% | 0.17 | 30 | 73.3% | 0.26 | -4.10pp |
| obi_top5 >= 0.5 | 0 | n/a | n/a | 43 | 72.1% | 0.23 | n/a |

## Candidate Gate Backtest On Snapshot-Covered Rows

| Gate | decisive cut | kept_n | kept_WR | kept_avg_R | note |
| --- | ---: | ---: | ---: | ---: | --- |
| EU/US session | 20 | 23 | 82.6% | 0.45 | usable |
| bb_expanding = true | 8 | 35 | 77.1% | 0.29 | usable |
| abs(slope_1h) >= 30 | 4 | 39 | 74.4% | 0.25 | usable |
| vol_ratio_sig >= 2 | 30 | 13 | 69.2% | 0.17 | usable |
| adx_4h >= 40 | 26 | 17 | 58.8% | -0.00 | usable |

## Filtering Concept

Add a non-invasive context gate after `compute_signal()` returns `ENTRY` and before the signal is logged/sent.
The gate should read only fields already present in `SignalResult.context`, `SignalResult.engine_vars`, and `SignalResult.microstructure`.

Implementation sketch:

1. Keep current candle-rule engine unchanged.
2. Add `strategy.context_gate.enabled` and per-bucket thresholds in `config.yaml`.
3. In `ws_main_screener.py`, after `result.entry_signal == "ENTRY"`, call a small `context_gate_allows(result)` helper.
4. If rejected, log to `signal_log_notrade.jsonl` with `drop_reason=context_gate:<reason>` and do not write `main_signals.jsonl`.
5. Re-run this script after every new label batch; do not promote any gate with `kept_n < 10`.

Do not hard-code the current snapshot-mined gate yet. The raw snapshot file has 61 rows, but only 59 are matching `ws_main_screener` rows and only 43 are decisive TP/SL rows, so it is hypothesis-grade only.
