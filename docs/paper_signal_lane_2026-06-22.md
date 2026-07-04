# Operational paper-watch signal lane — 2026-06-22 (research-only)

Built an operational PAPER signal lane on top of the research farm: it turns live movers + fresh keyless
OKX candles into human-readable, actionable PAPER-WATCH cards (entry / stop / TP / invalidation / max-hold
/ reason-now / expiry), observes them to an outcome on elapsed bars, and writes a deterministic visual
review. Hard boundary held: NO orders, NO .env/AUTO_TRADE/private endpoints; Telegram is an opt-in
notification surface only; an LLM cannot mint a signal (deterministic schema + checks gate).

## 1. What was before
The farm computed and rejected well but never emitted "what to do now and how to review it later". Paper
outcomes existed only as batch backtests of hard-validated cards — no current entry zone / invalidation /
expiry, no live watch card, no visual review.

## 2. What was built (new, isolated — research farm untouched)
- `src/research_lab/paper_signals/contract.py` — `PaperActionSignal` (all operational fields) +
  `validate_signal` (the deterministic gate: zone/stop/TP must be internally consistent) + `render_card`.
- `…/store.py` — append-only JSONL + latest-per-id state view + dashboard snapshot.
- `…/lane.py` — selection gates, deterministic geometry (ATR-bounded stop, 1R/2R TP, TF-horizon max-hold),
  lifecycle `observe` (armed→opened→stop/take/timeout/expired, limit-pullback fill, no look-ahead),
  deterministic `review` (R-multiple diagnosis) + text/PNG visual artifacts.
- `scripts/strategy_lab/paper_signals_run.py` — runner: fresh candles → gates → signal → observe → review →
  store + cards + gate-by-gate report; `--notify` (Telegram, off by default, env-gated).
- Surfaced in `farm_status_report` (paper-signals line). Tests: `tests/test_paper_signals.py` (16).

## 3. The 3-5 paper signals (REAL, from fresh OKX data)
5 generated from the live-mover universe + fresh candles. Geometry decided on bars up to a boundary
max_hold+arm back (no look-ahead), then observed on the elapsed bars.

| signal | side | entry zone | stop | TP (1R/2R) | risk(1R) | hold | outcome | review |
|---|---|---|---|---|---|---|---|---|
| BICO 15m | SHORT | 0.04036-0.04085 | 0.04202 | 0.03919/0.03802 | 2.9% | 28b | stop −2.86% (−0.99R) | bad_exit_gave_back |
| RESOLV 15m | SHORT | 0.02017-0.02072 | 0.02204 | 0.01885/0.01753 | 6.5% | 28b | expired_no_entry | expired_no_entry |
| RESOLV 1h | LONG | 0.01839-0.01891 | 0.01715 | 0.02015/0.02139 | 6.6% | 30b | stop −6.74% (−1.03R) | bad_exit_gave_back |
| UB 15m | LONG | 0.13838-0.14078 | 0.13263 | 0.14653/0.15228 | 4.1% | 28b | stop −4.16% (−1.02R) | bad_exit_gave_back |
| UB 1h | SHORT | 0.07506-0.07662 | 0.07895 | 0.07273/0.07040 | 3.1% | 30b | stop −3.04% (−0.98R) | valid_loss |

Every signal has entry/stop/TP/invalidation/max-hold + a deterministic reason and a visual review artifact
(`state/derived/paper_reviews/<id>.md` + `.png`). Risk is bounded (~1R losses, not the absurd −33% an
early structural-stop draft produced — caught and fixed with an ATR-bounded stop + a `risk_too_wide` gate).

## 4. Active / pending / closed
All 5 reached a terminal review state on elapsed bars: 4 stop (~−1R), 1 expired_no_entry. (Run with a
boundary at "now" instead to emit live `armed`/`pending` watch cards for the next 24-48h.)

## 5. Where dashboard / graph / Telegram are
- `state/derived/paper_signals.json` (snapshot) + `paper_signals.jsonl` (audit log) — read by
  `farm_status_report` ("paper signals … by_status"). Obsidian/graph rebuilt.
- Telegram: `--notify` sends the cards ONLY if `TELEGRAM_BOT_TOKEN` + a paper chat id are already in env;
  otherwise `skipped:no_token_or_chat`. Surface only, never a decision maker, never an order.

## 6. Visual reviews created
5 `.md` (ASCII path vs entry + levels + diagnosis) and 5 `.png` (price + entry zone + stop + TP lines).
Deterministic-first; an `llm_diagnosis` hook exists but is left null (constrained JSON, never the gate).

## 7. Outcomes recorded
5 outcomes with net%, net_R, MFE/MAE, capture, bars_held, diagnosis. Honest read: continuation entries on
already-extended movers mostly reverted to ~−1R — consistent with the prior finding that mover momentum is
a direction coin-flip. The lane is the deliverable; a winning setup is NOT claimed.

## 8. What needs the next cycle
- Run the lane in "live now" mode (boundary=now) to seat 3-5 `armed` watch cards and let the lifecycle
  mature them over 24-48h (then review).
- Wire the lane into `farm_loop` (one bounded call per cycle) so signals refresh automatically.
- Add a dedicated dashboard panel + graph nodes (signal→setup→validation→outcome→review→memory).
- Broaden sources (POSITIVE_VALIDATED, tactical context) beyond live movers; add a fade variant.

## 9. Checks that passed
- `pytest tests/test_paper_signals.py` → 16 passed (contract gate, store, selection gates, geometry,
  lifecycle transitions, review, **AST no-live-order boundary**). `ruff` clean. Full suite: see commit.
- dry-run (no apply) + apply smoke on 5 candidates (store + 5 review .md/.png + snapshot written).
  `git diff --check` clean.

## 10. Commits / push
See the farm-lane commit range on branch feature/calc-farm (pushed). Nothing is edge or paper-ready; this
is an operational PAPER observation lane, not a live trading system.
