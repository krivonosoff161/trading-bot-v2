# Block 3 Pump Analysis

- Current baseline 2026-05-16..2026-05-18: n=121, WR=32.2%, net=-19.98%, avg=-0.17%
- Expanded sample 2026-05-03..2026-05-18: n=757, WR=38.3%, net=+23.19%, avg=+0.03%
- Live MFE/MAE coverage: current_window=121/121 from labels; full_live direct_or_log=221/523 direct labels plus log_backfill=151, ambiguous=0, unmatched=151, best_log_start=2026-05-09.
- Archive 2026-05-03..2026-05-09 does not have local orchestrator MFE log, so breakeven reinterpretation is only reliable on the live subset.

## Pair Drag: Current
| Pair | n | WR | net | avg |
| --- | ---: | ---: | ---: | ---: |
| APR-USDT-SWAP | 5 | 40.0% | -6.90% | -1.38% |
| BSB-USDT-SWAP | 9 | 22.2% | -6.19% | -0.69% |
| BABY-USDT-SWAP | 5 | 0.0% | -5.17% | -1.03% |
| RIVER-USDT-SWAP | 3 | 0.0% | -4.23% | -1.41% |
| LAB-USDT-SWAP | 3 | 0.0% | -3.47% | -1.16% |
| BILL-USDT-SWAP | 15 | 33.3% | -2.89% | -0.19% |
| UB-USDT-SWAP | 4 | 25.0% | -1.63% | -0.41% |
| BEAT-USDT-SWAP | 5 | 40.0% | -1.39% | -0.28% |
| SPACE-USDT-SWAP | 1 | 0.0% | -1.35% | -1.35% |
| HUMA-USDT-SWAP | 1 | 0.0% | -1.29% | -1.29% |

## Pair Drag: Expanded
| Pair | n | WR | net | avg |
| --- | ---: | ---: | ---: | ---: |
| UB-USDT-SWAP | 23 | 26.1% | -10.86% | -0.47% |
| BSB-USDT-SWAP | 27 | 25.9% | -10.83% | -0.40% |
| RLS-USDT-SWAP | 11 | 18.2% | -7.75% | -0.70% |
| APR-USDT-SWAP | 8 | 37.5% | -7.73% | -0.97% |
| LAYER-USDT-SWAP | 23 | 34.8% | -7.07% | -0.31% |
| BABY-USDT-SWAP | 9 | 11.1% | -6.79% | -0.75% |
| RIVER-USDT-SWAP | 5 | 0.0% | -6.18% | -1.24% |
| ICP-USDT-SWAP | 5 | 0.0% | -5.68% | -1.14% |
| WAL-USDT-SWAP | 3 | 33.3% | -5.15% | -1.72% |
| ONDO-USDT-SWAP | 4 | 0.0% | -4.51% | -1.13% |

## Breakeven Candidate Slice (live SL only)
- mfe>=1.0%: n=10, WR=0.0%, net=-21.36%, avg=-2.14%
- mfe>=0.8%: n=16, WR=0.0%, net=-27.40%, avg=-1.71%

## Tape Coverage on Current Baseline
| Pair | current_n | usable_tape | file_present_no_window | note |
| --- | ---: | ---: | ---: | --- |
| APR-USDT-SWAP | 5 | 0 | 0 | no tape files on disk |
| RIVER-USDT-SWAP | 3 | 0 | 0 | no tape files on disk |
| LAB-USDT-SWAP | 3 | 0 | 0 | no tape files on disk |
| BABY-USDT-SWAP | 5 | 2 | 1 | file exists but day coverage incomplete |
| BSB-USDT-SWAP | 9 | 2 | 7 | file exists but day coverage incomplete |
| BILL-USDT-SWAP | 15 | 14 | 1 | file exists but day coverage incomplete |

## BABY Tape Slice
- Rule tested: `pre_buy_ratio<0.50 && pre_cvd<0 && post_buy_ratio<0.40 && post_cvd<0`
- Covered BABY trades with usable tape: 2
- 2026-05-16 07:20 SL net=-0.98% pre_buy=0.463 pre_cvd=-92479 post_buy=0.246 post_cvd=-134114 -> VETO
- 2026-05-16 07:30 SL net=-0.68% pre_buy=0.494 pre_cvd=-4451 post_buy=0.342 post_cvd=-49581 -> VETO
- 2026-05-17 23:35 SL file exists but no ticks in entry window (day coverage gap; file ended early)

## Sim0-Sim9 on Current Baseline
- Sim0 current baseline: n=121, WR=32.2%, net=-19.98%, avg=-0.17% | delta_vs_base=+0.00pp
- Sim1 BABY off: n=116, WR=33.6%, net=-14.81%, avg=-0.13% | delta_vs_base=+5.17pp
- Sim2 BABY+RIVER off: n=113, WR=34.5%, net=-10.58%, avg=-0.09% | delta_vs_base=+9.41pp
- Sim3 APR half: n=121, WR=32.2%, net=-16.53%, avg=-0.14% | delta_vs_base=+3.45pp
- Sim4 BSB half: n=121, WR=32.2%, net=-16.89%, avg=-0.14% | delta_vs_base=+3.10pp
- Sim5 BILL cap2 ban2: n=112, WR=32.1%, net=-18.05%, avg=-0.16% | delta_vs_base=+1.94pp
- Sim6 all overrides: n=104, WR=34.6%, net=-2.09%, avg=-0.02% | delta_vs_base=+17.89pp
- Sim7 ban_after_sl_streak=2 all pairs: n=110, WR=32.7%, net=-15.24%, avg=-0.14% | delta_vs_base=+4.75pp
- Sim8 hard blocks APR/RIVER/LAB + BABY tape veto: n=108, WR=34.3%, net=-3.71%, avg=-0.03% | delta_vs_base=+16.27pp
- Sim9 deployable hybrid blocks + BSB half + BILL cap2: n=96, WR=35.4%, net=+4.83%, avg=+0.05% | delta_vs_base=+24.81pp
