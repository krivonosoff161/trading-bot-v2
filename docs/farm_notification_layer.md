# Farm Visibility & Notification Layer — design (Telegram NOT connected)

Status: **DESIGN / DEFERRED** · Last updated: 2026-06-18

This is a design note only. **No Telegram credentials are wired and no real messages are
sent.** Telegram returns later as a *notification layer*, never as part of the calculation
core. Implementation requires an explicit, separate operator command. Paper/research only.

## Principle

The farm's structured state (`farm_tasks.sqlite` + `logs/farm/*.jsonl`) is the source of
truth. Every surface below is a **read-only consumer** of that state. Notifications are an
output edge, not an input to compute or money.

## 1. Dashboard (already reads lifecycle)

`farm_cockpit.build_cockpit` (v2) already exposes a `lifecycle` section from
`farm_tasks.sqlite`: `by_state`, `by_task_type`, `blocked_reasons`, `deferred_reasons`,
`intake_unconsumed`, `calcs_completed_today`, `unique_candidates`, `validation`
(hard_status), `export_followups`, plus the GPU/CPU split and a `live_trading:false`
banner. The web dashboard / `farm_status_report` render this. Future polish: a small
time-series from `cycle_log.jsonl` (throughput, blocked-over-time) — read-only, no new
write path.

## 2. Obsidian (meaningful summaries, not raw cycles)

`farm_obsidian` should publish *semantic* notes, not raw cycle dumps:
- per promoted / validated unique candidate (one note, updated on re-arm),
- a rolling "farm state" note (latest cycle pivot, blocked/deferred reasons, calcs today),
- a "data gaps" note (NEEDS_OI/FLOW/MICRO counts + which symbols).
Source = `unique_candidates` + the latest `cycle_log.jsonl` row + `status_counts()`. It must
summarize, never append a note per cycle (that would be log spam in note form).

## 3. Telegram (future notification layer — design only)

When (and only when) explicitly enabled by a separate command, a notifier reads farm state
and emits **alerts a human should act on**:

| Trigger | Source | Example |
|---|---|---|
| Promoted candidate | `unique_candidates.validation_status = FORWARD_PAPER` | "BTC-USDT-SWAP 1h momentum_breakout → FORWARD_PAPER" |
| Validation verdict | `unique_candidates.hard_status` | "PAPER_FORWARD_READY / FAILED_OOS …" |
| Blocked data requirement | `blocked_reasons` (e.g. NEEDS_MICRO_DATA persistent) | "3 families blocked: NEEDS_MICRO_DATA (no provider)" |
| Error needing action | `logs/farm/errors.jsonl` | "worker error: …" |
| Loop health | `cycle_log.jsonl` (no progress for N cycles / pivot=blocked) | "farm idle: blocked:no_eligible_tasks for 2h" |

### Hard constraints for the future implementation

- Telegram is a **separate process / opt-in flag**, reading farm state files — it is NOT
  imported by `farm_coordinator` / `farm_tasks_db` / the compute path.
- Credentials come from the existing `src/utils/telegram.py` env path **only when the
  operator turns it on**; until then this doc is the only artifact.
- Notifier is **read-only** on farm state; it never mutates tasks, queues, or candidates,
  and has no order/`.env`/`AUTO_TRADE` access.
- Rate-limited / deduped (one alert per state change, mirror the cycle-log change
  signature) so it does not spam.

## Status

- Dashboard lifecycle: **implemented** (cockpit v2).
- Obsidian semantic summaries: **partial** (`farm_obsidian` exists for farm_results;
  task-lifecycle / unique-candidate notes are a follow-up).
- Telegram notifier: **DEFERRED** — design only, not built, no creds wired.
