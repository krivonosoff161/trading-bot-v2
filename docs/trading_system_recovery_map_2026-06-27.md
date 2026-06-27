# Trading System Recovery Map - 2026-06-27

Status: **active recovery map, paper/research only**.

Purpose: make the current trading stack understandable after the farm/PFR/paper work.
This document answers one operational question: what is alive, what is isolated, and
what still must be audited before the old product runtime can be used again.

## Latest Verified State

Verified with:

```bash
python -m scripts.strategy_lab.operational_health --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --fail-on-blocked
```

Current result:

- `ready_for_visible_paper_research_loop = pass`
- `operator_next_actions.launch_blocked = false`
- `operator_next_actions.blocking = []`
- `operator_next_actions.operator_configuration = [paper_telegram_surface]`
- `operator_next_actions.intentional_boundaries = [main_runtime_consumer, manual_product_analyzer_boundary]`
- `operator_next_actions.rebuild_actions = []`
- `paper_chain_counts = pass`
- `paper_runtime_observed = pass`
- `paper_main_runtime_current = pass`
- `training_data_exports = pass`
- `paper_signal_training_export = pass`
- `journal_rebuild_available = pass`
- `AUTO_TRADE = false`
- `execution_allowed = false`

Current artifact counts:

| Artifact | Current value | Required invariant |
|---|---:|---|
| `paper_signals.jsonl` rows | 5388 | JSONL readable, private-root derived artifact |
| `paper_signals.by.source` | `farm: 5388` | PFR remains explicit and bounded, not execution authority |
| `paper_signals.by.status` | `armed: 2989`, `opened_paper: 1747`, `reviewed: 652` | watch lifecycle is populated |
| `main_paper_instructions` | 10 | active paper rows exported into main-readable instructions |
| `main_paper_consumed.accepted` | 10 | consumer rejects 0 invalid rows |
| `main_paper_runtime_queue.queued` | 10 | all queued as `watch_paper` |
| `main_paper_runtime_observation.observed` | 10 | invalid/provider errors are 0 |
| `paper_telegram_preview.rendered` | 10 | invalid preview rows are 0 |
| `paper_telegram_delivery` | 10 dry-run rows | network send remains off |
| `paper_signal_training.jsonl` rows | 652 | schema-valid, `paper_only=true`, current vs source |
| `scripts/journal.xlsx` | current vs training export | workbook is an operator surface, not source of truth |

This is the current operator proof that the project is back to a coherent
paper/research cycle. It is not a claim of trading edge, unattended Telegram delivery,
or live/demo-money execution readiness.

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
  -> paper_telegram_sender dry-run delivery audit
  -> paper_signal_training export
  -> optional scripts/build_journal.py rebuild
  -> scripts/journal.xlsx Paper Watch sheet
```

The visible operator entry point is:

```bat
bat\strategy_lab_control_room.bat
```

The visible status monitor should use the fast status report:

```bash
python -m scripts.strategy_lab.farm_status_report --fast
```

Run the full report without `--fast` only for manual audit/drilldown. It can rebuild
heavier derived research views from the private artifact tree and is intentionally not
the default monitor command.

The one-window farm loop is:

```bat
bat\strategy_lab_farm_full_cycle_loop.bat
```

That wrapper passes `--paper-signals-max-observe` (default 20 via
`STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE`) so active paper cards mature in bounded
batches instead of making one visible cycle walk the whole backlog.
It also passes `--paper-signals-pfr-reserved` (default 2 via
`STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED`) so live-mover generation cannot starve
already validated farm/PFR candidates.

## Paper Source Priority

The main-paper path does not read directly from old `main.py`. It receives a derived,
paper-only instruction view from Strategy Lab artifacts. Priority is:

1. `source=farm` live-mover paper signals are generated first from the outcome-memory
   ranked mover universe.
2. `source=pfr_farm` signals are generated second, only when `--pfr-db-path` is
   explicitly provided. The PFR lane is bounded (`max_pfr_scan` default 30), can reserve
   slots, and shares dedup/setup-id guards with the live-mover lane.
3. `main_paper_bridge` exports only active paper rows (`armed`, `opened_paper`) into
   `MainPaperInstruction.v1`.
4. `main_paper_runtime_adapter` sorts accepted paper rows by family, timeframe, risk,
   symbol, and source signal id before the public-candle observer watches them.

`operational_health` exposes this as `paper_priority_policy.v1`. The invariant is
`execution_allowed=false` and `old_main_py_consumer=false`.

The preflight gate is:

```bash
python -m scripts.strategy_lab.operational_health --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --fail-on-blocked
```

Both visible wrappers run this blocked-only preflight before opening/starting work.
Warnings and planned boundaries remain visible operator information. A `blocked`
readiness gate stops the launch and must be fixed before the long paper/research cycle.
The same report now exposes `operator_next_actions.v1`, a derived checklist that
classifies the visible gates without changing their meaning:

- `blocking` means the launch must not start;
- `operator_configuration` means an external operator setting/review is missing,
  for example `PAPER_CHAT_ID`;
- `intentional_boundaries` means a safety boundary is deliberately kept, for example
  old `main.py` isolation;
- `rebuild_actions` means a generated artifact is missing or stale and should be
  rebuilt before trusting the operator picture.

The normal visible paper/research state is `operator_next_actions.launch_blocked=false`.
This does not imply live trading readiness and does not prove a trading edge.

Required operator facts:

- `mode = paper_research_only`
- `AUTO_TRADE = false`
- `canonical_launch_surface = pass`
- `legacy_live_runtime_isolated = pass`
- `legacy_loop_guards = pass`
- `telegram_delivery_ownership = pass`
- `telegram_analyzer_execution_boundary = pass`
- `paper_main_runtime_current = pass`
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

`operational_health` also exposes `paper_main_runtime_current`. That is the positive
counterpart to the old-main boundary: it passes only when the current paper-only
main-compatible runtime (`src.research_lab.main_paper_runtime`) has produced a clean
observation artifact. `main_runtime_consumer = planned` is therefore not a missing
paper-chain step; it is the explicit statement that old live `main.py` remains
outside the farm/PFR paper path.

## Data Priority

When `farm_loop --run-paper-signals` runs, paper signal selection priority is:

1. live mover universe ranked by outcome memory;
2. active paper-signal store lifecycle and dedup;
3. bounded PFR DB seeding, only when `--pfr-db-path` is explicit; in the visible
   wrapper 2 of the new-signal slots are reserved for PFR before live movers can
   fill the full cycle cap;
4. main-readable paper instruction export;
5. contract consumer audit;
6. runtime queue;
7. public-candle paper observation;
8. offline Telegram preview.

This means PFR is a validated seed source, not the only source and not a live order
authority.

`operational_health` exposes the current mix as `paper_source_composition.v1`:

- `paper_signals.by.source` shows whether rows came from the default live/paper lane
  (`farm`) or optional validated farm seed lane (`pfr_farm`);
- `paper_signals.by.setup_family` and `.by.timeframe` show what the current paper
  watch population is actually made of;
- `main_runtime_queue.by.runtime_action` must remain `watch_paper`;
- `priority_min` / `priority_max` on the queue show the deterministic adapter priority
  range after consumer validation;
- `pfr_activation.requires_explicit_db_path = true`;
- `execution_allowed = false`.

This is the operator-facing proof that the paper runtime is fed by the intended
source lanes and that old live `main.py` still has no ownership of the queue.

## Telegram And LLM

Telegram is split into surfaces:

- paper Telegram preview: offline card rendering, no network send by default;
- paper Telegram delivery audit: dry-run over preview cards during the canonical
  farm cycle; network delivery still requires the separate explicit `--send` command;
- scanner/news Telegram: operator context surface;
- Telegram analyzer bot: separate product/analyzer surface, not the farm runner;
- legacy scanner: diagnostic/history only.

LLM is also split:

- `src.utils.llm_client`: scanner/advisory provider router (`alibaba`/`yandex`);
- `src.utils.llm_formatter`: older chart/text formatter for the Telegram analyzer.
  Its default path is Yandex-only, but text-card generation can opt in to the shared
  `LLM_PROVIDER` router with `PRODUCT_ANALYZER_LLM_ROUTER=llm_client`. The product
  launchers (`start.bat` and `bat/start_telegram_bot.bat`) now set that opt-in by
  default when the variable is absent;
- `src.research_lab.llm_provider`: Strategy Lab proposal gate, disabled by default.

None of these paths can promote a setup, bypass validation, or enable execution.

Important legacy boundary: `scripts.telegram_bot` can still import
`scripts.auto_execute` inside the old scanner loop, but only after the explicit legacy
opt-in `TELEGRAM_BOT_ALLOW_AUTO_EXECUTE=1`; `AUTO_TRADE` is then checked as the second
guard. `scripts.auto_execute` can set leverage and place OKX orders when enabled, so
`start.bat` is still execution-adjacent and must not be used as the Strategy Lab
paper/PFR launcher. The current paper alert path remains `paper_telegram_preview`
followed by a dry-run `paper_telegram_sender` audit over the preview artifacts.
This keeps delivery status current without sending Telegram messages.

Provider boundary: a green Alibaba scanner/advisory path is not proof that every
Telegram analyzer feature is using Alibaba. Text-only `generate_client_text` and
`generate_edu_text` use the shared router under the product launchers; premium
vision still calls the legacy formatter path and needs a separate provider/prompt
audit before product Telegram delivery is treated as fully revived.

Text boundary: `product_analyzer_prompt_integrity = pass` covers the core chart prompt,
not every legacy operator-facing string. `operational_health` also exposes
`legacy_product_text_quality`; it must remain `pass`. A warning there would mean old
product/Telegram files contain mojibake markers. That warning would not invalidate the
farm/PFR paper loop, but it would block treating `start.bat` / `scripts.telegram_bot` as
a polished product surface.

Manual product boundary: `scripts.analyze_chart` is a report/snapshot/chart generator
with optional `--send-telegram`; it is off by default. `scripts.run_latest_analysis` is
interactive and can lazy-import `scripts.auto_execute` after an ENTRY result only when
`AUTO_TRADE` is enabled and `RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE=1` is also set.
These tools are useful for manual product review, but they are not the farm/PFR paper
runtime and must stay outside the canonical launch path until their prompts, provider,
Telegram text, and execution hook are reviewed.

Machine launch boundary: `operational_health` exposes
`product_analyzer_launch_contract.v1`. It must show that `start.bat` is not current for
farm/PFR, Telegram bot `main()` does not start the legacy `_scanner_loop`,
`analyze_chart` does not send Telegram by default, `run_latest_analysis` gates old
`auto_execute` behind `RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE`, the product stack is not
used by the farm/PFR runtime, old `main.py` does not consume the paper queue, and
`execution_allowed=false`. This is a launch-path isolation proof, not a claim that old
product Telegram delivery is ready for unattended operation.

Detailed product-surface audit:
[`product_analyzer_revival_audit_2026-06-27.md`](product_analyzer_revival_audit_2026-06-27.md).

Latest bounded farm/PFR/paper smoke:
[`farm_paper_cycle_smoke_2026-06-27.md`](farm_paper_cycle_smoke_2026-06-27.md).

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
- `training_data.excel_journal_freshness.stale_vs_source == false` when the local
  workbook is expected to be current;
- readiness gate `paper_signal_training_export = pass`.
- readiness gate `journal_rebuild_available = pass` only when `scripts/journal.xlsx`
  exists and is not older than `paper_signal_training.jsonl`.

The local Excel workbook is an operator surface, not a source of truth. Current
`scripts/build_journal.py` rebuilds a `Paper Watch` sheet from the private
`paper_signal_training.jsonl` export and includes family, result, diagnosis, and net
summary blocks. The canonical farm loop refreshes the private training export after
`--run-paper-signals`; Excel is still rebuilt explicitly so a workbook lock cannot
stall the long-running loop. `operational_health` now reports Excel freshness against
the training JSONL so a stale workbook is visible instead of silently passing as current.
The canonical data remains the private JSONL/snapshot export.

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
