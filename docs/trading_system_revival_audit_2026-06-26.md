# Trading System Revival Audit - Farm/Paper/Main Boundary

Status: **ACTIVE operator audit**. Date: 2026-06-26, refreshed 2026-06-27.

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
  -> state/derived/main_paper_runtime_queue.json/jsonl
  -> state/derived/main_paper_runtime_observation.json/jsonl
  -> state/derived/paper_telegram_preview.json/jsonl
  -> paper-only lifecycle observation (not live executor)
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
| `main_paper_runtime_adapter.py` | Accepted paper audit -> main-compatible paper queue | 3 | Builds a private `watch_paper` queue; still no old main runtime import. |
| `main_paper_runtime.py` | Paper queue -> lifecycle observation | 3 | Observes queued paper items on public candles and writes reviewed/pending status; no old main runtime import. |
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
`main_runtime_consumer = planned` until a tested old-main executor exists. This is
deliberate: the system can export main-readable paper instructions and observe them in a
paper-only runtime today, but the old live `main.py` runtime is still not the executor.

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

Follow-up hardening: `paper_signals_run --notify` requires `PAPER_CHAT_ID`
explicitly and does not fall back to `SCANNER_CHAT_ID`. Scanner and paper-watch
notifications are separate operator surfaces.

### F8 - Main-paper runtime needed a queue before touching the old main engine

The old `main.py` is a real runtime: it imports the OKX client, sets leverage at startup,
checks account positions, and can call `place_market_order`. Farm/PFR outputs must not
be wired into that file directly.

Fix: `src.research_lab.main_paper_runtime_adapter` now reads accepted
`main_paper_consumed` records and rebuilds
`state/derived/main_paper_runtime_queue.jsonl` plus `.json`. Queue rows are
main-compatible paper watch items with:

- `runtime_action = watch_paper`;
- `paper_only = true`;
- `execution_allowed = false`;
- concrete entry/stop/take/max-hold fields from the shared `SignalContract`;
- full paper-observation context (`entry_zone`, `boundary_ts`, `expires_at`,
  `max_hold_bars`, `risk_pct`, `data_fingerprint`, `dedup_key`, `source_mode`, and
  `exit_mode`) so the next runtime does not need to parse internal paper-signal records;
- deterministic priority from family, timeframe, and risk.

This is the handoff point for paper lifecycle observation. It is not a live executor and
does not import the old main engine, Telegram, exchange clients, `.env`, or order code.

### F9 - Main-paper runtime observation was missing after the queue

After F8 the queue was self-contained, but it still only proved that the handoff existed.
There was no separate process that read the queue, fetched public candles, and advanced
paper lifecycle without importing the old live engine.

Fix: `src.research_lab.main_paper_runtime` now consumes
`state/derived/main_paper_runtime_queue.json/jsonl`, rebuilds deterministic
`PaperActionSignal` objects, observes them on public OKX candles, applies the existing
paper-signal lifecycle/review logic, and writes:

- `state/derived/main_paper_runtime_observation.jsonl`;
- `state/derived/main_paper_runtime_observation.json`.

The observer is also invoked automatically by `farm_loop --run-paper-signals` after
`main_paper_runtime_queue` is rebuilt and before the offline Telegram preview is rendered.
The standalone script surface remains available for rebuilding the observation view:

```bash
python -m scripts.strategy_lab.main_paper_runtime --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --limit 50 --apply
```

Boundary: this observer imports no old `main.py`, no `src.exchange`, no Telegram, no
credential loader, and no order path. It reports pending/reviewed/no-data/provider-error
states only.

### F10 - Excel journal did not surface the paper-watch lane

The farm/paper system had `paper_signal_training.jsonl`, but `scripts/build_journal.py`
still rebuilt only the older scanner/main/pump/impulse/manual sheets. That meant the
operator could run paper-forward work and still not see those outcomes in the familiar
Excel journal.

Fix: `scripts/build_journal.py` now adds a `Paper Watch` sheet from the private derived
`state/derived/paper_signal_training.jsonl` export. It shows paper family, side, exit
mode, status, result, net %, net R, MFE/MAE, capture, diagnosis, risk, and hold horizon,
plus local summary counts by family/diagnosis/result. The loader is read-only, filters the
expected `PaperSignalTrainingRow.v1` schema, and does not call OKX account endpoints,
Telegram, or LLM providers. The generated `scripts/journal.xlsx` remains ignored by git.

### F11 - Operator launch names were easy to confuse

The repo has several historical launchers with overlapping names. That created a real
operator risk: starting the wrong window could look like "the bot is alive" while bypassing
the current farm/PFR/paper lifecycle, or worse, could lead someone toward the old
order-capable runtime.

Current verified ownership:

- `bat\strategy_lab_control_room.bat` is the preferred visible operator start for the
  restored paper/research cycle. It opens farm loop, dashboard, graph, and status windows.
- `bat\strategy_lab_farm_full_cycle_loop.bat` is the canonical one-window farm loop.
- `bat\strategy_lab_farm_full_cycle_stop.bat` is the clean stop path for that loop.
- `bat\strategy_lab_start.bat` is an older standalone lab wrapper, not the current
  farm/PFR/paper lifecycle.
- `start.bat` starts the Telegram analyzer product surface, not Strategy Lab farm and not
  old `main.py`.
- `start_all.bat` is legacy/frozen product-stack history.
- `main.py` is still a live order-capable runtime and must not consume farm/PFR
  instructions directly.

Fix: the runbook and ownership map now make these boundaries explicit. This is
documentation hardening rather than a code change; the code boundary remains enforced by
the existing paper-only artifacts and tests.

## Operator Commands

Preflight:

```bash
python -m scripts.strategy_lab.operational_health \
  --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" \
  --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
```

Bounded compute cycle:

```bash
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --run-paper-signals --enrich-funding --enrich-oi --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --paper-signals-max-observe 0 --paper-signals-max-pfr-scan 1 --paper-signals-fetch-timeout 3 --main-paper-runtime-limit 1 --max-plan-events 1 --max-prepares 1 --max-enrich 1 --max-sweeps 1 --max-worker-jobs 1 --max-paper-cards 1 --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

This command is bounded by counts, but it is not a fast smoke check: `--run-worker`
can start a real `evaluate_spec()` calculation. Use the fast wiring smoke below before
operator runs; use this command only when intentionally spending compute.

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

Build the main-compatible paper runtime queue:

```bash
python -m scripts.strategy_lab.main_paper_runtime_adapter --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Observe that queue on public candles:

```bash
python -m scripts.strategy_lab.main_paper_runtime --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --limit 50 --apply
```

Fast wiring smoke for farm -> paper-watch -> main instruction -> runtime observation ->
paper Telegram preview:

```bash
python -m scripts.strategy_lab.farm_loop --once --apply --provider synthetic --no-discovery-refresh --max-plan-events 0 --max-prepares 0 --max-enrich 0 --max-sweeps 0 --max-worker-jobs 0 --max-paper-cards 0 --max-followups 0 --no-followups --true-forward-max-candidates 0 --run-paper-signals --paper-signals-max-new 0 --paper-signals-max-pfr-scan 0 --paper-signals-max-observe 0 --paper-signals-fetch-timeout 1 --main-paper-runtime-limit 0 --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

This is intentionally not a full farm run. The warning about worker/validation/paper being
off is expected; the smoke exists to prove the derived paper surfaces quickly before a long
visible loop. With `--main-paper-runtime-limit 0`, it does not overwrite the latest real
`main_paper_runtime_observation` artifact.

Export terminal paper-watch outcomes into training-friendly rows:

```bash
python -m scripts.strategy_lab.paper_signal_training_export --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Rebuild the Excel journal with UTF-8 output on Windows:

```bash
python -X utf8 scripts\build_journal.py
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

1. Main live executor: the rebuildable `main_paper_instructions`, `main_paper_consumed`,
   `main_paper_runtime_queue`, and `main_paper_runtime_observation` views exist, but the
   old main scanner/runtime still must not execute them. Any future live/paper executor
   must be reviewed separately and must not import farm/PFR data into the old `main.py`.
2. Telegram paper channel: audit text, chart rendering, and notification routing before
   enabling paper-signal alerts. The offline text preview now exists; real delivery is
   still opt-in and should stay separate.
3. Journal modernization: the paper-signal training export now exists; next work is to
   extend dashboard/charts over the new `Paper Watch` sheet. The raw paper-watch sheet is
   now present; private account fills remain opt-in.
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

Result at the time of this audit: bounded apply completed in paper/research mode. Later
follow-up clarified the terminology: with `--run-worker`, this command is a bounded
compute cycle, not a guaranteed fast smoke. It may start a real strategy sweep through
`worker_once`; the fast wiring smoke below is the quick preflight.

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
- `main_paper_runtime_queue.queued = accepted paper rows`, `execution_allowed = false`
- `main_paper_runtime_observation.rows_read > 0` after the same farm-loop cycle or a
  standalone observer rebuild, with
  `execution_allowed = false`
- `paper_telegram_preview.rendered = 20`, `invalid = 0`, `sends_network = false` after
  preview build
- fast wiring smoke completed in about 2 seconds with `main_paper_bridge.instructions = 54`,
  `main_paper_consumer.accepted = 54`, runtime queue and observation stages built,
  `paper_telegram_preview.rendered = 20`, and no send path
- `main_bridge.orders_enabled_by_bridge = false`
- `readiness.main_runtime_consumer = planned`
- `readiness.main_instruction_view_available = pass` after bridge export
- `readiness.main_paper_consumer_available = pass` after consumer audit
- `readiness.main_paper_runtime_queue_available = pass` after queue build
- `readiness.main_paper_runtime_observation_available = pass` after the farm-loop
  observation stage or standalone observer rebuild
- `readiness.paper_runtime_observed = pass` when the observer has no invalid/provider errors
- `readiness.paper_telegram_preview_available = pass` after preview build
- `paper_signal_training_export.rows = terminal paper-watch outcomes` after export
- `scripts/build_journal.py` rebuilt `scripts/journal.xlsx` with a `Paper Watch` sheet
  over the current private export (`609` rows on the checked machine); private OKX fills
  stayed skipped because `JOURNAL_ENABLE_PRIVATE_FILLS` was not enabled

Targeted tests:

- `tests/test_pfr_bridge.py tests/test_paper_signals.py tests/test_operational_health.py tests/test_build_journal_safety.py`: 87 passed.
- `tests/test_farm_loop_integration.py tests/test_paper_runtime.py tests/test_paper_contract.py`: 65 passed, 1 pre-existing CUDA warning.
- `tests/test_main_paper_bridge.py tests/test_main_paper_runtime_adapter.py tests/test_main_paper_runtime.py tests/test_operational_health.py`: passed.
- `tests/test_build_journal_safety.py`: passed after the `Paper Watch` sheet addition.
- `git diff --check`: clean.

## Verification On 2026-06-27

Current commands re-run:

```bash
python -m scripts.strategy_lab.paper_signal_training_export --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --json
python -m scripts.strategy_lab.operational_health --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
python -m scripts.strategy_lab.status --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
python -m scripts.strategy_lab.farm_status_report --json
python -X utf8 scripts\build_journal.py
```

Observed current state:

- `operational_health` exits 0 and reports `mode=paper_research_only`,
  `auto_trade=False`, scanner LLM provider `alibaba`, scanner Telegram configured,
  paper Telegram not configured.
- `main_bridge.status = paper_runtime_observed`.
- Paper chain counts: `instructions=53`, `accepted=53`, `rejected=0`,
  `queued=50`, `invalid_queue=0`, `observed=5`, `reviewed=5`,
  `runtime_errors=0`, `preview=20`, `invalid_preview=0`.
- `main_runtime_consumer = planned`: old live main is intentionally not the executor.
- `paper_signals.total = 652`, `armed = 41`, `opened_paper = 12`, `reviewed = 599`.
- `pfr_bridge.records_loaded = 53`, `passed_quality = 43`, `rejected_quality = 10`,
  `unique_setups = 11`, `risk_too_wide = 26`.
- `main_paper_bridge.instructions = 53`, `execution_allowed = false`.
- `main_paper_consumer.accepted = 53`, `rejected = 0`.
- `main_paper_runtime_queue.queued = 50`, `invalid = 0`, `execution_allowed = false`.
- `main_paper_runtime_observation.rows_read = 5`, `observed = 5`, `reviewed = 5`,
  `pending = 0`, `provider_error = 0`, `execution_allowed = false`.
- `paper_telegram_preview.rendered = 20`, `invalid = 0`, `sends_network = false`.
- `paper_signal_training_export.rows = 599`, `terminal_only = true`,
  `paper_only = true`.
- `scripts/journal.xlsx` rebuild exits 0; `Paper Watch` exists with 610 rows including
  the header. Private OKX fills stay skipped unless `JOURNAL_ENABLE_PRIVATE_FILLS=1` is
  explicitly set.

Current conclusion: the full operator chain is alive as a paper/research lifecycle:
farm/PFR -> paper signals -> main-readable paper instructions -> contract consumer ->
paper runtime queue -> public-candle observation -> Telegram preview -> training export ->
Excel journal. The remaining product gap is deliberate: old `main.py` must still not
execute farm/PFR instructions until a separate executor contract is reviewed and tested.
