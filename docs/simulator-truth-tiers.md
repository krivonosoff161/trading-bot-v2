# Simulator Truth Tiers

Status: **REFERENCE CONTRACT**. Schema: `SimulatorAssumptionManifest.v2`.

The calculation farm's historical scalar, GPU and reference implementations are a
deterministic OHLC fixture. Their agreement is valuable implementation-parity evidence;
it is not an observation of executable price, event order, liquidity or account PnL.

## Immutable Models

`deterministic_ohlc_fixture.v1` preserves the existing next-bar-open, exact-trigger and
stop-first behavior. It is always labeled `deterministic_fixture`; legacy results are
never silently promoted or backfilled with facts unavailable when they were produced.

`ohlc_bar_plausibility_scenario.v2` adds adverse gap handling and explicit dual-touch
scenario bounds. When both stop and take are compatible with one candle, no scalar
event order is claimed. This raises the ceiling only to `bar_plausibility_scenario`.

Each manifest is content-bound to its model ID, evidence tier, entry/gap/same-bar/
dual-touch/cost/liquidity/funding/account policies, unsupported dimensions and claim
ceiling. Readers recompute the identity and reject missing, unknown or tampered input.

## Evidence Ceiling

Statistical validation, CPU/GPU parity and later candles cannot add simulator facts
that were absent at production. Validation requests, reports and setup cards therefore
carry the exact manifest plus unsupported dimensions. A deterministic fixture may
enter bounded paper observation, but reports must keep its fixture ceiling and may not
describe it as observed execution, market replay or account profitability.

Observed paper calibration requires separately authorized immutable order/fill/cancel
evidence. It cannot be synthesized from OHLC, private history is not migrated here, and
this contract grants no live or order authority.

## Fill, Cost And Account Rules

- An adverse stop gap cannot receive a price better than the declared first tradable
  bar price; long and short rules are symmetric.
- A wick touch does not prove maker quantity or queue order. Modeled fill quantity is
  capped by declared availability, and partial/no-fill states are explicit.
- Fees, spread, slippage, impact and funding are separate ledger components. A fixed
  slippage value proves none of the omitted dimensions.
- Funding is applied only from timestamped events crossed by the open interval.
- Partial fills conserve entry quantity and charge each declared cost exactly once.
- Independent what-if trade percentages are labeled as such. Portfolio claims require
  chronological account allocation; compounded equity and peak-to-trough drawdown use
  the declared equity basis.

Profit factor uses `ProfitFactorState.v2`: `finite`, `positive_infinity`, `undefined`
or `insufficient_data`. Non-finite states use JSON `null`, never `99`, `999` or JSON
`Infinity`; threshold consumers explicitly define their treatment.

## Forward Maturity

True-forward and paper observation may close on a barrier visible in available data.
They must not create a timeout until the complete declared hold horizon exists. New
bars strengthen temporal evidence only; they do not upgrade fill realism.

## Rollback

Disable v2 consumption while retaining its artifacts and exact identities. Do not
delete evidence, rewrite private history, infer unsupported dimensions, or restore
legacy fixture results under a higher truth tier.
