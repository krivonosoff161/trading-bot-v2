# AI_CONTEXT.md - Context For Remote Agents

Updated: 2026-06-10

This file is for remote coding/review agents that may not see local logs,
ignored data, `SESSION.md`, or runtime state.

## Project

`trading-bot-v2` is a Python research project around OKX market data and
news/event scanning.

Current active focus:

- `src/scout/` info-edge scanner;
- paper-only event journal;
- source/layer calibration;
- Telegram card quality;
- outcome measurement.

Not current focus:

- live auto-trading;
- real-money execution;
- rebuilding old WebSocket strategies;
- broad strategy tuning outside the scanner task.

## Current Architecture

Active scanner path:

```text
sources
  -> src/scout/news_buffer.py
  -> src/scout/router.py
  -> src/scout/dedup.py
  -> src/scout/agents/layer_agent.py
  -> src/scout/agents/orchestrator.py
  -> src/scout/agents/chief.py
  -> src/scout/scanner_journal.py
  -> src/scout/resolve_outcomes.py
```

Default operator command:

```bash
scanner.bat
```

Default scanner command:

```bash
python -u src\scout\scanner_v0.py --buffer --limit 5
```

The old WebSocket trading engines and strategy experiments remain in the repo,
but they are frozen/reference unless the user explicitly says otherwise.

## Scanner Layers

| Layer | Meaning |
|---|---|
| L1 | BTC/ETH majors and crypto regime |
| L2 | alts, memes, listings, token-risk, DEX/liquidity signals |
| L3 | metals |
| L4 | energy / oil / gas |
| L5 | equities, AI proxies, pre-IPO/proxy themes |

Current source families include RSS/Google News, OKX listings, SEC EDGAR,
DexScreener, GoPlus/RugCheck, token unlocks, BTC/ETH tactical market data, FRED,
EIA, OPEC, OilPrice and earnings calendar sources.

## Current Known Issues

Treat these as active context:

- The scanner currently produces too many `NO_GO` decisions.
- This is not automatically success. It may indicate over-strict filters or weak
  calibration.
- First calibration/hygiene pass is implemented: Telegram sends `GO` / `WATCH`
  chief cards by default, `NO_GO` stays in logs/training data unless
  `SCANNER_SEND_NO_GO=true`.
- Card text is now layer-aware and verdict-specific; watch fresh output for
  repeated phrasing.
- `resolve_outcomes.py --limit N` handles large mature queues.
- `calibration_report.py` explains missed `NO_GO` by source, layer, asset, phase,
  lead class and chief-called / low-confidence status.
- Macro/no-single-asset headlines need `MARKET_CONTEXT / WATCH_MARKET`, not forced
  trade verdicts.

## Remote-Agent Visibility

Remote agents usually see tracked files only. They may not see:

- `SESSION.md`;
- `.env`;
- current `logs/`;
- `data/scout/news_buffer.sqlite`;
- historical local caches;
- user/private Telegram/runtime artifacts.

Use [REMOTE_DATA_MANIFEST.md](REMOTE_DATA_MANIFEST.md) before concluding that a
local data file does not exist.

## Safe Commands To Suggest

Read-only / low-risk:

```bash
python -m pytest tests/test_scanner_router.py tests/test_scanner_runtime.py tests/test_scanner_records.py tests/test_source_quality_report.py -q
python -m src.scout.news_buffer stats
python -m src.scout.news_buffer ready --limit 5
python src/scout/resolve_outcomes.py --report
python src/scout/resolve_outcomes.py --limit 50
python src/scout/source_quality_report.py
python -X utf8 src/scout/calibration_report.py
python -m py_compile <file>
```

Do not ask remote users to run live/order scripts to compensate for missing local
data.

## Hard Boundaries

Do not:

- request `.env` values or secrets;
- set or suggest `AUTO_TRADE=true`;
- place orders;
- touch live order paths;
- rewrite historical logs without explicit instruction;
- assume ignored files are absent;
- make profitability claims from research/paper metrics.

## Useful Context Files

Always prefer these for current work:

- `CURRENT_STATE.md`
- `README.md`
- `ARCHITECTURE.md`
- `ROADMAP.md`
- `SCANNER_SPEC.md`
- `TASK.md`
- `docs/REMOTE_DATA_MANIFEST.md`
- `docs/scout_layer_source_plan_2026-06-07.md`

Historical files such as `PLAN.md`, `SERVICE_PIVOT.md`, and `PROJECT_VISION.md`
are preserved for audit trail, not as active direction.
