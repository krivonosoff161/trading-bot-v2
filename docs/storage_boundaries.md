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
share a reference epoch. Existing logs are `legacy_uncoordinated_storage`; safe append
segments and rotation require a separately frozen Package 08B and a later explicit
runtime rollout.

No public command activates quarantine for current private data. A future rollout needs
an exact path inventory, backup, quiescence/writer adoption, dry reachability/parity
report, abort metrics, rollback, and a new owner decision.
