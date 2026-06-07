# Scout Layer/Source Plan

Date: 2026-06-07

This document fixes the intake plan for the scanner by layer. It exists because the live runtime has drifted toward broad RSS/Google News while several planned event sources are still not connected. The result is predictable: BTC dominates, L2 starves, and many cards are `NO_GO` because they describe already-realized moves instead of giving early informational edge.

## Goal

Build a scanner that reads the right information for each layer:

- `L1`: BTC/ETH majors and crypto regime
- `L2`: alts, memes, on-chain, listings
- `L3`: metals / macro-precious
- `L4`: oil / energy
- `L5`: equities / AI proxies / pre-IPO
- `L6`: cross-layer watch and linkage

The scanner must distinguish:

- `realized`: already happened
- `expected`: scheduled / pending
- `context`: commentary / recap / opinion
- `watch`: attention-directing, not a trade verdict

## Current Diagnosis

As of 2026-06-07:

- live cards are dominated by RSS/Google News
- `L2` has native event sources now, but still lacks watch/on-chain depth
- on-chain, wallet and fund-flow sources are mostly planned, not live
- the system is often correct about "no news edge" but still misses tactical moves

This means the current scanner is closer to a lagging news filter than to a multi-layer event engine.

## Source of Truth

The machine-readable plan lives in:

- `src/scout/config/layer_source_matrix.yaml`

This file defines:

- per-layer thesis
- tradeable event families
- context-only families
- realized / expected / watch sources
- activation order
- global intake rules

## Layer Summary

### L1

Read:

- ETF/fund flows
- regulation
- exchange/protocol incidents
- macro releases that directly hit crypto
- tactical regime data (funding/OI/liquidations)

Live now:

- Cointelegraph
- Decrypt
- Google News crypto
- BTC/ETH tactical regime feed (OKX public funding/OI/liquidation monitor)
- FRED expected macro calendar (CPI / FOMC / Employment Situation)

Missing next:

- ETF flow feed

### L2

Read:

- listings/delistings
- unlocks
- rug/exploit/token-security flags
- whale moves
- DEX liquidity/volume spikes
- governance/tokenomics changes

Live now:

- OKX listings
- DexScreener
- GoPlus/RugCheck
- Token unlock feed (requires `TOKENOMIST_API_KEY`)

Missing next:

- Telegram alpha as watch-only
- wallet/on-chain flow

This is the highest-priority gap.

### L3

Read:

- Fed/CPI/payroll impact on metals
- China demand
- central bank buying
- mine disruptions

Live now:

- Google News metals
- FRED expected macro calendar

Missing next:

- macro follow-through quality and surprise resolution

### L4

Read:

- OPEC decisions
- EIA inventory data
- sanctions / supply shocks
- outages / pipeline / refinery disruptions

Live now:

- Google News energy
- OilPrice
- EIA WPSR expected release cadence
- OPEC official next-meeting cadence

Missing next:

- realized surprise handling on release day

### L5

Read:

- SEC filings
- earnings/guidance
- capital raises
- partnerships
- market-moving product launches for listed proxies

Live now:

- SEC EDGAR
- Google News equities

Missing next:

- earnings calendar
- company IR/newsroom feeds

### L6

Read:

- full text from L1-L5 event blocks
- buried references to linked entities
- cross-layer next-watch cues

Rule:

L6 should not issue trade verdicts. It should emit `watch_hint` and `linked_entities`.

## Activation Order

1. `L2 event sources`
   - DexScreener
   - GoPlus/RugCheck
   - Token unlocks

2. `L1 tactical sources`
   - ETF flow feed

3. `Expected macro layer`
   - FRED

4. `Energy official cadence`
   - EIA
   - OPEC

5. `L5 expected earnings`
   - earnings calendar

6. `L6 seed`
   - internal event blocks -> watch hints

## Execution Rules

- Keep raw ingest wide.
- Keep chief escalation narrow.
- Do not trade from commentary or recap.
- Distinguish `NO_NEWS_EDGE` from `NO_TRADE`.
- Treat Google News primarily as coverage, not alpha.
- Use official or event-native feeds first where possible.

## What Starts Now

This plan is now approved in-repo and encoded into config.

Implementation starts with:

1. `layer_source_matrix.yaml` as single source of truth
2. bringing `L2` live with event-native sources
3. bringing `L1` tactical BTC/ETH live
4. bringing `FRED` expected macro live
5. bringing `EIA/OPEC` expected energy cadence live
6. then equities official calendars

Until those steps are live, the scanner should be interpreted as a conservative event filter, not a full market-intelligence engine.
