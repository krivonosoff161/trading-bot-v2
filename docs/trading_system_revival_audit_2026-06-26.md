# Trading System Revival Audit - Farm/Paper/Main Boundary

Status: **ACTIVE operator audit**. Date: 2026-06-26.

This document records the current runtime truth after auditing the farm, PFR bridge,
paper runtime, paper-signal lane, main scanner, Telegram, journals, and runbooks. It is
not an edge claim and not a live-trading activation.

## Safety Boundary

The restored operator path is still paper/research only:

- public OKX market data only;
- no `.env` edits;
- no `AUTO_TRADE`;
- no order execution;
- no private exchange/account endpoints;
- Telegram is a surface, not a decision or execution layer.

## Canonical Data Flow

```text
OKX public discovery / scanner-watch intake
  -> scripts.strategy_lab.farm_loop
  -> state/farm_tasks.sqlite
  -> state/strategy_lab.sqlite compute queue
  -> worker jobs
  -> candidate classification
  -> hard validation
  -> setup_library cards
  -> scripts.strategy_lab.paper_loop / paper_runtime
  -> paper/paper_trades.jsonl

state/strategy_lab.sqlite PFR records
  -> paper_signals PFR bridge (only when --pfr-db-path is explicit)
  -> state/derived/paper_signals.jsonl
  -> visual paper-signal reviews and status snapshots
  -> state/derived/main_paper_instructions.json/jsonl
  -> state/derived/main_paper_consumed.json/jsonl
  -> state/derived/paper_telegram_preview.json/jsonl
  -> future reviewed runtime adapter (not live executor)
```

## Priority And Ownership

| Layer | Current role | Priority | Notes |
|---|---|---:|---|
| `farm_loop.py` | Canonical research/paper cycle | 1 | Plans, prepares, enriches, sweeps, validates, and runs paper lanes. |
| `paper_runtime.py` / `paper_loop.py` | Gated replay paper runtime | 2 | Reads only `PAPER_FORWARD_READY` setup cards. |
| `paper_signals/` | Operational forward-watch lane | 2 | Observes generated/PFR paper signals, writes JSONL/snapshots/reviews. |
| `pfr_bridge.py` | Farm validation -> paper-watch bridge | 2 | Active only with explicit `--pfr-db-path`; now wired into the visible wrapper. |
| `main_paper_bridge.py` | Paper-watch -> main-readable instruction view | 3 | Rebuildable artifact with `execution_allowed=false`; no old main runtime import. |
| `main_paper_consumer.py` | Main-readable instruction -> paper-watch audit | 3 | Validates `SignalContract` payloads and rejects bad instructions; still no executor. |
| `paper_telegram_preview.py` | Paper-watch audit -> operator card preview | 3 | Builds offline Telegram-card previews; never sends, never reads tokens/chat IDs. |
| `ws_main_screener.py` | Separate scanner/Telegram runtime surface | 4 | Does not consume farm/PFR outputs today. Do not treat it as the farm executor. |
| Telegram | Notification surface | 5 | Env-gated; not part of the decision path. |
| Excel journal | Reporting/training artifact | 5 | Rebuild is safe by default; private fills require explicit opt-in. |

## Findings

### F1 - PFR bridge existed but was not active in the visible full-cycle wrapper

`farm_loop.py` and `paper_signals_run.py` already supported `--pfr-db-path`, and
`pfr_bridge.py` correctly reads validated farm records from `state/strategy_lab.sqlite`.
However, `bat/strategy_lab_farm_full_cycle_loop.bat` did not pass that path. A long
visible run therefore executed the paper-signal lane without the PFR-backed setup source.

Fix: the wrapper now sets `STRATEGY_LAB_PFR_DB_PATH` to
`%TRADING_BOT_RESEARCH_ROOT%\state\strategy_lab.sqlite` by default and passes it to
`farm_loop --run-paper-signals`.

### F2 - Runbook smoke command used stale flags

`docs/farm_runbook.md` contained an old full-cycle smoke command with flags such as
`--max-enrich-funding`, `--max-enrich-oi`, `--max-validation`, and `--max-paper`. The
current CLI exposes `--max-enrich`, `--max-worker-jobs`, and `--max-paper-cards` instead.

Fix: the smoke and continuous commands now use the real CLI surface and include
`--pfr-db-path`.

### F3 - Main engine is not the farm/paper executor

The old/main WebSocket scanner can still produce Telegram-facing signal analysis, but it
does not read farm setup cards, PFR records, or paper-signal outcomes. That is intentional
until a separate bridge contract is designed and tested. The current safe priority is:

1. farm/PFR/paper-watch proves forward behavior;
2. journals and reviews preserve outcomes;
3. only then may a main-engine integration contract be designed.

Fix: `scripts.strategy_lab.operational_health` now reports this explicitly as
`main_bridge.status = not_connected` or `instruction_view_ready_not_consumed`, while also
showing whether paper/PFR sources, the derived main-paper instruction view, and old main
signal logs exist. This prevents a false operator assumption that the main runtime is
already consuming farm/PFR outputs.

Follow-up hardening: `operational_health` also emits a `readiness` matrix. The matrix
separates runnable paper/research gates from optional surfaces and marks
`main_runtime_consumer = planned` until a tested consumer exists. This is deliberate:
the system can export main-readable paper instructions today, but the old main runtime
is still not the executor.

### F4 - Main-readable paper instruction view was missing

The project had farm/PFR/paper-watch data and an old `SignalContract`, but no safe bridge
artifact between them. That made the next integration step ambiguous: either the old main
runtime would have to parse paper-signal internals, or someone would be tempted to import
the old runtime directly.

Fix: `src.research_lab.main_paper_bridge` now rebuilds
`state/derived/main_paper_instructions.jsonl` and
`state/derived/main_paper_instructions.json` from active paper-watch signals. Each item
contains a `SignalContract`-shaped payload and hard invariants:

- `paper_only = true`
- `execution_allowed = false`
- active source statuses only: `armed`, `opened_paper`
- no Telegram, `.env`, exchange, or order imports

`farm_loop --run-paper-signals` exports this view after each paper-signal cycle.

### F5 - Paper outcomes existed but had no dedicated training export

Paper-watch outcomes and deterministic reviews were present in
`state/derived/paper_signals.jsonl`, but a training pipeline would have to parse the
full signal audit log and infer which rows were terminal. That made later LLM/model
training ambiguous and easy to couple to the wrong artifact.

Fix: `src.research_lab.paper_signals.training_export` now builds
`state/derived/paper_signal_training.jsonl` and `.json` from latest terminal
paper-watch rows. The export is derived, private-root only, `paper_only=true`, and
does not call exchanges, Telegram, LLM providers, account endpoints, or order code.

### F6 - Main-readable instructions needed a tested consumer boundary

After F4, the farm could export `main_paper_instructions`, but there was still no tested
downstream check proving that those rows are valid `SignalContract` payloads before a
future main adapter reads them. Leaving only a bridge artifact would make the next step
ambiguous and could hide malformed instructions until runtime.

Fix: `src.research_lab.main_paper_consumer` now reads the bridge snapshot/JSONL,
reconstructs the shared `SignalContract`, checks `paper_only=true`,
`execution_allowed=false`, active source status, pair/side consistency, and writes
`state/derived/main_paper_consumed.jsonl` plus `.json`. Bad rows are kept as
`rejected_contract` with explicit problems. `farm_loop --run-paper-signals` runs this
consumer after the bridge export, and `farm_status_report`/`operational_health` surface
the accepted/rejected counts.

This is still not old-main execution. It is the safe consumer/audit layer needed before
designing a runtime adapter.

### F7 - Paper Telegram needed a dry-run preview before any send path

The project had Telegram send utilities and `paper_signals_run --notify`, but no
artifact proving what the operator-facing paper card would look like before enabling a
paper channel. Sending directly from active paper signals would mix content validation
with delivery and make formatting/length failures show up too late.

Fix: `src.research_lab.paper_telegram_preview` now reads accepted
`main_paper_consumed` rows, renders offline Telegram-card previews, escapes text for
HTML mode, enforces the `research-only, not an order` and `execution_allowed=false`
boundary, checks Telegram's 4096-character message limit, and writes
`state/derived/paper_telegram_preview.jsonl` plus `.json`. It does not import Telegram
senders, does not read `TELEGRAM_BOT_TOKEN` or chat IDs, and sends no network request.

`farm_loop --run-paper-signals` rebuilds this preview after the consumer audit, and
`farm_status_report` / `operational_health` surface the preview counts.

## Operator Commands

Preflight:

```bash
python -m scripts.strategy_lab.operational_health \
  --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" \
  --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
```

Bounded full-cycle smoke:

```bash
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --run-paper-signals --enrich-funding --enrich-oi --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --paper-signals-max-observe 0 --paper-signals-max-pfr-scan 1 --paper-signals-fetch-timeout 3 --max-plan-events 1 --max-prepares 1 --max-enrich 1 --max-sweeps 1 --max-worker-jobs 1 --max-paper-cards 1 --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Rebuild only the main-readable paper instruction view:

```bash
python -m scripts.strategy_lab.main_paper_bridge --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Validate that view into the paper-only main consumer audit:

```bash
python -m scripts.strategy_lab.main_paper_consumer --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Build offline paper Telegram previews:

```bash
python -m scripts.strategy_lab.paper_telegram_preview --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Export terminal paper-watch outcomes into training-friendly rows:

```bash
python -m scripts.strategy_lab.paper_signal_training_export --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Visible continuous run:

```bash
bat\strategy_lab_farm_full_cycle_loop.bat
```

Stop:

```bash
bat\strategy_lab_farm_full_cycle_stop.bat
```

Status:

```bash
python -m scripts.strategy_lab.status
python -m scripts.strategy_lab.farm_status_report
python -m scripts.strategy_lab.operational_health --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
```

## Remaining Work

These are intentionally not done in this pass:

1. Main runtime consumer: the rebuildable `main_paper_instructions` view exists, but
   the old main scanner/runtime still does not execute it. The paper-only consumer audit
   exists and validates the contract boundary first.
2. Telegram paper channel: audit text, chart rendering, and notification routing before
   enabling paper-signal alerts. The offline text preview now exists; real delivery is
   still opt-in and should stay separate.
3. Journal modernization: the paper-signal training export now exists; next work is to
   surface it in the Excel/dashboard layer and keep private account fills opt-in.
4. Dead-code retirement: archive/delete only after import and command references are
   proven unused by tests and docs.

The current pass restores a coherent full-cycle operator path. It does not claim that the
full trading product is complete.

## Verification On 2026-06-26

Commands run after the wrapper/runbook fix:

```bash
cmd /c bat\strategy_lab_farm_full_cycle_loop.bat
```

with `STRATEGY_LAB_FARM_DRY_RUN=1`, `STRATEGY_LAB_FARM_ONCE=1`, and small caps. Result:
the wrapper built a valid `farm_loop` command, printed the resolved PFR DB path, and exited
with code 0.

```bash
python -X utf8 -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --run-paper-signals --enrich-funding --enrich-oi --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --paper-signals-max-observe 0 --paper-signals-max-pfr-scan 1 --paper-signals-fetch-timeout 3 --max-plan-events 1 --max-prepares 1 --max-enrich 1 --max-sweeps 1 --max-worker-jobs 1 --max-paper-cards 1 --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Result: bounded apply smoke completed in paper/research mode. It found active work,
checked paper readiness, and kept the live/money boundary closed.

```bash
python -m scripts.strategy_lab.farm_status_report --json
```

Important observed counts:

- `pfr_bridge.records_loaded = 53`
- `pfr_bridge.passed_quality = 43`
- `pfr_bridge.unique_setups = 11`
- `pfr_bridge.risk_too_wide = 26`
- `paper_signals.total = 637`
- `paper_signals.armed = 27`
- `paper_signals.opened_paper = 12`
- `paper_signals.reviewed = 598`
- `main_bridge.status = not_connected` before first bridge export; then
  `instruction_view_ready_not_consumed`
- `main_paper_bridge.instructions = 54` on the current private snapshot after bridge export
- `main_paper_consumer.accepted = 54`, `rejected = 0` after consumer audit
- `paper_telegram_preview.rendered = 20`, `invalid = 0`, `sends_network = false` after
  preview build
- `main_bridge.orders_enabled_by_bridge = false`
- `readiness.main_runtime_consumer = planned`
- `readiness.main_instruction_view_available = pass` after bridge export
- `readiness.main_paper_consumer_available = pass` after consumer audit
- `readiness.paper_telegram_preview_available = pass` after preview build
- `paper_signal_training_export.rows = terminal paper-watch outcomes` after export

Targeted tests:

- `tests/test_pfr_bridge.py tests/test_paper_signals.py tests/test_operational_health.py tests/test_build_journal_safety.py`: 87 passed.
- `tests/test_farm_loop_integration.py tests/test_paper_runtime.py tests/test_paper_contract.py`: 65 passed, 1 pre-existing CUDA warning.
- `tests/test_main_paper_bridge.py tests/test_operational_health.py`: 8 passed.
- `git diff --check`: clean.
