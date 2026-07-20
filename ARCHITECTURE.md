# Architecture

Status: **ACTIVE**. Updated 2026-07-18.

This file is the current architectural source of truth. Dated reports and
older plans under `docs/` are historical evidence unless the
[Docs Home](docs/README.md) explicitly promotes them.

## Operating Boundary

The supported system is paper/research infrastructure. It may obtain public
market data and write local research artifacts, but it must not place orders,
use private exchange-account endpoints, or grant an LLM execution authority.

```text
scanner/news intake          manual hypotheses
        |                           |
        +----------> calculation farm <---------- public market data
                               |
                     deterministic sweep/classify
                               |
                  honest-backtest validation bridge
                               |
                  PAPER_FORWARD_READY setup cards
                               |
                paper observation + outcome records
                               |
              optional preview / explicit delivery edge
```

The diagram describes evidence flow, not a promise of performance or a live
execution path.

## Ownership

| Component | Owns | Cannot do |
|---|---|---|
| `src/scout/` | Public information intake and normalized context | Promote a trade or execute an order. |
| `src/research_lab/` | Farm scheduling, bounded sweeps, paper lifecycle | Import the old execution engine or access credentials. |
| `scripts/strategy_lab/` | Operators, workers, bridge invocation, reports | Bypass deterministic validation. |
| `honest-backtest` | Independent validation methods and verdicts | Run the farm or place orders. |
| Paper runtime | Observation, accounting, outcomes, card previews | Convert paper state into exchange actions. |
| LLM sidecars | Bounded advisory JSON or presentation text | Alter prices, verdicts, registry, permissions, or `.env`. |
| Telegram surfaces | Optional human-facing delivery | Become a farm controller or executor. |

## LLM Governance

LLMs are optional advisory sidecars. The calculator and reviewer may receive
sanitized, bounded input packs and return schema-validated proposals. Their
output is rejected unless deterministic validators accept it. They cannot read
credentials, raw private data, live account data, or call an order path.

See [LLM Proposal Contract](docs/llm_proposal_contract.md) and
[Local Calculator Mini-Swarm](docs/local_calculator_swarm_2026-07-10.md).

## Adaptive Research Center

Issue #172 activates the previously deferred paper-only feedback architecture.
The implementation uses four bounded roles:

1. the farm explores a typed, versioned parameter space with a reproducible
   resource-bounded sampler;
2. the validator owns independent evidence gates and applies declared costs
   exactly once;
3. the Trader Supervisor owns a deterministic, replayable per-symbol state
   machine and references private visual evidence without granting it authority;
4. the System Analyst routes evidence-backed environment candidates separately
   to farm, validator, and trader.

An environment candidate is not applied policy. The canonical cycle first runs
the recipient-owned request-contract gate and acknowledges the resulting
research request. Applying any policy still requires a later deterministic gate
and untouched evaluation. See [Adaptive Research Center Contract](docs/adaptive-research-center-contract.md).

The return path is explicit: accepted `RoleTaskSpec.v1` requests map to the
existing `schedule_retest`, `export_validation`, and deterministic paper-replay
owners. Terminal results become `SystemAnalystResultInput.v1`, are reviewed
once, and may create one bounded next generation. Generation two is terminal.

This architecture does not revive `main.py`, enable live trading, or permit a
model to edit code, weights, verdicts, levels, or process configuration.

## Operations

Use [Farm Ownership Map](docs/farm_ownership_map.md),
[Farm Runbook](docs/farm_runbook.md), and
[Entrypoint Catalog](docs/entrypoints.md) together. The catalog is authoritative
for launchers; no legacy command becomes supported merely because it exists.

`bat/research_control_center.bat` is the unified human-facing supervisor. It
keeps the public news publisher, scanner-to-watch-queue intake, canonical farm,
interactive Telegram bot, dashboard, graph builder, and local Ollama sidecar as
separate process owners. The UI starts with every contour disabled, prevents a
second control-center instance and duplicate canonical-farm ownership, records a
private heartbeat, and contains no execution or private-exchange entrypoint.
If a prior center left a local process running, recovered heartbeat or port
evidence is display-only. PID/start time and even an expected executable prove
liveness and identity checks, not stop ownership; only a process retained by
the current center's own `Popen` handle is stoppable.
Its second status line explains per-role work issued, queued, waiting,
completed, returned to the analyst, and the current bounded generation.

Canonical farm and standalone-worker mutation authority is persisted in
`ownership.sqlite` as an exact process identity, random owner instance,
renewable expiry and monotonically increasing fence. All apply modes, including
one-shot farm runs, acquire the same canonical resource. A stop intent can be
acknowledged only by that current owner/fence; mutable lock files and heartbeat
bytes are never authority.

Brain tasks and compute jobs use separate fenced claims. State transitions,
attempt history and audit rows commit transactionally. An executing attempt
whose lease expires remains `ambiguous`; a later fence cannot rewrite it.
Sweep dispatch uses a content-bound brain outbox because two SQLite databases
cannot share one atomic commit. Worker output remains provisional until a final
owner/fence check; run import and queue completion then commit together before
secondary indexes are published.

### Paper Evidence Authority

The public v2 paper-evidence implementation separates immutable authority from
replaceable views. A co-located SQLite store owns paper subject generations,
exact observation batches, lifecycle and account events, revisions, run-stage
manifests, writer fences, and the current-run pointer. A completed projection is
a verified read view over one atomic run; JSON/JSONL files and legacy v1 output
remain display-only and cannot become authority through filename presence.

The coordinator API is dependency-injected and off by default. No supported
launcher currently activates or migrates private v2 state. Future rollout must
retain the canonical farm lease as an outer preflight and independently acquire
the co-located paper writer fence. See
[Paper Evidence Generations](docs/paper-evidence-generations.md).

## Storage

Public Git holds source, tests, public-safe documentation, templates, and
small deterministic fixtures. Local/private storage holds data, logs,
credentials, model conversations, candidate rankings, journals, and raw
research output. The binding repository policy is
[docs/storage_boundaries.md](docs/storage_boundaries.md).

Automatic scanner/farm storage maintenance is report-only: outer research `apply` does
not authorize cache unlink, log truncation, event-spec pruning, or farm-history deletion.
The v2 quarantine API is an off-by-default synthetic OS-temp proof with a fixed root
capability and durable restore evidence; it is not activated for private storage and
does not reclaim physical bytes. The separate segmented JSONL API proves coordinated
canonical append, immutable intent evidence, no-replace seal, crash recovery, and
full-stream reads only for explicit adapters over the same synthetic capability and root
lock. No launcher or current farm/scanner producer or reader selects it. Legacy append
logs remain `legacy_uncoordinated_storage` until a separately authorized private
inventory, cutover, parity, and rollback package is completed.
