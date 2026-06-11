# Scanner To TA Confirmation Contract

Updated: 2026-06-11

## Purpose

This document defines the safe bridge between the active scanner and the old
Main/TA code.

The scanner answers:

```text
Why should this asset be watched?
```

The TA confirmation layer answers:

```text
Does the current market structure support, reject, or delay this event thesis?
```

It does not execute trades.

## Current Data Flow

```text
scanner_journal.jsonl
  -> watch_queue.jsonl
  -> setup_confirmation.confirm_setup()
  -> future setup_confirmation_journal.jsonl
```

Existing files:

- `src/scout/watch_queue.py`
- `src/strategy/setup_confirmation.py`
- `scripts/analysis/build_watch_queue.py`
- `tests/test_watch_queue.py`
- `tests/test_setup_confirmation.py`
- `tests/test_build_watch_queue.py`

## Watch Queue Contract

`logs/scout/watch_queue.jsonl` contains only scanner rows that need follow-up.

Eligible scanner verdicts:

- `WATCH`
- `GO`

Excluded:

- `NO_GO`
- drops
- intermediate cheap-agent output
- failed/retry-only events

Required invariants:

- `confirm_required` is `true`;
- `execution_allowed` is `false`;
- queue rows are idempotent by `watch_id`;
- queue rows keep scanner context, source URL, event type, side, levels, summary,
  invalidation, and source metadata.

Backfill command:

```bash
python scripts/analysis/build_watch_queue.py --dry-run
python scripts/analysis/build_watch_queue.py
```

## Confirmation Statuses

`confirm_setup(watch, signal_result)` returns one of:

| Status | Meaning |
|---|---|
| `WATCH_CONTINUE` | Event remains interesting, but TA does not confirm yet |
| `SETUP_FORMING` | Some structure exists, but not enough for a paper plan |
| `TRADE_PLAN_READY` | Scanner side and TA side align; paper levels can be recorded |
| `INVALIDATED` | TA side opposes the scanner thesis |
| `EXPIRED` | Watch TTL expired |
| `NEEDS_DATA` | No usable market/TA snapshot exists |

`TRADE_PLAN_READY` is not permission to trade. It is still paper-only and has
`execution_allowed=false`.

## Allowed Main/TA Use

Allowed:

- create a market snapshot for a scanner watch;
- confirm or reject the scanner side;
- produce paper-only levels;
- estimate risk/invalidations;
- render a chart as visual context;
- write a confirmation journal for later outcome analysis.

Forbidden:

- use old Main/TA `ENTRY` as a standalone trade signal;
- send orders from scanner, watch queue, or setup confirmation;
- connect this path to `AUTO_TRADE`;
- call `scripts/auto_execute.py`;
- treat chart rendering as a decision engine;
- use old client-facing TA text as a scanner trade recommendation.

## Next Implementation Step

The next code step should be a paper-only confirmation runner:

```text
open watches
  -> fetch/build current market snapshot
  -> compute read-only SignalResult
  -> confirm_setup()
  -> append setup_confirmation_journal.jsonl
```

The runner must not send orders. Telegram integration, if added, should be an
"extended analysis" card, not an execution prompt.

## Tests Required For Any Runner

- `AUTO_TRADE` remains untouched;
- no import of `scripts.auto_execute`;
- `execution_allowed=false` survives every status;
- expired watches do not produce trade plans;
- opposite TA side returns `INVALIDATED`;
- missing market data returns `NEEDS_DATA`;
- all outputs are append-only and replayable.
