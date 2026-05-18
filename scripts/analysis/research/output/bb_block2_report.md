# Block 2 BB Fade Analysis

## Archive Logged Sample
- Logged archive bb_fade sample: decisive_n=43, WR=60.47%, avg_R=0.30.
- Local tape validation for archive fade sample is unavailable: 0/47 signals have matching local tick files on disk.

### Archive by Regime
| Bucket | n | WR | avg |
| --- | ---: | ---: | ---: |
| DRIFT | 18 | 55.56% | 0.11 |
| RANGING | 24 | 62.50% | 0.37 |
| TRENDING | 1 | 100.00% | 2.28 |

### Archive by Symbol (n>=3)
| Bucket | n | WR | avg |
| --- | ---: | ---: | ---: |
| BTC-USDT | 5 | 60.00% | 0.06 |
| XRP-USDT | 8 | 50.00% | 0.22 |
| DOGE-USDT | 19 | 57.89% | 0.25 |
| ETH-USDT | 11 | 72.73% | 0.56 |

## Current Wick-Rejection Backtest
- Backtest over cached universe: total=657, decisive_n=344, WR=70.64%, avg_net=0.48%.
- Architecture differs materially from old 5m fade hint: new worker requires 15m band touch, 5m wick rejection, RR>=0.5, no Asia, and 1H trend veto.

### Backtest by BB Width
| Bucket | n | WR | avg |
| --- | ---: | ---: | ---: |
| 2-3% | 206 | 67.96% | 0.13 |
| 3-5% | 92 | 68.48% | 0.24 |
| 5%+ | 46 | 86.96% | 2.53 |

### Worst Symbols in Backtest (n>=5)
| Bucket | n | WR | avg |
| --- | ---: | ---: | ---: |
| SATS-USDT-SWAP | 9 | 33.33% | -0.60 |
| BONK-USDT-SWAP | 5 | 60.00% | -0.19 |
| PENGU-USDT-SWAP | 19 | 57.89% | -0.08 |
| GALA-USDT-SWAP | 12 | 66.67% | 0.15 |
| NEIRO-USDT-SWAP | 20 | 60.00% | 0.17 |
| NOT-USDT-SWAP | 17 | 70.59% | 0.18 |
| BOME-USDT-SWAP | 15 | 73.33% | 0.22 |
| TURBO-USDT-SWAP | 8 | 75.00% | 0.23 |

## Live Worker Check
- Live ws_bb_fade sample: all_n=3, decisive_n=2, WR=50.00%, net=-5.26%.
- 2026-05-16T16:30:01Z TRUTH-USDT-SWAP buy outcome=SL net=-5.87% bw_pct=7.92% vol_ratio=1.44 rsi=43.50
- 2026-05-17T12:40:01Z CHZ-USDT-SWAP sell outcome=TIME net=-0.68% bw_pct=3.66% vol_ratio=0.74 rsi=59.80
- 2026-05-17T14:15:00Z OFC-USDT-SWAP buy outcome=TP net=1.28% bw_pct=2.92% vol_ratio=0.88 rsi=42.50

## Verdict
- Archive logged FADE sample is positive but small and concentrated in majors; it is not enough to tune per-pair production overrides.
- Current wick-rejection logic backtests well on cache data, especially outside Asia and on wider bands rather than narrow squeezes.
- Live worker has only 3 trades. The TRUTH loss is a wide-band outlier (`bw_pct=7.92%`), but sample is too small to justify a new max-width cap yet.
- Keep Block 2 as preliminary: leave production BB Fade config unchanged until live sample reaches at least 20 decisive trades.
