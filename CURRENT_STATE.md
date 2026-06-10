# Current State

Updated: 2026-06-10

## Short Version

`trading-bot-v2` is currently an **info-edge scanner research project**.

The active work is `src/scout/`: news/event intake, layer routing, LLM-assisted
classification, Telegram cards, JSONL records, and outcome measurement.

The older WebSocket trading engines remain in the repository, but they are frozen
or reference-only for the current work.

## Active System

Runtime path:

```text
scanner.bat
  -> src/scout/scanner_v0.py --buffer --limit N
  -> data/scout/news_buffer.sqlite
  -> logs/scout/*.jsonl
```

Important files:

- `src/scout/scanner_v0.py` - scanner runtime.
- `src/scout/news_buffer.py` - SQLite intake buffer.
- `src/scout/router.py` - asset/layer/baseline routing.
- `src/scout/agents/layer_agent.py` - cheap fact extraction.
- `src/scout/agents/orchestrator.py` - code rules and chief escalation.
- `src/scout/agents/chief.py` - final `GO / NO_GO / WATCH` model.
- `src/scout/resolve_outcomes.py` - forward outcome scoring.
- `src/scout/source_quality_report.py` - source/routing report.
- `src/scout/calibration_report.py` - missed-`NO_GO` calibration report.
- `SCANNER_SPEC.md` - scanner design and as-built notes.
- `TASK.md` - local handoff.

## Current Diagnosis

The scanner is collecting data, but it is not calibrated yet.

Observed local direction:

- many cards become `NO_GO`;
- some `NO_GO` cards are followed by real movement;
- this may be correct for late public news, but it may also indicate overly strict
  filters, stale source classification, weak layer-specific logic, or generic chief
  prompting;
- Telegram output no longer shows every dataset card by default: `GO` / `WATCH`
  chief cards go to the channel, `NO_GO` stays in logs unless
  `SCANNER_SEND_NO_GO=true`.

Current priority:

1. Watch live scanner output after the chief prompt/gate change.
2. Run `calibration_report.py` after fresh outcomes accumulate.
3. Keep all `NO_GO` decisions in logs and training data.
4. Improve source/layer thresholds from evidence, not intuition.
5. Design `MARKET_CONTEXT / WATCH_MARKET` for no-single-asset headlines.

## Boundaries

Do not touch without explicit user approval:

- live order execution;
- `AUTO_TRADE`;
- real-money paths;
- `.env` secrets;
- production trading config;
- old frozen engines except for read-only reference.

Safe current work:

- scanner docs;
- scanner tests;
- scanner reporting;
- Telegram formatting for scanner;
- source-quality analysis;
- outcome-resolver reliability.
