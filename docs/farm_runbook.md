# Farm Runbook

Status: **ACTIVE**. Updated 2026-07-18.

This is the supported operator path for paper/research work. It does not
authorize live trading, private account access, or publishing local output.
The launcher catalog is authoritative for command ownership:
[entrypoints.md](entrypoints.md).

## Before Starting

1. Confirm no separate legacy loop owns the same data or Telegram delivery.
2. Run the read-only status surface:

   ```powershell
   bat\strategy_lab_status.bat
   ```

3. Check the machine-readable boundary report:

   ```powershell
   python -m scripts.strategy_lab.operational_health
   ```

4. Do not start if the report indicates an execution-capable legacy surface is
   part of the intended path. Resolve ownership first.

## Supported Modes

| Mode | Command | Use when |
|---|---|---|
| Low-load collection | `bat\paper_product_headless_loop.bat` | Normal long paper/research collection; no dashboard or delivery. |
| Low-load collection with cards | `bat\paper_product_headless_send_loop.bat` | An operator has explicitly chosen paper-card delivery. |
| Visible operation | `bat\paper_product_control_room.bat` | Local observation needs dashboard/graph/status windows. |
| Bounded acceptance | `bat\paper_acceptance_headless_loop.bat` | Collecting a defined private evidence cohort. |

Use a single supported mode at a time. Never run a legacy or diagnostic loop in
parallel with the canonical farm.

## What A Canonical Cycle Does

```text
intake -> data preparation/enrichment -> bounded sweep -> classification
       -> independent validation -> paper observation -> outcome/lineage records
       -> optional preview or explicitly enabled delivery
```

The core does not send Telegram by default. A delivery edge may read local
configuration only when an explicit send launcher/flag is used. Delivery does
not alter a verdict, readiness, or execution authority.

## Fast Local Checks

```powershell
# Plan only; no compute work.
python -m scripts.strategy_lab.farm_loop --once --dry-run

# Status and lifecycle summaries.
python -m scripts.strategy_lab.status
python -m scripts.strategy_lab.farm_status_report

# Public-repository hygiene before any commit.
python scripts/ci/check_tracked_artifacts.py
```

Do not paste raw output, paths, IDs, or journal rows into public issues. Create
a sanitized aggregate if a result needs discussion.

## Stop And Restart

- Request a clean canonical stop with
  `bat\strategy_lab_farm_full_cycle_stop.bat`.
- Do not terminate arbitrary Python processes from an operator script.
- Before a restart, use the status command and make sure the stop intent is
  cleared through the documented utility if it was intentional.
- The loop wrapper does not pre-delete its stop file. The exclusive farm owner
  acknowledges it only after acquiring the current lease/fence. A competing
  launcher therefore cannot erase another operator's stop request.
- A PID, fresh heartbeat, old lock path or expected executable is not proof of
  ownership. Recovered processes remain visible but non-stoppable.
- The RCC liveness heartbeat is deliberately smaller than its UI status
  snapshot. A heartbeat `ui_snapshot.stage` that remains active identifies a
  slow display probe; it is diagnostic evidence, not authority and not proof
  that the owned contours stopped. Child identity, ownership, fencing, ports,
  and process liveness remain separate hard-fail inputs.
- RCC starts owned console contours in a dedicated process group so the
  documented CTRL_BREAK path remains available. Shutdown is dependency
  ordered (consumers before shared providers), has a bounded deadline, and
  reports any residual owned PID without forced termination.
- Active canary monitoring keeps fast process/authority samples independent
  from deeper SQLite health work. One failed probe is recorded as degraded and
  does not count as a completed sample; loss of the lane's monotonic freshness
  budget is the fail-closed boundary.
- The setup-outcome memory refresh streams its paper JSONL input and reads
  unique candidates in bounded chunks. Status milestones are published only
  after real inputs or rows complete, so a long HDD read is distinguishable
  from an unchanged heartbeat.

### Provenance-bound RCC marker clearance

The three contour markers written by a canonical Research Control Center
graceful stop are separate from the generic Strategy Lab JSON stop intent.
After independently proving project processes, live owners, and owned ports are
all zero, record the exact marker hashes and run:

```powershell
python -m scripts.strategy_lab.clear_rcc_stop_intents `
  --private-root <canonical-private-root> `
  --expect STOP_FARM_FULL_CYCLE.txt=<sha256> `
  --expect STOP_NEWS_SCANNER.txt=<sha256> `
  --expect STOP_PUBLIC_NEWS.txt=<sha256> `
  --json
```

Only after this dry check reports all three markers eligible may the exact
command be repeated with `--apply`. The utility accepts only the canonical RCC
payload shape and recorded hashes, validates every present marker before
changing any marker, and never reads or mutates a database. Repeating the
exact apply changes zero markers. A missing hash, changed marker, foreign
payload, active process, owner, or port is a blocked operational preflight; do
not substitute `del`, `Remove-Item`, or raw SQL.

## Schema Rollout And Rollback

The v2 task/queue changes are additive, but this public code does not authorize
mutating a live private database. Runtime rollout requires a separate operator
decision: quiesce all writers, back up both SQLite files, verify one canonical
owner, then invoke the separately authorized migration operation that calls
`activate_farm_fencing_v2` and `activate_fencing_v2` before any runtime
initializer. Ordinary launchers, dashboards and status commands refuse a legacy
schema and never activate it. Abort on any legacy-writer trigger failure or
`legacy_running_unfenced` row requiring disposition.

After the first v2 fence is issued, do not downgrade to unfenced writers.
Rollback is forward-only: stop/disable consumers or deploy a v2-aware reader
while preserving ownership, transition, attempt and outbox history. Never reset
a fence or manufacture owners for historical rows.

## Data And Storage

Local/private artifacts include market-data caches, SQLite state, journals,
delivery audit, LLM invocation records, and outcomes. Their exact paths are
documented in [REMOTE_DATA_MANIFEST.md](REMOTE_DATA_MANIFEST.md); request only
sanitized summaries. The governing public/private policy is
[storage_boundaries.md](storage_boundaries.md).

## Escalation Conditions

Stop and investigate before continuing if:

- an execution-capable path appears in the canonical process chain;
- a launcher tries to read/write a credential unexpectedly;
- two independent delivery owners are active;
- a public artifact contains raw runtime data, private research, or a model
  conversation;
- a validation or paper lifecycle record loses its lineage.

The detailed architecture and ownership rules are in
[farm_ownership_map.md](farm_ownership_map.md) and
[farm_loop_lifecycle.md](farm_loop_lifecycle.md).
