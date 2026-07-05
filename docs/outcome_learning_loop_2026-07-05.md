# Outcome Learning Loop - 2026-07-05

This document describes the current self-improvement loop for the paper/research
trading system. It is a design-and-implementation note, not a live-trading claim.

## Boundary

- Paper/research only.
- `execution_allowed=false` at every new artifact boundary.
- LLMs may classify, explain, and suggest bounded next-test dimensions.
- LLMs must not set entry, stop, take profit, side, validator verdict,
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

## New Closed-Loop Path

```text
terminal paper outcome
  -> paper_signals.lane.review()
  -> TrainingRow.v2
  -> OutcomeLearningCase.v1
  -> outcome_reviewer advisory JSON
  -> state/llm_advice/outcome_reviews.jsonl
  -> next TrainingRow.v2 export links outcome_review_id
  -> accepted review compiles into a farm Recommendation
  -> existing feedback_followup path plans a bounded run_sweep or a note
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

The LLM receives a sanitized review pack. Exact entry/stop/TP levels and final
human card text are intentionally not included in that pack.

## What This Is Not

- It is not an auto-promoter.
- It is not a live executor.
- It is not a model that changes trade levels.
- It is not a guarantee of profitability.

Promotion remains downstream of deterministic retests, hard validation, PFR,
shadow/true-forward observation, and explicit operator review.

## Next Implementation Steps

Implemented in this slice:

1. Deterministic outcome routing and sanitized review packs.
2. Expanded advisory `outcome_reviewer` fields for review kind, bucket,
   actionability, tags, and counterfactual summaries.
3. `TrainingRow.v2` backlink fields for accepted outcome reviews.
4. Accepted outcome reviews can become bounded farm `Recommendation` objects and
   reuse the existing `feedback_followup.py` planner.

Next implementation steps:

1. Add summary counters to status/reporting so the operator sees learning-case
   distribution and rejected LLM reviews.
2. Link source-trust reviews to outcome clusters for scanner/news quality.
3. Route promising retest candidates into existing shadow/true-forward registries
   without granting paper-ready authority.
