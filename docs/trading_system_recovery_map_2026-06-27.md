# Trading System Recovery Map - 2026-06-27

Status: **active recovery map, paper/research only**.

Purpose: make the current trading stack understandable after the farm/PFR/paper work.
This document answers one operational question: what is alive, what is isolated, and
what still must be audited before the old product runtime can be used again.

## Current Canonical Loop

The working paper/research loop is:

```text
public OKX discovery / scanner-watch context
  -> scripts.strategy_lab.farm_loop
  -> farm task DB and strategy_lab compute queue
  -> worker / validation / paper readiness
  -> paper_signals live/PFR watch lane
  -> main_paper_bridge instruction view
  -> main_paper_consumer contract audit
  -> main_paper_runtime_adapter watch_paper queue
  -> main_paper_runtime public-candle observer
  -> paper_telegram_preview offline cards
  -> paper_signal_training export
  -> scripts/journal.xlsx Paper Watch sheet
```

The visible operator entry point is:

```bat
bat\strategy_lab_control_room.bat
```

The one-window farm loop is:

```bat
bat\strategy_lab_farm_full_cycle_loop.bat
```

That wrapper passes `--paper-signals-max-observe` (default 20 via
`STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE`) so active paper cards mature in bounded
batches instead of making one visible cycle walk the whole backlog.

The preflight gate is:

```bash
python -m scripts.strategy_lab.operational_health --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
```

Required operator facts:

- `mode = paper_research_only`
- `AUTO_TRADE = false`
- `canonical_launch_surface = pass`
- `legacy_live_runtime_isolated = pass`
- `legacy_loop_guards = pass`
- `telegram_delivery_ownership = pass`
- `telegram_analyzer_execution_boundary = pass`
- `ready_for_visible_paper_research_loop = pass` before a long unattended run

## Main Engine Boundary

The old `main.py` is not dead code, but it is not the current paper executor.
It is a live/demo order-capable runtime:

- imports the authenticated OKX client;
- can set leverage;
- can call `place_market_order`;
- can send Telegram trade messages;
- reads the old config/`.env` money path through `Config.load()`.

Therefore it must not consume farm/PFR paper instructions directly. The current safe
replacement path for observation is:

```text
src.research_lab.main_paper_runtime
```

`operational_health` exposes this as `main_engine_boundary.v1`:

- `order_capable = true`
- `sets_leverage = true`
- `imports_private_okx_client = true`
- `consumes_farm_tasks_db = false`
- `consumes_strategy_lab_db = false`
- `consumes_main_paper_queue = false`
- `safe_to_use_as_paper_executor = false`

This is intentional. A future product executor must be a separate reviewed contract,
not an import of farm/PFR data into old `main.py`.

## Data Priority

When `farm_loop --run-paper-signals` runs, paper signal selection priority is:

1. live mover universe ranked by outcome memory;
2. active paper-signal store lifecycle and dedup;
3. bounded PFR DB seeding, only when `--pfr-db-path` is explicit;
4. main-readable paper instruction export;
5. contract consumer audit;
6. runtime queue;
7. public-candle paper observation;
8. offline Telegram preview.

This means PFR is a validated seed source, not the only source and not a live order
authority.

## Telegram And LLM

Telegram is split into surfaces:

- paper Telegram preview: offline card rendering, no network send by default;
- scanner/news Telegram: operator context surface;
- Telegram analyzer bot: separate product/analyzer surface, not the farm runner;
- legacy scanner: diagnostic/history only.

LLM is also split:

- `src.utils.llm_client`: scanner/advisory provider router (`alibaba`/`yandex`);
- `src.utils.llm_formatter`: older Yandex-only chart/text formatter for the Telegram
  analyzer; it does not follow `LLM_PROVIDER`;
- `src.research_lab.llm_provider`: Strategy Lab proposal gate, disabled by default.

None of these paths can promote a setup, bypass validation, or enable execution.

Important legacy boundary: `scripts.telegram_bot` still imports
`scripts.auto_execute` inside the old scanner loop. `scripts.auto_execute` is guarded by
`AUTO_TRADE`, but it can set leverage and place OKX orders when that flag is enabled.
Therefore `start.bat` is execution-adjacent and must not be used as the Strategy Lab
paper/PFR launcher. The current paper alert path remains `paper_telegram_preview`,
which writes offline artifacts first.

Provider boundary: a green Alibaba scanner/advisory path is not proof that the Telegram
chart analyzer is using Alibaba. The analyzer calls `generate_client_text`,
`generate_premium_analysis`, and `generate_edu_text` in `llm_formatter`, so it needs a
separate prompt/provider audit before product Telegram delivery is revived.

Manual product boundary: `scripts.analyze_chart` is a report/snapshot/chart generator
with optional `--send-telegram`; it is off by default. `scripts.run_latest_analysis` is
interactive and can lazy-import `scripts.auto_execute` after an ENTRY result only when
`AUTO_TRADE` is enabled and `RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE=1` is also set.
These tools are useful for manual product review, but they are not the farm/PFR paper
runtime and must stay outside the canonical launch path until their prompts, provider,
Telegram text, and execution hook are reviewed.

Detailed product-surface audit:
[`product_analyzer_revival_audit_2026-06-27.md`](product_analyzer_revival_audit_2026-06-27.md).

## Journal And Training Data

The current training/reporting path is:

```text
paper_signals terminal rows
  -> paper_signal_training.jsonl/json
  -> scripts/build_journal.py
  -> scripts/journal.xlsx / Paper Watch
```

The operator proof is machine-checkable through `operational_health`:

- `training_data.paper_signal_training.rows > 0`;
- `training_data.paper_signal_training.schema_rows == rows`;
- `training_data.paper_signal_training.invalid_json == 0`;
- `training_data.paper_signal_training.paper_only_false == 0`;
- `training_data.paper_signal_training_freshness.stale_vs_source == false`;
- readiness gate `paper_signal_training_export = pass`.

The local Excel workbook is an operator surface, not a source of truth. Current
`scripts/build_journal.py` rebuilds a `Paper Watch` sheet from the private
`paper_signal_training.jsonl` export and includes family, result, diagnosis, and net
summary blocks. The canonical farm loop refreshes the private training export after
`--run-paper-signals`; Excel is still rebuilt explicitly so a workbook lock cannot
stall the long-running loop. The canonical data remains the private JSONL/snapshot export.

Private OKX fills remain opt-in only:

```text
JOURNAL_ENABLE_PRIVATE_FILLS=1
```

Default journal rebuild must not call private account endpoints.

## Do Not Use As Canonical Farm/Paper Entry Points

These paths may remain for history, diagnostics, or separate product surfaces, but they
are not the canonical farm/PFR/paper loop:

- `main.py`
- `start.bat`
- `start_all.bat`
- `bat\strategy_lab_start.bat`
- `scripts/ws/ws_scanner.py`
- `scripts/strategy_lab/scanner_farm_loop.py`
- `scripts/strategy_lab/universe_farm_loop.py`

`scanner_farm_loop.py` and `universe_farm_loop.py` are protected by explicit
`ARCHIVE-LEGACY` abort guards and require `--i-understand-legacy` to run. The
canonical replacement is `scripts.strategy_lab.farm_loop`.

Do not delete legacy paths until imports, docs, and tests prove retirement is safe.

## Remaining Recovery Work

1. Audit the old Telegram analyzer and chart formatter prompts, especially the
   `src.utils.llm_formatter` Yandex path and any operator-facing text.
2. Decide whether paper alerts should become a real opt-in sender after preview text and
   charts are reviewed.
3. Add higher-level Excel dashboard/charts over `Paper Watch` so outcomes, diagnoses,
   and family performance are visible without opening the raw sheet. The raw sheet and
   basic summaries already exist.
4. Design a future product executor contract only after paper-forward outcomes justify
   it. The executor must start from `SignalContract`/paper queue semantics, not from the
   old live `main.py` internals.
5. Retire legacy launchers only after machine checks and docs no longer reference them.

The project is back to a coherent paper/research loop. It is not yet a restored live or
demo-money trading product.
