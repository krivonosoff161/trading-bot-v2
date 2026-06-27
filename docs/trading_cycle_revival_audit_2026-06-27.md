# Trading Cycle Revival Audit (2026-06-27)

This note captures the current operator truth for reviving the trading project as
a paper/research lifecycle. It is intentionally conservative: it documents what
is connected now, what is deliberately isolated, and what must be audited before
the old product surfaces are revived.

## Current Canonical Cycle

The canonical paper/research owner is:

```text
scripts.strategy_lab.farm_loop --run-paper-signals
```

Current data priority:

1. Live mover universe ranked by outcome memory.
2. Paper-signal active store with dedup and lifecycle.
3. Bounded PFR database seeding after live movers.
4. `main_paper_bridge` exports active paper signals as a main-readable
   `SignalContract` view.
5. `main_paper_consumer` validates that view into an audit record.
6. `main_paper_runtime_adapter` builds a bounded `watch_paper` queue.
7. `main_paper_runtime` observes the queue on public candles.
8. `paper_telegram_preview` renders offline operator cards.
9. `paper_signal_training_export` exports current paper-only rows for future learning.
   `farm_loop --run-paper-signals` refreshes this private derived artifact in-cycle;
   Excel/journal rebuild remains an explicit operator step.

The old `main.py` is not in this chain.

## Verified Status

`operational_health` with the private Strategy Lab root reports:

- `ready_for_visible_paper_research_loop = pass`
- `paper_chain_counts = pass`
- `paper_runtime_observed = pass`
- `paper_signal_training_export = pass`
- PFR DB present
- non-empty main-readable paper instructions and accepted consumer rows, 0 rejected
- non-empty runtime queue rows, 0 invalid
- runtime observer ran with 0 invalid/provider errors
- current `PaperSignalTrainingRow.v1` rows, all paper-only, not stale against
  `paper_signals.jsonl`

Latest bounded preflight example after the training-export wiring fix:
20 instructions, 20 accepted consumer rows, 20 queued runtime rows, 1 observed runtime
row, 20 offline Telegram previews, and 642 current training rows.

The visible control room now runs this fast health check before opening the farm,
dashboard, graph, and status windows.

## Safety Boundary

The current paper/research cycle is not a live executor:

- no `.env` mutation;
- no `AUTO_TRADE` enablement;
- no private account/order endpoint use;
- no direct Telegram send by default;
- old `main.py` remains isolated from farm/PFR paper instructions.

The recursive farm guard covers `src/research_lab` and the main-paper wrapper
scripts. These wrappers must not import the old exchange client, auto-execute,
Telegram sender, config runtime, or live main path.

## Legacy Product Surfaces

These surfaces still exist and must not be treated as the canonical paper/PFR
runtime:

- `main.py` is order-capable, sets leverage, imports the private OKX client, and
  sends Telegram messages.
- `scripts.run_latest_analysis` is an interactive product wrapper and can reach
  `scripts.auto_execute` only under the `AUTO_TRADE` guard plus explicit
  `RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE=1` manual opt-in.
- `scripts.telegram_bot` is the old Telegram analyzer surface and can reach
  `scripts.auto_execute` only when `TELEGRAM_BOT_ALLOW_AUTO_EXECUTE=1` is set and the
  old `AUTO_TRADE` guard is also true.
- `scripts.analyze_chart` reads OKX credentials through `OKXClient`, writes local
  report/snapshot/chart artifacts, and can optionally send Telegram.
- `src.utils.llm_client` supports the scanner Alibaba/Yandex router through
  `LLM_PROVIDER`.
- `src.utils.llm_formatter` is a separate Yandex-only formatter path used by the
  chart analyzer.

These are not runtime failures. They are boundaries that must be audited before
product delivery is revived.

## Required Next Audit Before Product Revival

Before connecting Telegram/product analysis back into the operator workflow:

1. Keep `product_analyzer_prompt_integrity = pass`; the legacy formatter prompt must
   remain UTF-8 readable and free of mojibake markers.
2. Decide whether `llm_formatter` remains Yandex-only or becomes an adapter over the
   shared provider router.
3. Audit Telegram text and chart payloads for paper-only wording, risk language,
   price/SL/TP clarity, and no execution claims.
4. Keep the `run_latest_analysis` double gate intact: `AUTO_TRADE` alone must not import
   or call `scripts.auto_execute`; explicit `RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE=1`
   is required for manual execution tests.
5. Keep the Telegram analyzer double gate intact: `AUTO_TRADE` alone must not import or
   call `scripts.auto_execute`; explicit `TELEGRAM_BOT_ALLOW_AUTO_EXECUTE=1` is required
   for legacy Telegram execution tests.
6. Decide whether paper alerts use only `PAPER_CHAT_ID`; they must not fall back
   to scanner/default chats.
7. Keep the main paper runtime as observer/journal authority until a separate
   reviewed executor contract exists.

## Operator Commands

Fast health:

```bash
python -m scripts.strategy_lab.operational_health --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
```

Visible launch:

```bash
bat\strategy_lab_control_room.bat
```

Visible status monitor:

```bash
python -m scripts.strategy_lab.farm_status_report --fast
```

The full `farm_status_report` remains the manual audit/drilldown command and is not
intended for a tight visible monitor loop on a large private artifact tree.

Bounded dry-run smoke:

```bash
python -X utf8 -m scripts.strategy_lab.farm_loop --once --dry-run --run-worker --run-validation --run-paper --run-paper-signals --enrich-funding --enrich-oi --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --paper-signals-max-observe 20 --paper-signals-max-pfr-scan 5 --paper-signals-fetch-timeout 3 --main-paper-runtime-limit 5 --max-plan-events 5 --max-prepares 1 --max-enrich 1 --max-sweeps 1 --max-worker-jobs 1 --max-paper-cards 3 --data-days 7 --provider okx-public --backend auto
```

Bounded apply smoke actually run on public OKX after the launch audit:

```bash
python -X utf8 -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --run-paper-signals --enrich-funding --enrich-oi --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --paper-signals-max-observe 0 --paper-signals-max-pfr-scan 1 --paper-signals-fetch-timeout 3 --main-paper-runtime-limit 1 --max-plan-events 1 --max-prepares 1 --max-enrich 1 --max-sweeps 1 --max-worker-jobs 1 --max-paper-cards 1 --max-followups 1 --data-days 7 --provider okx-public --backend auto
```

Result: exit code 0 in 47 seconds, `main_paper_bridge instructions=20`,
`main_paper_consumer accepted=20 rejected=0`, `main_paper_runtime_queue queued=20`,
`main_paper_runtime_observation observed=1`, and `paper_telegram_preview rendered=20`.
This also verified the reason for the earlier long-running smoke: active paper-signal
observation must be capped (`--paper-signals-max-observe`) in operator loops.
The cycle now also refreshes `paper_signal_training_export`, so the private training
JSONL cannot silently lag behind the current paper-signal store.

## Non-Claims

This does not claim a profitable strategy, live readiness, or a safe production
executor. It claims only that the current farm/PFR/paper/main-paper observation
cycle is assembled, bounded, observable, and guarded from the old money path.
