# Outcome Learning Loop - 2026-07-05

This document describes the current self-improvement loop for the paper/research
trading system. It is a design-and-implementation note, not a live-trading claim.

## Boundary

- Paper/research only.
- `execution_allowed=false` at every new artifact boundary.
- LLMs may classify, explain, and suggest bounded next-test dimensions.
- LLMs may read planned and observed paper-trade levels as historical evidence.
- LLMs must not output replacement entry, stop, take profit, side, validator verdict,
  `paper_ready`, order size, order action, or execution permission.
- No `.env`, Telegram credentials, private OKX account endpoints, or live order
  modules are used by the outcome-learning code.

## Existing Spine

The project already had the important backbone:

```text
farm_loop
  -> farm_coordinator / farm_tasks.sqlite
  -> strategy_lab.sqlite compute queue
  -> hard validation / PFR
  -> paper_signals
  -> main_paper_* watcher
  -> paper_telegram_preview / delivery audit
  -> paper_signal_training_export
```

The new work does not create a second brain. It adds a review layer over the
existing `TrainingRow.v2` export and uses the already bounded
`outcome_reviewer` role.

## Headless Operating Mode

For long paper/product runs on the local machine, use:

```bat
bat\paper_product_headless_loop.bat
```

That wrapper runs the canonical farm full-cycle loop without opening the
dashboard, graph viewer, or status-monitor window. It enables bounded
outcome-learning reviews by default with small per-cycle caps, so the system can
accumulate evidence and advisory review links without loading the machine with
visual surfaces.

Telegram delivery remains off in that default mode. Use
`bat\paper_product_headless_send_loop.bat` only when reviewed paper cards should
be delivered to active subscribers. Both wrappers keep the same boundary:
paper/research only, no `AUTO_TRADE`, no old `main.py`, no orders, no private
exchange endpoints, and no LLM authority over execution.

## New Closed-Loop Path

```text
terminal paper outcome
  -> paper_signals.lane.review()
  -> TrainingRow.v2
  -> OutcomeLearningCase.v1 + read-only plan/outcome/path pack
  -> outcome_reviewer advisory JSON
  -> state/llm_advice/outcome_reviews.jsonl
  -> next TrainingRow.v2 export links outcome_review_id
  -> accepted review compiles into a farm Recommendation
  -> existing feedback_followup path plans a bounded run_sweep or a note
  -> OutcomePromotionGate.v1 explains the next non-execution stage
```

The important change is the backlink:

```text
TrainingRow.v2.outcome_review_id
TrainingRow.v2.outcome_learning_review_kind
TrainingRow.v2.outcome_learning_bucket
TrainingRow.v2.outcome_learning_actionability
```

This makes later analysis able to answer:

- which exact paper signal produced the outcome;
- what deterministic diagnosis was assigned;
- what advisory review was attached;
- whether the case was a loss, win, missed entry, or counterfactual/capture case;
- what bounded retest dimension was suggested.

## Deterministic Case Types

`src.research_lab.outcome_learning` currently routes rows into:

| Bucket | Review kind | Meaning | Typical next test |
|---|---|---|---|
| `loss` | `loss` | terminal negative result without useful favourable movement | regime/confirmation cluster before retest |
| `loss_after_positive_mfe` | `loss` | price moved in favour, then ended negative | exit/capture retest |
| `gave_back` | `loss` | deterministic diagnosis says favourable move was not retained | partial-BE vs fixed/time stop retest |
| `expired_no_entry` | `missed` | setup never filled | entry width/timeout/pretrigger watch |
| `breakeven` | `counterfactual` | protected by BE but unclear opportunity cost | BE policy comparison |
| `win_low_capture` | `counterfactual` | green outcome but poor MFE capture | TP ladder/max-hold retest |
| `win` | `win` | positive outcome worth preserving as pattern evidence | preserve/shadow same context |

The LLM receives a sanitized review pack. The pack now includes read-only
planned/observed trade facts so the reviewer can act like a post-trade analyst:

- original side, entry zone, midpoint, stop, TP1, risk, max-hold, exit mode;
- observed paper entry/exit, result, net, MFE/MAE, capture, bars held, partial/BE facts;
- a compact candle window from private prepared `market_data/<tf>/` when available;
- peer stats for the same family/timeframe.

Final human card text is intentionally not included in that pack. The reviewer
can propose hypotheses such as "test earlier profit lock", "compare BE policy",
or "add volume/regime confirmation", but it cannot return executable levels or
change the signal. Deterministic code turns accepted hypotheses into capped
`OutcomeRetestSpec`/`SweepSpec` rows and the farm tests them.

## What This Is Not

- It is not an auto-promoter.
- It is not a live executor.
- It is not a model that changes trade levels.
- It is not a guarantee of profitability.

Promotion remains downstream of deterministic retests, hard validation, PFR,
shadow/true-forward observation, and explicit operator review.

## Promotion Gate View

`src.research_lab.outcome_promotion_gate` is a read-only authority map over the
learning artifacts. It does not enqueue tasks and does not write trade state. It
joins accepted `outcome_reviewer` rows back to `TrainingRow.v2`, then checks the
existing `shadow_forward.json`, `true_forward.json`, and
`ready_strategy_catalog.json` artifacts.

The gate stages are intentionally conservative:

| Stage | Meaning |
|---|---|
| `review_only` | advisory note or cluster signal; do not spend follow-up compute yet |
| `needs_retest` | accepted review needs a bounded deterministic retest |
| `needs_shadow` | positive/preserve pattern needs a forward watch before trust |
| `needs_true_forward` | shadow exists; pin and collect genuinely new bars |
| `collect_true_forward` | true-forward is registered but not mature |
| `operator_review_only` | true-forward matured, but this is evidence, not edge |
| `eligible_for_operator_review` | true-forward matured and hard-ready catalog evidence exists; still no execution authority |

Every stage keeps `paper_only=true` and `execution_allowed=false`. Even
`eligible_for_operator_review` means "show the operator the evidence", not "trade
or promote automatically".

## Next Implementation Steps

Implemented in this slice:

1. Deterministic outcome routing and sanitized review packs.
2. Expanded advisory `outcome_reviewer` fields for review kind, bucket,
   actionability, tags, and counterfactual summaries.
3. `TrainingRow.v2` backlink fields for accepted outcome reviews.
4. Accepted outcome reviews can become bounded farm `Recommendation` objects and
   reuse the existing `feedback_followup.py` planner.
5. `OutcomePromotionGate.v1` status view over accepted outcome reviews,
   shadow-forward, true-forward, and the ready-strategy catalog.

Implemented in the trader-analyst expansion:

1. `TrainingRow.v2` preserves observed paper entry/exit, bars held, TP1/partial
   flags, and banked percentage.
2. `OutcomeLearningCase.v1.review_input` includes read-only `original_plan`,
   `observed_trade`, and `market_context` sections.
3. `agent_role_review_cycle` passes the private research root so prepared candle
   windows can be attached without public output or exchange/order access.
4. The `outcome_reviewer` prompt/contract accepts counterfactual tests,
   parameter hypotheses, path diagnosis, and farm-memory hints, while forbidding
   trade/execution authority.
5. `outcome_retest` consumes `next_test_dimensions`, `counterfactual_tests`, and
   `parameter_hypotheses` as bounded retest dimensions.
6. `outcome_retest_result` returns completed farm sweeps to their originating
   review/training row and classifies evidence as `improved_directional`,
   `no_improvement`, or `insufficient_evidence`.
7. Completed retest IDs rotate out of the bounded catalog; promotion gates and
   setup memory consume the returned verdict at candidate or explicit cell scope.

The result comparison is a directional retest against a single-trade baseline,
not a PnL-attribution claim. It cannot promote itself to paper/live authority.

Next implementation steps:

1. Link source-trust reviews to outcome clusters for scanner/news quality.
2. Add an explicit operator evidence report for `eligible_for_operator_review`
   cases without granting paper-ready or execution authority.
