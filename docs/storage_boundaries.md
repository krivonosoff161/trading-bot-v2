# Storage boundaries

Portfolio-level documentation and storage authority is defined in the
[Documentation Contract](https://github.com/krivonosoff161/krivonosoff161/blob/main/docs/documentation-contract.md).
This page narrows that contract for the public trading workbench.

This repository is the trading application and research backbone. Keep Git for
source code, public-safe documentation, configuration templates, tests, and
small deterministic fixtures.

Do not commit raw runtime or farm output:

- `.env` files, provider keys, Telegram tokens, exchange credentials
- live or paper trade ledgers with private routing details
- SQLite databases, worker state, heartbeat snapshots, runtime logs
- raw model prompts or responses
- generated farm experiments, market-data caches, and large backtest outputs
- agentic security canary run directories

Use local storage for raw artifacts:

```text
%USERPROFILE%\research-artifacts\trading-farm\
%USERPROFILE%\research-artifacts\security-harness\
```

Research that should become durable can be summarized in `docs/` or moved to the
private `trading-bot-research` repository after sanitization. The raw data stays
outside this repository.

## Destructive maintenance boundary

Automatic scanner and farm maintenance is report-only. The legacy storage-policy
helpers do not unlink cache files, prune event specs, truncate logs, or delete farm task
history even when an outer research command is in apply mode. Research `apply` is not
storage-destruction authority.

The public v2 quarantine proof is intentionally narrower:

- activation is supported only for a fresh dedicated child of the OS temporary
  directory using the fixed `synthetic_temporary_storage.v2` policy;
- production, repository, current private-root, and caller-defined activation are
  unsupported;
- a reserved `.storage-v2` manifest, marker, operation journal, lock, staging, and
  quarantine tree are never cache candidates;
- quarantine preserves exact bytes and relative paths on the same volume and supports
  content-bound restore; it never deletes evidence or overwrites an occupied path;
- moving bytes to same-volume quarantine reduces only a logical active-cache count.
  It reports `physical_bytes_reclaimed=0` and does not free disk space.

Event-spec reachability can be reported from synthetic copies of the farm-task and
strategy-lab databases, but cannot authorize a move: legacy producers and readers do not
share a reference epoch. Existing logs remain `legacy_uncoordinated_storage`.

## Writer-coordinated synthetic segments

The public segmented JSONL protocol is a separate, off-by-default proof over a freshly
activated `synthetic_temporary_storage.v2` root. It provides nine fixed farm/scout
stream mappings, canonical frames, a hash-chained SQLite intent journal, deterministic
append/seal recovery, and full-stream verification before returning records. It shares
the Package 08A root operation lock; it does not accept caller paths, schemas, stream
registration, thresholds, or durability modes.

The supported durability labels are deliberately narrow: allowlisted local ext/XFS/
Btrfs with dirfd-relative `renameat2(RENAME_NOREPLACE)` plus directory fsync, or fixed
local NTFS with no-replace `MoveFileExW(MOVEFILE_WRITE_THROUGH)`. These labels are not a
cross-platform power-loss-equivalence claim. Segment sealing preserves prior bytes and
does not delete, compress, quarantine, or reclaim physical space.

Only dependency-injected synthetic adapters can select this store. Current
`farm_journal.py`, `scanner_journal.py`, direct readers, launchers, status surfaces, and
automatic maintenance retain their legacy behavior. The package does not inspect,
import, merge, dual-write, rotate, or migrate current logs. Any private adoption still
requires an exact producer/reader inventory, backup, quiescence or atomic cutover,
parity/dedupe rules, abort metrics, rollback, and a new owner decision.

## Private archive foundation

The repository also contains an off-by-default `private_archive_storage.v1`
foundation. It is a capability and verification library, not a launcher,
configured path, producer migration, retention daemon, or authorization source.
It performs no environment discovery and exposes no default private root.

Activation requires an exact existing empty archive directory, an exact source
root, and a typed, current owner-authority input bound to this project, action,
roots, allowed artifact kinds, turn, and expiry. A non-synthetic archive must
be on a filesystem distinct from the source. The resulting root is bound by a
hashed capability and marker; links/reparse points and identity drift fail
closed.

Archive bytes are deterministic, content-addressed gzip objects. Immutable
hashed manifests are the source of truth; the local SQLite catalog is a
disposable query index that can be rebuilt from those manifests. Public-safe
derived JSONL is schema-checked and secret-scanned before archival and again
before bounded retrieval. Private payload archives are catalogued as
`metadata_only` and cannot be returned by the bounded public reader.

Migration plans bind the exact source path, digest, size, row count, revision,
schema, logical stream, and destination capability. Apply revalidates the
source, proves copy parity, and performs the requested decompression/restore
check itself before recording success. Read selection is a hash-chained,
append-only compare-and-append event bound to that same logical stream:
promotion requires copy and restore verification, rollback selects the
preserved legacy source, and neither transition deletes either copy.

The current repository does **not** activate this capability, connect it to an
RCC/farm producer, archive private runtime data, rotate files, reclaim disk, or
perform a real cutover. The checked tests use temporary synthetic roots only.
A private rollout still requires a separate exact-root authority, inventory,
DB/WAL/SHM-safe backup and restore proof where applicable, producer quiescence,
reader/writer parity, abort metrics, operational evidence, and an owner-approved
cutover/rollback procedure.

No public command activates quarantine for current private data. A future rollout needs
an exact path inventory, backup, quiescence/writer adoption, dry reachability/parity
report, abort metrics, rollback, and a new owner decision.
