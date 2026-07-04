# Microstructure Lane (Theme 40) — canonical reference

A SEPARATE research-only lane for orderbook pressure / liquidity walls / tape flow. It is NOT an OHLCV
strategy family, NOT part of `farm_loop`, and never promotes anything. Public market data only.

## Why it can't be judged on candles

A 15m/4h candle hides the order book and the tape. Walls (resting liquidity), top-N imbalance, spoof
cancels, and aggressive-flow follow-through live at the second/sub-second scale. The OHLCV farm and its
n>=10 statistical validator are the wrong instrument for these one-shot/tactical, microstructure
questions — hence a separate lane with its own data, features, replay, and outcome buckets.

## Two sub-lanes (honest split)

| sub-lane | data | status |
|---|---|---|
| **Tape pressure** (CVD / aggressive flow / speed) | REAL: 632 tape files, 55 symbols, 20 days, 115.5M ticks (`E:\trading-data\ticks`, trades-only) | replayed on real data → **weak_followthrough** (no edge) |
| **Orderbook walls** (imbalance / wall notional & distance / persistence / movement / spoof) | NONE historically → forward recorder collects it now | recorder works (keyless public), events detected; **needs more data** before any replay |

## Modules & one-command runs

| concern | module | command |
|---|---|---|
| features (pure, no-look-ahead) | `micro_features.py` | (imported) |
| tape replay (real data) | `micro_tape_replay.py` | `python -m src.research_lab.micro_tape_replay --max-files 18 --max-events-per-file 50 --symbols BTC-USDT-SWAP,ETH-USDT-SWAP,... --snapshot` |
| orderbook recorder (keyless public) | `micro_recorder.py` | `python -m src.research_lab.micro_recorder --symbols BTC-USDT-SWAP,ETH-USDT-SWAP --duration-seconds 600 --interval-seconds 2` |
| recorder readiness | `micro_recorder.py` | `python -m src.research_lab.micro_recorder --status` |
| events + outcome memory | `micro_memory.py` | `python -m src.research_lab.micro_memory --snapshot` |
| stop | `stop_intent` | `python -m src.research_lab.stop_intent` (recorder checks it each poll) |
| status line | farm status | `python scripts/strategy_lab/farm_status_report.py` |

## Findings to date (real data)

Tape-pressure replay, 900 events across BTC/ETH/DOGE + 3 memes: median net = **-0.10pp at every horizon
(10s–3m)** → gross follow-through ≈ 0; win 4–18%. **Tape-pressure has no mechanical follow-through.**
Orderbook walls: recorder collects real 50-level books keyless; events detected; not enough data yet to
replay follow-through.

## Outcome buckets (rejected = knowledge, not trash)

`followthrough_observed · weak_followthrough · valid_pressure_but_bad_exit · known_bad_wall ·
spread_too_wide · fake_wall_cancel · late_entry · needs_more_samples`. None is edge or paper-ready.

## Allowed vs forbidden claims

ALLOWED: "data collected", "events detected", "mechanical follow-through observed/not observed",
"needs more data", "candidate for forward collection", "known bad pattern".
FORBIDDEN: "edge found", "profitable", "ready to trade", anything paper-ready/live.

## Boundary

Keyless public endpoints only (`/api/v5/market/books`, `/market/trades`); the authed `okx_client` is
NOT imported. No `.env` / AUTO_TRADE / orders / private endpoints / Telegram. Bounded collector
(duration + top-N + stop-file + rotation + retention). Writes only under the private research root.
