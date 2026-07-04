# LLM proposal contract + governance (audit + Block 1/2)

**The LLM is an advisory research dispatcher, not a controller.** It proposes bounded JSON
`HypothesisProposal`s; deterministic code validates every one before anything can be queued. The
LLM never runs code, never queues compute, never trades, never changes the registry, and **can never
set `paper_forward_ready`** (only the hard validator does). It is **disabled by default**
(`STRATEGY_LAB_LLM_ENABLED` unset) and export-only without a configured provider.

Modules: [llm_proposals.py](../src/research_lab/llm_proposals.py) (prompt + parse + batch),
[proposal_schema.py](../src/research_lab/proposal_schema.py) (typed `Proposal`),
[proposal_validator.py](../src/research_lab/proposal_validator.py) (the gate),
[param_schemas.py](../src/research_lab/param_schemas.py) (param/RR/horizon authority over the registry).

## A proposal must carry
`setup_family` (a known registry family) · `requested_timeframe` (not 1m) · `symbols` (1–2 known) ·
`parameter_grid` keyed by family (1–4 variants) · `hypothesis` (names a market behavior, not a trade
call) · `expected_validation` (the reject/observe/pass condition) · `risk_flags` · `max_variants`.

## The deterministic gate rejects when
| reason | rule |
|---|---|
| `unknown_strategy_family` / `unknown_symbol` / `unknown_timeframe` | not in registry / universe / profiles |
| `unsafe_field` | a denylisted key (code/shell/order/auto_trade/api_key/secret/…) or unsafe wording (guaranteed / live-trade / place order) |
| `variants_too_large` | over the resource-policy variant cap, or `1m` full sweep |
| param errors incl. `take_pct:reward_risk_below_2r` | params outside registry-derived ranges; **take_pct must be ≥ 2× stop_pct** (units are percent points: `stop_pct=8` = 8%) |
| `wrong_horizon` | `hold_bars` outside the timeframe band (15m ≤192 bars ~48h · 1h ≤168 ~7d · 4h ≤60 ~10d · 1d ≤30 ~30d) |
| `known_bad_in_memory` | every symbol×variant is already confirmed-bad in the Setup Outcome Memory (a single fresh variant keeps it alive) |
| `output_boundary_violation` | output path points at the public repo |

3+ contract failures in a run disable the LLM for that run. The LLM also receives a **memory digest**
(confirmed_bad / wrong_exit counts, dead-heaviest families, re-validation survivors) so it proposes
against real failures instead of blind.

## Good proposals (would VALIDATE)
1. `mean_reversion_fade` / 4h / [BTC] / `{bb_period:20, hold_bars:8, stop_pct:6, take_pct:14}` —
   "test whether an earlier take-profit keeps the mean-reversion move instead of giving it back";
   expected_validation "reject if capture stays < 0.3 or n<10".
2. `momentum_breakout` / 1d / [ETH] / `{lookback:30, hold_bars:5, stop_pct:8, take_pct:16}` —
   "test a slower breakout to reduce late-entry"; RR=2, horizon ok, fresh params.
3. `range_volume_breakout` / 1h / [SOL] / `{range_lookback:20, hold_bars:12, stop_pct:8, take_pct:16}`
   — "test whether accumulation breakouts hold over hours"; horizon ok.

## Rejected proposals (and why)
1. `{stop_pct:8, take_pct:8}` → `take_pct:reward_risk_below_2r` (RR<2).
2. `momentum_breakout` / 15m / `{hold_bars:300}` → `wrong_horizon` (300×15m ≈ 75h > 48h band).
3. re-proposing a confirmed-bad `momentum_breakout` param set on the same cell → `known_bad_in_memory`.
4. `{hypothesis:"guaranteed live-tradable profit, place order"}` → `unsafe_field`/`unsafe_wording`.
5. `setup_family:"my_new_idea"` → `unknown_strategy_family` (cannot invent registry families).

The registry ([strategy_registry.py](../src/research_lab/strategy_registry.py)) stays the single source
of truth; `param_schemas.yaml` is validation/ranges/horizon **over** it, never a second catalog.
