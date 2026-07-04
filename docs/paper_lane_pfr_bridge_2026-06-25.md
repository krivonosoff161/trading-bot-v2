# Paper Lane + PFR Bridge — Implementation Notes (2026-06-25)

> **Audit update (2026-06-25, after DB schema check):**
> - **JOIN fixed**: `load_pfr_records()` now JOINs on `fr.run_id = c.run_id AND fr.candidate_id = c.candidate_id` (candidates PK is composite). Before: 147 inflated rows. After: 53 canonical records.
> - **Detection centralised**: `strategies/detectors.py` → single-bar helpers used by both batch generators and PFR bridge. Equivalence verified by `TestDetectorHelpers`.
> - **Bounded scan**: `generate_pfr_signals()` accepts `max_pfr_scan=30` — stops inspecting records after N to keep cycles bounded.
> - **farm_loop wired**: `--pfr-db-path` now works in `farm_loop.py --run-paper-signals` as well as `paper_signals_run.py`.
> - **Operational update (2026-06-26)**: standalone and farm-loop CLIs expose
>   public fetch timeout, PFR scan caps, and active-observation caps for fast
>   smoke checks. Telegram and journal surfaces remain guarded and outside the
>   decision path.
> - All paper/forward observation. NOT edge. NOT order. NOT production-ready.

## What was built

### PASS A — Simple BE stop at 0.5R (`lane.py`)

Added a breakeven stop trigger at 0.5R MFE inside `lane.observe()`.

**How it works:**
- After a position is opened, if price reaches `entry ± 0.5 × risk_dist` (the 0.5R level), the effective stop (`eff_stop`) is moved to the entry price.
- This protects profits on signals that reach 0.5R then reverse.

**Same-bar ambiguity (conservative rule):**
- The BE trigger check is placed AFTER the `hit_sl` check in the candle loop.
- If a bar simultaneously has `low ≤ original_stop` AND `high ≥ be_trigger`, the original stop fires first.
- BE is NOT retroactively applied to the same bar. It takes effect from the NEXT bar.
- This is identical to how `partial_be` already works — the existing design already applied this pattern.

**New result/diagnosis:**
- Result: `"simple_be"` (distinct from `"stop"` and `"partial_be"`)
- Diagnosis: `"breakeven_save"`
- Net pct ≈ 0 (gross; slightly negative with real costs)

**Pending state:**
- `be_done` flag is saved in `sig.outcome` so incremental observe() calls resume correctly.
- `eff_stop` was already saved in pending state (unchanged behavior).

**Interaction with partial_be:**
- If both BE at 0.5R and partial_be at TP1 (1R) are active:
  - BE fires first (moves `eff_stop` to entry, but `be_done=True, partial_done=False`)
  - Then partial_be fires at TP1 (banks half, sets `partial_done=True, eff_stop=entry`)
  - When stop hits: `partial_done=True` → kind = `"partial_be"` (not `"simple_be"`)
  - The partial_be takes priority since it involves banking half the position

---

### PASS B — PFR Bridge (`pfr_bridge.py`)

New module: `src/research_lab/paper_signals/pfr_bridge.py`

**Purpose:** Load PAPER_FORWARD_READY validated farm records and generate paper-watch signals using the EXACT params from `candidates.params_json` — no invented defaults.

**Key functions:**

| Function | Purpose |
|---|---|
| `load_pfr_records(db_path)` | Reads `farm_results JOIN candidates` from strategy_lab.sqlite. Returns [] if DB missing. |
| `apply_quality_policy(records, *, policy)` | Policy-driven filter (max_dd, min_n, min_wr, min_net). No symbol blacklists. |
| `build_pfr_momentum_breakout(row, candles, *, ...)` | Builds signal from validated params. Same detection logic as farm's `signals_momentum_breakout()`. |
| `build_pfr_mean_reversion_fade(row, candles, *, ...)` | Builds signal from validated params. Same detection as `signals_mean_reversion_fade()`. |
| `generate_pfr_signals(records, *, ...)` | Top-level generation with 3 dedup layers. |

**JOIN correctness (fixed 2026-06-25):**
`candidates` has PK `(run_id, candidate_id)`. The old JOIN on `candidate_id` only produced 147 rows from 53 PFR farm_results (1 PFR row × ~2.8 candidates rows per candidate_id). Fixed to `ON fr.run_id = c.run_id AND fr.candidate_id = c.candidate_id` → 53 canonical rows, no inflation.

**Detection (centralised 2026-06-25):**
Both `build_pfr_*` builders now call `src/research_lab/strategies/detectors.py`:
- `detect_momentum_breakout(candles, decision_idx, *, lookback, threshold_pct)` → `{side, reason, ref_level} | None`
- `detect_mean_reversion_fade(candles, decision_idx, *, lookback, move_pct)` → `{side, reason, move, base} | None`

The batch generators (`strategies/breakout.py`, `strategies/mean_reversion.py`) also call these in their loops — equivalence guaranteed by `TestDetectorHelpers.test_detector_agrees_with_batch_generator_*`.

**Param validation:**
- Required params are checked before any computation
- If ANY required param is `None`, returns `(None, "missing_params:...")` — never invents a default
- RR check: `take_pct >= stop_pct * 2.0` enforced

**Identity preservation:**
Every PFR signal's `validator_context` carries:
- `setup_id`: `f"setup-{candidate_id}"`
- `candidate_id`: the farm run's candidate
- `params_hash`: SHA1 of sorted params_json (12 chars)
- `source_validation_verdict`: `"PAPER_FORWARD_READY"`
- family, timeframe, symbol, pfr metrics (win_rate, n_trades, avg_net_pct)

**Quality policy (default, configurable):**
```python
DEFAULT_QUALITY_POLICY = {
    "max_drawdown_pct": 35.0,   # policy-driven, not a BEAT blacklist
    "min_n_trades": 15,
    "min_win_rate": 0.50,
    "min_avg_net_pct": 0.3,
}
```

**Signal geometry adaptation:**
Farm params use `stop_pct/take_pct` as fixed % from entry. Lane uses absolute prices. Adaptation:
- Entry zone: narrow band around signal price (ATR-scaled)
- Stop: `entry ± stop_pct%` in absolute price units
- TP: `entry ± take_pct%` in absolute price units
- Risk check: `risk_pct = risk / price * 100` must be ≤ `MAX_RISK_PCT` (8.0%)
  - Records with `stop_pct > 8%` will be rejected by this gate (correctly — too volatile for the lane)

**Dedup layers:**
1. `dedup_key` (`symbol|tf|family`) — already active in the mover lane
2. `setup_id` — same validated setup cannot have two active signals
3. `(dedup_key, data_fingerprint)` — same data, no new bars, skip

---

### PASS C — Integration into `cycle.py`

`run_cycle()` signature extended:
```python
run_cycle(private_root, *, ..., pfr_db_path: Path | None = None, pfr_quality_policy: dict | None = None)
```

PFR lane runs AFTER mover-based generation:
- Only fires if `pfr_db_path` is not None
- Respects the shared `max_new` cap (PFR signals count toward it)
- Shares the `by_key_active` dedup set with movers
- Reports counts in `report["pfr_counts"]`: `pfr_records_loaded`, `pfr_passed_quality`, `pfr_rejected_quality`, `pfr_unique_setups`, `pfr_generated`, etc.
- `run_loop()` also extended to pass `pfr_db_path` through

Operational caps added on 2026-06-26:

| Surface | Flag | Purpose |
|---|---|---|
| standalone paper-signal CLI | `--max-pfr-scan N` | Bound PFR record inspection per cycle. |
| standalone paper-signal CLI | `--max-observe N` | Bound active signal observation for smoke/preflight. Omit for normal forward observation. |
| standalone paper-signal CLI | `--public-fetch-timeout SEC` | Bound each public OKX candle fetch. |
| `farm_loop --run-paper-signals` | `--paper-signals-max-pfr-scan N` | Same PFR cap from the farm loop. |
| `farm_loop --run-paper-signals` | `--paper-signals-max-observe N` | Same active-observation cap from the farm loop. |
| `farm_loop --run-paper-signals` | `--paper-signals-fetch-timeout SEC` | Same public fetch timeout from the farm loop. |

Verified smoke command:

```bash
python -m scripts.strategy_lab.paper_signals_run \
  --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" \
  --mode live \
  --max-signals 1 \
  --max-observe 0 \
  --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" \
  --max-pfr-scan 2 \
  --public-fetch-timeout 3
```

Observed result on 2026-06-26: the command finished in seconds, observed zero active
signals by design, generated one bounded live paper signal, and did not touch live/money
paths.

---

## What this is NOT

- **NOT an edge claim.** Farm backtest passed validation ≠ forward-test edge. Paper lane is forward observation, not trading.
- **NOT a trading signal generator.** No order path, no .env, no AUTO_TRADE, no private endpoints.
- **NOT paper-ready.** PFR status = farm backtest cleared hard validation. Forward paper observation is the NEXT step. Until N forward cycles accumulate, no action is taken.
- **NOT production-ready.** Requires explicit `pfr_db_path` to activate. Default is `None` (inactive).

---

## Security boundary (unchanged)

All code in `src/research_lab/paper_signals/` is scanned by `TestNoLiveBoundary` for forbidden imports:
`okx_client`, `ccxt`, `order_exec`, `live_engine`, `auto_trade`, `credential`, `dotenv`, `hmac`, `private_endpoint`, `order_client`.

`pfr_bridge.py` passes this scan.

---

## Files changed

| File | Change |
|---|---|
| `src/research_lab/paper_signals/lane.py` | PASS A: BE at 0.5R, `simple_be` result, `breakeven_save` diagnosis |
| `src/research_lab/paper_signals/pfr_bridge.py` | PASS B: PFR bridge; PASS 1: JOIN fix; PASS 2: use detectors; PASS 4: max_pfr_scan |
| `src/research_lab/paper_signals/contract.py` | Added `"pfr_farm"` to SOURCES |
| `src/research_lab/paper_signals/cycle.py` | PASS C: `pfr_db_path`; PASS 1: renamed `pfr_records_loaded`, added `pfr_unique_setups` |
| `src/research_lab/strategies/detectors.py` | NEW (PASS 2): `detect_momentum_breakout`, `detect_mean_reversion_fade` single-bar helpers |
| `src/research_lab/strategies/breakout.py` | PASS 2: `signals_momentum_breakout` uses `detect_momentum_breakout` |
| `src/research_lab/strategies/mean_reversion.py` | PASS 2: `signals_mean_reversion_fade` uses `detect_mean_reversion_fade` |
| `scripts/strategy_lab/farm_loop.py` | PASS 3: `--pfr-db-path` arg + pass to `run_cycle()` + print `pfr_counts` |
| `scripts/strategy_lab/farm_status_report.py` | PASS 5: PFR bridge section in `collect()` and `_print()` |
| `scripts/strategy_lab/paper_signals_run.py` | `--pfr-db-path`, `--max-pfr-scan`, `--max-observe`, `--public-fetch-timeout` CLI flags |
| `scripts/strategy_lab/operational_health.py` | Read-only operator preflight for Telegram/LLM/journal/PFR readiness without leaking secrets |
| `scripts/build_journal.py` | Private OKX fill import is opt-in via `JOURNAL_ENABLE_PRIVATE_FILLS=1` |
| `tests/test_pfr_bridge.py` | PASS D+: tests for all 10 categories (41 total) |
| `tests/test_operational_health.py` | Secret-safe preflight reporting tests |
| `tests/test_telegram_utils.py` | Lazy environment loading for Telegram surfaces |
| `tests/test_build_journal_safety.py` | Journal private-endpoint opt-in safety test |
| `docs/paper_lane_pfr_bridge_2026-06-25.md` | This file: audit updates |
