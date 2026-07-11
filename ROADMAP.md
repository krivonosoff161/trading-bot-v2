# Roadmap

Status: **ACTIVE**. Updated 2026-07-11.

The project is a paper/research workbench. This roadmap is ordered by evidence
and maintainability, not by a path to live trading.

## Current: Documentation And Public-Safety Rebuild

1. Classify all documentation as active, reference, archive, or local-only.
2. Make the public onboarding and command catalog match the supported paper
   path.
3. Remove raw private research artifacts from the public index while preserving
   local copies outside Git.
4. Audit links, commands, boundaries, and cross-repository contracts.

Exit gate: a new contributor or search agent can identify the supported paper
path, distinguish it from legacy code, and avoid private data without relying
on a private handoff.

## Current: Verified Adaptive Research Center

1. Complete typed farm search, validation provenance, Trader Supervisor replay,
   and System Analyst feedback contracts under issue #172.
2. Run independent correctness, architecture, security, and documentation
   reviews and fix blocking findings.
3. Merge only after public/private scans and required checks pass.

Exit gate: all four roles exchange versioned, bounded artifacts without any LLM
gaining calculation, verdict, state-transition, or execution authority.

## Next: Paper Evidence Collection

1. Run bounded paper-only cycles with the canonical launcher.
2. Keep outcomes, lineage, and validation evidence in the local private root.
3. Review only sanitized aggregates and deterministic acceptance reports.
4. Change a family, threshold, or geometry profile only after the documented
   evidence gate is met.

Exit gate: enough fresh, reproducible paper evidence exists to make a bounded
research decision. This is not a profitability gate.

The former deferred design is retained as history in
[docs/deferred-adaptive-paper-architecture.md](docs/deferred-adaptive-paper-architecture.md);
the active contract is [docs/adaptive-research-center-contract.md](docs/adaptive-research-center-contract.md).

## Later Research Tracks

- Scanner source-quality and context calibration.
- Explicit data-gate handling for OI and microstructure.
- Independent validation improvements in `honest-backtest`.
- Operator reports that summarize paper evidence without publishing raw data.

## Out Of Scope Without A New Decision

- Real-money execution or automatic orders.
- Giving LLMs permission to control exchange, process, or credential surfaces.
- Publishing private research, raw prompts/responses, paper journals, or
  candidate rankings.
