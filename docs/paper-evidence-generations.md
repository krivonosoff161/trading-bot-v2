# Paper Evidence Generations

Status: **ACTIVE CODE CONTRACT; RUNTIME ROLLOUT DEFERRED**. Updated 2026-07-18.

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

`run_paper_generation_v2()` is a dependency-injected coordinator, not a default
launcher. Its caller must supply all of the following:

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
monotonic fence, and mutation sequence in the same database transaction.

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

## Activation, Migration, And Rollback

No supported launcher currently activates v2, and this package performs no
private migration or runtime rollout. A later rollout requires a separate,
explicit operator instruction plus quiescence, backup, dry inventory, shadow
replay, parity/exception reporting, abort metrics, and forward-only recovery.

Legacy artifacts are never upgraded by guessing missing identities. After v2
authority is activated, rollback may disable v2 consumers or use a v2-aware
compatibility reader, but it must preserve the database, event chains,
revisions, cursors, account generations, and materialization history. Returning
replacement-mode v1 writers to authority is not a valid rollback.

All public tests use temporary roots, synthetic rows, and temporary SQLite
databases. They do not read private runtime rows, choose a real provider, start
or stop processes, load credentials, send Telegram, or call exchange endpoints.
