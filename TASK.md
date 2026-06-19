# TASK / HANDOFF FOR CLAUDE AND CODEX

Updated: 2026-06-19

This file is the local handoff channel between agents in VS Code.
It is not the canonical architecture document.

## Current State

Current center: the calculation farm, not the scanner.

Canonical flow:

```text
farm_loop
  -> farm_coordinator over state/farm_tasks.sqlite
  -> data prepare / funding / OI
  -> run_sweep compute queue in state/strategy_lab.sqlite
  -> classify into unique_candidates
  -> hard validation from unique_candidates
  -> stamp-back into farm_results + unique_candidates
  -> setup_library cards
  -> paper_loop only for paper_forward_ready cards
  -> paper_trades.jsonl + paper_outcomes
```

This file is local handoff context, not the canonical architecture. For current
truth read `CURRENT_STATE.md`, `ARCHITECTURE.md`, `README.md`,
`docs/farm_loop_lifecycle.md`, `docs/farm_runbook.md`,
`docs/farm_ownership_map.md`, and `docs/paper_runtime_design.md`.

The scanner is still active as an upstream intake source, but it is not the
operational center. Old `universe_farm_loop` / `scanner_farm_loop` paths are
legacy unless a task explicitly asks to inspect them.

## Active Safety Boundary

- Do not touch `.env`, `AUTO_TRADE`, live order execution, private exchange
  endpoints, Telegram credentials, or the old main trading engine.
- New farm/paper modules must stay paper/research only and pass the existing
  AST boundary test.
- Public OKX market data, public funding/OI, local prepared candles, and local
  private-root artifacts are allowed.

## Current Runtime Focus

1. Keep `farm_loop` as the brain and `farm_tasks.sqlite.unique_candidates` as the
   canonical source for validation handoff.
2. Keep hard-validation identity fingerprint-level, not raw `candidate_id`.
3. Keep `setup_library` as the only feed into `paper_loop`.
4. Record paper outcomes both as JSONL and in `strategy_lab.sqlite::paper_outcomes`.
5. Use status tools (`farm_status_report`, `status`, `morning_report`) to show
   hard validation and paper handoff state.

## Useful Commands

```bash
python -m scripts.strategy_lab.farm_loop --once --dry-run
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --enrich-funding --enrich-oi
python -m scripts.strategy_lab.farm_status_report
python -m scripts.strategy_lab.paper_loop --once --dry-run
python -m scripts.strategy_lab.status
```

## Next Design Work

- richer paper promotion/demotion criteria;
- one-click operator switch from legacy loops to `farm_loop`;
- discovery ranking by movers;
- more GPU kernels for strategy families;
- microstructure provider when a real public/keyless or explicitly approved data
  source exists.

## Historical Scanner Handoff Below

Committed baseline before this handoff:

- `d6116d7` - honest Stage 0 metrics in `resolve_outcomes.py`
- `06e5294` - OKX perp instruments for L3 metals / L4 oil baselines
- `3790d87` - strong cross-layer asset matches in router
- `e43040c` - HYPE and WLD coverage
- `1ccbb0b` - propagate `cross_layer` through buffer pipeline

Do not build an execution-oriented `main_event_engine` yet.

## What Stage 0 Proved

Stage 0 is complete enough for the current decision.

Key result:

```text
90 scanner cards
19 mature outcomes
17 NO_GO + 2 WATCH
0 GO
```

Corrected metrics:

```text
NO_GO volatility missed at 3%:     16/17 (94%)
NO_GO directional missed at 3%:     9/17 (53%)
NO_GO idiosyncratic missed at 3%:   4/17 (24%)
```

Interpretation:

- The original "94% missed" was inflated by beta/fat-tail volatility.
- Real event-specific misses exist, but sample size is too small for a main engine.
- The biggest actionable issue was recall, not LLM reasoning.

Drop analysis:

```text
trash_lowmat: mostly clean
asset_capped: clean duplicates
dup/noise/stale/context: OK
no_tracked_asset: real blind spot
```

Recall diagnosis:

- 7-8 L5 events from crypto wires were blocked by source-layer constraints.
- Strong cross-layer fallback now recovers `COIN`, `ANTHROPIC`, `SPACEX`.
- `HYPE` and `WLD` were added to L2 coverage.
- Macro/no-single-asset headlines remain intentionally unassigned.

## Current Runtime Focus

Let the scanner run and observe the post-fix flow.

Check:

- `cross_layer=true` events are present and not noisy.
- HYPE/WLD route correctly only on strong names or `$`/pair confirmation.
- L3/L4 now get OKX price/outcome through `XAU/XAG/XPT/XPD/CL/NG-USDT-SWAP`.
- Alibaba/Yandex LLM role behavior remains stable.
- Telegram cards are not duplicated or malformed.

Useful commands:

```bash
python src/scout/resolve_outcomes.py --report
python -m src.scout.news_buffer stats
python -m src.scout.news_buffer ready --limit 5
python -m pytest tests/test_scanner_router.py tests/test_scanner_runtime.py tests/test_scanner_records.py -q
```

## Next Design Step

Do not create a sixth agent/process yet.

The next design item used to be a passive macro/context class:

```text
MARKET_CONTEXT / WATCH_MARKET
```

Purpose:

- capture macro, regulation, stablecoin, geopolitics, tax, policy headlines with no single asset;
- attach affected assets such as `BTC`, `ETH`, `CL`, `XAU`, `QQQ`;
- write context to logs for later analysis;
- do not emit trade recommendations by itself.

Open design questions:

- Store as separate `market_context.jsonl` or as journal rows with a context verdict?
- Deterministic macro-term gate first, or cheap LLM classification?
- How to map contexts to affected assets without polluting trade candidates?
- How to measure context usefulness later?

## Current Bridge State

Implemented:

- `src/scout/watch_queue.py` queues only `WATCH/GO`; `NO_GO` is excluded.
- `src/strategy/setup_confirmation.py` classifies scanner watches against a
  `SignalResult`-like object.
- `TRADE_PLAN_READY` is paper-only and keeps `execution_allowed=false`.
- `scripts/analysis/build_watch_queue.py --dry-run` shows how many existing
  journal rows are eligible.

Still missing:

- a paper-only runner that consumes open watches and writes confirmation results;
- per-asset `WATCH` synthesis;
- extended Telegram analysis by button/command;
- fresh 24-48h source onboarding measurements.

## Hard Constraints

- Python only.
- No Docker.
- No new services.
- Do not expand model-provider routing.
- Use existing `src/utils/llm_client.py`.
- Chart rendering is allowed only as visual context, not as a decision or
  execution trigger.
- Do not revive old `main_impulse_engine`.
- Do not reuse old signal logic blindly.
- Do not touch live order / auto-trade paths.

Safe reuse from frozen main:

- `src/strategy/signal_contract.py`
- `src/strategy/chart_renderer.py`
- records/logging pattern

## Working Principle

Data first.
One experiment second.
Architecture third.

For the next session:

1. Review 2-4 hours of scanner output after recall fix.
2. If flow is clean, design `MARKET_CONTEXT/WATCH_MARKET`.
3. Only after more mature outcomes, revisit Stage 1 market-context experiment.
