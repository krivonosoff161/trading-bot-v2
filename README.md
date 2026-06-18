# Trading Bot V2

Research project for market-data and news-driven trading infrastructure around OKX
crypto futures. The current active work is not live auto-trading. The active core is the
**universe-driven calculation farm** (`python -m scripts.strategy_lab.farm_loop`): a
paper-only, self-deciding research lifecycle that grinds the OKX universe, fetches data
(candles + public funding/OI), runs strategy sweeps, classifies, and hands candidates to
honest validation. The `info-edge scanner` (`src/scout/`) is now an **upstream intake
source** that feeds the farm, not the center.

> **Status:** research / paper / demo only. No profitability is claimed. This is
> not financial advice, not a signal service, and not a promise of future returns.
> Real-money execution is outside the current work.

## Current Direction

> **Update 2026-06-18 — center is the calculation farm.** The current research core is the
> **universe-driven calculation farm**: a continuous, self-deciding lifecycle
> (`scripts/strategy_lab/farm_loop.py` → `farm_coordinator` → `state/farm_tasks.sqlite`)
> that grinds the OKX universe, fetches data (candles + public funding/OI), runs strategy
> sweeps, classifies, and hands candidates to honest validation — paper/research only.
> Canonical docs: [docs/farm_loop_lifecycle.md](docs/farm_loop_lifecycle.md),
> [docs/farm_ownership_map.md](docs/farm_ownership_map.md),
> [docs/farm_runbook.md](docs/farm_runbook.md).

The project has three contours:

| Contour | Status | Purpose |
|---|---|---|
| Calculation farm (`research_lab` + `scripts/strategy_lab`) | **active core** | Continuous research lifecycle: intake → plan → prepare/enrich → sweep → classify → validation. |
| `src/scout/` info-edge scanner | active (upstream intake) | News/event intake; its `WATCH/GO` rows feed the farm. One intake source, no longer the center. |
| Old WebSocket trading engines | frozen/reference | Historical paper/demo strategies; useful logic already ported into research_lab families. |

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

`bat\news_scanner_loop.bat` runs the scanner and then, by default, resolves
mature outcomes after every pass. Tune it with `SCANNER_OUTCOME_LIMIT` and set
`SCANNER_RUN_OUTCOMES=false` only for diagnostics.

LLM budget controls enforce any non-zero local caps by default. Tune
`LLM_DAILY_RUB_CAP`,
`LLM_SCAN_RUB_CAP`, `LLM_MAX_TOKENS_PER_SCAN`, and
`LLM_MAX_CHIEF_PER_SCAN` in `.env`; set `LLM_STOP_ON_BUDGET=false` only for
manual diagnostics. Budget skips are logged as model usage with
`status=budget_skipped`; chief skips are journaled as `CHIEF_BUDGET_SKIPPED`
and are not sent to Telegram.

Private strategy-lab smoke run:

Use CPU/offline paths for smoke checks and deterministic validation. The sweep
worker has a real backend contract (`cpu`/`gpu`/`auto`) with two GPU-accelerated
stages: the `momentum_breakout` signal kernel AND the batched trade simulation
(SL/TP/max-hold first-touch, long & short) for the supported exit mode. Both run
on a cupy GPU backend when usable (CPU/GPU parity proven by tests); unsupported
exit modes and over-cap batches fall back to CPU with an explicit reason. CPU
stays the reference path. Separately, the 24/7 local-Ollama loop offloads the
calculator model to GPU. Check the sweep GPU backend with:

```bash
python -m scripts.strategy_lab.gpu_doctor
bat\strategy_lab_gpu_probe.bat
```

```bash
bat\strategy_lab_ollama_calculator_24x7.bat
ollama ps
```

Expected during an LLM call: `calculator:latest` shows `PROCESSOR 100% GPU`.
Stop it with `bat\strategy_lab_graceful_stop.bat` or Ctrl+C in the window.

```bash
python scripts/strategy_lab/run_experiment.py --spec configs/strategy_lab/l2_smoke.json
```

Continuous calculation farm (current core — paper/research only):

```bash
python -m scripts.strategy_lab.farm_loop --once --dry-run                       # plan only
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --enrich-funding --enrich-oi
python -m scripts.strategy_lab.farm_status_report                               # operator picture
```

See [docs/farm_runbook.md](docs/farm_runbook.md) for loop/validation flags, stop/restart,
and where artifacts are written.

Legacy one-command local start (still works; the legacy queue loop, not the new lifecycle):

```bash
bat\strategy_lab_start.bat
```

This is the legacy operator entrypoint. By default it syncs the private state
DB, queues a bounded `core_market / 1d` research plan, opens the local
dashboard, and starts the one-worker queue loop. The worker processes one job at
a time and is throttled by `configs/strategy_lab/resource_policy.yaml`, so the
desktop is not flooded.

Optional overrides before running the bat:

```bash
set STRATEGY_LAB_UNIVERSE=l2_high_beta
set STRATEGY_LAB_TIMEFRAME=15m
set STRATEGY_LAB_FULL=1              # use full per-timeframe caps
set STRATEGY_LAB_NIGHT_MODE=1        # opt in to relaxed night limits
```

Strategy Lab MVP 2.0 chain: data inventory -> strategy registry (12
deterministic strategies) -> queue -> worker -> simulation -> regime labeling
-> validator-lite (REJECT / OBSERVE / REGIME_SPECIFIC / FORWARD_PAPER) ->
private candidate registry -> deterministic proposal generator -> state DB ->
dashboard -> Obsidian notes -> LLM review pack (prepared only, never
auto-executed). Validation statuses are research labels, not profitability
claims. Full doc:
[docs/strategy_lab_mvp2.md](docs/strategy_lab_mvp2.md).

Next-architecture machine: public config for a universe of asset groups and
relation hints (`configs/strategy_lab/universe.yaml`), timeframe roles/limits
(`configs/strategy_lab/timeframe_profiles.yaml`), and a desktop-safety resource
policy (`configs/strategy_lab/resource_policy.yaml`, quiet by default). Modules
add event clusters, honest entry-timing metrics (lag, missed move, capture ratio,
MAE/MFE), and a validated coarse-sweep spec that gates 1m/heavy jobs.

The resource policy is now enforced at runtime (not only the schema): the worker
throttles by `min_seconds_between_jobs` / `max_jobs_per_hour`, caps per-job
variants by `max_variants_per_job`, defaults to `quiet_desktop`, and treats
`night_mode` as opt-in. `bat\strategy_lab_start.bat` wraps the safe default
chain. For diagnostics, generate bounded jobs from a universe group + timeframe,
then dry-run or apply manually:

```bash
python -m scripts.strategy_lab.enqueue_research_plan --universe core_market --timeframe 1d --dry-run
python -m scripts.strategy_lab.enqueue_research_plan --universe l2_high_beta --timeframe 15m --apply
python scripts/strategy_lab/worker_once.py            # respects throttle + variant cap
python -m scripts.strategy_lab.export_llm_review_pack --limit 10   # export only, no API call
```

MVP 3.0 discovery loop (event clusters -> bounded sweeps -> reducer verdicts ->
entry-timing -> private Obsidian graph). Each run also writes a private
`reducer_report.json` with per-(family, symbol) verdicts (REJECT / OBSERVE /
REGIME_SPECIFIC / FORWARD_PAPER / NEEDS_MORE_DATA) and reason codes; a single
lucky parameter without neighbor support is never promoted:

```bash
python -m scripts.strategy_lab.generate_event_sweeps --universe l2_high_beta --timeframe 15m --dry-run
python -m scripts.strategy_lab.generate_event_sweeps --universe l2_high_beta --timeframe 15m --apply
python -m scripts.strategy_lab.build_obsidian_graph   # private notes for non-REJECT candidates
```

Closed proposal loop (results -> review pack -> next proposals -> validate ->
queue -> worker). Deterministic, dry-run by default, no paid API. Typed proposals
flow `PROPOSED -> VALIDATED / REJECTED -> QUEUED` and are validated against
resource caps, timeframe policy (no 1m full sweep), and the private/public
boundary:

```bash
python -m scripts.strategy_lab.generate_next_proposals --limit 10 --dry-run
python -m scripts.strategy_lab.generate_next_proposals --limit 10 --apply
python -m scripts.strategy_lab.import_llm_proposals --file out.json --dry-run   # human-saved LLM output, no API
python -m scripts.strategy_lab.queue_validated_proposals --apply                # idempotent, bounded
```

MVP 4.0 adds a read-only 1m **event-microscope** locator (capped, trigger-only, no
downloader -- missing 1m data is a clean skip), **event-anchored entry timing** in
event-driven runs (lag, capture, missed move, `late_entry` -- no look-ahead), and a
**gated LLM send boundary** (only `NullReviewSender` ships; a real send needs
`--send` + `STRATEGY_LAB_LLM_ENABLED=1` + a provider + a daily budget cap):

```bash
python -m scripts.strategy_lab.microscope_scan --universe l2_high_beta   # read-only; reports missing if no 1m data
```

MVP 4.2 adds a **demand-driven 1m loader** (`prepare_1m_data`): it derives the
capped 1m windows the lab actually needs (event specs / queued jobs / a manual
request), checks the local cache, and -- only with `--apply` and a configured
provider -- writes just those windows under the private root. No full-market
download; default provider is `null` (no network), so `--apply` without a provider
prints "provider not configured / no data written". The real provider is
`okx-public` -- OKX **public** 1m candles, read-only, **no API key**, no
order/account endpoints (a small isolated public-only adapter; the existing
order-capable OKX clients are not reused):

```bash
python -m scripts.strategy_lab.prepare_1m_data --dry-run                  # shows which 1m windows are missing
python -m scripts.strategy_lab.prepare_1m_data --symbol BTC_USDT_SWAP --start 2026-06-10T00:00 --end 2026-06-10T03:00 --provider okx-public --apply
```

`strategy_lab_start.bat` can run the prepare step automatically before the worker,
but only when you opt in (`STRATEGY_LAB_PREPARE_1M=1`, plus `..._APPLY=1` +
`STRATEGY_LAB_MARKET_DATA_PROVIDER=okx-public` to actually fetch). Default start
fetches nothing; the worker never fetches by itself. See the operator guide.

MVP 4.3 adds a **controlled research cycle** (`research_cycle`): one bounded pass --
inspect -> generate proposals -> check 1m data -> optionally prepare -> queue (capped) ->
one throttled worker step -> status report. Dry-run by default (no queue/worker/
network); a real fetch needs `--apply --prepare-1m --prepare-1m-apply --provider
okx-public`. No hidden loop; the worker respects the throttle:

```bash
python -m scripts.strategy_lab.research_cycle --dry-run
python -m scripts.strategy_lab.research_cycle --apply --max-proposals 5 --max-queue 5 --max-worker-jobs 1
```

MVP 4.4 adds **data-complete research sessions** + an advisory **LLM proposal loop**.
`research_session` wraps the cycle with a data-readiness gate (a job is queued only
when its data is READY; missing/short/malformed are skipped with a reason, never
faked) and an export-only LLM layer (cheap -> chief; code validates every candidate;
the LLM never decides the queue and is never executed). LLM is disabled by default; a
real send needs `STRATEGY_LAB_LLM_ENABLED=1` + `STRATEGY_LAB_LLM_PROVIDER` + a
configured provider + a daily cap (Alibaba/Qwen documented but not shipped). Expected
pace is one or two serious variants per day -- not a 24/7 poller.

```bash
python -m scripts.strategy_lab.research_session --dry-run
python -m scripts.strategy_lab.research_session --apply --max-candidates 5 --max-queued 5 --max-worker-jobs 1
```

The first unattended controlled cycle is summarized as a public-safe showcase in
[examples/strategy_lab_first_cycle](examples/strategy_lab_first_cycle/README.md).
It proves the queue/worker/reporting loop can run unattended and stop cleanly;
the complete result corpus remains in the private research root.

The GPU backend is optional and capability-gated: both the `momentum_breakout`
signal kernel and the batched trade simulation (supported `fixed_sl_tp_hold`
mode) run on cupy when a real GPU backend is usable (`cpu`/`gpu`/`auto`,
`gpu_doctor` reports it); unsupported exit modes and over-cap batches fall back to
the CPU reference with an explicit reason. 1m is a trigger-only event microscope,
not full-universe scanning. LLM review is export-only (no automatic API call).
Full how-to: [docs/strategy_lab_operator_guide.md](docs/strategy_lab_operator_guide.md).
Design doc:
[docs/strategy_lab_architecture_next.md](docs/strategy_lab_architecture_next.md).

Data inventory for a spec:

```bash
python scripts/strategy_lab/build_data_inventory.py --spec configs/strategy_lab/l2_smoke.json
```

Queue the starter research pack without starting the dashboard:

```bash
python scripts/strategy_lab/enqueue_pack.py --dir configs/strategy_lab/starter --priority 50
```

Generate and queue bounded follow-up specs from existing private candidates:

```bash
python scripts/strategy_lab/autopilot_once.py --max-proposals 8 --priority 70
```

This is deterministic code-only autonomy. It creates parameter-neighborhood and
regime-specific follow-up specs from the private candidate registry. It does not
call an LLM, trade, or publish private results.

The starter pack and `autopilot_once.py` are advanced/manual tools now. The
default one-click start uses `enqueue_research_plan` so the active universe,
timeframe, and resource-policy limits are visible and reproducible.

One-command smoke demo:

```bash
bat\strategy_lab_demo_all.bat
```

This syncs state, queues `configs/strategy_lab/l2_smoke.json`, runs one queued
job, and opens the dashboard.

For continuous local research, prefer `bat\strategy_lab_start.bat` or, if the
queue is already prepared, `bat\strategy_lab_worker_loop.bat`. The older
`bat\strategy_lab_loop.bat` is a legacy fixed-spec loop kept for manual
diagnostics.

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

Strategy-lab tests:

```bash
python -m pytest tests/test_research_lab_*.py -q
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

Read these first (calculation farm = current active core):

- [docs/farm_loop_lifecycle.md](docs/farm_loop_lifecycle.md) - the canonical continuous research cycle (`farm_loop`).
- [docs/farm_ownership_map.md](docs/farm_ownership_map.md) - which loop owns what; legacy/archive paths.
- [docs/farm_runbook.md](docs/farm_runbook.md) - how to operate the farm (run/stop/restart, storage).
- [CURRENT_STATE.md](CURRENT_STATE.md) - short operational status.
- [ARCHITECTURE.md](ARCHITECTURE.md) - current project boundaries.
- [ROADMAP.md](ROADMAP.md) - current development sequence (Farm track first).
- [SCANNER_SPEC.md](SCANNER_SPEC.md) - scanner design (now an upstream **intake source**, not the center).
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
