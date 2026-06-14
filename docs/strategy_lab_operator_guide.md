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
- **Event-anchored entry timing** inside event-driven runs: when a job carries an
  event context, each run also measures how late the entry was vs the event
  (lag bars/minutes, capture ratio, missed move, MFE/MAE, a `late_entry` flag).
- **1m event-microscope locator** (`microscope_scan`): read-only, capped, and
  trigger-only. It locates existing local 1m files; it never downloads and reports
  a clear SKIPPED reason when 1m data is absent.
- **Demand-driven 1m loader** (`prepare_1m_data`): derives the capped 1m windows
  the lab needs (event specs / queued jobs / a manual request), checks the local
  cache, and — only with `--apply` and a configured provider — writes just those
  windows under the private root. Default provider is `null` (no network); the real
  provider is `okx-public` (OKX public candles, read-only, **no API key**).
- **Controlled research cycle** (`research_cycle`): one bounded pass that orchestrates
  inspect -> generate proposals -> check 1m data -> (optional) prepare -> queue
  (capped) -> one throttled worker step -> status report. Dry-run by default; no
  hidden loop; the worker respects the throttle (no storm).
- **Data-complete research session** (`research_session`): wraps the cycle with a
  data-readiness gate (queue only READY jobs; missing/short/malformed are skipped with
  a reason, never faked) and an export-only LLM advisory loop (cheap -> chief, code
  validates, no paid call by default). Single pass, not a daemon.
- **Strategy data contract + readiness** (`strategy_requirements`, `data_readiness`):
  each job declares its primary-timeframe history need; readiness is checked before any
  TA runs.
- **Gated LLM send path**: a provider-agnostic sender boundary whose only shipped
  implementation is `NullReviewSender` (never calls a network). A real send needs
  several explicit gates; export stays the default.
- Private candidate registry (REJECT excluded by default), private **Obsidian
  graph** notes, SQLite queue/state, read-only dashboard, and an **export-only**
  LLM review pack.

## What is planned (not done yet)

- A GPU batch backend (the `sweep_spec.backend` field exists; CPU only today).
- More market-data providers / timeframes (today: `okx-public` fetches **1m only**;
  `null` default + offline `synthetic` test provider remain). Full-market 1m
  download is intentionally unsupported.
- A real LLM provider behind the send boundary (today only `NullReviewSender`;
  sending stays export-only).

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

By default the start **does not fetch any market data**. The 1m prepare step is
opt-in (see "Auto-prepare on start" below).

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

## Daily checklist (one-command bats)

These are the everyday operator commands. None of them spend money, run live
trades, or write to the public repo.

| Need | Command |
|---|---|
| Start dashboard + worker | `bat\strategy_lab_start.bat` |
| See everything at a glance | `bat\strategy_lab_status.bat` |
| Export LLM review pack (no API) | `bat\strategy_lab_export_pack.bat` |
| Preview next proposals (dry-run) | `bat\strategy_lab_proposals_dry_run.bat` |
| Preview what would be queued | `bat\strategy_lab_queue_validated_dry_run.bat` |
| Check 1m event-microscope data | `bat\strategy_lab_microscope_scan.bat` |
| See what 1m data is needed (dry-run) | `bat\strategy_lab_prepare_1m_data.bat` |
| Run one controlled research cycle (dry-run) | `bat\strategy_lab_cycle_dry_run.bat` |
| Plan a full research session (dry-run) | `bat\strategy_lab_research_session_dry_run.bat` |
| Watch a 30-min research loop (dry-run) | `bat\strategy_lab_research_loop_30m_dry_run.bat` |
| Run a 30-min research loop (apply, no LLM) | `bat\strategy_lab_research_loop_30m_apply.bat` |
| Bounded no-LLM research loop (default 8h, configurable) | `bat\strategy_lab_research_loop_overnight_no_llm.bat` |
| Morning summary after a loop | `bat\strategy_lab_morning_report.bat` |
| Run hard validation + feedback + setup cards | `bat\strategy_lab_validate_candidates_pipeline.bat --apply --limit 10` |
| Gracefully stop the loop after current iteration | `bat\strategy_lab_graceful_stop.bat` |
| Clear a previous stop request | `bat\strategy_lab_clear_stop.bat` |
| Stop old dashboard/worker windows | `bat\strategy_lab_stop_notes.bat` |

**Morning:** run `strategy_lab_status.bat` to see worker state, queue, latest
verdicts, candidates, proposals and the private-root location; then
`strategy_lab_start.bat` if the worker is not running.

**During the day:** `strategy_lab_proposals_dry_run.bat` to preview follow-ups,
then apply explicitly only when you agree:
`python -m scripts.strategy_lab.generate_next_proposals --limit 10 --apply` and
`python -m scripts.strategy_lab.queue_validated_proposals --apply`.

**Evening:** `strategy_lab_status.bat` for the day's results, then
`strategy_lab_stop_notes.bat` to close the two lab windows (safe; a half-claimed
job is requeued on next start).

**Overnight (no-LLM, safe default):**

```powershell
.\bat\strategy_lab_research_loop_overnight_no_llm.bat
```

This starts a bounded research loop with no paid LLM, no network fetch,
and no live trading. It prints the private root, duration, sleep, queue cap,
and the morning status command before starting. When the loop finishes (or is
stopped with Ctrl+C), run:

```powershell
.\bat\strategy_lab_morning_report.bat
```

If the report shows `FORWARD_PAPER` or `REGIME_SPECIFIC` candidates, run the
hard-validation path before treating them as reusable setups:

```powershell
.\bat\strategy_lab_validate_candidates_pipeline.bat --apply --limit 10
.\bat\strategy_lab_morning_report.bat
```

That path writes private-root requests, reports, verdicts, feedback rows and
setup cards. It does not enable the main engine and does not imply live-trading
readiness.

Default duration is 480 minutes. For a day run, set the duration before launching:

```powershell
$env:STRATEGY_LAB_LOOP_MINUTES = "300"
.\bat\strategy_lab_research_loop_overnight_no_llm.bat
```

Use `300` for 5 hours, `360` for 6 hours, or `420` for 7 hours. Optional knobs:
`STRATEGY_LAB_LOOP_SLEEP_SECONDS` (default `60`) and
`STRATEGY_LAB_LOOP_MAX_QUEUED` (default `20`).

The overnight loop uses night-mode resource policy (relaxed caps) and runs
data-ready local jobs only. If you have prepared 15m/1h/4h data, those
timeframes will be tested too; otherwise only 1d jobs run.

**Overnight (with paid LLM, explicit opt-in only):**

```bash
bat\strategy_lab_research_loop_overnight_llm.bat
```

This requires you to first set `STRATEGY_LAB_LLM_ENABLED=1` and configure the
LLM provider env. It will NOT auto-enable paid LLM. Run a tiny live test first
(see "Tiny LLM live test" below) before using this overnight.

Quick status from the terminal:

```bash
python -m scripts.strategy_lab.status
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

## Research cycle (one controlled pass)

`research_cycle` runs the whole loop once, capped and operator-visible: inspect
current state -> generate next proposals -> check required 1m data -> optionally
prepare it -> queue validated proposals (capped) -> run one throttled worker step ->
write a status report. It only orchestrates the existing, individually-safe steps —
there is **no hidden loop** and **no automatic network fetch**.

```bash
# 1) Dry-run (default): inspect + plan only. No queue writes, no worker, no network.
bat\strategy_lab_cycle_dry_run.bat
python -m scripts.strategy_lab.research_cycle --dry-run

# 2) Apply without network: store proposals, queue up to the cap, run one worker step.
python -m scripts.strategy_lab.research_cycle --apply --max-proposals 5 --max-queue 5 --max-worker-jobs 1

# 3) Apply WITH explicit 1m prep using public OKX candles (no key, public market-data only):
python -m scripts.strategy_lab.research_cycle --apply --prepare-1m --prepare-1m-apply --provider okx-public --max-proposals 5 --max-queue 5 --max-worker-jobs 1
```

- **Default is safe**: `--dry-run` writes nothing except the status report
  (`state/research_cycle/latest.json`, private), queues nothing, runs no worker, and
  makes no network call.
- **Apply** stores/queues (capped by `--max-queue`, itself clamped to the resource
  policy) and runs at most `--max-worker-jobs` worker steps (default 1). The worker
  obeys the throttle: if it is in the cool-down it records `deferred` and stops — it
  never bypasses `resource_policy.yaml`.
- **Network fetch happens only** with `--apply` **and** `--prepare-1m` **and**
  `--prepare-1m-apply` **and** `--provider okx-public`. With `--provider null` the
  prepare step is a clean no-op (`provider not configured / no data written`).
  `--provider synthetic` (offline test data) needs `STRATEGY_LAB_ALLOW_SYNTHETIC=1`.
- `--no-worker` / `--no-proposals` skip those steps. `status` and the dashboard show
  the last cycle (mode, proposals queued, data missing/prepared, worker outcome).
- **Stop**: the cycle is a single pass and returns; there is nothing to stop. To run
  it on a cadence, the operator re-runs it (no built-in daemon).

## Research session (data-complete pass + LLM advisory)

`research_session` is the highest-level operator command. It wraps one research
cycle but adds two things the cycle alone does not: a **data-completeness gate**
(never queue a job whose data is incomplete) and an **LLM advisory layer**
(export-only by default). It is a single pass, not a daemon.

```bash
# Plan only (default): no queue writes, no worker, no network, no paid LLM.
python -m scripts.strategy_lab.research_session --dry-run

# Apply without network: queue ONLY data-ready validated proposals; one worker step.
python -m scripts.strategy_lab.research_session --apply --max-candidates 5 --max-queued 5 --max-worker-jobs 1

# Apply WITH explicit 1m prep (public OKX candles) for event-anchored work:
python -m scripts.strategy_lab.research_session --apply --prepare-1m --prepare-1m-apply --provider okx-public --max-candidates 5 --max-queued 5 --max-worker-jobs 1

# Export an LLM proposal-request pack (no API call), optionally attempt a gated send:
python -m scripts.strategy_lab.research_session --apply --llm-export
```

**cycle vs session.** `research_cycle` is the mechanical loop (generate → check data
→ optional prepare → queue → worker). `research_session` is the same loop plus the
data-readiness gate on the queue and the LLM advisory step, with one combined report.
Use the cycle for a quick mechanical pass; use the session for a "serious", data-
complete research pass.

### Why missing data blocks a test

A job is queued only when its data is **READY**. The session/cycle check each
proposal's primary-timeframe data (the local archive) before queueing:

- `READY` — file present with enough rows → queued.
- `MISSING_DATA` / `TOO_SHORT` / `MALFORMED` — **not queued**; recorded with a reason
  (and a suggested `prepare_1m_data` command when 1m is missing). The lab never runs
  technical analysis on missing/invalid data or fakes a result.

To fill missing 1m windows, prepare them explicitly (see "Prepare 1m data on
demand"), then re-run the session — the previously-missing jobs become READY.

### LLM proposal loop (cheap → chief, advisory only)

The LLM is advisory and disabled by default. The design is cheap → chief:

- The **cheap** model is asked (via the exported request pack) to propose strategy/
  filter/parameter variants as **strict JSON only** — no code, no shell, no trading.
- **Code validates every candidate** with the deterministic validator (known family/
  symbol/timeframe, bounded variants, safe wording, private boundary). Invalid
  candidates are rejected with a reason.
- Only the **validated** subset is eligible for a **chief** review pass (capped).
- The LLM never decides what enters the queue — **code does**. LLM output is never
  executed.

Default is export-only and offline. A real send needs **all** gates:
`STRATEGY_LAB_LLM_ENABLED=1`, `STRATEGY_LAB_LLM_PROVIDER=<provider>`, a configured
provider client, a daily budget cap, and `--llm-send` (and never on dry-run). No API
keys are stored or printed (env names only).

A real **proposal** provider now exists (separate from the review-pack send path,
which still ships only `NullReviewSender`). It is OpenAI-compatible
(**Alibaba/Qwen/openai-compatible**), synchronous, stdlib-only, and isolated from the
scanner runtime and its budget. It is wired into the research **loop** (below) via
`--llm-propose`. Configure it with:

| Env | Meaning |
|---|---|
| `STRATEGY_LAB_LLM_ENABLED=1` | master switch (off → no network ever) |
| `STRATEGY_LAB_LLM_PROVIDER` | `alibaba` / `qwen` / `openai-compatible` / `synthetic` |
| `STRATEGY_LAB_LLM_BASE_URL` | OpenAI-compatible base (e.g. dashscope compatible-mode) |
| `STRATEGY_LAB_LLM_API_KEY` | the key VALUE (header only; never logged/stored) |
| `STRATEGY_LAB_LLM_MODEL_CHEAP` | the cheap model id |
| `STRATEGY_LAB_LLM_DAILY_CAP` | RUB/day cap; a send/propose is blocked once reached |

Spend is recorded in a **lab-private** usage log (`reports/llm_usage/`, tokens/cost
only, never the scanner budget). `status` and the dashboard show the provider state
(`disabled` / `export_only` / `ready`) and today's request/token/RUB spend. The
offline `synthetic` provider (gated by `STRATEGY_LAB_ALLOW_SYNTHETIC=1`) returns
fixed candidates for pipeline testing — no network, no cost.

### Expected pace

This is a controlled research machine, not a 24/7 poller. **One or two serious
strategy/setup variants per day is an acceptable pace.** Heavy calculation is fine;
flooding CPU/API is not — the worker is capped (`--max-worker-jobs`, default 1) and
throttled by `resource_policy.yaml`, and network/LLM are opt-in.

## Research loop (controlled, time-bounded)

`research_loop` repeats the research session on a **wall-clock budget** and stops by
itself. It is **not** a daemon: it ends at `--duration-minutes` or `--max-iterations`,
sleeps `--sleep-seconds` between iterations, and writes a heartbeat each iteration. If
the worker is in its throttle cool-down it records `deferred` and simply waits for the
next iteration — it never storms the queue.

```bash
# Dry-run for 30 min: plan only each iteration; no queue writes, no worker, no network, no LLM.
bat\strategy_lab_research_loop_30m_dry_run.bat
python -m scripts.strategy_lab.research_loop --dry-run --duration-minutes 30 --sleep-seconds 60

# Apply for 30 min: queue data-ready jobs + one worker step per iteration. No network, no paid LLM.
bat\strategy_lab_research_loop_30m_apply.bat
python -m scripts.strategy_lab.research_loop --apply --duration-minutes 30 --sleep-seconds 60 --max-worker-jobs-per-iteration 1 --max-queued 5

# Apply + cheap LLM proposing (COSTS MONEY): requires the STRATEGY_LAB_LLM_* env above
# (provider/base_url/key/model) + STRATEGY_LAB_LLM_DAILY_CAP. The LLM provider is chosen
# ONLY by STRATEGY_LAB_LLM_PROVIDER; --provider is the DATA provider, never the LLM.
python -m scripts.strategy_lab.research_loop --apply --llm-propose --duration-minutes 30 --max-llm-contract-failures 3
```

- **Default is dry-run**: no queue writes, no worker, no network, no LLM. Only the
  heartbeat + loop report under the private root (`state/research_loop/`).
- **LLM proposing is gated and OFF by default.** `--llm-propose` does nothing on
  dry-run (never calls), and on `--apply` it still needs the full provider env and an
  unexhausted daily cap (a real network provider also re-checks the send gates). The
  offline `synthetic` provider (`STRATEGY_LAB_ALLOW_SYNTHETIC=1` +
  `STRATEGY_LAB_LLM_PROVIDER=synthetic`) exercises the path with no cost.
- **Bounded by construction**: duration is clamped (≤ 4h), iterations are capped, and
  each iteration runs at most `--max-worker-jobs-per-iteration` worker steps.
  Note: explicit `--night-mode` raises the duration ceiling to 12h; it does not
  remove worker throttling, queue caps, LLM daily caps, or the LLM contract breaker.
- **LLM contract breaker**: if the provider repeatedly returns invalid proposal JSON
  or the wrong top-level shape, `--max-llm-contract-failures` disables LLM proposing
  for the rest of that bounded run. The worker can continue processing already-ready
  queue items without burning more model calls.
- **Overnight paid LLM is explicit**: `bat\strategy_lab_research_loop_overnight_llm.bat`
  refuses to auto-enable paid calls. Set `STRATEGY_LAB_LLM_ENABLED=1` yourself first,
  keep `STRATEGY_LAB_LLM_DAILY_CAP` small, and watch `strategy_lab_status.bat`.
- `status` and the dashboard show the last loop (mode, iterations, queued, missing
  data, worker done/deferred, last LLM status + reject reasons) and today's LLM spend.

### First controlled cycle showcase

The first unattended controlled cycle is documented as a public-safe example:

```text
examples/strategy_lab_first_cycle/README.md
```

It ran the closed proposal queue for four hours under the safe desktop policy:
8 worker jobs completed, 0 failed, 0 missing-data skips, and 0 LLM/API spend.
The run produced private candidate rows and Obsidian/report artifacts, but only
aggregated proof-of-operation numbers are published. Full candidate tables,
parameters, SQLite state, and follow-up specs stay in the private research root.

The run also confirmed a deliberate safety ceiling: `research_loop` clamps
requested duration to four hours. A true overnight mode should be an explicit
future change, not an accidental removal of the safety cap.

### Multi-timeframe data (TODO)

Readiness is **timeframe-aware**: a proposal is queued only if a candle file for its
**requested** timeframe exists with enough rows. Today only **1d** candle JSON exists
under the feasibility glob, so 1d proposals run while **15m / 1h / 4h** proposals are
reported `MISSING_DATA` (with the present timeframes listed) instead of silently
running on daily bars.

To enable lower timeframes, extend the existing public OKX adapter
(`src/research_lab/market_data_provider.py`, today 1m-only) to fetch 15m/1h/1d history
into the feasibility glob format, mirroring the capped `prepare_1m_data` flow (bounded
pages, no full-market download, private-root only). Until then the honest behavior is
a clear `MISSING_DATA` + TODO, never a faked run.

**Prerequisite when adding multi-timeframe data:** the worker's file picker
(`experiment.choose_symbol_file` / `evaluate_spec`) is currently timeframe-blind — it
selects the largest candle file for a symbol regardless of timeframe, because today
each symbol has only one timeframe (1d) on disk. The research-loop/session/cycle queue
path is protected by the timeframe-aware readiness gate, but the *worker itself* and the
older `enqueue_research_plan` path are not. Before a second timeframe file for any symbol
lands in the glob, give `ExperimentSpec` a `timeframe` field and make `choose_symbol_file`
match it (return no file → the job is skipped, not run on the wrong bars), so the safety
holds at every entry point, not just the gated one.

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

- Sending a pack to a model goes through a gated boundary
  (`src/research_lab/llm_review_sender.py`). Every gate is required:
  1. `--send` was passed; 2. not a dry-run; 3. `STRATEGY_LAB_LLM_ENABLED=1`;
  4. a provider is configured; 5. a daily budget cap (`STRATEGY_LAB_LLM_DAILY_CAP`)
  is present and not exhausted.
- The only sender shipped today is `NullReviewSender`, which never calls a
  network, so gate 4 fails and `--send` always falls back to export-only and
  prints the blocking reason. Each attempt is recorded privately (no keys logged).
  The dashboard/status show the send gate state and `LLM review: disabled` unless
  the env flag is set.

## Event microscope (1m)

The 1m timeframe is a **trigger-only event microscope**, not a scanner. It is for
zooming into a single already-detected move on a couple of high-volatility
symbols — never a full-universe 1m sweep.

```bash
bat\strategy_lab_microscope_scan.bat
python -m scripts.strategy_lab.microscope_scan --universe l2_high_beta --json
```

- **Read-only and no downloader.** It only locates existing local 1m files. If a
  symbol has no usable 1m file it is reported as `missing` / `too_short` /
  `not_1m` — a clean skip, never a crash and never a network fetch.
- **Capped by the 1m timeframe profile**: at most `max_symbols_per_cycle` symbols,
  `max_event_windows` windows, `max_window_hours`×60 bars per window, and
  `max_variants_per_setup` variants (see `configs/strategy_lab/timeframe_profiles.yaml`).
- **Full-universe 1m sweeps stay blocked** in `sweep_spec` validation and the
  resource policy (`allow_1m_jobs: trigger_only`).

Today there is no local 1m data, so the scan reports every symbol as `missing` —
that is the expected, honest result until a 1m data path is added.

## Prepare 1m data on demand

The microscope only *reads* 1m data; it never downloads. `prepare_1m_data` is the
demand-driven loader that fetches **only** the capped 1m windows the lab actually
needs (current event sweeps / queued specs, or an explicit manual request).

```bash
# See what 1m windows are needed and which are missing (writes nothing):
bat\strategy_lab_prepare_1m_data.bat
python -m scripts.strategy_lab.prepare_1m_data --dry-run

# A specific window (UTC), dry-run (okx-public does NOT hit the network on dry-run):
python -m scripts.strategy_lab.prepare_1m_data --symbol BTC_USDT_SWAP --start 2026-06-10T00:00 --end 2026-06-10T03:00 --provider okx-public --dry-run

# Actually fetch public OKX candles + write (one symbol, small window):
python -m scripts.strategy_lab.prepare_1m_data --symbol BTC_USDT_SWAP --start 2026-06-10T00:00 --end 2026-06-10T03:00 --provider okx-public --apply

# Confirm it is visible afterwards:
python -m scripts.strategy_lab.microscope_scan --universe core_market
python -m scripts.strategy_lab.status
```

- **Default provider is `null` (no network).** With `--apply` and no configured
  provider it prints `provider not configured / no data written` and exits cleanly.
- **`okx-public`** fetches OKX **public** 1m candles only — read-only, **no API
  key**, no order/account/private endpoints, no symbol discovery. Dry-run makes no
  network call; fetch happens only on `--apply`.
- **No full-market download.** Requirements are capped by the 1m policy: at most
  `max_symbols_per_cycle` symbols, `max_event_windows` windows, and
  `max_window_hours`×60 bars per window. `--max-symbols` / `--max-windows` can only
  *lower* those caps, never raise them. The provider paginates only the requested
  window with a bounded page count (no infinite pagination) and a short backoff.
- Prepared 1m candles are written in the canonical OHLCV format (deduped, sorted,
  UTC) under the private root at `strategy-lab/market_data/1m/` — never the public repo.
- This is **research data only**: no live trading, no order/account endpoints, no
  paid LLM.
- A built-in offline `synthetic` provider (deterministic, clearly tagged, **not**
  real market data) exists for pipeline testing/demos and is gated behind
  `STRATEGY_LAB_ALLOW_SYNTHETIC=1`.

### Auto-prepare on start (opt-in, off by default)

`strategy_lab_start.bat` can run the prepare step automatically before the worker,
but **only when you opt in**. Default start fetches nothing. Env flags:

| Env flag | Default | Effect |
|---|---|---|
| `STRATEGY_LAB_PREPARE_1M` | `0` | `1` adds a prepare step (`[2c]`) to start |
| `STRATEGY_LAB_PREPARE_1M_APPLY` | `0` | `1` makes the step apply (fetch+write); `0` is dry-run |
| `STRATEGY_LAB_MARKET_DATA_PROVIDER` | `null` | `okx-public` (real, public, no key) or `synthetic` |
| `STRATEGY_LAB_PREPARE_1M_MAX_SYMBOLS` | — | optional, clamped to policy by the CLI |
| `STRATEGY_LAB_PREPARE_1M_MAX_WINDOWS` | — | optional, clamped to policy by the CLI |

- With `PREPARE_1M=1` and `APPLY=0` the step is a dry-run (no network, no writes).
- A real fetch needs `PREPARE_1M=1` **and** `APPLY=1` **and** a real provider
  (`okx-public`). With provider `null` the step prints
  `provider not configured / no data written`.
- A whole-start dry-run (`STRATEGY_LAB_START_DRY_RUN=1`) forces the prepare step to
  dry-run regardless, so a dry-run start never fetches.
- The worker itself never fetches; only this explicit start step (or the manual CLI)
  can. `status` and the dashboard show whether auto-prepare is on, its mode/provider,
  and whether it would touch the network.

Example — fetch real public OKX candles on start (one-off, this shell only):

```bash
set STRATEGY_LAB_PREPARE_1M=1
set STRATEGY_LAB_PREPARE_1M_APPLY=1
set STRATEGY_LAB_MARKET_DATA_PROVIDER=okx-public
bat\strategy_lab_start.bat
```

If 1m data is missing and auto-prepare is off, `microscope_scan` and `status` point
you to the exact `prepare_1m_data` commands instead of silently degrading.

## What "late entry" means

The lab's recurring pain is "direction was right, but the entry was late." When a
run has an event context, it measures entry quality against the event, not just
final PnL:

- **lag_bars / lag_minutes** — how long after the move started the entry happened.
- **capture_ratio** — fraction of the move captured from entry to move end.
- **missed_move_pct** — how much of the move was already gone at entry.
- **mfe_pct / mae_pct** — best favorable / worst adverse excursion after entry.
- **late_entry** — flagged when little of the move is captured (capture < 0.3) or
  most of it is already gone (missed ≥ 50%).

These are honest diagnostics, **not** profitability claims. The reducer uses them
to flag `entry_late`, so a setup that only "works" by entering late is not
promoted.

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
