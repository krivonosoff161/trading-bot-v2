# TASK / HANDOFF FOR CLAUDE AND CODEX

Updated: 2026-07-05

This file is the local handoff channel between agents in VS Code.
It is not the canonical architecture document.

## Current State

Current center: the calculation farm plus the validator-backed main-paper watcher,
not the scanner and not old live `main.py`.

New 2026-07-05 context: the first outcome-learning loop slice is in place. Terminal
paper outcomes can be routed into deterministic `OutcomeLearningCase.v1` records,
reviewed by the bounded `outcome_reviewer`, and linked back into later
`TrainingRow.v2` exports through `outcome_review_id` and learning labels. This is
advisory paper/research only; it is not a promotion gate and not live execution.

Read first:

- `CURRENT_STATE.md`
- `ARCHITECTURE.md`
- `ROADMAP.md`
- `docs/farm_runbook.md`
- `docs/session_handoff_2026-07-03.md`
- `docs/outcome_learning_loop_2026-07-05.md`

Current verified runtime:

- branch/head: `feature/calc-farm`, `7bbc65c feat: harden farm loop observability`
- canonical loop: running as `python pid=18900`
- mode: `paper_only=true`
- execution: `execution_allowed=false`
- `AUTO_TRADE=false`
- old `main.py`: isolated
- health: no blocking gates, `ready_for_visible_paper_research_loop=pass`

Canonical flow:

```text
farm_loop
  -> farm_coordinator over state/farm_tasks.sqlite
  -> data prepare / funding / OI
  -> run_sweep compute queue in state/strategy_lab.sqlite
  -> classify into unique_candidates
  -> hard validation from unique_candidates
  -> stamp-back into farm_results + unique_candidates
  -> setup_library cards
  -> paper_signals / PFR bridge
  -> main_paper_bridge
  -> main_paper_consumer
  -> main_adaptive_policy
  -> main_paper_runtime_queue
  -> main_paper_runtime_observation
  -> main_paper_trade_ledger
  -> paper_telegram_preview / paper_telegram_delivery audit
  -> paper_signal_training_export + journal
  -> OutcomeLearningCase.v1
  -> outcome_reviewer advisory JSON
  -> outcome_review_id backlink in TrainingRow.v2
```

This file is local handoff context, not the canonical architecture. For current
truth read `CURRENT_STATE.md`, `ARCHITECTURE.md`, `README.md`,
`docs/farm_loop_lifecycle.md`, `docs/farm_runbook.md`,
`docs/farm_ownership_map.md`, `docs/paper_runtime_design.md`, and
`docs/session_handoff_2026-07-03.md`.

The scanner is still active as an upstream intake source, but it is not the
operational center. Old `universe_farm_loop` / `scanner_farm_loop` paths are
legacy unless a task explicitly asks to inspect them.

## Critical Product Gap

The user expected a main-style paper executor:

```text
"what if we opened this?"
  -> main computes/records pseudo-position
  -> human Telegram card
  -> later outcome/training row
```

The current system is stricter:

```text
validator/PFR-backed paper rows only
  -> watch_paper queue
  -> public-candle observation
  -> ledger/training
```

Broad farm paper signals are retained as research/training data and do **not** become
subscriber/main cards unless they have a validator-backed `ready_strategy_id`.

Next product build target, if the user continues this thread:

- design and implement a separate reviewed `main_paper_executor` contract;
- keep `execution_allowed=false`;
- use shared deterministic trade math for entry/SL/TP/RR/risk/outcome;
- allow LLM only as bounded advisor/explainer, not price/permission authority;
- write pseudo-trade lifecycle and outcome rows;
- render human-readable subscriber cards;
- do **not** wire farm outputs directly into old live/order-capable `main.py`.

Next learning-loop build target:

- convert accepted outcome-review suggestions into bounded follow-up/retest plans;
- reuse `feedback_followup.py`, `setup_outcome_memory.py`, `shadow_forward.py`, and
  `true_forward.py`;
- do not create a second farm brain, second memory system, or LLM-controlled
  promotion path.

## Active Safety Boundary

- Do not touch `.env`, `AUTO_TRADE`, live order execution, private exchange
  endpoints, Telegram credentials, or the old main trading engine.
- New farm/paper modules must stay paper/research only and pass the existing
  AST boundary test.
- Public OKX market data, public funding/OI, local prepared candles, and local
  private-root artifacts are allowed.

## Current Runtime Focus

1. Keep `farm_loop` as the brain and `farm_tasks.sqlite.unique_candidates` as the
   canonical source for validation handoff.
2. Keep hard-validation identity fingerprint-level, not raw `candidate_id`.
3. Keep `setup_library` as the only feed into `paper_loop`.
4. Record paper outcomes both as JSONL and in `strategy_lab.sqlite::paper_outcomes`.
5. Use status tools (`farm_status_report`, `status`, `morning_report`) to show
   hard validation and paper handoff state.

## Useful Commands

```bash
python -m scripts.strategy_lab.farm_loop --once --dry-run
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --enrich-funding --enrich-oi
python -m scripts.strategy_lab.farm_status_report
python -m scripts.strategy_lab.paper_loop --once --dry-run
python -m scripts.strategy_lab.status
python -m scripts.strategy_lab.operational_health --private-root C:\Users\krivo\github_projects\trading-bot-research\strategy-lab --pfr-db-path C:\Users\krivo\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite --json
```

## Next Design Work

- reviewed `main_paper_executor` for main-style paper pseudo-trades and readable cards;
- validator/PFR-to-main strategy catalog semantics;
- clearer Telegram subscriber card ownership;
- LLM role routing for scanner/farm/validator/main-product surfaces;
- richer paper promotion/demotion criteria after the executor emits reliable outcomes.

## Historical Scanner Handoff Below

Committed baseline before this handoff:

- `d6116d7` - honest Stage 0 metrics in `resolve_outcomes.py`
- `06e5294` - OKX perp instruments for L3 metals / L4 oil baselines
- `3790d87` - strong cross-layer asset matches in router
- `e43040c` - HYPE and WLD coverage
- `1ccbb0b` - propagate `cross_layer` through buffer pipeline

Do not build an execution-oriented `main_event_engine` yet.

## What Stage 0 Proved

Stage 0 is complete enough for the current decision.

Key result:

```text
90 scanner cards
19 mature outcomes
17 NO_GO + 2 WATCH
0 GO
```

Corrected metrics:

```text
NO_GO volatility missed at 3%:     16/17 (94%)
NO_GO directional missed at 3%:     9/17 (53%)
NO_GO idiosyncratic missed at 3%:   4/17 (24%)
```

Interpretation:

- The original "94% missed" was inflated by beta/fat-tail volatility.
- Real event-specific misses exist, but sample size is too small for a main engine.
- The biggest actionable issue was recall, not LLM reasoning.

Drop analysis:

```text
trash_lowmat: mostly clean
asset_capped: clean duplicates
dup/noise/stale/context: OK
no_tracked_asset: real blind spot
```

Recall diagnosis:

- 7-8 L5 events from crypto wires were blocked by source-layer constraints.
- Strong cross-layer fallback now recovers `COIN`, `ANTHROPIC`, `SPACEX`.
- `HYPE` and `WLD` were added to L2 coverage.
- Macro/no-single-asset headlines remain intentionally unassigned.

## Current Runtime Focus

Let the scanner run and observe the post-fix flow.

Check:

- `cross_layer=true` events are present and not noisy.
- HYPE/WLD route correctly only on strong names or `$`/pair confirmation.
- L3/L4 now get OKX price/outcome through `XAU/XAG/XPT/XPD/CL/NG-USDT-SWAP`.
- Alibaba/Yandex LLM role behavior remains stable.
- Telegram cards are not duplicated or malformed.

Useful commands:

```bash
python src/scout/resolve_outcomes.py --report
python -m src.scout.news_buffer stats
python -m src.scout.news_buffer ready --limit 5
python -m pytest tests/test_scanner_router.py tests/test_scanner_runtime.py tests/test_scanner_records.py -q
```

## Next Design Step

Do not create a sixth agent/process yet.

The next design item used to be a passive macro/context class:

```text
MARKET_CONTEXT / WATCH_MARKET
```

Purpose:

- capture macro, regulation, stablecoin, geopolitics, tax, policy headlines with no single asset;
- attach affected assets such as `BTC`, `ETH`, `CL`, `XAU`, `QQQ`;
- write context to logs for later analysis;
- do not emit trade recommendations by itself.

Open design questions:

- Store as separate `market_context.jsonl` or as journal rows with a context verdict?
- Deterministic macro-term gate first, or cheap LLM classification?
- How to map contexts to affected assets without polluting trade candidates?
- How to measure context usefulness later?

## Current Bridge State

Implemented:

- `src/scout/watch_queue.py` queues only `WATCH/GO`; `NO_GO` is excluded.
- `src/strategy/setup_confirmation.py` classifies scanner watches against a
  `SignalResult`-like object.
- `TRADE_PLAN_READY` is paper-only and keeps `execution_allowed=false`.
- `scripts/analysis/build_watch_queue.py --dry-run` shows how many existing
  journal rows are eligible.

Still missing:

- a paper-only runner that consumes open watches and writes confirmation results;
- per-asset `WATCH` synthesis;
- extended Telegram analysis by button/command;
- fresh 24-48h source onboarding measurements.

## Hard Constraints

- Python only.
- No Docker.
- No new services.
- Do not expand model-provider routing.
- Use existing `src/utils/llm_client.py`.
- Chart rendering is allowed only as visual context, not as a decision or
  execution trigger.
- Do not revive old `main_impulse_engine`.
- Do not reuse old signal logic blindly.
- Do not touch live order / auto-trade paths.

Safe reuse from frozen main:

- `src/strategy/signal_contract.py`
- `src/strategy/chart_renderer.py`
- records/logging pattern

## Working Principle

Data first.
One experiment second.
Architecture third.

For the next session:

1. Review 2-4 hours of scanner output after recall fix.
2. If flow is clean, design `MARKET_CONTEXT/WATCH_MARKET`.
3. Only after more mature outcomes, revisit Stage 1 market-context experiment.
