# Adaptive trading research handoff - 2026-07-07

This note preserves the operator decisions from the July 7 discussion so the
next implementation pass does not lose the intended architecture after context
compaction.

## Core direction

The project is not meant to be a static signal sender. The target is an adaptive
paper-trading research lab:

```text
farm proposes and sweeps variants
-> validator grades them
-> main-paper records paper execution
-> outcome analyst explains what happened
-> learning memory stores the result
-> farm uses that memory in later sweeps
```

The LLM layer should not trade directly and should not invent final prices by
itself. Its role is to guide research, propose hypotheses and sweep ranges, ask
the deterministic code to calculate variants, and explain closed outcomes.

## Safety boundaries

- Live trading stays off for this phase.
- Real exchange/API account data may be read for manual comparison, but the bot
  must not place or modify real orders.
- `.env`, API keys, raw private logs, and private strategy internals must not be
  sent to public GitHub or raw cloud-model prompts.
- Cloud LLMs may receive sanitized packs only.
- Local LLMs may see richer local project context, but still through role
  boundaries and without direct execution authority.

## Paper money model

Initial paper model:

- paper deposit: `700 USDT`
- position size: about `30-40 USDT`
- futures leverage: `3x-5x`
- paper mode should account for commission, slippage/spread where available,
  and leveraged exposure.

The Excel journal is important and should be inspected before implementation.
It should become part of the recordkeeping layer for paper/manual comparison,
but it must be cleaned/prepared carefully and not expose secrets.

## Farm intent

The farm must support two modes:

- `quality mode`: fewer signals, deeper checks.
- `flow mode`: more signals, more learning material.

GPU use should be controlled. The farm may run continuously, but it should not
consume all GPU memory. Current/live opportunities should have higher priority;
background sweeps and heavier research should run with lower priority.

The farm should not only apply a static pattern library. It must explore:

- entry variants
- stop variants
- TP1/TP2 variants
- risk/reward variants
- hold-time variants
- quick exit vs base exit vs extended hold
- trailing stop modelling
- long and short paths when the market context supports them
- pair-specific and timeframe-specific behavior
- visual/candle structure and technical analysis from stored data, where
  feasible

The farm should send no more than one or two best variants per market situation
to Telegram to avoid user confusion.

## Main TP/SL problem

Observed pain: TP/SL and exits look too static and often sit too close to entry.
The system can take a tiny result or miss the real move. This creates poor
economic behavior even when the technical signal is not completely wrong.

The next architecture must explicitly track:

- too-small target
- stop too close
- exit before the move begins
- missed large continuation after small take
- price moved into profit but later closed badly
- horizon mismatch between `15m`, `1h`, and longer setups

`missed_profit_capture` should be a first-class outcome/error label.

## Validator intent

Validator should not be only pass/fail. Target grading:

```text
rejected
watch_only
experimental
paper_allowed
pfr_validated
```

If validator rejects a setup but paper later shows the idea had value, the case
should be stored as a validator learning case, not discarded. If validator lets
a weak setup through, that also becomes a learning case.

Validator should account for commission, slippage/spread, leverage effects,
current market regime where practical, and quality of exit logic, not only raw
direction.

## LLM swarm roles

Do not call every model on every signal. The orchestrator should call roles only
when justified by the case.

Candidate roles:

- scout/context role: reads market/news/context and labels the situation.
- sweep planner: proposes what parameter ranges the deterministic farm should
  calculate.
- risk critic: looks for weak points in a candidate.
- validator reviewer: explains why validator cut or downgraded a setup.
- outcome analyst: studies closed paper/manual outcomes.
- librarian/memory role: compresses useful cases into reusable learning memory.
- chief role: used only for complex or disputed cases.

Local models are preferred for calculator/sweep work. Cloud models can support
strong reasoning, news, and difficult reviews, but only with sanitized inputs.

## Outcome analyst intent

Outcome analyst must be one of the strongest roles. It should analyze both good
and bad results. Negative trades are not the only learning source; good trades
explain what worked.

For each complete cycle it should preserve:

- why the setup was entered
- what parameters were used
- what was expected
- what actually happened
- what exit alternatives would have done
- whether human execution beat machine execution
- what should change in farm sweeps
- what should change in validator grading

It may request more candle/history data through project code when needed. It
must not change live trades or rewrite computed outcomes.

## Learning memory

Memory is not just logs. It should become an operational learning base with
separate views for each model/role without duplicating raw data everywhere.

Important buckets:

- good cases
- bad cases
- missed profit cases
- validator false reject
- validator false accept
- human execution cases
- strategy family score
- pair/timeframe score
- parameter set history

The unit of experience should be a complete, clean cycle:

```text
setup -> calculated variants -> validator grade -> paper result -> outcome analysis -> memory update
```

The long-term direction includes local fine-tuning, but only after a clean
dataset exists. Fine-tuning on noisy or broken cycles would train the wrong
behavior.

## Telegram/product layer

Telegram is a user-facing surface, not the internal research brain. Cards should
clearly distinguish:

- calculated farm focus
- experimental paper signal
- PFR-validated signal
- manual analysis

Calculated non-PFR signals may be sent as focus/attention signals if clearly
marked. Users should understand they must think and manage risk themselves.

## Manual trades and Excel journal

Manual user trades are valuable as learning cases. The system should compare:

```text
machine plan vs human execution
```

Human trades should be imported/read for analysis only. The bot must not manage
the real account in this phase.

## Implementation roadmap

1. Inventory current farm, validator, main-paper, outcome, memory, Telegram, and
   Excel-journal paths.
2. Produce a precise gap map: what already works, what is duplicated, what is
   missing, and where the feedback loop breaks.
3. Implement/normalize the paper money model around `700 USDT`, `30-40 USDT`
   position size, and `3x-5x` leverage.
4. Extend farm sweeps for adaptive entry/exit/TP/SL/hold/trailing variants.
5. Add validator grading and validator learning cases.
6. Expand outcome analyst into a real trade analyst with alternative-exit
   comparison.
7. Create learning-memory views that feed future farm sweeps.
8. Integrate manual/Excel trade cases as read-only learning inputs.
9. Keep Telegram labels honest and user-readable.
10. Add docs/tests/status reports before long unattended runs.

## Measurement correction - 2026-07-10

The July 8-10 private replay showed that the feedback loop must first repair its
measurement foundation. The old observer reused `open_index`, which was relative
to a fetched list that restarted at index zero on every cycle. It also applied the
entry-window wall-clock expiry to already opened paper positions. These two
behaviors produced negative hold counts and `expired_no_entry` rows with stored
entries.

The corrected contract is:

```text
boundary_ts
-> last_observed_bar_ts advances once per candle
-> opened_at_bar_ts records the fill candle
-> bars_waited accumulates only before entry
-> bars_held accumulates only after entry
-> entry-window expiry applies only while armed
-> repeated no-data after entry becomes a data invalidation, not no-entry
```

The frozen 474-signal cohort now produces identical one-shot and incremental
results, with zero stored-entry/no-entry contradictions and zero negative hold
counts. This proves lifecycle determinism, not profitability; account-level PnL
and trading calibration remain later phases in epic #158.

## Implementation status - retest bridge

The outcome analyst now has a deterministic bridge into farm follow-up work:

```text
outcome review -> OutcomeRetestSpec -> bounded SweepSpec -> schedule_retest -> run_sweep
```

Important constraint: an outcome retest is a compact hypothesis check, not a
full broad farm sweep. The retest generator caps the executable variant grid to
8 variants before the task reaches `validate_sweep_spec`. This prevents the
previous failure mode where a review was cataloged as queueable, but the farm
later skipped it as `invalid_retest_spec: variant grid exceeds max_variants`.

Broader adaptive searches still belong to the normal farm/sweep-planner layer.
Outcome retest should only prove whether the analyst's immediate hypothesis is
worth feeding back into that broader layer.

## Implementation status - product memory into search focus

The broad product paper memory is now part of the search-layer ranking. It does
not make a setup validated and does not grant trade authority. It only nudges
which live movers the paper loop looks at first:

- positive product paper history gives a small priority bonus;
- weak/loss-heavy product paper history gives a bounded penalty;
- thin history is ignored;
- known-bad memory still remains the hard skip/deprioritize path.

This makes the loop less blind: Telegram/product paper outcomes can affect
future focus, while strict validation remains owned by the validator/PFR path.

## Current top priority

The first engineering priority is not tuning one TP value. It is to make the
feedback loop real:

```text
farm -> validator -> paper -> outcome -> memory -> next farm sweep
```

Until that loop is working, signal calibration will keep feeling like guessing.

## Implementation slice - paper economics and richer retests

The first concrete slice added the missing paper-money accounting layer and
expanded the retest bridge after outcome review:

- canonical paper account model: `700 USDT` deposit, `35 USDT` paper position
  inside the agreed `30-40 USDT` band, `3x` leverage with `5x` cap.
- strict `main-paper` ledger and broad `paper-product` ledger now attach
  `paper_account` to each row.
- training export now carries paper money fields, so outcome analysis can compare
  behavior in USDT, not only `take/stop/simple_be`.
- project snapshot and farm status report expose aggregate paper PnL.
- outcome retests now build a bounded `normal` sweep with hold, stop, and
  RR-safe take-profit variants instead of only a tiny two-point TP/hold check;
  the grid is capped at 18 variants so it fits the existing `1h` resource cap.

Latest private-artifact rebuild on the existing dataset showed:

```text
main-paper strict terminal trades: 1, paper PnL -3.087 USDT
paper-product terminal trades: 1597, wins 322, losses 1163
paper-product paper PnL: -324.64635 USDT, avg -0.203285 USDT
outcome retest catalog: 50 specs, 50 queueable
```

This is not a trading conclusion yet. It means the accounting surface is now
honest enough to show that the current broad paper flow is economically weak and
needs adaptive exit/entry retests before calibration.

## Implementation slice - product paper memory visibility

The next slice fixed a memory/reporting gap: broad subscriber-facing paper cards
were recorded in `paper_signal_memory.jsonl` and `paper_signal_training.jsonl`,
but the strict setup memory was not showing their economic learning signal.

Important separation:

- strict setup memory remains tied to `unique_candidates` and hard-validation
  artifacts.
- broad product paper memory is aggregated separately from
  `paper_signal_training.jsonl`.
- product paper memory never grants `PAPER_FORWARD_READY`, never creates a live
  permission, and is not treated as validator proof.

Latest private-artifact rebuild on the existing dataset:

```text
strict setup memory: 5000 records, paper_memory_rows 0, paper_ready_without_hard_pass 0
product paper memory: 2228 rows, 1597 terminal rows
product paper PnL: -324.64635 USDT, avg -0.203285 USDT
product wins/losses: 322 / 1163
gave-back cases: 305
```

This means the project now honestly distinguishes:

```text
PFR / validator memory = strict evidence
broad paper product memory = learning signal for next sweeps and analyst review
```

Also fixed:

- `lane.load_known_bad()` now recognizes the current `CONFIRMED_BAD` setup
  memory class instead of the obsolete `REJECTED_CONFIRMED_BAD` class.
- `farm_status_report` now prints `product paper memory` under outcome memory.
- `setup_outcome_memory.json` now contains a separate `product_paper_memory`
  block in the private derived snapshot.
- `farm_loop` now refreshes `setup_outcome_memory.json` after
  `paper_signal_training_export` / `product_signal_training_export`, so the next
  paper cycle reads fresh memory instead of relying on a manual rebuild.

Verification:

```text
python -m pytest tests/test_setup_outcome_memory.py tests/test_paper_signals.py::TestKnownBadGate -q
37 passed

python -m pytest tests/test_paper_money_model.py tests/test_main_paper_trade_ledger.py tests/test_paper_product_trade_ledger.py tests/test_paper_signal_training_export.py tests/test_outcome_learning.py tests/test_farm_coordinator.py::test_outcome_review_followup_becomes_retest_sweep tests/test_paper_telegram_preview.py tests/test_paper_product_quality_report.py tests/test_project_snapshot.py tests/test_setup_outcome_memory.py tests/test_paper_signals.py::TestKnownBadGate -q
101 passed

python -m ruff check ...
All checks passed

python -m pytest tests/test_farm_loop_stage_visibility.py tests/test_farm_journal.py::test_farm_loop_can_run_paper_step_in_dry_run -q
21 passed

Final affected-surface regression after adding the farm-loop memory refresh:
122 passed
```

Operational note from full status after this slice:

```text
COMPLETION: PAUSED_WITH_WORK
eligible_now=42 running=3 deferred_future=5 blocked=36
```

No runtime was restarted in this slice. Before the next unattended run, reconcile
or restart the farm loop deliberately so stale `running` work does not confuse
the operator view.

## Calibration evidence gate (2026-07-10)

The lifecycle repair exposed a second-order problem: the existing private
training export still consisted of legacy lifecycle rows. Those rows are useful
for forensic comparison, but their entry/hold labels predate durable candle
cursors and therefore cannot safely select the next farm geometry.

The canonical rule is now:

```text
legacy outcome -> forensic background only
PaperSignalLifecycle.v2 terminal outcome -> calibration evidence
calibration evidence below sample floor -> bounded probe only
calibration evidence above sample floor -> retain/demote/retest verdict
verdict -> profile label only; deterministic family code still owns prices
```

`state/derived/trading_policy_calibration.json` is private and aggregate. It
reports sample sizes, Wilson 95% win-rate bounds, net/capture/give-back metrics,
and observational profile verdicts. The report explicitly does not claim causal
attribution, does not promote a setup, and keeps `execution_allowed=false`.
