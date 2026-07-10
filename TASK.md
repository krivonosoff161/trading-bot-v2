# TASK / HANDOFF FOR CLAUDE AND CODEX

Updated: 2026-07-10

This file is the local handoff channel between agents in VS Code.
It is not the canonical architecture document.

## Current State

Current engineering task: GitHub epic #158, phase 2 / issue #151. Build a
reconciled paper account after repairing lifecycle truth in merged PR #159.
The lifecycle implementation replaces list-relative `open_index` replay with durable
`last_observed_bar_ts`, `opened_at_bar_ts`, cumulative wait/hold counters, and
idempotent candle processing. An opened signal no longer uses the entry-window
`expires_at` transition and therefore cannot become `expired_no_entry`.

Private acceptance evidence over the frozen July 8-10 cohort:

- 474 signals and all local candle sets available;
- one-shot versus incremental replay mismatches: 0;
- stored-entry plus `expired_no_entry`: 36 before, 0 after replay;
- negative `bars_held`: 26 before, 0 after replay.

The private CSV/candle evidence remains outside the public repository. Public
code remains paper-only and does not read `.env`, send Telegram, or access live
order/private exchange paths during this phase.

Phase 2 adds `paper_account_events.jsonl` and `paper_account.json` under the
private derived state. The ledger starts at `700 USDT`, reserves `35 USDT` at
`3x` for one primary main-paper thesis per scenario, records fees/slippage and
realized PnL, and rejects allocations when margin is unavailable. Broad product
geometry variants remain counterfactual and must not be read as shared account
equity. The event ledger is append-only and idempotent by stable event ID.

Current center: the calculation farm plus the validator/PFR-backed main-paper watcher,
not the scanner and not old live `main.py`.

New 2026-07-08 context: the PFR handoff was tightened after the user noticed that
subscriber cards were still broad farm cards while validated PFR candidates existed.
The full-cycle launcher, `farm_loop`, standalone paper-signal runner, `cycle.run_cycle`,
and `pfr_bridge.generate_pfr_signals` now share `max_pfr_fetches=12`. The private
product-quality report distinguishes `pfr_trigger_scan_limited` from ordinary
`waiting_for_live_trigger`. The paper-signal cycle now reads recent private
`pfr_gap_telemetry.jsonl` trigger-distance memory and prioritizes near-trigger PFR
records before spending the bounded public-candle fetch budget. This is deterministic
paper/research routing only; it does not grant live execution or LLM authority.

Follow-up 2026-07-08 context: PR #139 fixes the downstream priority gap. Validated
PFR rows now sort before ordinary calculated farm rows in `main_paper_runtime_queue`,
so Telegram preview/card selection sees `validated_pfr` candidates first instead of
letting broad farm cards fill the preview limit. Verified state after local derived
artifact refresh and merge: `instructions=31`, `queued=31`, `active_source={farm:29,
pfr_farm:2}`, `validated_instructions=2`,
`pfr_trigger_state=main_paper_has_pfr_instructions`, `quality_action=collect_outcomes`.
Still paper-only: `execution_allowed=false`, no live orders, no old `main.py`, no
Telegram send authority, no `.env` edits.

New 2026-07-05 context: the first outcome-learning loop slice is in place. Terminal
paper outcomes can be routed into deterministic `OutcomeLearningCase.v1` records,
reviewed by the bounded `outcome_reviewer`, and linked back into later
`TrainingRow.v2` exports through `outcome_review_id` and learning labels. This is
advisory paper/research only and not live execution. The second slice adds
`OutcomePromotionGate.v1`: accepted reviews are joined to existing
`shadow_forward`, `true_forward`, and `ready_strategy_catalog` artifacts and
classified into the next non-execution stage. The gate writes no trade state and
keeps `execution_allowed=false`.

Read first:

- `CURRENT_STATE.md`
- `ARCHITECTURE.md`
- `ROADMAP.md`
- `docs/farm_runbook.md`
- `docs/session_handoff_2026-07-03.md`
- `docs/outcome_learning_loop_2026-07-05.md`

Current verified snapshot after PR #139:

- branch/head: `main`, `72a531e fix: prioritize validated PFR main-paper queue (#139)`
- relevant trading processes: none running during the handoff snapshot
- mode: `paper_only=true`
- execution: `execution_allowed=false`
- old `main.py`: isolated / not part of this PFR handoff
- active paper product: `31` active rows, `29` ordinary farm rows and `2` active
  `pfr_farm` rows
- latest aggregate state:
  `pfr_ready=24`, `validated_instructions=2`, `pfr_generated=4`,
  `pfr_trigger_state=main_paper_has_pfr_instructions`

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
  -> outcome_promotion_gate status view
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

- run the canonical visible paper cycle and let the two active `pfr_farm` rows reach
  observation/outcome;
- verify subscriber-card delivery only after preview remains headed by
  `validated_pfr` rows and Telegram sending is explicitly enabled by the operator;
- continue the separate adaptive farm/validator/main/outcome architecture work only
  after this PFR handoff evidence is visible in the next cycle;
- keep `execution_allowed=false`;
- do **not** wire farm outputs directly into old live/order-capable `main.py`.

Next learning-loop build target:

- let the next runtime/training cycle accumulate linked `outcome_review_id` rows;
- add an operator evidence report for `eligible_for_operator_review` cases;
- link source-trust reviews to outcome clusters for scanner/news quality;
- keep using `feedback_followup.py`, `setup_outcome_memory.py`,
  `shadow_forward.py`, and `true_forward.py`;
- do not create a second farm brain, second memory system, or LLM-controlled
  promotion/execution path.

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
