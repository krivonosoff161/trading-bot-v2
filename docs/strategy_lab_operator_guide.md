# Strategy Lab — Operator Guide

Date: 2026-06-13. How to run the local research machine safely. It produces
research labels and candidates, **not** profitability claims. No live trading,
no `.env`/secrets, no automatic LLM spend.

## What is implemented today

- Deterministic strategy simulation over local candles (12 strategies, regime
  labels, validator-lite).
- **Runtime resource-policy enforcement**: the worker reads
  `configs/strategy_lab/resource_policy.yaml` and throttles itself
  (`min_seconds_between_jobs`, `max_jobs_per_hour`) and caps per-job variants
  (`max_variants_per_job`). Default mode is `quiet_desktop`; `night_mode` is
  opt-in only.
- **Job generation from universe + timeframe** (`enqueue_research_plan`) with
  dry-run / apply and idempotent queueing.
- **Event-driven sweep generation** (`generate_event_sweeps`): bounded sweeps
  from strong historical moves, compiled via `SweepSpec` -> `ExperimentSpec`.
- **Reducer** verdicts with reason codes + first-class **entry-timing** metrics.
- Private candidate registry (REJECT excluded by default), private **Obsidian
  graph** notes, SQLite queue/state, read-only dashboard, and an **export-only**
  LLM review pack.

## What is planned (not done yet)

- A GPU batch backend (the `sweep_spec.backend` field exists; CPU only today).
- A 1m event-microscope live data path / downloader.
- Sending review packs to a model (export works; sending is gated off).

See [strategy_lab_architecture_next.md](strategy_lab_architecture_next.md).

## Safe start

Normal operator start is one command:

```bash
bat\strategy_lab_start.bat
```

Default behavior: sync the private state DB, queue a bounded `core_market / 1d`
research plan, open the dashboard, and start one throttled worker loop. The
dashboard opens at `http://127.0.0.1:8765`.

Optional overrides before running the bat:

```bash
set STRATEGY_LAB_UNIVERSE=l2_high_beta
set STRATEGY_LAB_TIMEFRAME=15m
set STRATEGY_LAB_FULL=1
set STRATEGY_LAB_NIGHT_MODE=1
```

Manual/debug chain:

```bash
# 1. See what would run (writes nothing):
python -m scripts.strategy_lab.enqueue_research_plan --universe core_market --timeframe 1d --dry-run

# 2. Queue it (writes specs to the private root + SQLite queue):
python -m scripts.strategy_lab.enqueue_research_plan --universe core_market --timeframe 1d --apply

# 3. Run one job (respects the resource policy throttle/cap):
python scripts/strategy_lab/worker_once.py

# 4. Continuous worker (sleeps min_seconds_between_jobs between jobs by default):
python scripts/strategy_lab/worker_loop.py
```

`--timeframe` accepts `1d`, `1h`, `15m`. `1m` is a trigger-only event microscope
and is intentionally not used for full sweeps. Add `--full` to use the full
per-timeframe caps instead of the smoke subset, and `--night-mode` to use the
relaxed limits.

For CI or dry-run checks of the bat itself, use:

```bash
set STRATEGY_LAB_START_DRY_RUN=1
bat\strategy_lab_start.bat
```

## Discovery loop (event sweeps, reducer, Obsidian)

Beyond fixed plans, the lab can discover research targets from historical moves
and aggregate results into verdicts:

```bash
# Propose bounded sweeps from strong historical moves (dry-run default):
python -m scripts.strategy_lab.generate_event_sweeps --universe l2_high_beta --timeframe 15m --dry-run
python -m scripts.strategy_lab.generate_event_sweeps --universe l2_high_beta --timeframe 15m --apply

# After runs complete, write private Obsidian notes for non-REJECT candidates:
python -m scripts.strategy_lab.build_obsidian_graph
```

- Event sweeps are bounded by the resource policy (`autopilot_generate_max`); the
  event is historical and each sweep runs the normal no-lookahead simulator. 1m
  stays a trigger-only microscope.
- Every run now also writes `reducer_report.json` (private): per-(family, symbol)
  verdicts (`REJECT / OBSERVE / REGIME_SPECIFIC / FORWARD_PAPER / NEEDS_MORE_DATA`)
  with reason codes. A single lucky parameter without neighbor support is never
  promoted.
- Entry-timing aggregates (capture ratio, MFE/MAE, late-entry rate) are recorded
  per run and shown on the dashboard.

## Closed research loop (proposals)

The lab can close the loop: results -> review pack -> next proposals -> validate ->
queue -> worker -> new results. Every step is deterministic, dry-run by default,
and never calls a paid API.

```bash
# 1. Generate next-experiment proposals from the registry (rule-based, deterministic):
python -m scripts.strategy_lab.generate_next_proposals --limit 10 --dry-run
python -m scripts.strategy_lab.generate_next_proposals --limit 10 --apply   # writes private proposals.jsonl

# 2. (Optional) import proposals a human saved from an LLM (no API call here):
python -m scripts.strategy_lab.import_llm_proposals --file path\to\llm_output.json --dry-run
python -m scripts.strategy_lab.import_llm_proposals --file path\to\llm_output.json --apply

# 3. Queue only VALIDATED proposals (idempotent, bounded by max_queue_size):
python -m scripts.strategy_lab.queue_validated_proposals --dry-run
python -m scripts.strategy_lab.queue_validated_proposals --apply

# 4. Worker runs them as usual (throttled / capped):
python scripts/strategy_lab/worker_once.py
```

- Proposals are typed objects with status `PROPOSED -> VALIDATED / REJECTED -> QUEUED`,
  validated against resource caps, timeframe policy (no 1m full sweep), known
  symbols/families, bounded variants, safe wording, and the private/public
  boundary. They live in `strategy-lab/proposals/proposals.jsonl` (private).
- The rule-based generator only *requests the next test* — it never promotes or
  claims profitability. LLM review stays export-only; importing model output is a
  manual file read, and queueing always requires an explicit `--apply`.
- This is separate from the older `autopilot_once.py` (manual/advanced) which
  writes `proposals/proposal_registry.jsonl` + `proposals/specs/`; the closed
  loop uses `proposals/proposals.jsonl` + `proposals/queued_specs/`.

The one-click start never generates or queues proposals by default. Set
`STRATEGY_LAB_PROPOSAL_DRY_RUN=1` before `bat\strategy_lab_start.bat` to also run
`generate_next_proposals --dry-run` (print only, never apply).

## Dry-run vs apply

- `--dry-run` (default) prints the planned job(s) and any skipped symbols
  (e.g. `no_usable_data`) and writes nothing.
- `--apply` writes one spec per planned job under
  `trading-bot-research/strategy-lab/plans/specs/` and queues it. Re-running the
  same plan does not duplicate pending jobs (idempotent by deterministic spec).

## Resource policy (CPU safety)

`configs/strategy_lab/resource_policy.yaml`, default `quiet_desktop`:
one worker, `min_seconds_between_jobs: 900`, `max_jobs_per_hour: 2`,
`max_variants_per_job: 24`, no heavy or full-1m jobs. When the throttle blocks a
run, `worker_once.py` prints `deferred reason=... wait_seconds=...` and exits
cleanly (no job consumed). `night_mode` relaxes only the keys it lists and is
used only when you pass `--night-mode` (or set `STRATEGY_LAB_NIGHT_MODE=1`).

## Stopping it

There is no daemon. Stop the loop with Ctrl+C in the "Strategy Lab Worker"
terminal (or close it). One job finishes at a time, so stopping is safe; a
half-claimed job is requeued by `reap_stale_jobs` on the next start.

## Avoiding any LLM/API spend

- No code path calls a paid API.
- `export_llm_review_pack` only writes a local summaries pack:

```bash
python -m scripts.strategy_lab.export_llm_review_pack --limit 10
```

- Sending to a model requires BOTH `STRATEGY_LAB_LLM_ENABLED=1` and `--send`,
  and even then no client is wired, so it reports "not configured" and exits.
  The dashboard shows `LLM review: disabled` unless the env flag is set.

## Inspecting the dashboard

```bash
python scripts/strategy_lab/serve_dashboard.py   # or bat\strategy_lab_dashboard.bat
```

Open `http://127.0.0.1:8765`. Read-only, localhost-only, no secrets, no absolute
private paths. Shows resource mode, universe coverage, queue health
(pending/running/completed/failed), last worker status (incl. deferred reason),
a Research Summary card (latest reducer verdicts, entry-timing aggregate,
Obsidian note count, next-run/deferred reason), candidate counts by verdict, and
the LLM-review enabled/disabled flag.

## Where private outputs go

`%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\` (override with
`TRADING_BOT_RESEARCH_ROOT`): run artifacts, candidate registry, plan specs,
review packs, SQLite state, Obsidian vault. The public repo holds code, configs,
schemas, and docs only.

## What "good candidate" means (and does not)

A candidate that reaches `OBSERVE`, `REGIME_SPECIFIC`, or `FORWARD_PAPER` passed
some lite gates and is **worth more testing** — it is not a profitable or
live-tradable strategy. `FORWARD_PAPER` means "track on paper next", nothing
more. `REJECT` rows stay in the run artifacts but are kept out of the candidate
registry by default (use `--include-rejects` for debugging).
