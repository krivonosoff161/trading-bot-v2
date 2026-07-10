# Validation Bridge Contract

Status: **REFERENCE CONTRACT**. Version: `1.0.0`.

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
