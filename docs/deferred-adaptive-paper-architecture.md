# Deferred Adaptive Paper Architecture

Status: **DEFERRED**. Recorded: 2026-07-10.

## Purpose

This note preserves the next trading-system implementation initiative while the
public documentation and repository structure are being rebuilt. It is not the
current task and does not authorize parameter changes, process restarts, live
trading, or access to private research artifacts.

## Target Loop

```text
farm -> deterministic sweep -> honest validation -> paper lifecycle
-> outcome analysis -> role-specific memory -> bounded next sweep
```

The target remains paper-only. `execution_allowed=false`; LLM roles may propose
bounded hypotheses and explain outcomes, but cannot choose final trade levels,
promote validation status, access credentials, or place orders.

## Decisions Already Made

- Maintain separate quality and flow modes without exhausting the local GPU.
- Search adaptive entry, stop, take-profit, hold and exit variants through
  deterministic project code, not model-invented arithmetic.
- Preserve both positive and negative outcomes, including missed-profit and
  validator false-reject/false-accept cases.
- Keep paper economics explicit through a bounded local configuration; do not
  publish account-sizing or leverage settings as public strategy parameters.
- Keep manual trades as read-only comparison evidence; never grant the bot
  authority over a real account.
- Keep local calculator work local where practical; cloud roles receive only
  sanitized, role-specific packs.

## Return Criteria

Resume this initiative only after the public documentation work has produced:

1. one unambiguous map of the active farm, validator, paper and Telegram paths;
2. a verified public/private storage contract and a decision on historical
   journal/research artifacts;
3. a reproducible paper-only onboarding path with the validator dependency
   documented;
4. a clean acceptance audit of paths, documentation and repository boundaries.

## Local History

Detailed operator Q&A and implementation handoffs are retained locally as
private historical context. The public source of current sequencing is
[ROADMAP.md](../ROADMAP.md).
