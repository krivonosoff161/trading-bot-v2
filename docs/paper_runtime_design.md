# Paper Trading Runtime - Design And Current Contract

Status: **CONTRACT + MINIMAL PAPER LOOP + AGGREGATE FEEDBACK IMPLEMENTED**.
Last updated: 2026-07-10.

## Main-Paper Account Reconciliation

The newer main-paper watcher has a separate account projection in
`src/research_lab/paper_account_ledger.py`. It does not replace the validated
setup runtime described below. It answers a different question: what would one
shared paper account have been able to fund?

- uses a bounded local paper-account configuration;
- reserves margin only for accepted primary theses;
- keeps leverage and position caps as local research settings, not public
  strategy parameters;
- funds one primary thesis per instrument/timeframe/side/boundary scenario;
- records sibling geometry variants as counterfactual exclusions;
- appends stable open/close/rejection events and derives the current snapshot;
- records realized PnL and the fee/slippage components already present in the
  deterministic outcome;
- has no exchange, order, `.env`, provider, or Telegram imports.

`paper_product_trade_ledger` remains useful for broad research comparison, but
its summed what-if PnL is explicitly not shared account equity.

Paper runtime is the forward executor for already validated setup cards. It is not a live
trading engine. It does not import `.env`, `AUTO_TRADE`, order clients, private exchange
endpoints, Telegram, or the old main engine.

## Core Principle

```text
farm result -> honest validation -> PAPER_FORWARD_READY setup card
  -> PaperTradePlan
  -> no-look-ahead paper simulation on local prepared candles
  -> paper_trades.jsonl + paper_outcomes
  -> farm/status feedback
```

Raw scanner/watch/news/intake rows have no path into paper runtime.

## Implemented Contract

`src/research_lab/paper_contract.py` provides:

- `PaperTradePlan`
- `PaperTradeOutcome`
- `PaperRuntimeState`
- `plan_from_setup_card(card)`

`plan_from_setup_card` accepts only a real `SetupCard` with:

- `paper_forward_ready == True`
- `lite_status == "FORWARD_PAPER"`
- `hard_status == "PAPER_FORWARD_READY"`
- executable params: `hold_bars`, `stop_pct`, `take_pct`
- percent-point units: `8` means 8%, not 0.08%
- reward/risk at least 1:2: `take_pct >= 2 * stop_pct`

The plan is order-free. It carries identity/provenance, symbol/timeframe/family, params,
entry/stop/take/hold rules, costs, validation verdict, and risk metadata. It does not
carry order IDs, exchange side fields, leverage, API keys, or live execution flags.

`direction` can be `long`, `short`, or `both`. Missing `direction`/`side` in params means
`both`, because several lab families are two-sided. When a paper trade is simulated, the
outcome records the concrete signal side (`long` or `short`) so later analysis does not
have to infer it.

## Implemented Runtime

`src/research_lab/paper_runtime.py` and `scripts/strategy_lab/paper_loop.py`:

- read only `paper_forward_ready` cards from `setup_library`;
- build `PaperTradePlan`;
- load local prepared candles from the private root;
- generate signals incrementally so future bars cannot create the entry;
- reuse `experiment.finalize_trade` for fill/exit/cost/MFE/MAE arithmetic;
- append `paper/paper_trades.jsonl`;
- upsert `state/strategy_lab.sqlite::paper_outcomes`;
- stamp `paper_status` back into farm state best-effort.

`farm_loop --run-paper` calls this runtime inside the canonical farm cycle.

## Journal Schema

`PaperTradeOutcome.v1` records:

- join keys: `trade_id`, `setup_id`, `candidate_id`, `params_hash`, `data_fingerprint`;
- market keys: `symbol`, `timeframe`, `family`, concrete `direction`;
- lifecycle state and close reason;
- planned and simulated entry/exit;
- fees, slippage, funding placeholder;
- MFE/MAE, net percent, R-multiple;
- validity and schema metadata.

`R-multiple = net_pct / stop_pct`.

## Feedback

Paper feedback is additive:

- `paper_trades.jsonl` is append-only.
- `paper_outcomes` stores aggregate outcome rows.
- `farm_results.paper_status` and `unique_candidates.paper_status` mirror state where
  the join metadata is available.
- `setup_lifecycle` rebuilds positive, negative, mixed, and no-sample research groups
  from the append-only journal and validation provenance. Negative outcomes are kept for
  analysis instead of being discarded.

Paper confirmation is still paper-only. It does not promote live trading.

## Deferred

- Funding accrual over actual funding windows.
- Multi-TP / partial closes.
- Rich promotion/demotion rules after enough forward paper trades.
- Direct Telegram imports or side effects inside the paper runtime. The separate
  opt-in delivery edge is implemented and documented in `farm_notification_layer.md`.
- Live trading integration.

## Mandatory Boundaries

The farm/paper tests must keep these true:

- no private imports (`src.exchange.okx_client`, old main engine, Telegram, `.env` config);
- no order methods/tokens;
- raw intake rejected;
- validated-only setup cards;
- no look-ahead;
- journal and feedback written only in apply mode;
- `FAILED_COSTS` and `NEEDS_MORE_DATA` are not promoted to paper.
