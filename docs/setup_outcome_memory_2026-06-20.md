# Setup Outcome Memory — rejected-as-knowledge + read-through gate (Phase A / S1+S4)

**What it is.** A *derived, rebuildable* read-model over the farm's canonical artifacts, plus a
**read-through gate** so a repeated signal consults prior outcomes BEFORE the farm spends compute.
It is **not a second source of truth** and adds **no registry**: it is rebuilt entirely from
`farm_tasks.unique_candidates` + `setup_lifecycle` (verdicts/cards/paper) + `trade_path_diagnostics`
(reject sub-reason + path facts) + `exit_recovery` (recovered exits). Module:
[setup_outcome_memory.py](../src/research_lab/setup_outcome_memory.py).

**Research-only invariant.** No outcome here ever grants `PAPER_FORWARD_READY` or is a trade signal.
`paper_forward_ready` stays owned by the hard validator + setup cards; the summary surfaces
`paper_ready_without_hard_pass` (must be 0 — a non-zero count is a leak bug, tested).

## Two layers

1. **Read-through gate (cheap hot path).** `build_gate_index(unique_candidates)` aggregates prior
   outcomes per exact sweep (`symbol|timeframe|family|data_fingerprint`) and per family cell
   (`symbol|timeframe|family`). `lookup(...)` returns a `MemoryVerdict`:
   - `skip_known_bad` — the **same data fingerprint** already produced zero eligible candidates
     (re-running identical data reproduces it; the path to more evidence is *new data* or
     re-validation, not a re-sweep). Reason `confirmed_bad_identical_data` / `no_eligible_identical_data`.
   - `revisit` — an eligible/recovered prior exists here → flag for re-validation, don't blindly re-sweep.
   - `deprioritize` — a family cell historically all-rejected (≥5 priors, ≥80% rejected, 0 eligible)
     on *other* fingerprints → still allowed, but down-ranked.
   - `fresh` — unseen or inconclusive → normal compute.

   The coordinator builds the index once per cycle and consults it in `_create_run_sweep` before
   keying a fresh sweep (`use_outcome_memory=True`, default on; counters `sweeps_skipped_memory` /
   `sweeps_deprioritized`). **This is what makes "memory used by the next farm cycle" real.**

2. **Rich read-model.** `build_memory_index()` joins per-trade path facts (MFE/MAE/capture) and
   recovered exits into one record per candidate, then `write_memory_snapshot()` writes the derived
   `state/derived/setup_outcome_memory.json` (research artifact, never read by farm/validator/paper).

## Outcome classes (closed vocabulary)

| class | meaning | tradeable? |
|---|---|---|
| `POSITIVE_VALIDATED` | cleared the hard validator (`PAPER_FORWARD_READY`) — stays positive even if a later paper sample is negative (that lives in lifecycle_state) | only this, via the validator — never via memory |
| `STATISTICAL_CANDIDATE` | lite-eligible (FORWARD_PAPER/REGIME_SPECIFIC), awaiting hard validation | no |
| `EXIT_RECOVERED` | wrong_exit whose `(symbol,tf,family)` cell has a T3-A recovered exit | **no — research candidate for re-validation** |
| `THIN_BUT_PROMISING` | 3–9 trades, net-positive (validator's n<10 power floor auto-fails it) | no |
| `TACTICAL_1_2_TRADE` | 1–2 trades, net-positive — a window, never a statistical edge | no |
| `WRONG_EXIT` / `WRONG_TIMEFRAME` / `COST_SENSITIVE` | recyclable rejects (exit/horizon/cost suspected) | no |
| `NEEDS_OI_DATA` | family needs OI/micro data not present | no |
| `CONFIRMED_BAD` | n≥10 with power and net≤0 — genuinely bad | no |
| `INSUFFICIENT_DATA` | 0 trades — nothing to evaluate | no |
| `UNCHARACTERIZED` | falls through the taxonomy (e.g. n 3–9 net≤0) — needs the deeper MFE/MAE drill | no |

Sub-views (the "separate databases" as filters over the one index, not new stores):
`positive_setups` / `statistical_candidates` / `recovered_setups` / `tactical_setups` /
`rejected_research` / `confirmed_bad_setups` / `needs_data_setups`.

## How a repeated scanner/farm signal uses prior memory

At plan time the coordinator keys `(symbol, timeframe, family, data_fingerprint)` and calls `lookup`:
identical-data-known-dead is **skipped** (no compute), an all-rejected family cell is **down-ranked**,
and an eligible/recovered prior is flagged **revisit** (→ Phase D re-validation) instead of a blind
re-sweep. Only genuinely unseen/inconclusive cells get a `fresh` sweep. Disable with
`use_outcome_memory=False`.

## What this is NOT

- **Not a proven edge.** `recovered`, `tactical`, `thin`, `statistical_candidate` are *candidates for
  honest re-validation* (with the multiple-testing correction), never confirmed edges. The corpus
  gate verdict (only a handful of `PAPER_FORWARD_READY` ever, FAILED_COSTS the dominant hard failure)
  still stands.
- **Not a money/live path.** Read-only, no orders, no `.env`, no `AUTO_TRADE`, no private endpoints.
- **EXIT_RECOVERED is coarse** at this phase: it is joined at `(symbol,tf,family)` cell granularity
  (the exit-recovery snapshot has no `uc_key`), so it can over-mark sibling wrong_exit params in the
  same cell. The gate never depends on this label; it is tightened to per-candidate in Phase D.

Inspect: `python -m src.research_lab.setup_outcome_memory --snapshot` ·
`python -m scripts.strategy_lab.farm_status_report` (the "outcome memory" line). Live numbers in the
private research doc `scripts/analysis/research/setup_outcome_memory_2026-06-20.md`.
