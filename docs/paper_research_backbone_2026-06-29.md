# Paper/Research Backbone - 2026-06-29

This document is public-safe. Raw candles, lineage mappings, LLM traces, training
rows, screenshots, and runtime artifacts stay under the private Strategy Lab root:
`strategy-lab/`.

## Current Contract

The canonical paper/research path is:

`scanner event -> market data packet -> feature packet -> calculator advice -> sweep/validation/memory -> paper signal -> main paper watch -> Telegram preview -> outcome/training -> dashboard/graph`

Schema names:

- `ScannerEvent.v1`
- `MarketDataPacket.v1`
- `FeaturePacket.v1`
- `LineageLink.v1`
- `CalculatorAdvice.v1`
- `TrainingRow.v2`
- `HumanFeedback.v1`
- `PaperResearchStatus.v1`

Legacy ids are preserved as source refs. New lineage fields are additive and do not
rewrite old logs.

## Safety Rules

- `.env` is not edited.
- `AUTO_TRADE` remains off.
- No private OKX/account/order endpoints are used by the backbone.
- Old `main.py` stays a legacy boundary; current main-compatible runtime is
  `main_paper_runtime`.
- Telegram paper sender remains explicit opt-in; preview and delivery dry-run are
  default.
- LLM output is advisory only. It cannot set `paper_ready`, alter entry/stop/TP, set
  validator verdicts, or execute anything.

## Private Artifacts

Important private labels:

- `strategy-lab/state/lineage/scanner_events.jsonl`
- `strategy-lab/state/lineage/data_packets.jsonl`
- `strategy-lab/state/lineage/feature_packets.jsonl`
- `strategy-lab/state/lineage/cycle_links.jsonl`
- `strategy-lab/state/lineage/backfill_mapping.jsonl`
- `strategy-lab/state/llm_advice/calculator_advice.jsonl`
- `strategy-lab/state/derived/paper_signal_training.jsonl`
- `strategy-lab/state/derived/paper_telegram_preview.jsonl`

The backfill pass is non-destructive and writes only derived mappings.

## Resource Caps

Default caps:

- scanner events per cycle: 50
- data packets per cycle: 50
- feature packets per cycle: 50
- LLM advisor calls per cycle: 5
- candles per packet: 512
- Telegram previews per cycle: 20
- Telegram sends per cycle: 0
- disk growth per day: 512 MB
- runtime per stage: 180 seconds

Skip/defer reasons are normalized, including `missing_candles`, `stale_data`,
`window_too_short`, `spread_too_wide`, `oi_unavailable`, `provider_error`,
`llm_disabled`, `llm_timeout`, `llm_schema_reject`, `known_bad_memory`,
`validator_reject`, `manual_review_required`, and `legacy_unknown_source`.

## Operator Commands

Set `TRADING_BOT_RESEARCH_ROOT` to the private Strategy Lab root before running these
commands. The public repository must not store private raw artifacts.

Sanitized status:

```powershell
python -m scripts.strategy_lab.paper_research_status --private-root $env:TRADING_BOT_RESEARCH_ROOT --json
```

Non-destructive backfill:

```powershell
python -m scripts.strategy_lab.lineage_backfill --private-root $env:TRADING_BOT_RESEARCH_ROOT --json
```

Training export:

```powershell
python -m scripts.strategy_lab.paper_signal_training_export --private-root $env:TRADING_BOT_RESEARCH_ROOT --json
```

Bounded paper smoke with public OKX data:

```powershell
python -m scripts.strategy_lab.farm_loop --apply --once --run-paper-signals --provider okx-public --paper-signals-max-new 1 --paper-signals-max-observe 1 --max-plan-events 1 --max-prepares 0 --max-sweeps 0 --max-worker-jobs 0 --max-validations 0 --max-paper-cards 1 --main-paper-runtime-limit 1 --private-root $env:TRADING_BOT_RESEARCH_ROOT --quiet
```

Integrated bounded paper + calculator smoke without changing `.env`:

```powershell
python -m scripts.strategy_lab.farm_loop --apply --once --run-paper-signals --run-calculator-advisor --calculator-provider ollama --calculator-model calculator --calculator-base-url http://127.0.0.1:11434/v1 --calculator-timeout 120 --paper-signals-max-new 0 --paper-signals-max-observe 0 --true-forward-max-candidates 0 --max-plan-events 0 --max-prepares 0 --max-sweeps 0 --max-worker-jobs 0 --max-validations 0 --max-paper-cards 1 --main-paper-runtime-limit 0 --private-root $env:TRADING_BOT_RESEARCH_ROOT --quiet
```

Calculator disabled-state smoke:

```powershell
python -m scripts.strategy_lab.calculator_advisor_smoke --private-root $env:TRADING_BOT_RESEARCH_ROOT --json
```

Calculator Ollama smoke without changing `.env`:

```powershell
python -m scripts.strategy_lab.calculator_advisor_smoke --private-root $env:TRADING_BOT_RESEARCH_ROOT --provider ollama --model calculator --base-url http://127.0.0.1:11434/v1 --timeout 120 --json
```

Operational health:

```powershell
python -m scripts.strategy_lab.operational_health --private-root $env:TRADING_BOT_RESEARCH_ROOT --pfr-db-path "$env:TRADING_BOT_RESEARCH_ROOT\state\strategy_lab.sqlite" --fail-on-blocked
```

Graph viewer:

```powershell
python -m scripts.strategy_lab.build_graph_viewer --private-root $env:TRADING_BOT_RESEARCH_ROOT --max-candidates 50
```

Lineage graph viewer:

```powershell
python -m scripts.strategy_lab.build_lineage_graph_viewer --private-root $env:TRADING_BOT_RESEARCH_ROOT --max-links 500
```

## Current Acceptance Snapshot

Latest verified state:

- full pytest: `1845 passed`
- targeted paper/backbone tests: green
- ruff over changed files: clean
- `git diff --check`: clean, with only CRLF normalization warnings
- operational health: blocking `0`, rebuild actions `0`
- public OKX bounded smoke: `20` paper instructions, `20` accepted, `20` previews,
  `sends_network=false`, `execution_allowed=false`
- calculator disabled smoke: records `provider_not_configured`
- calculator Ollama smoke: accepted `CalculatorAdvice.v1` for the latest feature packet
- integrated farm-loop calculator stage: `processed=1`, `accepted=1`,
  `execution_allowed=false`

Known intentional warnings:

- Manual product analyzer remains a separate legacy/product boundary and is not the
  farm/PFR paper runtime.
