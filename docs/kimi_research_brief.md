# Kimi Research Brief

## Purpose

This file is a single self-contained briefing for external research/review.
It describes the current project state, architecture, limitations, known product gaps,
representative outputs, and the exact questions that need answering.

**Important:** the final response must be written in Russian.
The end user is Russian-speaking, so recommendations, explanations, and output examples
should be phrased for a Russian-speaking reader.

The goal is not to revive a trading bot. The goal is to improve a data-driven
chart analysis service that produces useful, actionable explanations for a human client.

---

## Project State

- Project: `trading-bot-v2`
- Current direction: `analyzer-first`, not `bot-first`
- Delivery channel: Telegram-first
- Target product: concierge AI chart analyzer
- Current stage from plan: `S1 - Concierge Analyzer MVP`

### What the project is building now

Not an auto-trading bot.

It is building a showable chart analysis workflow that:
- accepts a chart screenshot,
- gets a market snapshot from OKX by `symbol + captured_at`,
- produces structured output,
- sends result to Telegram.

### Why the project pivoted

The previous direction was a live trading bot with `Strategy E`.
That strategy is now closed and archived.

Main reasons for the pivot:
- too much time was spent on strategy tuning,
- the system turned into an indicator filter machine,
- the market demand that appeared was for clear chart analysis, not for an autonomous bot.

---

## Critical Historical Context

## Strategy E status

`Strategy E` is closed and should not be revived casually.

Pattern that was tested:
- `1H trend -> 15m pullback -> 5m breakout`

Core logic:
- 1H: `EMA(20/50) + ADX + DI`
- 15m: pullback near `EMA20` with weaker pullback volume
- 5m: breakout with strong volume

### Why it failed

- Triple condition was too restrictive and rarely aligned.
- Pullback volume logic based on a tiny number of candles was unstable.
- 1H ADX was too inertial.
- The system was built indicator-outward, not market-structure-inward.

### Important lesson

Do not fix the project by adding many new hard filters.
That is exactly how the previous approach collapsed into low-frequency / zero-signal behavior.

---

## Current Product Truth

The current system should be described honestly as:

`symbol + timestamp market analyzer with screenshot delivery`

It is **not** a true screenshot analyzer yet.

Why:
- the engine does not read chart content from the image;
- it computes the entire market analysis from OKX candle data;
- the image is only used at the end to generate `annotated.png` with a text sidebar.

That distinction matters because:
- any unrelated image can still be accepted,
- old screenshots can be analyzed as if they were current,
- the system currently gives an illusion of image understanding that it does not actually have.

---

## Relevant Files And Roles

### Product / docs

- `PLAN.md`
- `SERVICE_PIVOT.md`
- `docs/strategy_e_postmortem.md`
- `config.yaml`

### Engine

- `scripts/analyze_chart.py`
- `src/strategy/signal.py`
- `src/strategy/indicators.py`
- `src/exchange/okx_client.py`

### Delivery

- `scripts/telegram_bot.py`
- `src/utils/telegram.py`

---

## Current End-To-End Flow

## 1. Telegram intake

User sends an image to Telegram bot.

Bot flow:
- accepts photo/document image,
- asks the user to choose a trading pair,
- sets `captured_at` from Telegram message timestamp,
- calls `analyze_chart.run(symbol, captured_at_iso, image_path)`,
- sends back `client summary` and `annotated.png`.

### Current Telegram weaknesses

- uses polling, no watchdog / process supervision,
- opens a new `aiohttp.ClientSession` per API call,
- weak error handling and limited diagnostics,
- if idle/network issues happen, bot may sit in a broken state,
- no robust reconnect/heartbeat/file traceback setup,
- `captured_at` is derived from message time, not from the actual screenshot capture time.

---

## 2. Analysis engine

`scripts/analyze_chart.py`:
- fetches historical candles from OKX for `1H`, `15m`, `5m`,
- computes indicators,
- reuses `get_signal()` from `src/strategy/signal.py`,
- builds:
  - operator report,
  - client summary,
  - JSON snapshot,
  - optional annotated PNG.

### Important limitation

The image does not participate in trading logic.

The market logic is computed entirely from OHLCV data by:
- `symbol`
- `captured_at`

The image is only pasted together with summary text afterward.

---

## 3. Output artifacts

For each run, the analyzer creates:
- `*_report.md`
- `*_snapshot.json`
- `*_annotated.png`

The report is technical.
The client summary is more product-facing.
The annotated image is the original screenshot with a right-side text panel.

---

## Current Signal Logic

### 1H trend

Bull trend:
- `EMA20 > EMA50`
- `+DI > -DI`
- `ADX >= threshold`

Bear trend:
- `EMA20 < EMA50`
- `-DI > +DI`
- `ADX >= threshold`

If neither trend is present:
- result = `no_trend_1h`

### 15m setup

Conditions:
- price must be near `EMA20` within `pullback_touch_atr * ATR`
- structure must stay intact relative to `EMA50`
- pullback volume must not be too strong versus prior impulse

If not:
- result = `no_pullback_15m`
- or `pullback_volume_strong`

### 5m trigger

Conditions:
- breakout of recent structure,
- volume above moving average threshold,
- DI confirmation in the direction of the trend.

If not:
- `no_breakout_5m`
- `breakout_volume_weak`
- `di_not_confirmed_5m`

### Exact trade plan

Exact `entry / sl / tp1 / tp2` is currently computed only when the signal is already confirmed.

That is a product problem because the user often needs an actionable pending plan before confirmation.

---

## Current Strategy Parameters

From `config.yaml`:

### Trading

- leverage: `5`
- poll interval: `10s`

### Symbols

- `BTC-USDT`
  - `target_margin_usdt: 200`
  - `min_sl_percent: 0.003`
- `SOL-USDT`
  - `target_margin_usdt: 100`
  - `min_sl_percent: 0.005`

### Strategy

- `ema_fast: 20`
- `ema_slow: 50`
- `adx_period: 14`
- `adx_threshold_1h: 18`
- `pullback_touch_atr: 0.5`
- `pullback_volume_bars: 2`
- `pullback_volume_factor: 2.0`
- `breakout_lookback_5m: 3`
- `trigger_volume_ma_period: 20`
- `trigger_volume_factor: 1.1`
- `sl_buffer_atr: 0.2`
- `sl_min_atr: 1.2`
- `tp_r_multiple: 2.0`

---

## Current Product Problems

## 1. Trust problems

- System visually presents itself like a screenshot analyzer, but it is not.
- Any image can pass through.
- An old screenshot can be analyzed as if it were current.

## 2. Engineering problems

- Telegram transport is fragile.
- Logging and diagnostics are weak.
- Polling recovery is basic.
- The analyzer and delivery layers are still too tightly coupled.

## 3. Analysis problems

- Current market view is too narrow: mostly `EMA/ADX/DI/ATR/volume`.
- This is often enough to say `observe`, but not enough to explain market structure deeply.
- Current logic is inherited from a closed strategy, so expansion must be careful.

## 4. Actionability problems

The user/investor may like the analysis but still not know what to do.

Typical current outputs are too vague:
- "wait for breakout candle"
- "price near EMA20"
- "long only"

What the user really needs:
- enter now or not,
- long / short / none,
- exact trigger price,
- planned entry,
- exact stop,
- tp1 / tp2,
- invalidation level,
- optionally leverage/size explanation based on risk.

---

## Additional Clarifications

These are direct answers to common scoping questions.

### What assets are analyzed

Current focus: crypto only.

More specifically:
- liquid crypto instruments on OKX,
- current working examples: `BTC-USDT`, `SOL-USDT`, `XRP-USDT`, `DOGE-USDT`,
- market data in code is currently requested as OKX `SWAP` instruments.

Do not optimize research for:
- equities,
- forex,
- multi-asset portfolio logic.

### What timeframes matter most

Current core stack:
- `1H` = higher-timeframe context / regime,
- `15m` = setup / pullback / zone,
- `5m` = trigger / execution logic.

That is the current main operating model.

Research can suggest useful additions, but should not assume a full multi-timeframe explosion.
If a new timeframe is proposed, it must have a very clear reason.

### What strategy family the project wants to support

Current product is not trying to support many unrelated strategies at once.

Primary focus:
- trend-following / continuation style analysis,
- pullback / reclaim / breakout style scenarios,
- strong `no-trade` detection when context is weak.

Possible research direction:
- improve regime awareness so the analyzer can distinguish:
  - trend continuation,
  - no-trade / range,
  - possibly later range-specific scenarios.

But the project should not become a strategy zoo.

### What data is already available from OKX

Currently available and already used:
- historical OHLCV candles via REST,
- ticker / current price,
- instrument info for swap symbols,
- account / position / order methods in the existing client,
- candle confirmation flag from OKX history candles.

Currently **not** part of the analyzer stack:
- order book / depth,
- tick-by-tick order flow,
- WebSocket-based live microstructure,
- liquidation tape style data.

So first-wave recommendations should prefer features computable from REST OHLCV.

### Time / infrastructure / resource constraints

Preferred constraints for near-term implementation:
- stay inside the current Python repo,
- keep current Telegram-first delivery,
- avoid heavy infrastructure changes,
- avoid forcing cloud rebuilds or a full new service architecture,
- avoid WebSocket / order-flow requirements for the first wave,
- prefer features that can be implemented this week on top of the current stack.

Lightweight optional additions are acceptable only if they are realistic:
- small validation layer,
- compact vision sanity-check,
- minimal external service if absolutely justified.

But recommendations should assume:
- current infrastructure first,
- low complexity,
- fast iteration,
- no giant platform rewrite.

---

## Representative Outputs

These are real examples from `scripts/analysis_output`.

## Example A: BTC-USDT, watch mode

Summary of the technical report:
- 1H trend: bullish
- 15m setup: near EMA20, structure intact
- 5m trigger: no breakout
- final reason: `no_breakout_5m`

Problem:
- output says the setup is close,
- but still does not tell the investor the exact trigger/entry/stop/tp plan in a fully actionable format.

Notable detail:
- pullback volume ratio `1.06` is still labeled as "weak", because current logic only rejects when it exceeds a much looser threshold.

## Example B: SOL-USDT, outside market

Summary:
- 1H trend absent (`ADX 13.2`)
- 15m setup invalid
- 5m local activity exists, but no higher-timeframe context
- final reason: `no_trend_1h`

Strength:
- "do not trade" logic is explicit.

Weakness:
- still needs better market-structure explanation and clearer return condition.

## Example C: XRP-USDT, no pullback

Summary:
- 1H trend bullish
- 15m price too far from EMA20
- final reason: `no_pullback_15m`

Strength:
- it does point to a return-to-zone scenario.

Weakness:
- the plan is still narrative-heavy and not exact enough for non-technical users.

---

## External Comparison Test

An external vision model was tested on one BTC screenshot and gave a more locally bearish / short-biased interpretation.

However:
- that model misread one visible indicator on the chart,
- specifically, it interpreted a KDJ-style indicator as ADX.

Conclusion:
- pure vision-only LLM analysis is not reliable enough as source of truth.
- The right direction is hybrid:
  - market data is the truth,
  - vision can be used for sanity-check, chart validation, or packaging.

---

## What Needs To Be Improved

The project needs recommendations in several areas at once:

### 1. Product diagnosis

- What is this product honestly today?
- What are the biggest trust risks?
- What are the biggest gaps between "interesting analysis" and "actionable service"?

### 2. Telegram resilience

- reconnect logic
- polling robustness
- session reuse
- timeouts
- traceback logging
- watchdog / restart strategy

### 3. Image validation

How to prevent "any random image" from being accepted:
- cheap validation rules,
- chart sanity-check,
- optional vision-based verification,
- realistic reject flow.

### 4. Analysis expansion

Need broader analysis, but not a new filter monster.

Research should separate:
- `soft context features`
- `plan builder features`
- `hard filters`

### 5. Output redesign

Need a clearer client schema:
- `NOW / WATCH / OUT`
- `LONG / SHORT / NONE`
- exact trigger
- exact plan
- invalidation
- confidence / quality score

### 6. Risk / leverage explanation

Need a clear answer:
- whether leverage can be suggested honestly without deposit and risk budget,
- and if not, what risk-based sizing model is required.

---

## Research Constraints

Very important constraints:

- Do not turn the project back into a live trading bot.
- Do not propose a giant research framework.
- Do not propose adding 20 new indicators for the sake of complexity.
- Do not rely on vision-only analysis.
- Do not repeat the `Strategy E` failure mode of stacking hard filters until frequency dies.

Preferred principle:

`data truth > image interpretation`

And for new features:
- first strengthen explanation,
- then strengthen planning,
- only then consider gating/filtering.

---

## Candidate Research Directions To Evaluate

Please evaluate these directions explicitly.

### Market structure

- HH / HL / LH / LL
- swing highs / lows
- BOS / CHOCH
- local support / resistance
- range boundaries

### Volatility / regime

- ATR percentile
- expansion vs compression
- Bollinger position / squeeze
- trend vs range regime

### Volume / context

- relative volume beyond one candle
- impulse vs pullback volume context
- OBV slope / context

### Levels / confluence

- previous day high / low
- session high / low
- VWAP / anchored VWAP if realistic on current stack
- distance to key levels

### Candle quality

- body / range
- wick asymmetry
- close location in range
- breakout candle quality

### Trade planning

- better invalidation logic
- scenario quality before entry
- zone width
- stop distance
- R:R quality before confirmation

### Momentum

- RSI regime / divergence
- MACD slope / histogram only if truly useful

---

## Exact Questions For Review

Please answer in the following structure.

Write the full answer in Russian.

### 1. Diagnosis

- Give a blunt diagnosis of the current system.
- Explain what the system should honestly be called today.

### 2. Product truth

Separate:
- trust problems
- engineering problems
- analysis problems
- output/actionability problems

### 3. P0 fixes

What must be fixed immediately.

### 4. P1 fixes

What should be improved next for the biggest quality gain.

### 5. P2 ideas

What can be explored later.

### 6. Candidate features by bucket

For each proposed feature provide:
- name
- what it measures
- why it helps
- calculation logic
- required data
- can it be computed now from REST OHLCV
- bucket: soft context / plan builder / hard filter
- overfitting risk
- explainability for client
- priority

### 7. TOP-10 shortlist

Best overall features to evaluate.

### 8. TOP-5 first implementation

Best candidates for immediate first rollout.

### 9. Dangerous features to avoid

Features likely to recreate the old over-filtering failure.

### 10. Proposed client output schema

Redesign the output to support:
- `status: NOW / WATCH / OUT`
- `direction: LONG / SHORT / NONE`
- if `NOW`:
  - entry
  - stop
  - tp1
  - tp2
- if `WATCH`:
  - exact trigger price
  - planned entry
  - planned stop
  - planned tp1 / tp2
  - invalidation level
- if `OUT`:
  - why there is no trade
  - under what condition to come back
- confidence / quality score
- short investor-friendly action block

### 11. Risk / leverage model

Answer:
- can leverage be suggested honestly without deposit and risk budget,
- if not, what risk-based sizing inputs are needed,
- what output format is easiest for an investor to understand.

### 12. Concrete implementation order

Propose a realistic order for implementation this week.

---

## One-Line Summary

This project is a data-driven chart analysis service with a Telegram delivery layer.
It already produces structured output, but it suffers from trust gaps, narrow analysis,
weak actionability, and transport fragility.

The task is to make it broader, clearer, and more actionable without turning it back
into an over-engineered, over-filtered trading bot.
