# Old Main Trading Engine — Audit & Ownership Map (2026-06-18)

Status: **AUDIT / DESIGN** (no code change in this pass). Paper/research only.

Goal: the old main engine is **no longer a signal source**. This documents what it is,
what is dangerous, what is reusable, what to archive, and how responsibility splits
between the farm, a new paper runtime, honest-backtest, Telegram, and old main.

Method: deep multi-agent read of `main.py`, `src/strategy`, `src/data`, `src/exchange`,
`src/utils`, `scripts/ws`, plus the farm's existing simulation prior-art. The import-safety
boundary below is a **design decision**, not an asserted runtime fact: it is enforced only
once the coding pass extends the existing AST import-boundary test
(`tests/test_farm_loop_integration.py::test_new_modules_have_no_live_trading_coupling`) to
cover the new paper-runtime modules.

---

## Этап 1 — File map (role · I/O · live-dep · reuse · archive · replacement)

### The live/money path — ARCHIVE, never import

| File | Role | Live/private dependency (file:line) | Replacement |
|---|---|---|---|
| `main.py` | Live orchestrator: poll → `get_signal` → size → **place real order** → track/close → Telegram | order `place_market_order` (204); `set_leverage` (36); `get_positions`/`get_last_position_close` (126/255); `.env` via `Config.load`/`OKXClient(...)` (353); Telegram (15,61,229,322) | Paper runtime loop pattern (signal→size→**paper fill**→journal); no order/.env/Telegram |
| `scripts/auto_execute.py` | Turns ENTRY into a **live position** (deviation guard, OCO, timeout close); the hard money gate `if not AUTO_TRADE: return` (89) | `place_market_order` (171), `close_position` (261), `.env`+`AUTO_TRADE` (21-31,52-55) | Paper executor: same input dict → simulated fill, reuse extracted pure math, paper position record |
| `scripts/run_latest_analysis.py` | Interactive launcher; lazy-imports `auto_execute.execute_signal` (130-135). Update 2026-06-27: the import now also requires explicit `RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE=1`. | reaches the .env-gated money path only after the manual wrapper opt-in | Non-interactive driver routing ENTRY → **paper** executor |
| `src/exchange/okx_client.py` | OKX REST client — order methods + auth + public getters in one module | **DENYLIST (whole module):** `place_market_order` (285), `close_position` (324), `set_leverage` (362); auth `_sign`/`_headers`; reads `.env` creds | **Paper runtime must NOT import this module at all.** Public market data comes from the public-only providers instead: `src/research_lab/providers/okx_public.py` (`OkxPublicMarketDataProvider`, keyless candles) via `research_lab/market_data_provider.get_provider`, `src/exchange/okx_meta.py` (keyless instrument specs), `src/research_lab/providers/okx_flow.py` (keyless funding/OI) |
| `src/data/main_impulse_engine.py`, `impulse_pump_engine.py`, `impulse_pump_trading.py` | Legacy paper/live WS engines (AUTO_TRADE-asserted paper, but live WS + Telegram coupling) | read `AUTO_TRADE`, Telegram, live WS feed | Closed engines (pump/impulse postmortems). Do not import. Detectors already ported to research_lab families |
| `scripts/ws/ws_*.py` (`ws_main_impulse`, `ws_impulse_pump`, `ws_smart_pump`, `ws_bb_fade`, screeners, `run_pump_watchdog`) | Live WS runners | live WS / AUTO_TRADE / Telegram | Archive-as-is (live runners). Pure detectors already in `research_lab/strategies/*` |
| `src/utils/telegram.py` | Telegram sender (bot token from env) | external Telegram I/O | Future notification layer only (design-only, deferred) — see `docs/farm_notification_layer.md` |
| `src/config.py` | `.env` reader (`OKX_API_KEY/SECRET/PASSPHRASE`), raises if missing | `.env` creds | Paper reuses only `as_strategy_dict()` + yaml params (no creds) |

### Pure / public — candidates to reuse (no live/private/Telegram dependency observed)

The paper runtime's decision input is the **PASS SetupCard** (its `family` re-run via
`strategy_registry.get_strategy(family).generate_signals`), **never** raw old-main signal
output. The old `get_signal`/`compute_signal` are *not* the paper-runtime decision core —
they are kept only as historical/reference logic or as pure-math extraction candidates into
`research_lab` (most detectors are already ported there). The paper runtime accepts only a
PASS SetupCard; it does not consume `get_signal` output.

| File / symbol | Why reusable | Reuse for paper runtime |
|---|---|---|
| `src/strategy/signal.py::get_signal` | imports only numpy/loguru/indicators; no live/order/.env/Telegram dependency observed | historical/reference only; pure-math extraction candidate into `research_lab` — **not** the paper decision core |
| `src/strategy/signal_engine.py::compute_signal` | pure given candles+config; its Telegram-card/LLM text builders must NOT be ported | reference / extraction candidate only; the paper runtime drives the SetupCard's family, not this |
| `src/strategy/signal_contract.py::SignalContract` | frozen dataclass, order-free typed plan (`pair/side/entry/stop/exit_rule/max_hold_min/follow/regime/...`) | **base `PaperTradePlan` on this shape** |
| `src/strategy/setup_confirmation.py` | paper-only classifier; hard-sets `execution_allowed=False`, `paper_only=True` | confirmation logic reference |
| `src/exchange/okx_meta.py` | keyless **public** `/api/v5/public/instruments` (instrument specs / tick / contract value), no auth | per-symbol sizing/min-size in paper |
| `src/research_lab/providers/okx_flow.py` | keyless public funding + OI history | funding accrual + OI context |
| `src/research_lab/features/*` | EMA/ATR/ADX/RSI/Bollinger/SuperTrend/swings/FVG/VWAP — parity-tested mirror of `indicators.py` | features for plan generation |
| `src/data/*_records.py` | JSONL signal/outcome/training schema + `quality_flags` + schema versioning | **prior art for the paper outcome journal** |
| `Config.as_strategy_dict()` + yaml | strategy params, no creds | plan params |

### Reusable pure math to extract (currently entangled with I/O)

- `main.py::_calc_sz` (75-104) and SL/TP geometry (179-190).
- `auto_execute._calc_contracts` (73-81) and deviation-guard + fill-recalc (120-149).
- These are pure arithmetic worth lifting into a paper sizing/fill helper — **copy the math, never import the module** (the modules reach the order path).

---

## Этап 2 — Responsibility split (who owns what)

| Component | Owns | Must NOT |
|---|---|---|
| **Calculation farm** (`farm_loop`/`farm_coordinator`/`research_lab`) | research brain: universe grind, data planning, strategy sweeps, classification, **validated setup discovery** | execute, hold forward positions, touch orders |
| **Honest-backtest** (`honest_backtest_bridge`) | hard judge: costs/OOS/robustness/overfit verdicts → `PAPER_FORWARD_READY` | be a signal source |
| **Paper runtime** (NEW) | accept ONLY validated setups, **execute in paper**, position lifecycle, fees/slippage/funding/outcome, journal, feedback to farm | import any order/live/.env/Telegram path; accept raw scanner/watch/news directly |
| **Telegram** | future **notification only** (alerts on validated/paper results) | be a decision input or part of compute |
| **Old main engine** | historical source / a few reusable **pure** utilities (copy, don't import) | be a signal source or be wired into the new path |

**Flow:** `farm_loop` → validated candidate (`FORWARD_PAPER`) → honest-backtest
(`PAPER_FORWARD_READY`) → **SetupCard** → **paper runtime** (forward paper execution) →
**paper_outcomes** → back into the farm (promotion/demotion) → (later) Telegram alert.

The old engine sits entirely outside this loop.

---

## Safety boundary (design decision — to be enforced by test in the coding pass)

**DENYLIST — paper runtime + farm must never import these modules:**
- `src/exchange/okx_client.py` — **whole module** (order methods `place_market_order:285` /
  `close_position:324` / `set_leverage:362`, auth, `.env` creds). Public candles come from
  the public-only providers below, not from this client.
- `scripts/auto_execute.py`, `main.py`, `scripts/run_latest_analysis.py` — the live money path.
- `src/utils/telegram.py`, `src/scout/scanner_v0.py` (Telegram-capable).
- `src/config.py` (.env creds).
- `src/data/{impulse_pump,main_impulse}_engine.py` (legacy live engines).

This boundary is **not** an asserted runtime guarantee yet. The coding pass MUST extend the
existing AST import-boundary test
(`tests/test_farm_loop_integration.py::test_new_modules_have_no_live_trading_coupling`) to
include every new paper-runtime module, so the denylist becomes enforced rather than stated.

**Candidates to reuse (no live/private dependency observed):** `signal_contract.py`,
`setup_confirmation.py`, `okx_meta.py` (keyless public specs),
`research_lab/providers/okx_public.py` + `market_data_provider` (keyless public candles),
`okx_flow.py` (keyless funding/OI), `research_lab/features/*`, `*_records` schema,
`Config.as_strategy_dict`. `signal.py`/`compute_signal` are reference/extraction candidates
only, **not** a decision core (the paper runtime decides only from PASS SetupCards).

See [paper_runtime_design.md](paper_runtime_design.md) for the contract, lifecycle,
journal, feedback, tests, and migration plan.
