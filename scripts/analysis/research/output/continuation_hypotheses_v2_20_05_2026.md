# GPT Hypotheses V2 - 20.05.2026

Focus: whether wider impulse stop and looser exits fix the round-1 tight-trail problem.

## Exit Mode Families

| mode | rows | best net | avg net | best portfolio capture | avg stopped_before_mfe |
| --- | ---: | ---: | ---: | ---: | ---: |
| giveback_30 | 75 | -0.14% | -0.32% | 8.79% | 32.19% |
| giveback_40 | 75 | -0.10% | -0.31% | 12.01% | 32.19% |
| giveback_50 | 75 | -0.05% | -0.32% | 13.71% | 32.19% |
| round1_tight_trail | 75 | -0.44% | -0.58% | -42.11% | 77.46% |
| structure_k1 | 75 | 0.45% | -0.37% | 20.22% | 18.13% |
| structure_k2 | 75 | 0.36% | -0.38% | 15.95% | 24.52% |
| structure_k3 | 75 | 0.23% | -0.40% | 12.30% | 27.88% |

## Impulse Stop Buffer

| buffer | rows | best net | avg net | best portfolio capture | avg stopped_before_mfe |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.00% | 175 | 0.31% | -0.38% | 16.85% | 37.29% |
| 0.10% | 175 | 0.45% | -0.38% | 20.22% | 34.73% |
| 0.20% | 175 | 0.44% | -0.39% | 19.88% | 32.78% |

## Interpretation

- `stopped_before_mfe_rate` tests the case-13 diagnosis directly: if it falls but net stays negative, the wider stop fixed shakeout but increased loss size or still failed capture.
- Portfolio capture (`avg_gross / avg_MFE`) is the core metric for tail capture. Positive MFE without capture is not tradable edge.
- Cluster rows in the main report show whether the 2+ explosions regime is actually executable, not just visually attractive.
