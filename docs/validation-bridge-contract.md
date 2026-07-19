# Validation Bridge Contract

Status: **REFERENCE CONTRACT**. Version: `1.3.0`.

Version `1.3.0` adds immutable simulator-assumption identity, evidence-tier
claim ceilings, unsupported execution dimensions and tagged metric state.
Version `1.2.0` added time-aware observation, complete-family panel, split, and
dependence-evidence profiles. Legacy request files are not silently upgraded:
they must be regenerated from their source candidate. The
bridge cannot guess cost ownership, PBO orientation, common timestamps,
feature horizons, family exclusions, or a dependence model.

`trading-bot-v2` produces bounded research candidates and consumes validation
reports. [`honest-backtest`](https://github.com/krivonosoff161/honest-backtest)
owns the generic skeptical validation methods. This contract is public-safe:
it describes schema shape and authority limits, never private candidate rows,
parameters, market data, or runtime evidence.

## Producer Responsibilities

The farm exports a `CandidateForValidation` with stable provenance, declared
cost assumptions, trial metadata, simulated returns, data-window metadata, and
a schema version. The candidate is written under the private research root;
the public repository contains only code and schema definitions.

## Validator Responsibilities

The bridge invokes the vendored `backtest_sanity` statistical core and writes a
`HardValidationReport` plus `HardValidationVerdict` under the private research
root. Validation is fail-loud when the required statistical core is absent,
unless an operator explicitly permits an inconclusive degraded mode.

## Evidence Channels

The outer request is `CandidateForValidation` version `1.3.0`. Its validation
metrics may carry these content-bound nested objects:

- `ValidationObservationSet.v2`: one explicitly timestamped per-period return
  series with entry, exit, feature-information horizon, return basis, data
  snapshot plus evidence hash, computed evaluation-window identity, trial,
  symbol, timeframe, and family identity;
- `SearchTrialPanel.v2`: an explicit time-major `(time, trials)` matrix whose
  stable columns reconcile to the complete `SearchTrialEvidence.v2` ledger;
- `IntervalSplitManifest.v2`: retained, purged, and embargoed observation IDs
  derived from actual intervals and reproducible from the observation set;
- `DependenceEvidence.v2`: method, seed, block policy, effective count, and an
  explicit statement of whether the method is authoritative-suitable.

Numeric shape or equal list length is not panel evidence. Trial-major
per-trade ordinal vectors are `invalid_legacy_orientation`; they are never
transposed, padded, zero-filled, inner-joined, or backfilled from current data.
Duplicate or undeclared gaps fail the fixed time grid.
Feature information must be available no later than the observation entry;
post-entry labels cannot be disguised as decision-time features. A split with
no retained train or no retained test observations is invalid.

The panel count must equal both its columns and the immutable effective family
count. Cumulative parent multiplicity or a cross-symbol family therefore makes
a current-only panel unavailable unless all required parent/scope observation
series are independently bound; survivor/current columns are never presented
as the complete PBO/DSR family.

The verdict keeps four separate channels:

1. authoritative PSR/MinTRL and existing deterministic checks;
2. shadow PBO/DSR (`valid`, `invalid`, `unavailable`, or justified
   `not_applicable`);
3. complete-search-family evidence;
4. time/dependence suitability.

A malformed or missing shadow metric cannot change the authoritative
`overfit_psr` result. Missing family or time/dependence evidence instead fails
its own named hard check and prevents `PAPER_FORWARD_READY`. PBO/DSR remain
shadow-only; promotion requires a separate approved policy and migration.

Every request and report also carries an exact `SimulatorAssumptionManifest.v2`
identity plus its unsupported dimensions. Validation may measure returns but
cannot raise their claim ceiling: `deterministic_fixture` remains fixture
evidence even when statistical checks pass. Unknown, missing or content-tampered
simulator identities fail the outer contract. See [Simulator Truth Tiers](simulator-truth-tiers.md).

The current IID bootstrap/sign-flip path is a cheap non-authoritative kill
test for dependent or overlapping trades. An accepted interval/block-aware
generic method must be implemented upstream in `honest-backtest`, then
deliberately re-vendored. This repository does not patch the vendored method.

## Current-Generation Completion

The artifact directories are history, not an implicit current batch. The canonical farm
orchestrator passes the bridge only the non-empty candidate-ID list exported by the
current producer invocation. A zero-ID export validates nothing and defers the task; it
must never fall back to scanning historical requests.

Before the producer writes a request or invokes validation, the orchestrator atomically
publishes an empty manifest with `producer_complete=false`. This immediately revokes the
previous generation. If export, validation, stamp-back, or card creation then crashes,
readers remain fail-closed on that pending generation. After the current batch finishes,
the orchestrator atomically replaces it with
`hard_validation/current_generation.json` using schema
`HardValidationGeneration.v1`. The completed manifest binds:

- exact farm task IDs and hashes of their payloads;
- exact exported candidate IDs and request bytes;
- SHA-256 identities for the producer, bridge, contract, setup writer, and vendored
  validator code; every declared file is mandatory, not silently omitted;
- exact report, verdict, and SetupCard bytes for every completed vertical chain;
- an explicit producer-completion flag and paper-only/execution-denied boundary.

Once this manifest exists, paper, lifecycle, and farm follow-up readers accept only
SetupCards listed in its `active` map whose canonical path, hash, candidate ID, identity,
params, and hard status still match the request/report/verdict chain. The canonical PFR
paper bridge also joins each SQLite row to the current request's source run/candidate and
requires exact symbol, timeframe, strategy, and params equality with the verified card.
Thus an old `PAPER_FORWARD_READY` database row cannot bypass a pending, empty, invalid,
or newer generation. Missing, malformed, incomplete, code-stale, or tampered generation
evidence fails closed. Before the first orchestrated apply pass, absence of the manifest
is an explicit legacy compatibility state; those artifacts are readable but are not
generation-bound evidence. Manual diagnostic pipelines do not publish current-generation
authority.

Final authority is published before claimed validation tasks become completed or deferred.
If final publication fails, those tasks remain `running`; the normal single-owner startup
orphan reconciliation can requeue them while the pending generation keeps readers closed.
The PFR loader requires an explicit private root even in the legacy state, so callers cannot
accidentally bypass generation discovery by omitting context.

## Authority Boundary

`PAPER_FORWARD_READY` is the strongest positive hard status. It permits only
the strict paper/forward observation path. It does not permit a live order,
change `execution_allowed`, override a risk gate, or promote an LLM proposal.

## Statuses

```text
HARD_REJECT | FAILED_OVERFIT | FAILED_COSTS | FAILED_FRAGILITY
FAILED_OOS | FAILED_DATA_QUALITY | REGIME_ONLY | NEEDS_MORE_DATA
PAPER_FORWARD_READY
```

Unknown versions or statuses are errors, not passes. Full method-side details
are in the sibling [Validation Bridge Contract](https://github.com/krivonosoff161/honest-backtest/blob/main/docs/validation-bridge-contract.md).

## Forward Evidence

Paper lifecycle and outcome-learning artifacts preserve lineage in the private
research root. They are evidence for future bounded research, not public
performance claims and not an execution interface.

## Migration And Rollback

Regeneration from immutable source evidence is the only v1.3 migration. No
private validation history is rewritten and no legacy PBO value is inferred.
Rollback may disable v1.3 consumption while retaining its evidence; it must not
restore a legacy trial-major PBO artifact as accepted evidence.
