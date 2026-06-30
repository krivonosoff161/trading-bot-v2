# Main adaptive policy layer

Date: 2026-06-30

## Purpose

This pass adds a safe adaptive policy layer between validated paper instructions
and the main-compatible paper runtime.

The goal is not to revive the legacy live `main.py` executor. The goal is to
make the main paper lane record **how** a setup should be watched:

```text
farm/PFR paper signal
  -> main_paper_instruction
  -> main_paper_consumer
  -> main_adaptive_policy
  -> main_paper_runtime_queue
  -> main_paper_runtime_observation
  -> paper_signal_training
```

## Boundary

The adaptive policy can select bounded labels:

- execution profile
- entry profile
- exit profile
- stop profile
- max-hold profile
- regime hint
- reason codes

It cannot set:

- entry prices
- stop prices
- take-profit prices
- validator verdicts
- paper-ready flags
- leverage
- orders
- execution permission

All price geometry still comes from deterministic code and `trade_math`.

## Why this matters

The trading system needs a role-aware loop:

- scanner finds market situations;
- farm/calculator searches and validates setup variants;
- validator separates statistical, tactical, weak, and bad classes;
- main paper executor watches accepted paper plans;
- Telegram surfaces readable cards;
- training export records the outcome.

Before this pass, the main paper queue had the signal, but not the execution
policy rationale. Now each queued paper item carries an adaptive policy id and
profile fields, and terminal training rows can link the selected policy to the
eventual outcome.

## Current implementation

New module:

- `src/research_lab/main_adaptive_policy.py`

Updated consumers:

- `src/research_lab/main_paper_runtime_adapter.py`
- `src/research_lab/main_paper_runtime.py`
- `src/research_lab/paper_signals/training_export.py`
- `scripts/strategy_lab/farm_loop.py`
- `scripts/strategy_lab/farm_status_report.py`

Private artifacts:

- `state/derived/main_adaptive_policy.jsonl`
- `state/derived/main_adaptive_policy.json`

## Policy examples

`early_tp_tactical`:

- `fast_tactical_watch`
- `limit_or_pullback`
- `early_tp_partial_be`
- `tight_atr_cap`

`reversal_fade` / `mean_reversion_fade`:

- `mean_reversion_watch`
- `reclaim_or_retest`
- `partial_be_or_fast_tp`
- `structure_stop`

`continuation` / `pullback_continuation` / `momentum_breakout`:

- `cautious_followthrough_watch`
- `pullback_required`
- `partial_be`
- `wide_move_cap`

This is intentionally conservative because recent forward data showed that raw
continuation on stretched movers is fragile.

## LLM role

The LLM role is future-safe and bounded:

- allowed: suggest one of the bounded profile labels;
- allowed: explain why a profile is suitable;
- rejected: raw prices, stops, take-profits, leverage, execution flags, validator
  verdicts, or paper-ready claims.

`validate_advisor_policy()` rejects forbidden LLM fields before they can enter
the runtime.

## Verification

Targeted tests:

```text
tests/test_main_adaptive_policy.py
tests/test_main_paper_runtime_adapter.py
tests/test_main_paper_runtime.py
tests/test_paper_signal_training_export.py
tests/test_farm_loop_stage_visibility.py
```

Result:

```text
31 passed
```

No `.env`, `AUTO_TRADE`, live/private order path, or legacy live executor was
enabled.

