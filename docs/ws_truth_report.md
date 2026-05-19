# WS Truth Report - Block 1

Scope: only `logs/signals/main_signals.jsonl` joined with `logs/signals/main_signals_labels.jsonl`.
Archive REST data is excluded from all metrics and summary statements.

- total labeled WS signals: 85
- decisive TP/SL signals: 60
- TIME/non-decisive signals: 25

R note: price-based R is used when valid. For rounded micro-price TP rows where price precision makes R non-positive, the fallback is `TP1=+0.5R`, `TP2=+1.0R`; `SL=-1R`.

Rule: `n < 5` means no conclusion; `5 <= n < 10` means preliminary, not actionable.

## Regime x Style

| regime | style | n | WR | avg_R | PF | note |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| DRIFT | FAST | 21 | 90.5% | 0.63 | 7.60 | usable |
| RANGING | FAST | 3 | 66.7% | 0.42 | 2.25 | N/A - no conclusion |
| TRENDING | FAST | 6 | 66.7% | 0.32 | 1.95 | preliminary, not actionable |
| TRENDING | SWING | 30 | 63.3% | 0.02 | 1.06 | usable |

## By Pair

| symbol | n | WR | avg_R | PF | note |
| --- | ---: | ---: | ---: | ---: | --- |
| ADA-USDT-SWAP | 2 | 100.0% | 1.00 | inf | N/A - no conclusion |
| AI-USDT-SWAP | 2 | 100.0% | 0.67 | inf | N/A - no conclusion |
| BABY-USDT-SWAP | 1 | 100.0% | 0.50 | inf | N/A - no conclusion |
| BASED-USDT-SWAP | 2 | 50.0% | -0.30 | 0.40 | N/A - no conclusion |
| BILL-USDT-SWAP | 0 | n/a | n/a | n/a | N/A - no conclusion |
| BOME-USDT-SWAP | 3 | 100.0% | 0.67 | inf | N/A - no conclusion |
| BTC-USDT-SWAP | 3 | 100.0% | 0.60 | inf | N/A - no conclusion |
| CHIP-USDT-SWAP | 0 | n/a | n/a | n/a | N/A - no conclusion |
| CHZ-USDT-SWAP | 2 | 50.0% | -0.25 | 0.50 | N/A - no conclusion |
| DOGE-USDT-SWAP | 2 | 50.0% | 0.24 | 1.49 | N/A - no conclusion |
| ETH-USDT-SWAP | 2 | 100.0% | 1.50 | inf | N/A - no conclusion |
| GALA-USDT-SWAP | 1 | 100.0% | 0.54 | inf | N/A - no conclusion |
| HMSTR-USDT-SWAP | 4 | 75.0% | 0.50 | 3.00 | N/A - no conclusion |
| JELLYJELLY-USDT-SWAP | 1 | 100.0% | 0.38 | inf | N/A - no conclusion |
| KAT-USDT-SWAP | 3 | 0.0% | -1.00 | 0.00 | N/A - no conclusion |
| LAYER-USDT-SWAP | 0 | n/a | n/a | n/a | N/A - no conclusion |
| LINEA-USDT-SWAP | 2 | 100.0% | 0.50 | inf | N/A - no conclusion |
| MEME-USDT-SWAP | 1 | 0.0% | -1.00 | 0.00 | N/A - no conclusion |
| MEW-USDT-SWAP | 6 | 50.0% | -0.10 | 0.80 | preliminary, not actionable |
| NEIRO-USDT-SWAP | 3 | 100.0% | 0.98 | inf | N/A - no conclusion |
| NOT-USDT-SWAP | 1 | 100.0% | 0.45 | inf | N/A - no conclusion |
| OFC-USDT-SWAP | 1 | 0.0% | -1.00 | 0.00 | N/A - no conclusion |
| PENGU-USDT-SWAP | 1 | 100.0% | 0.80 | inf | N/A - no conclusion |
| PEOPLE-USDT-SWAP | 1 | 100.0% | 0.75 | inf | N/A - no conclusion |
| PLUME-USDT-SWAP | 1 | 100.0% | 0.45 | inf | N/A - no conclusion |
| PUMP-USDT-SWAP | 2 | 0.0% | -1.00 | 0.00 | N/A - no conclusion |
| SAHARA-USDT-SWAP | 2 | 100.0% | 1.12 | inf | N/A - no conclusion |
| SOL-USDT-SWAP | 2 | 50.0% | -0.10 | 0.80 | N/A - no conclusion |
| TRUTH-USDT-SWAP | 3 | 66.7% | -0.11 | 0.66 | N/A - no conclusion |
| TURBO-USDT-SWAP | 2 | 100.0% | 0.30 | inf | N/A - no conclusion |
| UB-USDT-SWAP | 1 | 100.0% | 0.44 | inf | N/A - no conclusion |
| XRP-USDT-SWAP | 3 | 100.0% | 0.83 | inf | N/A - no conclusion |

## By UTC Hour

| hour_utc | n | WR | avg_R | PF | note |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 1 | 0.0% | -1.00 | 0.00 | N/A - no conclusion |
| 1 | 4 | 50.0% | -0.07 | 0.87 | N/A - no conclusion |
| 2 | 4 | 50.0% | -0.25 | 0.50 | N/A - no conclusion |
| 3 | 2 | 0.0% | -1.00 | 0.00 | N/A - no conclusion |
| 4 | 0 | n/a | n/a | n/a | N/A - no conclusion |
| 5 | 2 | 100.0% | 0.52 | inf | N/A - no conclusion |
| 6 | 7 | 85.7% | 0.43 | 3.99 | preliminary, not actionable |
| 7 | 4 | 100.0% | 0.69 | inf | N/A - no conclusion |
| 8 | 0 | n/a | n/a | n/a | N/A - no conclusion |
| 9 | 2 | 50.0% | -0.32 | 0.37 | N/A - no conclusion |
| 10 | 1 | 100.0% | 0.75 | inf | N/A - no conclusion |
| 11 | 1 | 0.0% | -1.00 | 0.00 | N/A - no conclusion |
| 12 | 2 | 100.0% | 1.12 | inf | N/A - no conclusion |
| 13 | 4 | 75.0% | 0.16 | 1.63 | N/A - no conclusion |
| 14 | 4 | 75.0% | 0.23 | 1.91 | N/A - no conclusion |
| 15 | 9 | 100.0% | 0.94 | inf | preliminary, not actionable |
| 16 | 4 | 75.0% | 0.47 | 2.87 | N/A - no conclusion |
| 17 | 3 | 100.0% | 0.67 | inf | N/A - no conclusion |
| 18 | 2 | 100.0% | 0.61 | inf | N/A - no conclusion |
| 19 | 2 | 50.0% | 0.00 | 1.00 | N/A - no conclusion |
| 20 | 1 | 0.0% | -1.00 | 0.00 | N/A - no conclusion |
| 22 | 0 | n/a | n/a | n/a | N/A - no conclusion |
| 23 | 1 | 0.0% | -1.00 | 0.00 | N/A - no conclusion |

