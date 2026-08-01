# Entrypoint Inventory

Status: **REFERENCE**

- Verified: 2026-08-01
- Verified against: `c20322f887977c5e3c3ec2c242ca560617d056fa`
- Scope: exhaustive classification of non-supported batch surfaces
- Evidence: [public documentation guard](../scripts/ci/check_public_docs.py)
- Residual risks: retained scripts may depend on stale private environments.
- Next gate: a command can move to the supported catalog only with production
  ownership, boundary, tests, and review evidence.

Nothing on this page is a supported operator launcher. The files remain tracked
for maintenance, diagnosis, experiment reproducibility, or history. Do not run
them beside the canonical RCC/farm unless a separate task explicitly promotes
and verifies the exact command.

## Local Maintenance Or Separate Products

- `clear_cache.bat` - local Python-cache maintenance.
- `scanner.bat` - separate legacy scanner wrapper.
- `start.bat` - separate analyzer wrapper, not the canonical RCC entrypoint.
- `start_all.bat` - legacy multi-window launcher.
- `stop.bat` - legacy companion stop, never canonical process control.
- `update_journal.bat` - private journal-derived maintenance.
- `bat\collect_logs.bat` - local archive maintenance with destructive effects.
- `bat\analyze_latest.bat` - execution-adjacent manual analysis.

## Diagnostics And Bounded Experiments

- `bat\news_scanner_audit_today.bat`
- `bat\okx_scanner_day_test_loop.bat`
- `bat\research_machine_demo_visible.bat`
- `bat\roblox_safe_news_capture.bat`
- `bat\run_micro_recorder.bat`
- `bat\run_scout_daily.bat`
- `bat\start_tape.bat`
- `bat\strategy_lab_dashboard.bat`
- `bat\strategy_lab_demo_all.bat`
- `bat\strategy_lab_enqueue_smoke.bat`
- `bat\strategy_lab_enqueue_starter_pack.bat`
- `bat\strategy_lab_export_hard_validation_requests.bat`
- `bat\strategy_lab_export_pack.bat`
- `bat\strategy_lab_gpu_probe.bat`
- `bat\strategy_lab_graph_viewer.bat`
- `bat\strategy_lab_llm_tiny_test.bat`
- `bat\strategy_lab_microscope_scan.bat`
- `bat\strategy_lab_morning_report.bat`
- `bat\strategy_lab_paper_telegram_sender_loop.bat`
- `bat\strategy_lab_prepare_1m_data.bat`
- `bat\strategy_lab_prepare_market_data.bat`
- `bat\strategy_lab_run_hard_validation.bat`
- `bat\strategy_lab_sync_db.bat`
- `bat\strategy_lab_validate_candidates_pipeline.bat`
- `bat\strategy_lab_worker_loop.bat`
- `bat\strategy_lab_worker_once.bat`

These surfaces may read private artifacts, call public providers, incur model
cost, open local views, or mutate research state. Their presence proves only
that the diagnostic or experiment is retained.

## Legacy, Superseded, Or Broken

- `bat\start_scanner.bat`
- `bat\strategy_lab_autopilot_once.bat`
- `bat\strategy_lab_cycle_dry_run.bat`
- `bat\strategy_lab_local_llm_advisor_dry_run.bat`
- `bat\strategy_lab_loop.bat`
- `bat\strategy_lab_obsidian_graph.bat`
- `bat\strategy_lab_ollama_calculator_24x7.bat`
- `bat\strategy_lab_once.bat`
- `bat\strategy_lab_proposals_dry_run.bat`
- `bat\strategy_lab_queue_validated_dry_run.bat`
- `bat\strategy_lab_research_loop_30m_apply.bat`
- `bat\strategy_lab_research_loop_30m_dry_run.bat`
- `bat\strategy_lab_research_loop_overnight_calculator.bat`
- `bat\strategy_lab_research_loop_overnight_llm.bat`
- `bat\strategy_lab_research_loop_overnight_no_llm.bat`
- `bat\strategy_lab_research_session_dry_run.bat`
- `bat\strategy_lab_scanner_bridge_loop.bat`
- `bat\strategy_lab_start.bat`
- `bat\strategy_lab_stop_notes.bat`

Legacy presence is not evidence of support. A future reactivation requires a
new ownership analysis, public/private review, synthetic tests, and an explicit
entrypoint promotion.
