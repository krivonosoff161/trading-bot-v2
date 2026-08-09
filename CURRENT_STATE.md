# Current State

Status: **CURRENT**

- Verified: 2026-08-09
- Verified against: `3d0ffc1b9e0311756e6ecf879c11e228dbad3501`
- Scope: implemented public capabilities, bounded limitations, and next gates
- Evidence: [machine roadmap](docs/trading-portfolio-roadmap.yaml) and the
  production tests linked for each module
- Residual risks: private runtime health, data completeness, and service
  availability are deliberately excluded.
- Next gate: complete a separately owner-authorized long paper-only canary
  before claiming continuous operational reliability.

This page describes the public repository, not a live runtime. It does not
publish process identities, private database rows, observations, outcomes,
model conversations, recipients, credentials, or workstation-specific state.

## Implemented Public Contracts

| Capability | Status | Evidence ceiling |
|---|---|---|
| Public research ingestion | implemented bounded | Adapters and degraded modes are tested; continuous provider availability is not proved. |
| Strategy Lab and experiment registry | implemented bounded | Bounded scheduling, lineage, and candidate records exist; a long reliability window remains open. |
| Deterministic simulation | implemented bounded | Declared truth tiers and synthetic parity are covered; full market execution fidelity is not claimed. |
| Independent validation bridge | implemented bounded | `honest-backtest` can try to falsify a candidate; a pass means only not rejected. |
| Fenced candidate lifecycle | implemented bounded | Owner, fence, claim, generation, and idempotency contracts are tested. Continuous validation maintenance honors the configured batch capacity, scans a bounded window past terminal orphan/ineligible and artifact-unavailable head tasks, and applies upstream classification backpressure at a configurable high-water mark without discarding classify work. Aggregate backlog, oldest age, arrival, service, and drain estimates are observable; a finite retry budget terminalizes repeated failures. A batch with no exportable candidate preserves the last valid generation; private runtime continuity and SLO compliance remain operational questions. |
| Paper observation | implemented bounded | Current-generation authority is content-verified once per cycle, active cards are loaded directly without a historical catalog scan, and signal evaluation preserves prefix no-lookahead semantics inside a declared-history-bounded recent horizon with completed-chunk progress and fail-closed cancellation. Public-data failures are classified as `provider_error`, `data_gap`, or `genuine_no_market_data`; they preserve the active observation, append separate operational incident/recovery evidence, and are censored from family ranking, paper/product training, setup memory, adaptive consumers, and LLM outcome review. The canonical farm now requires an explicitly activated Paper Evidence v2 cutover, binds its writer lease to the same owner/process identity, atomically promotes bridge through projection, and blocks stale-generation Telegram delivery. Operational cutover and continuous private-runtime recovery remain unproved. |
| Research Control Center | implemented bounded | The canonical paper-only supervisor separates process/authority, database, and real product-progress lanes. T+0 requires completed scanner and farm work from the current run; stale stages, validation SLO breach, generation races, or technical learning leakage fail closed, while a completed zero-signal pass is honest idle and provider degradation remains observable. A fresh 48-hour canary is still required. |
| LLM advisory contour | implemented bounded | Inputs and outputs are bounded proposals; deterministic code owns calculations, verdicts, state changes, and permissions. |
| Evidence/storage capability | implemented bounded | Content-bound archives, synthetic migration, and plan-digest-bound backup retention exist; applying them to private roots remains separately owner-gated. |
| Paper-card delivery | implemented bounded | Preview, deduplication, and guarded delivery exist; recipients, content, and acknowledgement state stay private. |
| Execution denial boundary | implemented | No supported entrypoint grants live order authority. |

The detailed ownership, evidence paths, missing proof, and next gate for every
row are canonical in the [Trading Portfolio Roadmap](docs/trading-portfolio-roadmap.md).

## Open Evidence Gates

1. **Storage containment:** apply the reviewed retention plan only after
   post-merge code verification, preserve one integrity/restore-verified full
   generation, prove archive restoration and second-apply idempotence, then
   enforce the storage budget before runtime.
2. **Operational reliability:** complete the bounded paper-only canary without
   a real hard fail. Its preflight must first prove the reviewed backup/free-space
   budget. A clean unit-test suite is not runtime proof.
3. **Data and lifecycle continuity:** demonstrate that missing public data,
   interrupted work, delivery ambiguity, and recovery preserve lineage and do
   not manufacture outcomes or duplicates.
4. **Validation service SLO:** prove during the paper-only canary that the
   configured service capacity drains rather than grows the validation backlog,
   the oldest-age SLO remains bounded, and backpressure never loses classify work.
5. **Paper authority cutover:** after all product-chain phases merge, prove
   quiescent backup/restore, shadow parity, exact-revision marker activation,
   writer/fence identity, and rollback readiness before RCC launch.
6. **Quality calibration:** only after reliability, measure signal, validator,
   role, and LLM advisory quality against immutable held-out evidence.

GitHub issue #224 tracks end-to-end paper-only observation. Issue #155 remains
a later calibration task; it is not evidence that policies are ready to change.

## Explicitly Not Supported

- Live trading, real-money orders, private account actions, or private exchange
  endpoints.
- Profitability, signal-service, investment-advice, or live-readiness claims.
- LLM authority over prices, parameters, validation verdicts, lifecycle state,
  process control, credentials, or execution.
- Publication of private datasets, strategy parameters, candidate rankings,
  runtime logs, prompts/responses, recipients, or operational evidence.

## Verification Boundary

Use [docs/entrypoints.md](docs/entrypoints.md) only as a command contract. A
runtime start still requires a fresh external owner-authority manifest and the
[Farm Runbook](docs/farm_runbook.md) preflight. Public documentation never
grants process authority.

The development sequence is maintained in [ROADMAP.md](ROADMAP.md).
