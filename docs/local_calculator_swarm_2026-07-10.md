# Local Calculator Mini-Swarm

Updated: 2026-07-10

## Purpose

The farm calculator is a bounded local sidecar, not a trading brain. It runs
three sequential roles on one allowlisted Ollama model:

1. `calculator_context_classifier` classifies the sanitized feature context.
2. `calculator_hypothesis_proposer` suggests allowlisted sweep dimensions.
3. `calculator_hypothesis_critic` accepts or rejects that bounded hypothesis.

All three roles use `calculator-swarm`, created reproducibly from
`ops/ollama/calculator-swarm.Modelfile`. The older `calculator` model has a
hard-coded proposal system prompt and is not valid for these three contracts.

## Control Plane

Every calculator and cloud-reviewer call passes through the private
`LLMInvocation.v1` ledger:

```text
sanitized input + role + prompt/normalizer version
  -> hash and pre-call dedup
  -> provider/model allowlist
  -> role/provider circuit breaker
  -> model call
  -> schema and semantic validation
  -> token/cost/status audit
  -> bounded advisory artifact or rejection
```

The ledger stores hashes and aggregate usage metadata. It does not store API
keys, `.env`, raw prompts, recipient identifiers or private exchange data.

## Local Smoke Evidence

One bounded smoke over the latest private `FeaturePacket.v1` was used to tune
the contracts. The final run completed all three passes. The critic rejected the
proposed hypothesis, so no sweep proposal was emitted. Aggregate ledger state
after the smoke series:

```text
invocations: 9
accepted passes: 5
schema rejected passes: 3
provider errors: 1
tokens: 6875
cost: 0 RUB
```

Earlier rejected passes are intentionally retained in the private ledger as
evidence that forbidden/unknown output was not silently accepted. This is
paper/research-only and keeps `execution_allowed=false`.

## Boundaries

- Local calculator provider: Ollama only.
- Allowlisted model: `calculator-swarm` (tag variants allowed).
- Cloud providers cannot occupy calculator roles.
- Cloud reviewer roles receive sanitized role packs only.
- LLMs cannot choose side, prices, risk, validator verdict, readiness or orders.
- Deterministic code compiles and tests any accepted sweep dimension.
