# Remote Data Manifest

Purpose: this file tells remote coding agents what important project data exists locally but is not available in the GitHub checkout because it is ignored by `.gitignore`.

Remote agents must not assume missing ignored files do not exist. If a task needs one of these files or folders, ask the user or local Codex session for a safe summary/export.

## How To Use

1. Read this file before making conclusions from missing files.
2. Check `.gitignore` before saying a file is absent from the project.
3. If a required file is listed here, request a safe export instead of guessing.
4. Do not ask for raw secrets, `.env`, API keys, or full private logs.
5. Prefer small sanitized artifacts: `summary.md`, `metrics.json`, `sample_logs.jsonl`, or selected report excerpts.

## Ignored Context And Data

| Path / pattern | Local purpose | Why ignored | What to request if needed |
|---|---|---|---|
| `SESSION.md` | Current local session memory and handoff notes. | Working memory, may contain volatile/private context. | Ask for a sanitized session summary or the relevant section. |
| `.claude/` | Claude local memory, settings, and session artifacts. | Tool-private working state. | Ask for a summary of the relevant Claude memory item. |
| `.env`, `*.env` | API keys and runtime secrets. | Secrets must never be committed or shared. | Do not request raw values. Ask whether dummy/demo mode is enough. |
| `logs/` | Current live/runtime logs after the latest reset. | Runtime data, can contain private operational details. | Ask for a sanitized log excerpt, counts, or metrics summary. |
| `logs_archive/` | Archived logs, including `logs_archive/09.05.2026/`. | Large/private runtime history. | Ask for a specific sanitized excerpt or generated analysis summary. |
| `scripts/backtest_candle_cache.pkl` | Local candle cache for backtests. | Generated binary cache, large. | Ask whether the cache exists locally and request derived metrics, not the raw cache. |
| `scripts/backtest_candle_cache_*.pkl` | Dated/ranged candle caches, e.g. 35d/65d. | Generated binary caches, large. | Ask for the cache date range and a safe backtest result export. |
| `scripts/backtest_mark_index_cache_*.pkl` | Mark/index candle caches for backtest validation. | Generated binary caches, large. | Ask for availability and derived validation metrics. |
| `scripts/backtest/backtest_candle_cache_*.pkl` | Backtest-local candle caches. | Generated binary caches, large. | Ask local Codex/user to run the target script or export summary metrics. |
| `scripts/backtest/backtest_mark_index_cache_*.pkl` | Backtest-local mark/index caches. | Generated binary caches, large. | Ask for derived metrics or a specific local run. |
| `scripts/journal.xlsx`, `*.xlsx` | Excel journals and multi-sheet signal/trade analysis. | Generated/private working data. | Ask for selected sheet export as CSV/markdown summary. |
| `scripts/backtest/*.json` | Generated backtest result JSON files. | Generated outputs. | Ask for the specific JSON result or summarized table if needed. |
| `scripts/backtest_result.txt`, `scripts/backtest_*.txt`, `scripts/backtest_run_*.txt` | Generated backtest text reports. | Generated outputs. | Ask for the specific report excerpt. |
| `scripts/backtest_runs/*` | Generated backtest run folders. | Generated outputs. | Ask for the run id and summary artifacts. |
| `scripts/backtest/backtest_runs/*` | Generated backtest run folders under backtest module. | Generated outputs. | Ask for the run id and summary artifacts. |
| `scripts/_sweep_variants/` | Generated parameter sweep variants. | Generated outputs. | Ask for sweep table/results summary. |
| `scripts/signal_log.jsonl` | Signal pipeline runtime data. | Live runtime data. | Ask for sanitized signal counts or sample rows. |
| `scripts/signal_labels.jsonl` | Signal labels and outcomes. | Generated/private analysis data. | Ask for aggregate metrics or selected anonymized rows. |
| `scripts/signal_log_notrade.jsonl` | No-trade signal runtime data. | Live runtime data. | Ask for aggregate metrics or selected sample rows. |
| `scripts/ws/cache/*` | WebSocket/pump engine caches. | Generated runtime caches. | Ask whether cache exists and request derived pump/backtest metrics. |
| `scripts/subscriptions.json` | Client/user subscription data. | Private user data. | Do not request raw file. Ask for anonymized counts only. |
| `scripts/pattern_db.csv` | Generated pattern database. | Generated research artifact. | Ask for selected aggregate stats or sanitized rows. |
| `docs/*.mp4`, `docs/video*_frames/`, `docs/video*_transcript_*.txt` | Video analysis artifacts. | Large/generated media artifacts. | Ask for a text summary or selected transcript excerpt. |
| `docs/*.png`, `docs/*.jpg`, selected image names | Local screenshots/images. | Large/private media artifacts. | Ask for the specific image to be attached manually if needed. |
| `docs/статистика.csv` | Local stats export. | Generated/private report. | Ask for a sanitized CSV excerpt or markdown table. |

## Known Current Local Artifacts

These are known to exist in the local workspace as of 2026-05-09, but remote agents may not see them in GitHub:

| Local artifact | Notes |
|---|---|
| `SESSION.md` | Current session handoff exists locally. |
| `logs/` | Current logs exist after 2026-05-09 reset. |
| `logs_archive/09.05.2026/` | Archived logs for May 9 exist locally. |
| `scripts/backtest_candle_cache_35d.pkl` | Local 35-day candle cache. |
| `scripts/backtest_candle_cache_65d.pkl` | Local 65-day candle cache. |
| `scripts/backtest_mark_index_cache.pkl` | Local mark/index cache. |
| `scripts/backtest_mark_index_cache_35d.pkl` | Local 35-day mark/index cache. |
| `scripts/backtest_mark_index_cache_65d.pkl` | Local 65-day mark/index cache. |
| `scripts/backtest/backtest_candle_cache_65d.pkl` | Backtest module local 65-day candle cache. |
| `scripts/backtest/backtest_mark_index_cache_65d.pkl` | Backtest module local 65-day mark/index cache. |

## Remote Agent Request Template

Use this format when local-only data is needed:

```text
I need local-only data that is ignored by Git:
- required path/pattern:
- why it is needed:
- exact fields/metrics needed:
- safe output format requested:

Please provide a sanitized summary/export. Do not share secrets or raw private logs.
```

## Safety Rules

- Never request real OKX API keys, Telegram tokens, `.env`, or raw secrets.
- Never run or suggest live trading scripts to compensate for missing data.
- If `SESSION.md`, logs, caches, or journals are missing from GitHub, treat them as local-only, not nonexistent.
- For research tasks, ask local Codex/user to run scripts against local caches and return summarized metrics.
- If docs and code disagree, verify against code first, then flag the stale doc item.
