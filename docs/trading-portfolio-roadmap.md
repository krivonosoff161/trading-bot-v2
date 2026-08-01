# Trading Portfolio Roadmap

Status: **CURRENT**

- Verified: 2026-08-01
- Verified against: `c20322f887977c5e3c3ec2c242ca560617d056fa`
- Trading repository SHA: `c20322f887977c5e3c3ec2c242ca560617d056fa`
- Validator repository SHA: `0f537a8fa0b80b17d100d38c0696f9a07d8e4ba6`
- Scope: public-safe ownership, capability, evidence, and authority map
- Evidence: [machine source](trading-portfolio-roadmap.yaml) and
  [documentation validator](../scripts/ci/check_trading_portfolio_docs.py)
- Digest contract: SHA-256 over canonical UTF-8 text with LF line endings
  (`hash_canonicalization: utf8_lf`), independent of checkout line endings.
- Residual risks: documentation and tests do not prove private runtime health,
  signal quality, profitability, or readiness for live execution.
- Next gate: keep the two repository projections aligned, then complete the
  separately authorized paper-only operational canary.

This is the canonical public map of the trading research direction. The YAML
file is the machine source; this page is its human-readable projection. A
module marked implemented is not automatically active in a private runtime.
The canonicalization rule changes only text line endings before hashing; any
semantic content change still produces a different digest.

## Evidence Flow

```text
public research ingestion
  -> bounded Strategy Lab and experiment registry
  -> deterministic simulation
  -> independent honest-backtest falsification
  -> fenced candidate lifecycle
  -> paper-only observation and outcomes
  -> optional paper-card delivery

LLM advisory -> proposals only -> deterministic gates
evidence/storage -> content and lineage support, not trading authority
RCC -> owner-gated supervision of the paper-only contours
execution -> outside the supported portfolio and separately owner-gated
```

## Capability Map

| Module | Owner | Status | Authority | Proven boundary | Next gate |
|---|---|---|---|---|---|
| Research ingestion | `trading-bot-v2` | implemented bounded | none | Public inputs; collected data stays private. | Prove lineage and degraded behavior in a bounded canary. |
| Strategy Lab | `trading-bot-v2` | implemented bounded | paper only | Schedules research; cannot place orders. | Complete the long-duration reliability window. |
| Experiment registry | `trading-bot-v2` | implemented | none | Public schemas; private rows and rankings. | Preserve lineage compatibility. |
| Deterministic simulation | `trading-bot-v2` | implemented bounded | none | Declared scenario truth tiers, not market fidelity. | Add only evidence-backed dimensions. |
| Skeptical validation | `honest-backtest` | implemented bounded | none | A pass means not rejected, never profitable. | Maintain the versioned bridge and untouched evidence. |
| Candidate lifecycle | `trading-bot-v2` | implemented bounded | paper only | Fenced state transitions; private identities. | Prove interruption and recovery invariants. |
| Paper observation | `trading-bot-v2` | implemented bounded | paper only | Records observations/outcomes without orders. | Complete a stable acceptance window. |
| Research Control Center | `trading-bot-v2` | implemented bounded | separately owner-gated | Supervises one canonical paper-only profile. | Green 48-hour canary. |
| LLM advisory | `trading-bot-v2` | implemented bounded | advisory | Proposals are untrusted and schema-gated. | Held-out usefulness evaluation after reliability. |
| Evidence/storage | `trading-bot-v2` | implemented bounded | separately owner-gated | Synthetic capability; private adoption is not implied. | Reviewed inventory, parity, cutover, and rollback. |
| Delivery | `trading-bot-v2` | implemented bounded | separately owner-gated | Paper cards only; recipients and ACK state are private. | Observe delivery continuity in canary. |
| Execution boundary | `trading-bot-v2` | implemented denial | separately owner-gated | No supported live order path. | Separate future architecture and owner decision. |
| Release/operational gates | `trading-bot-v2` | implemented bounded | separately owner-gated | CI proves public code, not private readiness. | Keep merge and runtime gates separate. |

## Status Semantics

- `implemented`: the bounded public contract exists and has deterministic tests.
- `implemented_bounded`: a real contract exists, but an operational,
  completeness, or fidelity ceiling remains.
- `experimental`: importable or testable, but not a stable portfolio contract.
- `planned` or `blocked`: no claim of current capability.
- `historical` or `superseded`: evidence only, never current authority.
- `unable_to_prove`: documentation must not promote the claim.

## Repository Boundary

`trading-bot-v2` owns research orchestration, paper lifecycle, advisory model
integration, evidence policy, delivery, and operator gates.
[`honest-backtest`](https://github.com/krivonosoff161/honest-backtest) owns the
independent skeptical validation methods. The repositories exchange only a
public-safe versioned contract; neither receives execution authority from the
other.

## Non-Claims

This roadmap does not establish profitability, signal quality, continuous
runtime health, live readiness, provider availability, or order authority.
Private data, strategy parameters, model conversations, recipients, credentials,
runtime rows, and operational evidence are deliberately absent.
