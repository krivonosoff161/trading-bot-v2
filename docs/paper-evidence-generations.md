# Paper Evidence Generations

Status: **CURRENT**

- Verified: 2026-08-29
- Verified against: `2eeb6a646040ea23cead64cb36c83de974adb2bd`
- Scope: immutable paper-evidence authority, canonical integration, and gated cutover
- Evidence: [paper evidence tests](../tests/test_paper_evidence_store_v2.py)
  and [cutover tests](../tests/test_paper_generation_cutover.py)
- Residual risks: public integration does not establish that private cutover,
  parity, backup, or a long canary has succeeded.
- Next gate: separately authorized operational shadow/parity, cutover, and
  runtime proof.

This document defines the public v2 paper-evidence boundary. The implementation
is paper/research only. It cannot place orders, enable execution, read a private
exchange account, or turn simulated outcomes into profitability claims.

## Authority Layers

| Layer | Role | Authority |
|---|---|---|
| Immutable SQLite events | Subjects, exact candle observations, lifecycle transitions, account events, revisions, writer fences, and run manifests | Sole v2 lifecycle and paper-account authority |
| Completed projection | Content-addressed, generation-tagged current view rebuilt from one completed run | Read authority for current downstream presentation only |
| JSON/JSONL files | Replaceable exports and historical v1 artifacts | Display-only; filename presence never proves v2 completion |

`PaperEvidenceStore` uses an explicit `activate()` operation. Ordinary readers
open an existing database read-only and do not create it, migrate it, or change
its journal mode. Once a selected authority database exists, an incomplete,
unreadable, or digest-mismatched generation fails closed; readers do not fall
back to a convenient legacy filename.

## One Generation Run

`run_paper_generation_v2()` remains dependency-injected. The canonical farm
reaches it only through `CanonicalPaperGenerationRuntime`, after validating an
external cutover marker and opening an already activated store. Its caller must
supply all of the following:

- an explicitly activated `PaperEvidenceStore`;
- a live co-located `paper_evidence_writer` lease and fence;
- an immutable paper-account genesis ID;
- a bounded public or synthetic candle provider;
- explicit producer sequence, parent, code, method, simulator, and lifecycle
  identities.

The run records bridge, consumer, queue, observer, account, and projection
stages under one `PaperGenerationRun.v2`. The completed producer manifest,
stage digests, lifecycle/account intents, projection materialization, and
current-run pointer are revalidated before the final transaction. A failed
stage cannot promote a mixed generation, mutate account state, or authorize a
withdrawal. The previous completed projection remains current.

The canonical farm lease remains an outer process-ownership preflight. It does
not replace or bypass the writer lease stored in the paper-evidence database.
Every v2 mutation rechecks the co-located owner, process identity, expiry,
monotonic fence, and mutation sequence in the same database transaction. A
dedicated renewal thread keeps the paper-writer lease alive while bounded
public-data work is in progress. Renewal failure is propagated to the farm
foreground, and graceful stop joins the thread before releasing the outer farm
owner.

## Immutable Account And Lifecycle Evidence

Logical signal/runtime IDs are lookup keys only. A subject generation binds the
complete producer member, validation generation, queue content, simulator and
method identity. Relevant content changes create an explicit new generation;
active allocated positions cannot be silently transferred to changed content.

The account genesis binds currency, deposit, leverage, allocation, cost,
rounding, and method policy using integer microunits. A configuration change
requires an explicit child genesis and cannot reinterpret earlier events.
Lifecycle and account events are append-only, prior-hash chained, source-event
bound, and committed together. Revised outcomes append adjustment evidence;
they never overwrite the original close.

Withdrawal requires absence from an authenticated, complete producer
generation (or a future explicit revocation contract). Partial, failed, stale,
or digest-mismatched producer output withdraws nothing. Open account allocation
also remains reserved when its subject is withdrawn.

## Downstream Rules

Training export, lineage, preview, calibration, acceptance, product-quality,
snapshot, farm-status, and operational-health readers distinguish these states:

- `completed`: compatible current v2 generation;
- `legacy_unversioned_projection`: readable historical display only;
- `incomplete`, `run_mismatch`, `digest_mismatch`, or `unreadable`: no current
  items and no legacy fallback.

Rows used for learning or adaptation must bind the exact terminal lifecycle
event, paper subject generation, paper generation run, and account generation.
A mutable runtime ID is not sufficient authority.

`trading_policy_calibration` and the adaptive product-memory summary use the
same fail-closed row selector. It accepts only numeric terminal account results
whose run, subject and account IDs match the current completed projection.
Legacy files, stale generation reports, display-only projections and rows that
carry only a lifecycle-schema label remain visible as excluded counts; they
cannot demote, promote or rerank a geometry profile.
The paper cycle recomputes its calibration view from that selector; it does not
grant authority to a previously written calibration JSON file.

The canonical v2 path does not execute the legacy product-ledger, thesis/exit,
or unrelated product-event training writers because they do not own a v2
generation envelope. Their historical files remain display/reference evidence.
Preview, paper training, lineage, retest selection, System Analyst tasks, role
dispatch, calibration, setup memory, quality reporting, and Telegram delivery
must match the exact current run. Stale accepted role work is skipped rather
than dispatched into the new generation.

## Activation, Migration, And Rollback

The canonical farm launcher now requires
`STRATEGY_LAB_PAPER_EVIDENCE_V2_REQUIRED=1`. It does **not** activate or
migrate a database implicitly. Startup fails closed unless
`state/paper_evidence_cutover.v2.json` is active, digest-valid, paper-only,
points to the one canonical relative database path, and names an existing
account generation whose model digest matches the store. Activation and every
canonical open also compare the marker's code identity with the bounded local
`git rev-parse HEAD`; a marker from another revision is rejected before the
writer lease or any materialization is acquired.

The supported operational primitives are:

- `python -m scripts.strategy_lab.paper_generation_cutover ... shadow-parity`
  compares normalized legacy and v2 content in a shadow root;
- `... shadow-replay --shadow-root <new-root> ...` copies only the authenticated
  paper-signal ledger to a distinct root, performs one bounded public-data v2
  run, and emits aggregate hashes/parity without copying configuration,
  delivery state, identities, or credentials;
- `... activate --code-identity <exact-revision> --confirm-quiescent` creates
  or opens the selected v2 store, creates the immutable account genesis, proves
  integrity, and publishes the active marker last;
- `... rollback` disables a pre-runtime cutover by replacing the marker with a
  digest-bound `rolled_back` state. It deletes neither the database nor its
  evidence, and a second rollback changes nothing.

These primitives do not prove the required operational preconditions. The
operator must first prove zero owners/processes, WAL-aware backup and restore,
database integrity, exact revision equality, shadow parity or documented
exceptions, and a safe storage budget.

Legacy artifacts are never upgraded by guessing missing identities. The first
v2 run is a forward-only generation over currently authenticated active
signals. Once v2 runtime has published evidence, returning replacement-mode v1
writers to authority is not a valid rollback. Incomplete or corrupt v2
authority continues to fail closed rather than falling back to filenames.

All public tests use temporary roots, synthetic rows, and temporary SQLite
databases. They do not read private runtime rows, choose a real provider, start
or stop processes, load credentials, send Telegram, or call exchange endpoints.
