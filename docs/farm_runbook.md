# Farm Runbook

Status: **CURRENT**

- Verified: 2026-08-29
- Verified against: `2eeb6a646040ea23cead64cb36c83de974adb2bd`
- Scope: supported paper-only preflight, start, monitor, and graceful stop
- Evidence: [RCC tests](../tests/test_research_control_center.py),
  [pre-heartbeat tests](../tests/test_rcc_preheartbeat_startup.py), and
  [entrypoint catalog](entrypoints.md)
- Residual risks: all runtime commands still require fresh external owner authority.
- Next gate: keep stop/finalizer behavior aligned with production code.

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
- After a host reboot, an expired persisted owner may be reclaimed during the
  next canonical acquisition only when its recorded process start is provably
  earlier than the current boot. This handles protected PID reuse without
  treating AccessDenied as process death on the same boot; ambiguous same-boot
  identity remains a startup refusal and every takeover advances the fence.
- The RCC heartbeat publishes the immutable RCC PID and process-start identity.
  Any finalizer must bind both values to the currently live process and fail
  closed when the start identity is missing or differs; it must never derive
  the expected start time by probing a heartbeat PID after the fact.
- Before that heartbeat exists, the RCC atomically publishes a digest-bound
  `state/control-center/startup.json` record with its exact revision, attempt,
  PID/process-start identity, completed startup stage, and paper-only boundary.
  `scripts.strategy_lab.rcc_startup_status` binds a launch to the new attempt
  and checks the live process generation. A constructor failure, malformed
  evidence, dead/PID-reused process, or missing new attempt after the bounded
  evidence grace is an immediate startup failure; exception messages and
  environment values are never retained. A missing contour listener remains a
  dependency-level `starting` state and is evaluated by its separate deadline.
- The same heartbeat publishes a bounded `shutdown` contract. A finalizer may
  request the documented graceful stop only for the exact live RCC generation
  while `shutdown.state=running`. When the identity-matched RCC reports
  `stopping`, its internal dependency-ordered hard-fail/operator stop already
  owns shutdown and the finalizer waits for quiescence instead of posting a
  second `WM_CLOSE`. `stop_failed`, missing state, or malformed timestamps fail
  closed and never authorize forced termination.
- RCC-owned child start identities are captured through the same canonical
  process probe used by external consumers; mixing native FILETIME seconds
  with a separately rounded process timestamp is not an identity comparison.
- The RCC liveness heartbeat is deliberately smaller than its UI status
  snapshot. A heartbeat `ui_snapshot.stage` that remains active identifies a
  slow display probe; it is diagnostic evidence, not authority and not proof
  that the owned contours stopped. Child identity, ownership, fencing, ports,
  and process liveness remain separate hard-fail inputs.
- RCC starts owned console contours in a dedicated process group so the
  documented CTRL_BREAK path remains available. Shutdown is dependency
  ordered (consumers before shared providers), has a bounded deadline, and
  reports any residual owned PID without forced termination. For contours
  that normally consume a stop marker, RCC waits for marker acknowledgement
  first and only then sends CTRL_BREAK to the same verified PID/start process
  group as its bounded graceful fallback.
- Active canary monitoring keeps the 15-second exact owned-process-handle lane
  independent from authority/fencing SQLite, filesystem/Telegram status,
  Windows listener inventory, deeper SQLite health work and completed product
  progress. The fast lane performs no SQLite open, status-file read, listener
  inventory, ancestry walk or PID reopen. Each dependency lane publishes its
  exact active stage and elapsed time in the minimal heartbeat and fails on its
  own freshness deadline. Listener ownership has its own 90-second freshness lane:
  a transient bounded inventory timeout cannot starve process/owner/fence
  sampling, but repeated loss still fails closed. A complete foreign or missing
  Ollama listener after T+0 remains an immediate hard failure. Scanner passes
  and farm cycles publish atomic safe aggregates only at real completion
  boundaries. The product lane requires current-run checkpoints before T+0;
  an ordinary heartbeat cannot refresh its scanner, farm, validation, or
  generation SLO. A completed pass with zero candidates is honest `idle`, and
  bounded public-provider failure is `degraded`; stale stages, validation SLO
  breach, generation mismatch, or retained technical learning evidence fail
  closed. The green T+0 product report carries the original launch-time run
  boundary into steady monitoring; the steady adapter must use
  `ProductProgressMonitor.from_green_t0_report`, recheck scanner/farm sequences,
  and must not rebase that boundary to the later T+0 observation. The first
  farm final checkpoint may cross its ordinary 300-second completion SLO during
  a cold setup-memory or paper-runtime pass. It remains `starting` only when
  `farm_progress` contains a fresh canonical stage plus real completed milestone,
  and only inside the fixed 600-second cold-start ceiling. A stale, malformed,
  or heartbeat-only progress row does not extend either deadline. Once current
  generation delivery, analyst routing, role reconciliation, setup memory,
  calibration, and quality evidence are complete, the farm publishes a separate
  mandatory checkpoint. Optional calculator and broad role-review LLM calls run
  afterward under the normal steady-state progress SLO and do not block T+0.
  Their later failure or staleness remains visible and fail-closed. The canonical
  RCC starts this supervisor
  with the paper profile: early owner/listener absence remains `starting`
  inside the bounded cold-start budget, then the first green owner/fence
  generation and a fresh, exact identity/fence-matched process-lease supervisor
  become the steady-state baseline. Their disappearance, expiry, generation
  change, supervisor failure/staleness, foreign Ollama listener, or
  required-contour exit is an immediate hard failure and invokes the same
  dependency-ordered RCC stop. A blocked deep probe cannot refresh the fast
  lane or suppress its deadline. A bounded external evidence adapter treats
  one ordinary listener/process probe exception as `degraded`, without
  inventing missing owner or supervisor fields; repeated probe loss fails when
  the monotonic freshness deadline expires. An explicit safety violation in a
  complete sample remains an immediate hard failure. After a successful
  hard-fail contour stop, RCC closes its own UI/instance through the existing
  application close event so final quiescence includes the supervisor process.
  RCC and external evidence adapters use the same listener inventory provider:
  an isolated Python child performs native Windows TCP enumeration through
  `psutil`, and its exact process tree is contained in a kill-on-close Windows
  job. Output uses a temporary file instead of inherited pipes, and timeout
  cleanup closes that exact kill-on-close job instead of synchronously waiting
  in `TerminateJobObject` behind a blocked kernel inventory call. The provider does not import project
  code and remains fail-closed on timeout, invalid output, or unproven cleanup.
  The minimal heartbeat exposes the current safe
  `runtime_probe.stage` (`spawn`, `inventory`, `cleanup`, `decode`, `complete`)
  so a freshness loss identifies the blocked stage without storing command
  output, process arguments, private paths, or listener payloads.
- The setup-outcome memory refresh streams its paper JSONL input and reads
  unique candidates in bounded chunks. Reject characterization loads and
  releases one run-artifact index at a time instead of retaining the complete
  multi-run corpus. The canonical farm also maintains a derived incremental
  reject-characterization cache bound to the classifier version/context, the
  complete candidate source digest, and each run artifact's size/mtime identity.
  A complete prior snapshot can bootstrap only unchanged pre-snapshot rows;
  changed or unavailable groups are re-read with the ordinary classifier. The
  cache is never validation or paper authority. Status milestones are published
  only after real inputs, rows, or run-artifact groups complete, and safe cache
  hit/recompute/reread counters are included in product progress. Both a
  canonical stop intent and the latched owner/claim failure are checked between
  real chunks, so shutdown and fail-closed cancellation do not depend on an
  artificial timer milestone.
- Validation maintenance applies the same contract to its current bounded
  batch. Export, deterministic checks, and artifact work publish a durable
  milestone only after a real chunk completes; the process lease stores that
  exact milestone. Owner/fence loss is checked before every write or lifecycle
  side effect. An empty current batch is deferred and generation-stamped
  without loading historical verdict/request directories, so stale candidate
  references cannot turn a no-op batch into an unbounded maintenance scan.

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
command be repeated with `--apply`. The utility accepts the canonical RCC
payload shape for all three names. It also accepts the documented
`strategy_lab_farm_full_cycle_stop.bat` payload only for
`STOP_FARM_FULL_CYCLE.txt`, because that entrypoint can replace the farm marker
during a coordinated external stop. Every accepted marker remains bound to its
recorded hash; the BAT payload is never accepted for the scanner or public-news
marker. The utility validates every present marker before changing any marker
and never reads or mutates a database. Repeating the exact apply changes zero
markers. A missing hash, changed marker, foreign payload, active process,
owner, or port is a blocked operational preflight; do not substitute `del`,
`Remove-Item`, or raw SQL.

### Owner-authorized legacy marker disposition

An unrecognised marker remains fail-closed: the normal command above must not
be used to reinterpret it.  The only exception is a separately authorized,
hash-and-byte-bound migration for the two scanner/public-news marker names.
Before it can run, independently prove zero project processes, owners and
owned ports, preserve the exact bytes in the approved private evidence root,
and require both names, SHA-256 values, byte counts and an authority ID.  The
command is dry-run first; its apply mode archives both exact bytes and a sealed
private manifest before it removes either source.  It rejects every other name,
encoding, root, link/reparse escape, hash drift and archive collision.  A
replay may only verify the same archive and change zero markers.  This is a
typed incident disposition, never a general-purpose marker deletion mechanism.

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

## Validation Maintenance Disposition

An `export_validation` head task is not allowed to revoke a valid generation
until its unique-candidate identity is eligible and its request can be prepared.
The runtime scans a bounded number of claimed tasks, terminally skips old
missing or ineligible identities under their existing fence, and continues to a
later eligible task. Recent candidate-visibility gaps and temporarily missing
artifacts defer for a bounded retry; repeated no-verdict or unexportable work
becomes terminal after the documented attempt budget.

Bulk disposition is an offline maintenance operation. Prove processes, owners,
ports, running tasks, and database integrity are quiescent; write the plan only
to private evidence storage; review its reason counts and digest; then apply the
same digest through `scripts.strategy_lab.validation_task_disposition`. Never
substitute raw SQL. Reapply the exact plan to prove that zero rows change.

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
