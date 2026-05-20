# Network Context - 20.05.2026

Method: for every pair explosive 1m candle with pair sample `n >= 30`, join the same-minute `BTC-USDT-SWAP` and `SOL-USDT-SWAP` candle from tape.
Classification is `isolated` when the network candle moved less than `0.3%`; otherwise `aligned` if signs match and `opposite` if signs differ. WR uses close-entry 3m reversal return.

## Best Predictor Per Pair

| pair | events | best ctx | isolated WR/n | aligned WR/n | opposite WR/n | decision |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| AI-USDT-SWAP | 220 | BTC | 52.80%/214 | 83.33%/6 | n/a/0 | no filter |
| BASED-USDT-SWAP | 71 | BTC | 40.58%/69 | 100.00%/1 | n/a/0 | no filter |
| BILL-USDT-SWAP | 983 | BTC | 52.75%/982 | 100.00%/1 | n/a/0 | no filter |
| BSB-USDT-SWAP | 98 | BTC | 50.52%/97 | 100.00%/1 | n/a/0 | no filter |
| EDEN-USDT-SWAP | 438 | SOL | 54.73%/433 | 50.00%/4 | 100.00%/1 | no filter |
| JELLYJELLY-USDT-SWAP | 36 | BTC | 52.78%/36 | n/a/0 | n/a/0 | no filter |
| OFC-USDT-SWAP | 39 | SOL | 78.79%/33 | 50.00%/6 | n/a/0 | no filter |
| RLS-USDT-SWAP | 203 | SOL | 60.50%/200 | 100.00%/3 | n/a/0 | no filter |
| SAHARA-USDT-SWAP | 113 | SOL | 54.95%/111 | 50.00%/2 | n/a/0 | no filter |
| SPACE-USDT-SWAP | 125 | BTC | 58.68%/121 | 25.00%/4 | n/a/0 | no filter |
| TRUTH-USDT-SWAP | 692 | SOL | 52.92%/684 | 50.00%/4 | 50.00%/2 | no filter |
| UB-USDT-SWAP | 389 | BTC | 50.90%/387 | 100.00%/1 | 0.00%/1 | no filter |

## Full BTC/SOL Tables

### AI-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 214 | 52.80% | 0.08% | -0.12% |
| BTC | aligned | 6 | 83.33% | 0.32% | 0.12% |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 211 | 52.61% | 0.08% | -0.12% |
| SOL | aligned | 9 | 77.78% | 0.28% | 0.08% |
| SOL | opposite | 0 | n/a | n/a | n/a |

### BASED-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 69 | 40.58% | -0.29% | -0.49% |
| BTC | aligned | 1 | 100.00% | 0.97% | 0.77% |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 65 | 41.54% | -0.25% | -0.45% |
| SOL | aligned | 5 | 40.00% | -0.54% | -0.74% |
| SOL | opposite | 0 | n/a | n/a | n/a |

### BILL-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 982 | 52.75% | 0.04% | -0.16% |
| BTC | aligned | 1 | 100.00% | 0.53% | 0.33% |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 975 | 52.82% | 0.04% | -0.16% |
| SOL | aligned | 5 | 60.00% | -0.33% | -0.53% |
| SOL | opposite | 3 | 33.33% | -1.14% | -1.34% |

### BSB-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 97 | 50.52% | 0.01% | -0.19% |
| BTC | aligned | 1 | 100.00% | 0.67% | 0.47% |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 97 | 50.52% | 0.01% | -0.19% |
| SOL | aligned | 1 | 100.00% | 0.67% | 0.47% |
| SOL | opposite | 0 | n/a | n/a | n/a |

### EDEN-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 435 | 54.94% | 0.02% | -0.18% |
| BTC | aligned | 3 | 33.33% | 0.13% | -0.07% |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 433 | 54.73% | 0.01% | -0.19% |
| SOL | aligned | 4 | 50.00% | 0.26% | 0.06% |
| SOL | opposite | 1 | 100.00% | 0.36% | 0.16% |

### JELLYJELLY-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 36 | 52.78% | 0.05% | -0.15% |
| BTC | aligned | 0 | n/a | n/a | n/a |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 36 | 52.78% | 0.05% | -0.15% |
| SOL | aligned | 0 | n/a | n/a | n/a |
| SOL | opposite | 0 | n/a | n/a | n/a |

### OFC-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 36 | 75.00% | 0.28% | 0.08% |
| BTC | aligned | 3 | 66.67% | 0.59% | 0.39% |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 33 | 78.79% | 0.30% | 0.10% |
| SOL | aligned | 6 | 50.00% | 0.31% | 0.11% |
| SOL | opposite | 0 | n/a | n/a | n/a |

### RLS-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 203 | 61.08% | 0.29% | 0.09% |
| BTC | aligned | 0 | n/a | n/a | n/a |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 200 | 60.50% | 0.29% | 0.09% |
| SOL | aligned | 3 | 100.00% | 0.44% | 0.24% |
| SOL | opposite | 0 | n/a | n/a | n/a |

### SAHARA-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 113 | 54.87% | 0.04% | -0.16% |
| BTC | aligned | 0 | n/a | n/a | n/a |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 111 | 54.95% | 0.05% | -0.15% |
| SOL | aligned | 2 | 50.00% | -0.38% | -0.58% |
| SOL | opposite | 0 | n/a | n/a | n/a |

### SPACE-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 121 | 58.68% | 0.09% | -0.11% |
| BTC | aligned | 4 | 25.00% | -0.48% | -0.68% |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 121 | 58.68% | 0.09% | -0.11% |
| SOL | aligned | 4 | 25.00% | -0.48% | -0.68% |
| SOL | opposite | 0 | n/a | n/a | n/a |

### TRUTH-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 688 | 52.91% | 0.08% | -0.12% |
| BTC | aligned | 2 | 50.00% | 0.25% | 0.05% |
| BTC | opposite | 0 | n/a | n/a | n/a |
| SOL | isolated | 684 | 52.92% | 0.08% | -0.12% |
| SOL | aligned | 4 | 50.00% | 0.30% | 0.10% |
| SOL | opposite | 2 | 50.00% | -0.07% | -0.27% |

### UB-USDT-SWAP

| context | group | n | WR 3m | avg 3m | net 3m |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | isolated | 387 | 50.90% | -0.02% | -0.22% |
| BTC | aligned | 1 | 100.00% | 0.43% | 0.23% |
| BTC | opposite | 1 | 0.00% | -2.54% | -2.74% |
| SOL | isolated | 384 | 50.78% | -0.03% | -0.23% |
| SOL | aligned | 2 | 100.00% | 0.54% | 0.34% |
| SOL | opposite | 3 | 33.33% | 0.27% | 0.07% |

## Conclusion

Use the `decision` column only when the group has enough observations; small aligned/opposite buckets should not drive production filters.
If a pair shows lower aligned WR with both BTC and SOL, the next paper run should exclude aligned network impulses for that pair; otherwise keep network filtering off.
