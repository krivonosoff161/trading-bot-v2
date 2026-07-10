# Architecture

Status: **ACTIVE**. Updated 2026-07-10.

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

## Current And Deferred Work

The active public workbench ends at paper evidence and independent validation.
The proposed adaptive paper architecture is deliberately **deferred**, recorded
in [docs/deferred-adaptive-paper-architecture.md](docs/deferred-adaptive-paper-architecture.md).
It is not a permission to revive `main.py` or enable live trading.

## Operations

Use [Farm Ownership Map](docs/farm_ownership_map.md),
[Farm Runbook](docs/farm_runbook.md), and
[Entrypoint Catalog](docs/entrypoints.md) together. The catalog is authoritative
for launchers; no legacy command becomes supported merely because it exists.

## Storage

Public Git holds source, tests, public-safe documentation, templates, and
small deterministic fixtures. Local/private storage holds data, logs,
credentials, model conversations, candidate rankings, journals, and raw
research output. The binding repository policy is
[docs/storage_boundaries.md](docs/storage_boundaries.md).
