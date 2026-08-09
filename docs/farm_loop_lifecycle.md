# Farm Loop - Continuous Research Lifecycle

Status: **CURRENT**

- Verified: 2026-08-01
- Verified against: `c20322f887977c5e3c3ec2c242ca560617d056fa`
- Scope: canonical calculation-farm lifecycle, claims, fencing, and idempotency
- Evidence: [lifecycle tests](../tests/test_farm_lifecycle_core.py)
- Residual risks: long-duration interruption behavior needs operational evidence.
- Next gate: preserve these invariants through the paper-only canary.

This is the canonical calculation-farm lifecycle. It supersedes the older
`universe_farm_loop` and `scanner_farm_loop` operator paths. The core is
paper/research only: no `AUTO_TRADE`, no order execution, and no private exchange
endpoints. The core does not send Telegram by default; a separate delivery edge
may read local configuration only when an explicit send path is selected.

## What It Is

`scripts/strategy_lab/farm_loop.py` drives
`research_lab.farm_coordinator.run_coordinator_cycle`. Each pass picks the next
meaningful step for each symbol/timeframe/family and records it as a typed lifecycle task
with a machine-readable reason. It does not spin forever on `already_queued`; saturated
work pivots to deferred-ready work, universe discovery, or `blocked:no_eligible_tasks`.

## Two Databases

| DB | Module | Role |
|----|--------|------|
| `state/farm_tasks.sqlite` | `farm_tasks_db.py` | Brain: typed lifecycle, scheduling, reasons, fingerprints. |
| `state/strategy_lab.sqlite` | `state_db.py` | Compute queue: the worker executes materialized sweep jobs. |

The brain decides what should happen. The compute queue only executes bounded jobs. A
`run_sweep` brain task materializes into `strategy_lab.sqlite` and links back through
`materialized_queue_job_id`. After the cross-database outbox is acknowledged, the brain
task is parked as `deferred:materialized_awaiting_worker` with no claim and no
`deferred_until`. It therefore remains an active dedup key but cannot expire, be
reclaimed, or be executed a second time while the fenced compute queue owns the job.
Only the exact acknowledged materialization ID, task fence, queue job ID, and terminal
queue status can finish that parked task.

Newly acknowledged outbox rows retain the content-bound spec path, digest, task fence,
and compute queue binding, but release the redundant replay `spec_json`. The immutable
event-spec artifact remains the replay input. Historical acknowledged payload copies
may be released only by the project-level verified migration method while the farm is
quiescent: its dry-run validates every artifact and produces a plan digest; apply
requires that exact digest, uses compare-and-swap updates, and optionally performs the
post-commit SQLite compaction and integrity check. Pending, dispatched, ambiguous, and
superseded rows are never eligible.

## Task Types And States

Task types: `intake_event`, `resolve_instrument`, `prepare_data`, `enrich_funding`,
`enrich_oi`, `run_sweep`, `classify_result`, `export_validation`,
`run_or_refresh_validation`, `schedule_followup`.

States: `queued`, `running`, `deferred`, `blocked`, `completed`, `skipped`, `failed`.
Every non-running state carries a `machine_reason`: examples are `data_missing`,
`too_short`, `NEEDS_OI_DATA`, `NEEDS_FLOW_DATA`, `NEEDS_MICRO_DATA`, `data_ready`,
`gate_cleared`, `compute_completed`, `compute_deduped`.

## Re-Arm Rule

`run_sweep` task keys include symbol, timeframe, family, and `data_fingerprint`.
Identical active tasks are deduped. Identical recently completed tasks are not recreated
until TTL expires. Fresh candles or enrichment change the fingerprint, so the farm can
recompute on new data without being blocked forever by old completed work.

Data-gated families use one fingerprint-independent gate task until the missing gate
clears, for example `NEEDS_OI_DATA`.

## Cycle

1. **Intake:** scanner `watch_queue.jsonl` is read as a file through `intake_adapter`;
   the scanner module itself is not imported. OKX discovery can refill the universe.
2. **Plan:** `data_planner` decides prepare/defer/enrich/block/run_sweep per symbol and
   timeframe. Missing data becomes `prepare_data`; too-short fresh listings defer to a
   concrete time; OI/funding families request enrichment; microstructure remains blocked
   as `NEEDS_MICRO_DATA`.
3. **Unblock:** blocked sweeps return to `queued` when their data gate clears.
4. **Execute:** in apply mode, bounded prepare/enrich/sweep work runs. Successful prepare
   can re-plan into a sweep in the same cycle.
5. **Worker:** `--run-worker` drains a bounded number of compute jobs from
   `strategy_lab.sqlite`. Its process ownership lease remains renewable through
   evaluation, terminal queue publication, secondary indexes, status publication, and
   release. The queue-job lease stops only at the serialized terminal queue transition;
   it is never renewed after completion. Real completed candle/variant milestones make
   progress visible. A process-lease, job-lease, identity, fence, or release failure is
   a fail-closed priority-worker error that interrupts the foreground farm instead of
   being reported as `worker_already_running`.
6. **Classify:** completed runs are read from `metrics.json`, written to
   `unique_candidates`, and exported for validation when eligible.
7. **Validation:** `--run-validation` exports candidates, runs the honest-backtest bridge
   in-process, stamps verdicts back into `farm_results` and `unique_candidates`, and
   writes `setup_library` cards. The handoff uses bounded stored trade records in
   `metrics.json`; legacy aggregate-only artifacts are rebuilt from local candles when
   possible, never guessed from averages. Only IDs exported and completed in this exact
   invocation may be stamped or activated. A pending manifest revokes the prior generation
   before producer side effects begin; the completed atomic `HardValidationGeneration.v1`
   manifest content-binds tasks, requests, producer/validator and paper-reader code,
   reports, verdicts, and cards. The priority worker continuously drains at most
   `--max-validations` ready candidates per slot. Its bounded fair scan continues past
   orphan, ineligible, or artifact-unavailable head tasks; repeated no-artifact or
   no-verdict attempts terminalize instead of reclaiming forever. When the active
   validation backlog reaches `--validation-backlog-high-water`, classification remains
   queued until service capacity is available. Status publishes active/eligible counts,
   oldest age, one-hour arrival/service rates, and a drain estimate; the operator SLO is
   `--validation-backlog-slo-seconds`. Zero exportable candidates preserve the prior
   completed generation rather than activating historical or incomplete artifacts.
8. **Follow-up:** hard-validation feedback is converted into bounded
   `schedule_followup` tasks. Queueable actions (`NARROW_PARAMS`, `WIDEN_PARAMS`,
   `REGIME_SWEEP`) become ordinary typed `run_sweep` tasks and then use the same
   worker path as every other calculation. Follow-ups are capped, deduped, TTL-bound,
   and can be disabled with `--no-followups`.
9. **Paper:** `--run-paper` reads only current-generation `PAPER_FORWARD_READY` setup
   cards (or explicitly legacy cards before the first generation manifest), builds
   `PaperTradePlan`, simulates against local prepared candles, writes
   `paper/paper_trades.jsonl`, and upserts `paper_outcomes`. A setup card is paper-ready
   only if hard validation passed and executable params include `hold_bars`, `stop_pct`,
   `take_pct`, with percent-point units and at least 1:2 reward/risk.
10. **Derived setup lifecycle:** status/reporting rebuild positive, negative, mixed, and
   no-sample setup groups from canonical artifacts. These groups are research evidence,
   not promotion/deletion rules.
11. **Pivot:** the cycle reports `work_available`, `advanced_lifecycle`,
   `discovery_refill`, or `blocked:no_eligible_tasks`.

## Parameter Authority

`strategy_registry.py` remains the source of truth for strategy IDs, defaults,
timeframes, and required data. `configs/strategy_lab/param_schemas.yaml` is only a
validator overlay: it rejects unknown keys, invalid types/ranges, bad percent units, and
paper/LLM proposals whose `take_pct` is less than `2 * stop_pct`.

Internally generated farm sweeps may normalize old registry defaults into executable
1:2 exits before calculation. External/LLM proposals are not silently normalized; they are
rejected with explicit reason codes.

## Data Gates

- **Funding:** keyless public OKX funding-rate history, enabled by `--enrich-funding`.
- **Open interest:** keyless public OKX open-interest history, enabled by `--enrich-oi`.
  A merged `oi` field clears `NEEDS_OI_DATA`.
- **Microstructure:** no keyless public provider for `obi_top5`, `trade_delta_100`, or
  `spread_bps`; these families honestly remain `NEEDS_MICRO_DATA`.

## Logs

Structured legacy logs live under `<private_root>/logs/farm/`. Automatic maintenance
reports their size but does not rotate or truncate them:

- `cycle_log.jsonl` - one row per cycle.
- `task_transitions.jsonl` - one row per state change.
- `errors.jsonl` - worker/cycle errors.

Operators should use `status` and `farm_status_report`; raw logs are audit material, not
the normal dashboard.

The candidate registry uses an immutable atomic write-ahead segment followed by one
bounded fsynced append to the compatibility JSONL path. Normal job publication is
therefore O(delta) and never rewrites the historical registry in the worker critical
path. A segment is removed only after the append is durable; readers merge and
deduplicate any segment retained by an interrupted cleanup. An in-place compact rewrite
fails closed while retained segments exist; transactional offline compaction remains an
explicit maintenance concern.

`farm_priority_worker_status.json`, `worker_status.json`, RCC heartbeat
`compute_pipeline`, and `operational_health` expose the same redacted compute state.
`claim_failed` and `worker_failed` are active hard failures, not ordinary retryable
source errors. Stale failure artifacts from a stopped farm remain evidence but do not
impersonate a current hard failure.

The legacy `storage_policy` remains report-only. A separate runtime-storage
capability now supports writer-coordinated sealing: appenders close completed
lines under one OS lock, atomically rename an oversized active file, and release
the sealed source only after the content-addressed archive records a successful
restore proof. An interrupted archive keeps the sealed source for the next
maintenance pass. Recent structured rows remain in a bounded tail projection;
semantic ids required for lineage and invocation deduplication remain in a
compact SQLite index rebuilt before initial cutover. Current JSON projections,
task/ownership databases, queue/outbox state, stop intents, and Paper Evidence
generation files are never rotation targets.

This path is off by default. A normal farm `apply` cycle still grants no storage
authority: activation requires an exact source root, exact archive root, exact
revision, a separately passed fresh owner manifest, and a pre-existing archive
capability covering every stream. Once activated, archive or budget failure is
fail-closed rather than hidden by the legacy best-effort reporter. The budget
checks controlled source bytes, content-addressed archive bytes, and independent
minimum free-space floors for both source and archive volumes.

## Commands

```bash
# Plan only, writes nothing.
python -m scripts.strategy_lab.farm_loop --once --dry-run

# One compute cycle.
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --enrich-funding --enrich-oi

# One full compute + validation + paper cycle.
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --enrich-funding --enrich-oi

# Continuous full cycle.
python -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation --run-paper --enrich-funding --enrich-oi --sleep-seconds 180 --stop-file STOP

# Visible operator wrapper.
bat\strategy_lab_farm_full_cycle_loop.bat
bat\strategy_lab_farm_full_cycle_stop.bat

# Status.
python -m scripts.strategy_lab.status
python -m scripts.strategy_lab.farm_status_report
```

## Honest Limits

- `FAILED_COSTS` and `NEEDS_MORE_DATA` are not runtime failures. They mean validation
  refused to create a paper setup.
- Paper simulation does nothing until a setup card is actually `PAPER_FORWARD_READY`.
- Microstructure is blocked until a provider exists.
- Older `scanner_farm_loop`, `universe_farm_loop`, and `research_loop` remain available
  for diagnostics/history, but they are not the current farm core.
