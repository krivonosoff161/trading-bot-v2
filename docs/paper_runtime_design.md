# Paper Trading Runtime — Design (farm → paper → feedback)

Status: **CONTRACT + MINIMAL PAPER LOOP + AGGREGATE FEEDBACK IMPLEMENTED; richer runtime planned.**
The typed contract (`src/research_lab/paper_contract.py`: `PaperTradePlan`,
`PaperTradeOutcome`, `PaperRuntimeState`, `plan_from_setup_card`) is built and tested.
The first bounded runtime (`src/research_lab/paper_runtime.py`,
`scripts/strategy_lab/paper_loop.py`) reads `paper_forward_ready` setup cards, runs a
no-look-ahead candle pass over local prepared data, and appends
`paper/paper_trades.jsonl`. It also upserts `state/strategy_lab.sqlite::paper_outcomes`
and stamps `paper_status` best-effort into farm state so status/dashboard can show whether
the paper layer actually recorded outcomes. Funding accrual and multi-TP lifecycle are
still planned. Paper/research only — no `.env`, no `AUTO_TRADE`, no orders, no private
endpoints, no Telegram.

Companion to [old_main_audit_2026-06-18.md](old_main_audit_2026-06-18.md). The paper
runtime is a **forward** executor of *already-validated* setups. It reuses the farm's
proven trade arithmetic (`experiment.finalize_trade`) and never re-derives a money path.

Design principle: **only validated setups enter, paper-only execution, outcomes flow back
to the farm.** Raw scanner/watch/news can never reach the paper runtime directly — only a
`PASS` SetupCard can.

---

## Этап 3 — Contract `PaperTradePlan` (farm → paper)

A frozen dataclass (mirrors `signal_contract.SignalContract` + `hard_validation_contract.SetupCard`).
Built ONLY from a SetupCard whose `paper_forward_ready == True`. No order fields.

```python
@dataclass(frozen=True)
class PaperTradePlan:
    # identity / join keys
    setup_id: str               # "setup-<candidate_id>"
    candidate_id: str           # hard-validation id; raw farm id/uc_key stay in request metadata for stamp-back
    params_hash: str            # data_fingerprint.params_hash(params)
    data_fingerprint: str       # the data version the setup was validated on
    # what to trade
    symbol: str
    timeframe: str
    family: str                 # strategy_id; sided via strategy_registry.get_strategy(family).generate_signals
    direction: str              # "long" | "short" — resolved from validated params at build; family re-confirms the side per signal at the trigger bar
    params: dict                # full param dict from SetupCard.params (hold_bars, stop_pct, take_pct, ...)
    # rules (price-level form; all derived from params at fill, like the farm sim)
    entry_rule: str             # "next_bar_open" (reuse farm fill model) | "level" (family-provided)
    stop_loss: dict             # {"type":"pct","value":stop_pct} -> price = entry*(1∓stop_pct/100)
    take_profit: list[dict]     # [{"type":"pct","value":take_pct,"size_frac":1.0}, ...]  (multi-TP optional)
    invalidation: dict          # pre-fill cancel rule, e.g. {"type":"regime_flip"} | {"type":"price_beyond","level":...}
    max_hold: dict              # {"bars": hold_bars} -> minutes via bar_seconds(timeframe); time_exit at close
    # costs / carry (reuse farm defaults; funding is the NEW accrual)
    fees_bps: float = 7.0
    slippage_bps: float = 3.0
    funding_handling: str = "accrue_public"   # accrue per public funding window over the hold; "ignore" = backtest-parity
    # provenance / gating
    source_validation_verdict: dict           # {"lite": validation_status, "hard": hard_status}; MUST be FORWARD_PAPER + PAPER_FORWARD_READY
    risk_limits: dict                          # {"max_concurrent_per_symbol":1,"max_open_plans":N,"max_risk_pct":...}
    metadata: dict                             # {run_dir_label, regime, created_at, generated_by:"farm.setup_library"}
```

Construction is gated: the builder `plan_from_setup_card(card)` (in
`src/research_lab/paper_contract.py`) raises `PaperPlanError` unless `card` is a real
`SetupCard` instance **and** `card.paper_forward_ready` **and**
`card.lite_status == "FORWARD_PAPER"` **and** `card.hard_status == "PAPER_FORWARD_READY"`.
There is **no path** from an `IntakeEvent` / watch dict / scanner row to a `PaperTradePlan`
(a non-`SetupCard` argument is rejected outright).

As implemented, the builder derives the fields the `SetupCard` does not carry directly:
- `direction` comes from the validated `params` (`direction`/`side`); missing/invalid → raise.
- `params_hash` = `data_fingerprint.params_hash(card.params)` (canonical join key).
- `data_fingerprint` is reconstructed via `data_fingerprint.compute_fingerprint(...)` from
  `card.data_window` (the card carries no standalone fingerprint string).
- `stop_loss`/`take_profit`/`max_hold` come from `params['stop_pct']`/`['take_pct']`/`['hold_bars']`.
- `entry_rule` defaults to `"next_bar_open"`; `invalidation` defaults to `{"type":"none"}`.
The contract also adds a `contract_version` field (`paper.v1`) and a `PaperRuntimeState`
enum (the lifecycle states in Этап 4) — `PaperTradeOutcome.from_plan` carries the join keys
(`candidate_id`/`setup_id`/`params_hash`/`data_fingerprint`) forward from the plan.

**OUT seam (no new export code):** the farm already writes PASS cards to
`setup_library/setup_index.jsonl` (`setup_library.write_setup_library`). The paper runtime
is a **reader** over that index filtered by `paper_forward_ready == True`. Params +
`strategy_registry.get_strategy(family).generate_signals` are sufficient to run the sim.

---

## Этап 4 — Paper position lifecycle

A no-look-ahead state machine: bars are fed in arrival order; SL/TP/timeout are evaluated
against the **current** bar and history only (never a future bar). Exit arithmetic reuses
`experiment.finalize_trade` (first-touch barrier, stop-before-take same-bar tie).

| State | Input event | Journal write | Metrics |
|---|---|---|---|
| `planned` | PASS SetupCard read + gate ok | plan row (`PaperTradePlan` dict) | — |
| `armed` | plan activated, waiting for entry trigger (next eligible bar) | arm row (armed_at) | — |
| `opened` | entry trigger on an arriving bar → simulated fill at `entry_rule` price (next-bar open) | open row (fill price, ts, notional, risk_pct=stop_pct) | notional, risk_pct |
| `partially_closed` *(optional)* | a non-final TP level touched (`high>=tp_i` long / `low<=tp_i` short) | partial row (realized frac pnl, remaining size) | realized partial net%, remaining |
| `closed_tp` | take touched (after any partials) | close row (exit, net_pct, r_multiple, mfe/mae, funding) | R, net%, outcome="take" |
| `closed_sl` | stop touched (stop checked before take within a bar) | close row | R, net%, outcome="stop" |
| `closed_timeout` | `hold_bars` elapsed, no barrier → exit at bar close | close row | R, net%, outcome="time_exit" |
| `closed_invalidation` | invalidation rule fires while armed/opened (regime flip / level broken) | close/cancel row (reason) | partial/none |
| `cancelled` | armed plan dropped pre-fill (setup demoted, data gap, risk limit) | cancel row (reason) | — |
| `error` | data missing / provider error / exception | **explicit** error row (reason, never silent) | — |

Funding (the one PnL gap vs the farm batch sim): while a position is `opened`, accrue the
public funding rate at each funding window crossed during the hold (sign by `direction`),
folded into `net_pct` alongside fees+slippage. `funding_handling:"ignore"` reproduces
exact backtest parity for A/B checks.

No look-ahead is structural: the runtime holds an open position and only sees a bar when it
arrives, exactly mirroring forward reality (the farm sim has all future candles up front;
this does not).

---

## Этап 5 — Outcome journal schema

JSONL (mirror `src/data/*_records.py`: append-only, schema-versioned, `quality_flags`).
Path: `<private_root>/paper/paper_trades.jsonl`.

```jsonc
{
  "schema": "PaperTradeOutcome.v1",
  "trade_id": "...",                 // unique per paper trade
  "setup_id": "setup-<candidate_id>",
  "candidate_id": "...",             // hard-validation id; joins back via request metadata
  "symbol": "BTC-USDT-SWAP", "timeframe": "1h", "family": "momentum_breakout",
  "direction": "long",
  "planned_at": "...", "armed_at": "...", "opened_at": "...", "closed_at": "...",
  "planned_entry": 0.0,              // entry the plan expected
  "actual_sim_entry": 0.0,          // simulated fill (next-bar open)
  "exit_price": 0.0,
  "fees_pct": 0.07, "slippage_pct": 0.03, "funding_accrued_pct": 0.0,
  "mae_pct": 0.0, "mfe_pct": 0.0,    // reuse finalize_trade excursions
  "r_multiple": 0.0,                 // NEW: net_pct / stop_pct (risk_pct); farm works in net_pct, R is computed here
  "net_pct": 0.0,                    // ret - fees - slippage - funding
  "pnl_paper_pct": 0.0,              // net_pct * notional fraction (paper, unit or sized)
  "reason": "closed_tp",             // closed_tp|closed_sl|closed_timeout|closed_invalidation|cancelled|error
  "outcome": "take",                 // take|stop|time_exit (from finalize_trade)
  "data_quality": "ok",              // ok|thin|gap (from data_fingerprint / inventory)
  "data_fingerprint": "...", "params_hash": "...",
  "linked_farm_task_id": 123,        // the run_sweep/classify task that produced the setup
  "linked_validation_request": "<candidate_id>",   // the hard-validation request id
  "valid": true, "invalid_reasons": [], "recorded_at": "..."
}
```

`R-multiple` is the one genuinely new number (the farm has none): `r = net_pct / stop_pct`
(stop_pct from params). MFE/MAE/outcome/net_pct are reused from `finalize_trade`.

---

## Этап 6 — Feedback back to farm

Mirror the existing hard-validation handoff (`validation_handoff.refresh_from_artifacts`),
do not overload `farm_results` (its row is per-backtest, not per-forward-trade).

1. **Aggregate table implemented:** `paper_outcomes` in `strategy_lab.sqlite` (additive
   schema v5 migration):
   ```
   paper_outcomes(
     candidate_id TEXT PRIMARY KEY,    -- hard-validation id; source uc_key is in request metadata
     setup_id, symbol, timeframe, family TEXT,
     paper_started_at, last_update TEXT,
     n_paper_trades INTEGER, win_rate REAL,
     avg_net_pct REAL, sum_r REAL,
     avg_mfe_pct REAL, avg_mae_pct REAL, max_drawdown_pct REAL,
     paper_status TEXT                 -- PAPER_LIVE | PAPER_CONFIRMED | PAPER_DIVERGED | PAPER_STOPPED
   )
   ```
2. **Implemented:** `paper_status TEXT DEFAULT ''` on `farm_results`, plus a mirror onto
   `farm_tasks.sqlite.unique_candidates` when the setup card came from lifecycle validation
   request metadata.
3. **One derived label** `paper_state(validation_status, hard_status, paper_status)`
   mirroring `validation_state` — extends the decision-machine chain lite → hard → paper.

**Promotion / demotion rule (forward vs backtest agreement):**
- `PAPER_CONFIRMED` — `n_paper_trades >= N_MIN` AND forward `avg_net_pct` agrees in sign
  with the backtest AND within a tolerance band → setup *stronger*; eligible for a
  human-reviewed setup card (still paper; live remains a separate, out-of-scope decision).
- `PAPER_DIVERGED` — forward sign disagrees or forward net materially below backtest →
  *weaker*; demote (re-validate with fresh data, or archive).
- `PAPER_STOPPED` — risk limit hit / repeated SL / data gap → halt tracking, flag for review.
- `PAPER_LIVE` — actively tracking, not enough trades to judge yet.

BACK seam reuses `validator.validate_candidate` (lite gate) and `reducer.reduce_results`
(stability across params) before a setup is even eligible for paper, and the
`candidate_id` join everywhere.

---

## Этап 7 — Mandatory tests

1. **no-private-import (boundary):** AST test that every paper-runtime module imports
   none of the DENYLIST (extend the existing
   `test_new_modules_have_no_live_trading_coupling`). Assert no `src.exchange.okx_client`
   **at all** (not just the order methods — public candles come from
   `research_lab.providers.okx_public` / `market_data_provider`), no `auto_execute`, `main`,
   `run_latest_analysis`, `telegram`, `scanner_v0`, `config`(.env), `*_engine`, no
   `AUTO_TRADE` token.
2. **validated-only:** `plan_from_setup_card` raises for a card with
   `paper_forward_ready=False` / lite status != FORWARD_PAPER.
3. **raw intake rejected:** a scanner watch / IntakeEvent dict cannot construct a
   `PaperTradePlan` (only a PASS SetupCard can).
4. **fees/slippage/funding accounted:** deterministic PnL test incl. funding accrual over N
   funding windows; `funding_handling:"ignore"` == backtest parity.
5. **deterministic SL/TP/timeout:** feed a synthetic candle stream; assert exact
   `closed_sl`/`closed_tp`/`closed_timeout` and the stop-before-take same-bar tie match
   `finalize_trade`.
6. **no look-ahead:** a TP that is only reachable on a *future* bar must not close the
   position early; bars processed incrementally.
7. **journal written:** `paper_trades.jsonl` row + `paper_outcomes` aggregate row after a
   closed trade; `quality_flags` populated.
8. **feedback to farm:** `paper_outcomes` upsert + `farm_results.paper_status` stamp +
   `paper_state` label; `candidate_id` join holds.
9. **live path inaccessible:** assert the runtime never reaches an order method even under
   an ENTRY signal (the fill is always simulated).

---

## Этап 8 — Migration plan (PR / commit sequence)

1. **audit docs + ownership map** — this doc + `old_main_audit_2026-06-18.md` (no code).
2. **schemas/contracts** — `PaperTradePlan` dataclass + `PaperTradeOutcome` schema +
   `paper_state` label fn (pure, fully unit-tested). No runtime yet.
3. **paper runtime skeleton** — implemented as a bounded reader over `setup_library` PASS
   cards plus local prepared candles. The runtime reuses `finalize_trade`, writes
   `paper_trades.jsonl`, deduplicates deterministic `trade_id`s, and remains local-only.
4. **journal / aggregate** — JSONL writer, `paper_outcomes` table, additive migration,
   and `paper_status` stamp-back are implemented. Funding accrual is still planned.
5. **farm export to paper** — minimal OUT reader implemented (PASS SetupCards →
   `PaperTradePlan`); richer scheduling/backpressure remains planned.
6. **feedback import** — basic `paper_outcomes` → `farm_results.paper_status` +
   `unique_candidates.paper_status` mirror is implemented; promotion/demotion labels remain
   planned.
7. **dashboard/status** — `paper_status` and `paper_outcomes` are surfaced in
   `farm_cockpit`, `farm_status_report`, `status`, and `morning_report`.
8. **Telegram notification design later** — already deferred (`farm_notification_layer.md`).

Each PR ships with its tests; `pytest -q` / `ruff` / `git diff --check` / the boundary test
stay green. No `.env` / `AUTO_TRADE` / order / Telegram touched at any step.

---

## Reuse summary (do not reinvent)

| Need | Reuse |
|---|---|
| Public candle data (keyless) | `research_lab.providers.okx_public.OkxPublicMarketDataProvider` via `market_data_provider.get_provider` — **not** `okx_client` |
| Fill / exit / cost / MFE / MAE | `experiment.finalize_trade` (+ `simulate_trades` for batch parity checks) |
| Lite gate before paper | `validator.validate_candidate` |
| Stability across params | `reducer.reduce_results` |
| Setup feed (OUT) | `setup_library` PASS cards (`paper_forward_ready==True`) |
| Outcome stamp-back pattern (BACK) | `validation_handoff.refresh_from_artifacts` |
| Keys / fingerprint | `data_fingerprint.params_hash` + `data_fingerprint` |
| Typed IO | `signal_contract.SignalContract`, `hard_validation_contract.{SetupCard,TradeRecord}` |
| Journal pattern | `src/data/*_records.py` (JSONL + schema + `quality_flags`) |
| Per-symbol sizing (optional) | `okx_meta` public instrument specs |
| Funding (the NEW accrual) | `providers.okx_flow` public funding + `flow_merge` |

## Blocked / deferred (honest)

- **Funding accrual** does not exist in the farm sim — must be added in the paper runtime.
- **R-multiple** is a schema slot only — computed for the first time here (`net_pct/stop_pct`).
- **Live forward lifecycle / no-look-ahead position holding** — new (farm sim is batch).
- **Multi-TP / partial closes / deviation guard** — present in old `auto_execute` math
  (extract pure parts), absent in the farm single-exit sim; optional, phase 4+.
- **Telegram notification** — deferred (design-only, `farm_notification_layer.md`).
- **Live trading** — out of scope by hard boundary; main-engine reconfiguration is a
  separate later stage.
