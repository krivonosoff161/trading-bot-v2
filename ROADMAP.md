# Roadmap

Status: **ACTIVE**. Updated 2026-07-18.

The project is a paper/research workbench. This roadmap is ordered by evidence
and maintainability, not by a path to live trading.

## Completed Foundation Gate

The shared environment repair now has one stable/product checkout plus one
development worktree, compact replace-in-place continuity, classified
archives/runtime data, bounded local-state write amplification, and a reviewed
Git integration path. Runtime authority remains separate from documentation and
Git state.

After that gate, repair the research station in this dependency order:

1. Candle truth, exact manifests and requirements owned by all 27 strategies.
2. Physical separation of decision-time and post-outcome data.
3. End-to-end source/news/data/code/experiment identity.
4. Complete search-trial evidence and untouched independent validation.
5. Honest GPU telemetry, reference simulation and unambiguous learning labels.
6. One immutable AdaptiveTrial through farm, validator, paper and analyst.
7. Prompt/tools/versioned-RAG/evals before any local-model weight training.

Implementation status (2026-07-14): all seven items above were reviewed in PR
[#178](https://github.com/krivonosoff161/trading-bot-v2/pull/178), passed the
non-live server checks, and were squash-merged into `main`. The private candle
store was migrated non-destructively with the JSON sources retained, and the
supported paper/research cycle was started under separate operator authority
for observation. DSR/PBO are intentionally shadow-only until measured evidence
justifies a separate promotion decision. LoRA/adapter training remains deferred.

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

## Current: Owner, Claim And Fencing Review

The public-code implementation adds exact process leases, fenced brain/compute
claims, append-preserving attempts, content-bound materialization replay and
provisional worker publication. Review and synthetic tests do not authorize a
private runtime migration. The rollout gate remains quiescence, backup, legacy
row disposition, v2-only writers and a separate operator decision.

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
- Extend the 27-family synthetic history-boundary proof with separately authorized
  private-history coverage manifests if that evidence campaign is approved.
- Independent validation improvements in `honest-backtest`.
- Operator reports that summarize paper evidence without publishing raw data.

## Deferred Decisions With Return Gates

| Decision | Why deferred | Return condition |
| --- | --- | --- |
| Delete old worktrees, JSON candle files or duplicate-looking paths | Identity, ancestry, runtime ownership and reproducibility are not yet fully proved | Separate inventory and parity report passes; explicit deletion decision |
| LoRA or adapter training | Zero accepted local-model advice has no proven root cause yet | Prompt/tools/RAG ablation leaves a stable measured residual failure |
| GARCH, Kalman, OU or alternative search | Complex methods cannot be compared honestly on uncertain data/trial identity | Candle Truth, exact trial identity and untouched validation are complete |
| Complex VRAM scheduler | GTX 1050 3 GB has no measured capacity benefit for concurrent LLM and numeric work | Hardware changes or profiling proves a real scheduling bottleneck |
| External simulator or vectorbt integration | Independent semantics and licensing require separate review | Local reference simulator exists and a license/isolation decision is approved |
| Automatic promotion or live execution | Paper/LLM agreement is not scientific or monetary authority | Remains out of scope unless a future explicit safety and product decision replaces this rule |

## Out Of Scope Without A New Decision

- Real-money execution or automatic orders.
- Giving LLMs permission to control exchange, process, or credential surfaces.
- Publishing private research, raw prompts/responses, paper journals, or
  candidate rankings.
