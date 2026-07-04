# Paper-signal lane → self-driving cycle — runbook + report (2026-06-22, research-only)

The operational paper-watch lane is now a repeatable, self-correcting research/paper cycle:
fresh OKX data → multi-family signal generation → armed/live observation → terminal outcome → visual
review → outcome memory → next-cycle gate/priority adjustment → status/surface. Five build cycles,
each audit → implement → run → analysis → commit. Hard boundary held throughout: NO orders / .env /
AUTO_TRADE / private endpoints; Telegram is opt-in surface only; an LLM can never mint or alter a signal.

## RUNBOOK (operator commands)
Private root: `C:\Users\krivo\github_projects\trading-bot-research\strategy-lab`. All keyless/public.

```
# one dry-run (plan only, writes nothing)
python -X utf8 -m scripts.strategy_lab.paper_signals_run --mode live

# one apply (seat live armed watch cards + snapshot + status)
python -X utf8 -m scripts.strategy_lab.paper_signals_run --mode live --apply

# bounded N cycles (observe -> close -> remember -> generate), stop-file aware
python -X utf8 -m scripts.strategy_lab.paper_signals_run --mode live --loop 8 --sleep-seconds 600 \
    --stop-file <root>\state\STOP_PAPER.txt

# replay diagnostic (labelled; resolves on already-elapsed bars to seed the learning memory)
python -X utf8 -m scripts.strategy_lab.paper_signals_run --mode replay --loop 4

# status (current cards + diagnosis counts)
python -X utf8 -m scripts.strategy_lab.paper_signals_run --status

# optional Telegram (surface only; sends ONLY if TELEGRAM_BOT_TOKEN + PAPER_CHAT_ID already in env)
... --apply --notify

# surfaces
python -X utf8 -m scripts.strategy_lab.farm_status_report          # shows "paper signals: by_status"
python -X utf8 -m scripts.strategy_lab.build_obsidian_graph        # graph/obsidian refresh
```

### Where things live (private root, git-ignored)
- `state/derived/paper_signals.jsonl` — append-only audit (one row per signal version; latest = current).
- `state/derived/paper_signals.json` — current-state snapshot (dashboard/farm_status read this).
- `state/derived/paper_signals_status.json` — by_status + diagnosis counts + last cycle.
- `state/derived/paper_signal_memory.jsonl` — terminal outcomes (the learning base).
- `state/derived/paper_reviews/<signal_id>.md` + `.png` — the visual replay per terminal outcome.

### How to stop / how to read outcomes
Stop: create the `--stop-file` (or Ctrl+C). Read: `--status`, or open a review `.md`/`.png`, or the
status JSON. Status is the source of truth for "what happened".

## FINAL REPORT (5 cycles)

**1. What was broken.** The lane was a one-shot generator: signal_id used `int(now)` (re-runs duplicated),
no re-observe of armed signals across runs, no outcome memory, only one family (continuation on extended
movers, which reverted), no learning.

**2. What was built.**
- C1 — repeatable restart-safe cycle: stable `signal_id`+`dedup_key`+`data_fingerprint`; observe→close→
  remember→generate; live vs replay; `--loop N`/`--stop-file`/`--status`; status JSON.
- C2 — family registry: continuation, pullback_continuation, reversal_fade (tactical), liquidity_sweep_
  reclaim, early_tp_tactical + watch_only; `entry_after_move` guard (continuation refuses exhaustion, fade
  requires it); RR>=2 except tactical; TF-aware holds.
- C3 — bounded multi-cycle run; outcomes (all types) → memory; a visual package per terminal.
- C4 — learning loop: richer diagnosis enum; `learn_known_bad` skips a (symbol,tf,family) after >=3 all-bad;
  `family_priority` orders by good-rate; LLM `validate_advice` schema gate (advisor-only).
- C5 — operator commands + this runbook + surface in farm_status_report.

**3. Signals created (this session, fresh OKX).** 24 terminal (replay, 4 cycles) across 5 families +
5 live armed (forward watch). Plus the dedup/regen gates kept it from re-grinding (regen_ttl rose each
cycle).

**4. Active / closed / expired.** Live: 5 armed (pending forward). Replay memory: 24 terminal.

**5. Outcomes by diagnosis (R-based, deterministic).**
`bad_exit_gave_back=10, good_signal=8, wrong_direction=2, stop_too_tight=2, missed_pullback=1,
valid_loss=1`. 8/24 (33%) good_signal — the multi-family expansion produces winners where the original
continuation-only lane stopped out almost everywhere.

**6. Dominant diagnosis → next-cycle action.** `bad_exit_gave_back` (10) dominates: favourable move given
back → targets too far / needs partial-TP or trailing. This is the concrete next-cycle lever (tune the
TP plan / add a trailing exit), surfaced by the learning loop — not guessed.

**7. Gates that cut.** `regen_ttl`/`dedup_same_data` (no re-grind), `watch_only_choppy_no_trend` (no-trade
in chop), `entry_after_move_exhausted` (no chasing), `not_extended_no_fade`, `rr_below_2`,
`risk_too_wide`, `stale_data`, `learned_known_bad`.

**8. What improved across cycles.** continuation-only (≈all stops) → 5-family lane with 33% good_signal;
one-shot → repeatable+restart-safe; no memory → outcomes feed the next cycle's skip-list and family order.

**9. Still deferred / why.** Live-now signals need wall-clock bars to mature (24-48h) — seated, pending.
A dedicated dashboard panel + graph nodes (signal→setup→outcome→review→memory) is a surface follow-up
(status line + JSON snapshot ship now). Source breadth (POSITIVE_VALIDATED / tactical) is a next lever.

**10. Checks.** 28 targeted tests (contract gate, store, selection gates, geometry, families, lifecycle,
richer diagnosis, learning/no-regeneration, AST no-live-order). ruff clean. `git diff --check` clean.
dry/apply/replay-loop/live all run on fresh OKX. Full pytest: see commit.

## Honest framing
Nothing here is edge or paper-ready. The deliverable is a **self-correcting paper/research loop** that
explains *why* each signal resolved and changes the next selection accordingly — exactly the brief. Losses
are knowledge (the diagnosis distribution is the steering signal), not failure.
