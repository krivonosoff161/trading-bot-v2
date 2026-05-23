# Main Rebuild Replay - 23_05_2026

Replay `2026-05-04T19:00:00Z` -> `2026-05-14T19:00:00Z`. Same direction/regime/peak-guard/universe/fee/slippage as the exit re-run. New axis: entry_mode (close=current late entry, mid, open=early bound). Stop is structural, widened for SWING. Acceptance = NET>0 after fee, both sides, n>=20.

## Best Exit Per Cell x Entry Mode

| cell | entry | best exit | filled | OLD net | NEW net | delta | WR | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DRIFT | close | giveback_30 | 25 | -0.59% | -0.39% | 0.19% | 12.00% | NO-GO: net<=0 |
| DRIFT | mid | scaled_tp_75 | 25 | -0.59% | -0.08% | 0.51% | 68.00% | NO-GO: net<=0 |
| DRIFT | open | scaled_tp_75 | 25 | -0.59% | 0.07% | 0.66% | 84.00% | NO-GO: side split fails |
| RANGING | close | structure_k2 | 29 | -0.11% | 0.37% | 0.47% | 55.17% | NO-GO: side split fails |
| RANGING | mid | structure_k3 | 29 | -0.11% | -0.01% | 0.09% | 31.03% | NO-GO: net<=0 |
| RANGING | open | scaled_tp_100 | 29 | -0.11% | -0.09% | 0.01% | 79.31% | NO-GO: net<=0 |
| TRENDING_IMPULSE | close | structure_k1 | 55 | -0.04% | 0.01% | 0.05% | 45.45% | NO-GO: side split fails |
| TRENDING_IMPULSE | mid | scaled_tp_100 | 55 | -0.04% | 0.48% | 0.52% | 78.18% | GO |
| TRENDING_IMPULSE | open | structure_k1 | 55 | -0.04% | 0.88% | 0.92% | 49.09% | GO |

## Entry-Timing Effect (exit fixed = structure_k2)

| cell | entry | filled | new net | delta | entry lag | capture |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DRIFT | close | 25 | -0.58% | 0.01% | 0.30% | 12.24% |
| DRIFT | mid | 25 | -0.43% | 0.16% | 0.30% | 11.01% |
| DRIFT | open | 25 | -0.27% | 0.31% | 0.30% | 12.59% |
| RANGING | close | 29 | 0.37% | 0.47% | -0.26% | 34.36% |
| RANGING | mid | 29 | -0.09% | 0.01% | -0.26% | 15.52% |
| RANGING | open | 29 | -0.22% | -0.12% | -0.26% | 18.78% |
| TRENDING_IMPULSE | close | 55 | -0.01% | 0.03% | 0.87% | 26.78% |
| TRENDING_IMPULSE | mid | 55 | 0.43% | 0.47% | 0.87% | 25.85% |
| TRENDING_IMPULSE | open | 55 | 0.86% | 0.90% | 0.87% | 28.64% |

## Side Split For Best (Cell x Entry)

| cell | entry | exit | side | filled | new net |
| --- | ---: | ---: | ---: | ---: | ---: |
| DRIFT | close | giveback_30 | long | 13 | -0.33% |
| DRIFT | close | giveback_30 | short | 12 | -0.46% |
| DRIFT | mid | scaled_tp_75 | long | 13 | -0.01% |
| DRIFT | mid | scaled_tp_75 | short | 12 | -0.15% |
| DRIFT | open | scaled_tp_75 | long | 13 | 0.12% |
| DRIFT | open | scaled_tp_75 | short | 12 | 0.02% |
| RANGING | close | structure_k2 | long | 11 | 0.65% |
| RANGING | close | structure_k2 | short | 18 | 0.19% |
| RANGING | mid | structure_k3 | long | 11 | 0.21% |
| RANGING | mid | structure_k3 | short | 18 | -0.15% |
| RANGING | open | scaled_tp_100 | long | 11 | -0.03% |
| RANGING | open | scaled_tp_100 | short | 18 | -0.13% |
| TRENDING_IMPULSE | close | structure_k1 | long | 23 | -0.67% |
| TRENDING_IMPULSE | close | structure_k1 | short | 32 | 0.50% |
| TRENDING_IMPULSE | mid | scaled_tp_100 | long | 23 | 0.56% |
| TRENDING_IMPULSE | mid | scaled_tp_100 | short | 32 | 0.41% |
| TRENDING_IMPULSE | open | structure_k1 | long | 23 | 0.37% |
| TRENDING_IMPULSE | open | structure_k1 | short | 32 | 1.25% |

## Read

- If NEW net turns positive only as entry_mode moves close->mid->open, the binding constraint is entry timing, and a tick early-entry layer is the unlock.
- `open` overstates the gain (enters at impulse-bar open); the live tick layer lands between mid and open. Treat `mid` as the conservative read.
- A cell that stays NO-GO even at `open` is not an entry problem - it is direction or no-edge, do not chase it with a faster entry.
