# Project Map

Status: **ACTIVE**. Reviewed: 2026-07-10.

`trading-bot-v2` is a public paper/research workbench around OKX public market
data. It is not a live-trading product. The active core is the calculation farm;
the scanner is an upstream intake surface, and old WebSocket engines are
reference or legacy code.

## Read Order For Maintainers

1. [Documentation Guide](README.md)
2. [Architecture](../ARCHITECTURE.md)
3. [Farm Ownership Map](farm_ownership_map.md)
4. [Entrypoint Catalog](entrypoints.md)
5. [Storage Boundaries](storage_boundaries.md)

## Public System Flow

```text
public news / market intake
  -> scout WATCH/GO records
  -> farm tasks and deterministic sweeps
  -> honest-backtest validation bridge
  -> paper lifecycle and public-candle observation
  -> private outcome/training evidence
  -> bounded future research sweep

separate explicit delivery edge
  -> reviewed paper-card preview
  -> active Telegram subscribers
```

The delivery edge is opt-in. It may load configuration only when an operator
starts a supported send wrapper. It does not grant execution authority.

## Directory Ownership

| Path | Status | Responsibility | Boundary |
|---|---|---|---|
| `src/research_lab/` | active core | Farm scheduler, data preparation, sweeps, validation handoff, paper lifecycle, outcome learning, LLM governance. | Paper-only; no order authority. Private derived state lives outside public Git. |
| `src/research_lab/paper_signals/` | active core | PFR-aware paper-signal lifecycle and public-candle observation. | No `.env`, private endpoint, or order path. |
| `src/research_lab/providers/` | active support | Public market-data/provider adapters. | Provider capabilities are explicit; private account data is excluded. |
| `src/research_lab/features/` and `strategies/` | active support | Deterministic features and bounded strategy families. | Results are research evidence, not profitability claims. |
| `src/scout/` | active intake | News/event collection, normalization, deduplication, routing, watch queue, source-quality records. | Upstream context; it does not control the farm or orders. |
| `src/scout/agents/` | active support | Bounded scanner analysis roles. | Provider budgets and delivery are separate policy surfaces. |
| `src/strategy/` | active support/reference | Shared indicators, chart rendering, setup confirmation contracts. | Not the farm's execution engine. |
| `src/exchange/` | restricted reference | OKX client and public instrument metadata. | `okx_client.py` is outside the canonical farm import boundary. |
| `src/data/` | frozen/reference | Historical WebSocket/paper engines and data helpers. | Never wire directly into the canonical farm. |
| `src/utils/` | shared support | Logging, LLM routing, Telegram routing, notification policy. | Transport modules are not farm authority. |
| `scripts/strategy_lab/` | active operator/diagnostic CLI | Farm CLI, health, paper preview, status, bounded maintenance scripts. | Use only commands listed in `entrypoints.md`. |
| `scripts/ws/` | legacy/reference | Historical WebSocket scanners and paper engines. | Not a canonical farm or product start path. |
| `scripts/archive/` | archive | Historical backtests and engine experiments. | Do not reactivate without a reviewed research task. |
| `scripts/backtest/` and `scripts/analysis/` | local research support | Historical analysis and local journals. | Generated inputs/outputs remain ignored or private. |
| `bat/` | operator entrypoints | Windows wrappers. | See `entrypoints.md`; do not infer safety from a filename. |
| `configs/` | active configuration | Public strategy-lab resource, universe, timeframe and smoke specs. | No secrets or live parameters. |
| `ops/` | active support | Reproducible local operational assets, including Ollama model definition. | No credentials or raw prompts. |
| `vendor/honest-backtest/` | version-pinned dependency | Vendored `backtest_sanity` statistical core. | Update upstream first, then re-vendor deliberately. |
| `tests/` | active verification | Unit, integration, boundary and paper-lifecycle checks. | Tests must not require keys, live orders, or private state. |
| `examples/` | public-safe examples | Reproducible public examples. | No private strategy edge or runtime outputs. |

## Main Contracts

### Farm To Validator

`src/research_lab/hard_validation_contract.py` defines the candidate/report
boundary. `honest_backtest_bridge.py` runs the vendored statistical core. A
hard-validation verdict is evidence about a candidate, not permission to trade.
`validation_generation.py` publishes the atomic current-generation manifest; artifact
directories remain history and are never scanned as implicit completion authority. The
PFR bridge additionally binds a current validated source identity to its exact SQLite row
before that row can feed the paper-only signal lane.

### Validator To Paper

Only a hard `PAPER_FORWARD_READY` setup can enter the strict validator-backed
paper lane. Broader product-paper observation is separately labelled and never
becomes live authority.

### Paper To Learning

Paper outcome records are transformed into bounded outcome-review and retest
inputs. LLM roles explain or propose test dimensions through schemas; deterministic
code decides accepted sweeps and all paper state transitions.

### Telegram And News

Telegram is a delivery or manual-analyzer surface. News scanning is an upstream
context source. Neither surface may promote validation state or place orders.

## External Repository Contract

[`honest-backtest`](https://github.com/krivonosoff161/honest-backtest) owns
generic validation methods: data integrity, splits, significance, costs,
robustness, forward evidence, and adversarial review. This repository owns
candidate generation and paper observation. The private `trading-bot-research`
repository owns non-public calculations and derived runtime evidence.

See [Vendored Validator](../vendor/honest-backtest/VENDOR.md) for the pinned
dependency policy and [Validation Bridge Contract](validation-bridge-contract.md)
for the public cross-repository schema boundary. No private candidate rows
belong here.

## What Is Not Public Source Of Truth

- local logs, databases, dashboards, Excel journals, provider credentials, and
  raw model inputs/outputs;
- old `main.py` execution-adjacent paths;
- dated research reports unless a current document explicitly promotes them;
- generated files physically present under ignored workspace folders.
