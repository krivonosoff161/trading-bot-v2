# Product Analyzer Revival Audit (2026-06-27)

Status: **guarded product surface; not the farm/PFR runtime**.

This audit covers the old chart/Telegram/main product path after the Strategy Lab
paper loop was restored. It answers one narrow question: what can be reused for product
paper delivery, and what must remain isolated until a separate review.

## Verified Components

| Component | Current role | Verified boundary |
|---|---|---|
| `scripts.analyze_chart` | Manual chart/report generator | Writes local report, snapshot, chart, and client summary. Telegram send is off by default. |
| `src.utils.llm_formatter` | Legacy chart text formatter | UTF-8 prompt is intact and still carries risk/non-claim language. Default provider path is Yandex-only. `generate_client_text` can opt in to the shared `llm_client` router with `PRODUCT_ANALYZER_LLM_ROUTER=llm_client`; premium vision and educational Q&A remain Yandex-only. |
| `scripts.run_latest_analysis` | Interactive wrapper over `analyze_chart` | Execution-adjacent, but the old `scripts.auto_execute` hook now requires both `AUTO_TRADE` and the explicit manual wrapper opt-in `RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE=1`. |
| `scripts.telegram_bot` | Legacy Telegram analyzer bot | Execution-adjacent, but the old `scripts.auto_execute` hook now requires both `TELEGRAM_BOT_ALLOW_AUTO_EXECUTE=1` and `AUTO_TRADE`; not the farm/PFR paper launcher. |
| `scripts.auto_execute` | Old demo/live order path | Guarded by `AUTO_TRADE`, but can set leverage and place OKX orders when enabled. |
| `src.utils.telegram` | Telegram send helper | Reads env at call time, does not print token/chat values, skips when not configured. |
| `scripts.strategy_lab.paper_telegram_sender` | Paper alert delivery surface | Reads validated `paper_telegram_preview` artifacts, dry-runs by default, and sends only with explicit `--send` to `PAPER_CHAT_ID`. |

Machine check:

```bash
python -m scripts.strategy_lab.operational_health --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
```

Expected product-surface facts:

- `telegram_chart_formatter_prompt_integrity = true`
- `telegram_chart_formatter_mojibake_detected = false`
- `telegram_chart_formatter_provider = yandex_only`
- `telegram_chart_formatter_status.schema = llm_formatter_provider.v1`
- `telegram_chart_formatter_status.provider = yandex`
- `telegram_chart_formatter_status.configured = true/false` depending on local
  `YANDEX_API_KEY` and `YANDEX_FOLDER_ID`
- `telegram_chart_formatter_status.model_label` is sanitized and must not expose the
  Yandex folder id or key values
- `telegram_chart_formatter_uses_llm_provider_env = false`
- `scanner_formatter_provider_mismatch = true` when `LLM_PROVIDER=alibaba` but the
  legacy chart formatter still uses the Yandex-only path
- `analyze_chart_send_default = false`
- `run_latest_analysis_imports_auto_execute = true`
- `run_latest_analysis_auto_trade_guarded = true`
- `run_latest_analysis_requires_auto_execute_opt_in = true`
- `telegram_analyzer_imports_auto_execute = true`
- `telegram_analyzer_auto_trade_guarded = true`
- `telegram_analyzer_requires_auto_execute_opt_in = true`
- `product_analyzer_prompt_integrity = pass`
- `manual_product_analyzer_boundary = warn`

Optional shared-router check for the text-only chart card:

```powershell
$env:PRODUCT_ANALYZER_LLM_ROUTER = "llm_client"
$env:LLM_PROVIDER = "alibaba"
python -m scripts.strategy_lab.operational_health --private-root "$env:USERPROFILE\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "$env:USERPROFILE\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
```

Expected opt-in facts:

- `telegram_chart_formatter_provider = shared_llm_client_opt_in`
- `telegram_chart_formatter_status.provider = alibaba` when `LLM_PROVIDER=alibaba`
- `telegram_chart_formatter_uses_llm_provider_env = true`
- `scanner_formatter_provider_mismatch = false`
- `telegram_chart_formatter_status.shared_router_entrypoints = ["generate_client_text"]`
- `telegram_chart_formatter_status.yandex_only_entrypoints = ["generate_premium_analysis", "generate_edu_text"]`

This opt-in is deliberately narrow: it only changes `generate_client_text`. It does not
revive Telegram sending, does not touch `start.bat`, does not call `auto_execute`, and
does not migrate the premium screenshot analyzer.

## Why This Is Still Not The Unified Executor

The current canonical Strategy Lab path is paper/research only:

```text
farm_loop --run-paper-signals
  -> paper_signals / PFR seeding
  -> main_paper_bridge
  -> main_paper_consumer
  -> main_paper_runtime_adapter
  -> main_paper_runtime observer
  -> paper_telegram_preview
  -> paper_signal_training_export
```

The old `main.py` and `scripts.auto_execute` are not dead code, but they are money-path
code. They import the authenticated OKX client and can reach leverage/order methods.
Therefore they must not consume farm/PFR paper instructions directly.

## Product Revival Order

1. Keep the farm/PFR/paper observer as the source of truth.
2. Use `paper_telegram_preview` as the first alert surface: offline, validated, no send.
3. Review the actual Telegram card text and chart payloads from derived paper artifacts.
4. If live paper alerts are needed, use the opt-in sender over preview artifacts only:
   `python -m scripts.strategy_lab.paper_telegram_sender --send`. It uses `PAPER_CHAT_ID`
   and does not fall back to scanner/default chats.
5. If the old product chart text must use the shared provider router, enable only the
   explicit text-card opt-in: `PRODUCT_ANALYZER_LLM_ROUTER=llm_client`. Keep reviewing
   the generated card before any Telegram send. Premium vision and educational Q&A still
   require a separate provider/prompt migration.
6. Keep `run_latest_analysis`, `scripts.telegram_bot`, and `auto_execute` out of the farm
   launch path until a new executor contract exists and has its own paper-first
   validation. The current manual wrapper requires
   `RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE=1` before it can import the old auto-execute
   module. The legacy Telegram analyzer similarly requires
   `TELEGRAM_BOT_ALLOW_AUTO_EXECUTE=1` before it can import the old auto-execute module.
   Both flags are for explicit legacy execution tests only, not for farm/PFR launches.

## Non-Claims

This audit does not claim that Telegram/product analysis is ready for unattended trade
delivery. It claims that the legacy formatter prompt is readable, the provider split is
visible, the analyzer does not send by default, and the execution-adjacent path is
explicitly isolated from the restored paper/farm loop by an additional manual opt-in
guard.
