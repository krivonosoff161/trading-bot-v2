# Trading Bot V2

Research project for market-data and news-driven trading infrastructure around OKX
crypto futures. The current active work is not live auto-trading. It is an
`info-edge scanner`: a paper-only event pipeline that collects market/news events,
routes them by asset and layer, records decisions, and later measures outcomes.

> **Status:** research / paper / demo only. No profitability is claimed. This is
> not financial advice, not a signal service, and not a promise of future returns.
> Real-money execution is outside the current work.

## Current Direction

The project has two separate contours:

| Contour | Status | Purpose |
|---|---|---|
| `src/scout/` info-edge scanner | active | News/event intake, layer routing, agent review, Telegram cards, forward outcome journal |
| Old WebSocket trading engines | frozen/reference | Historical paper/demo strategies and reusable utilities; not the current development focus |

The active scanner is being calibrated. It already writes a useful dataset, and
the first hygiene pass is in place: `NO_GO` stays in logs by default, while only
`GO` / `WATCH` chief cards are sent to Telegram unless `SCANNER_SEND_NO_GO=true`
is explicitly enabled. The current bridge writes `WATCH` / `GO` rows into a
paper-only watch queue for later technical confirmation. Main/TA is confirmation
and risk context only, not the source of trade intent.

## What Exists Today

### Info-edge scanner

Current runtime:

```text
sources
  -> SQLite news buffer
  -> asset/layer router
  -> materiality, temporal phase, dedup
  -> cheap layer agent
  -> rule orchestrator
  -> chief model for selected candidates
  -> Telegram card + JSONL records
  -> outcome resolver
```

Implemented pieces:

- Five scanner layers:
  - `L1`: crypto majors / BTC-ETH regime
  - `L2`: alts, memes, listings, token-risk and DEX signals
  - `L3`: metals
  - `L4`: energy / oil / gas
  - `L5`: equities, AI proxies and pre-IPO/proxy themes
- Durable SQLite intake buffer: `data/scout/news_buffer.sqlite`.
- Runtime logs under `logs/scout/`: journal, drops, ingest, routing audit, LLM
  budget, reasoning/events, outcomes and training/memory records.
- Source families include RSS, Google News, OKX listings, SEC EDGAR, DexScreener,
  GoPlus/RugCheck, FRED, EIA, OPEC, OilPrice and earnings calendar sources.
- LLM routing through `src/utils/llm_client.py` with Yandex fallback and Alibaba
  / Qwen roles configured through `.env`.
- Outcome resolver for both long and short sides with MFE/MAE and per-layer
  baseline/excess measurement; use `--limit N` for large backlogs.
- Source-quality reporting and routing audit records for calibration work.
- `src/scout/calibration_report.py` for missed-`NO_GO` analysis by source, layer,
  asset, phase, lead class, chief-called and low-confidence status.
- `logs/scout/watch_queue.jsonl` for `WATCH` / `GO` rows that need later
  technical confirmation.
- `src/strategy/setup_confirmation.py` for paper-only confirmation statuses:
  `WATCH_CONTINUE`, `SETUP_FORMING`, `TRADE_PLAN_READY`, `INVALIDATED`,
  `EXPIRED`, `NEEDS_DATA`.

### Frozen trading contour

The older WebSocket strategies and paper engines remain in the repository because
they provide reusable infrastructure and historical research:

- OKX clients and WebSocket data utilities
- indicators and chart rendering
- Telegram delivery helpers
- historical paper/research scripts
- old main/pump/BB fade experiments

They should be treated as reference or archive unless a task explicitly says
otherwise.

## Current Problems Being Worked

- **Over-conservative scanner:** too many `NO_GO` outcomes; filters and prompts need
  measured calibration by source, layer, asset, phase and lead class. The first
  chief-prompt fix is implemented, but it needs forward observation.
- **Telegram noise:** first pass implemented: `GO` / `WATCH` only by default;
  `NO_GO` remains in logs and can be re-enabled with `SCANNER_SEND_NO_GO=true`.
- **Generic card text:** first pass implemented: cards are layer-aware and
  verdict-specific; watch live output for repeated wording.
- **Outcome resolver throughput:** first pass implemented: use
  `python src/scout/resolve_outcomes.py --limit 50` for bounded runs.
- **Market context:** macro/regulation/geopolitical events without one clean asset
  need a `MARKET_CONTEXT / WATCH_MARKET` path, not forced trade verdicts.

## How To Run

Create `.env` from the example and fill only the keys you actually need:

```bash
pip install -r requirements.txt
cp .env.example .env
```

Run the scanner loop on Windows:

```bash
scanner.bat
```

Equivalent scanner command:

```bash
python -u src\scout\scanner_v0.py --buffer --limit 5
```

Smoke test the SQLite buffer without LLM/Telegram:

```bash
python -m src.scout.news_buffer init
python -u src\scout\scanner_v0.py --buffer --limit 0
python -m src.scout.news_buffer stats
```

Useful scanner commands:

```bash
python -m src.scout.news_buffer ready --limit 5
python -m src.scout.news_buffer show <doc_id>
python -m src.scout.news_buffer resolve --limit 50
python -m src.scout.news_buffer normalize --limit 100
python src/scout/resolve_outcomes.py --report
python src/scout/resolve_outcomes.py --limit 50
python src/scout/source_quality_report.py
python src/scout/chief_usage_report.py
python src/scout/llm_health_report.py --day 2026-06-11
python scripts/analysis/source_onboarding_report.py
python scripts/analysis/build_watch_queue.py --dry-run
python -X utf8 src/scout/calibration_report.py
```

LLM budget controls are opt-in. To hard-stop paid model calls after local caps,
set `LLM_STOP_ON_BUDGET=true` and tune `LLM_DAILY_RUB_CAP`,
`LLM_SCAN_RUB_CAP`, `LLM_MAX_TOKENS_PER_SCAN`, and
`LLM_MAX_CHIEF_PER_SCAN` in `.env`. Budget skips are logged as model usage with
`status=budget_skipped`; chief skips are journaled as `CHIEF_BUDGET_SKIPPED`
and are not sent to Telegram.

Private strategy-lab smoke run:

```bash
python scripts/strategy_lab/run_experiment.py --spec configs/strategy_lab/l2_smoke.json
```

For continuous local research, run `bat\strategy_lab_loop.bat`. It writes
results to the private `trading-bot-research` workspace by default; public code
shows the method, not the private candidate tables.

Local read-only dashboard:

```bash
bat\strategy_lab_dashboard.bat
```

Open `http://127.0.0.1:8765`. The dashboard is read-only, localhost-only, and
does not expose `.env` values or live-trading controls.

State DB and queue:

```bash
bat\strategy_lab_sync_db.bat
bat\strategy_lab_enqueue_smoke.bat
bat\strategy_lab_worker_once.bat
```

For unattended local research, run `bat\strategy_lab_worker_loop.bat` after
queueing allowed experiment specs. The worker processes one queued job at a
time and writes full results to the private research workspace.

Focused scanner tests:

```bash
python -m pytest tests/test_scanner_router.py tests/test_scanner_runtime.py tests/test_scanner_records.py tests/test_source_quality_report.py tests/test_calibration_report.py tests/test_resolve_outcomes.py -q
```

## Repository Map

```text
trading-bot-v2/
├── README.md
├── CURRENT_STATE.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── SCANNER_SPEC.md
├── TASK.md
├── config.yaml
├── scanner.bat
├── src/
│   ├── scout/       # active info-edge scanner
│   ├── utils/       # Telegram, logging, LLM clients
│   ├── exchange/    # OKX clients and instrument metadata
│   ├── strategy/    # indicators, signal helpers, chart rendering
│   └── data/        # historical/frozen paper engines and data utilities
├── scripts/         # operator/research scripts
├── tests/
└── docs/            # research reports, protocols and archives
```

## Documentation

Read these first:

- [CURRENT_STATE.md](CURRENT_STATE.md) - short operational status.
- [ARCHITECTURE.md](ARCHITECTURE.md) - current project boundaries.
- [ROADMAP.md](ROADMAP.md) - current development sequence.
- [SCANNER_SPEC.md](SCANNER_SPEC.md) - scanner design and as-built notes.
- [docs/scanner_llm_operations_2026-06-12.md](docs/scanner_llm_operations_2026-06-12.md) - LLM budget, scanner/main/strategy-lab operating plan.
- [docs/scanner_source_onboarding_2026-06-11.md](docs/scanner_source_onboarding_2026-06-11.md) - current one-source-per-layer experiment.
- [docs/scanner_ta_confirmation_contract.md](docs/scanner_ta_confirmation_contract.md) - scanner-to-TA bridge contract.
- [docs/main_research_verdict_index.md](docs/main_research_verdict_index.md) - why old Main/TA is confirmation-only.
- [TASK.md](TASK.md) - local handoff between agents.
- [docs/AI_CONTEXT.md](docs/AI_CONTEXT.md) - context for remote coding agents.
- [docs/REMOTE_DATA_MANIFEST.md](docs/REMOTE_DATA_MANIFEST.md) - ignored local data map.

Legacy/historical documents are kept for audit trail, not as current direction:

- [PLAN.md](PLAN.md)
- [SERVICE_PIVOT.md](SERVICE_PIVOT.md)
- [PROJECT_VISION.md](PROJECT_VISION.md)

## Methodology

The project is developed by one operator with AI-assisted review. Research claims
are treated as provisional until checked against logs, costs, fills and forward
outcomes. Failed directions are kept as postmortems instead of being quietly
rebranded as success.

Practical rules:

- data first;
- one experiment second;
- architecture third;
- no live-money path without explicit approval;
- no profitability claims without forward evidence.

## License And Status

This is a proprietary research project in active development. No open-source
license is granted at this stage.
