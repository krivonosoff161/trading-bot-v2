# Entrypoint Catalog

Status: **ACTIVE**. Reviewed: 2026-07-18.

This catalog is the authority for Windows batch entrypoints. It separates the
supported paper/product paths from diagnostic, legacy, and network/cost surfaces.
No batch file grants live trading authority. The old execution-adjacent paths
remain isolated and must not be used as farm launchers.

## Supported Paths

| Goal | Command | Effects |
|---|---|---|
| Unified Russian control center | `bat\research_control_center.bat` | One local UI for independently supervised paper/research contours; all switches start off. |
| Low-load paper research | `bat\paper_product_headless_loop.bat` | Canonical farm, validation, paper lifecycle, bounded reviews; no Telegram delivery. |
| Low-load paper cards | `bat\paper_product_headless_send_loop.bat` | Same path with explicit subscriber-card delivery and chart fetches. |
| Visible paper operation | `bat\paper_product_control_room.bat` | Canonical farm plus local dashboard/graph/status windows. |
| Visible paper operation with cards | `bat\paper_product_control_room_send.bat` | Same path with explicit paper-card delivery. |
| Bounded acceptance run | `bat\paper_acceptance_headless_loop.bat` | Creates/uses a private acceptance baseline, then runs headless paper collection. |
| Stop canonical farm | `bat\strategy_lab_farm_full_cycle_stop.bat` | Writes the private stop-file; the farm exits after a safe iteration. |
| Fast health view | `bat\strategy_lab_status.bat` | Read-only current status. |

Read [Farm Ownership Map](farm_ownership_map.md) and
[Farm Runbook](farm_runbook.md) before using a supported path.

The v2 paper-evidence coordinator is not wired to any supported launcher yet.
Its public API is off by default and exists for temporary-root/synthetic
verification. Do not infer activation or private migration from the presence of
`paper_evidence_store.py`; rollout requires a separate explicit operator action
under the [Paper Evidence Generations](paper-evidence-generations.md) contract.

Normal scanner/farm apply modes do not authorize destructive storage maintenance.
Legacy cache/log/spec/task retention is report-only; the synthetic v2 quarantine proof
has no supported launcher and cannot activate a repository or current private root. See
[Storage boundaries](storage_boundaries.md). Coordinated log rotation remains deferred.

`python scripts/ci/check_public_docs.py` verifies that every tracked `.bat`
file is catalogued and that public documentation links resolve locally.

## Side-Effect Legend

| Label | Meaning |
|---|---|
| `local` | Local read/write work only; may use the private research root. |
| `network` | Public market/news fetch, or a local dashboard socket. |
| `credential` | Reads `.env` only for an explicitly configured integration. |
| `cost` | Can call a paid cloud LLM when the operator enables it. |
| `send` | Sends Telegram or public-channel content when explicitly invoked. |
| `legacy` | Historical or superseded; never combine with the canonical farm. |
| `broken` | Retained for audit until removed or repaired; do not run. |

## Root Entrypoints

| File | Status | Side effects | Notes |
|---|---|---|---|
| `clear_cache.bat` | maintenance | local destructive cache cleanup | Removes Python cache folders only. |
| `scanner.bat` | reference scanner loop | network, credential, send | Requires `.env`; scanner delivery is configured separately. |
| `start.bat` | separate Telegram analyzer | credential, network | Starts `scripts/telegram_bot.py`; not a farm launcher. |
| `start_all.bat` | legacy | network, credential, legacy | Frozen multi-window scanner/tape stack. |
| `stop.bat` | legacy companion | local process termination, legacy | Stops windows created by `start_all.bat`; not the farm stop path. |
| `update_journal.bat` | local maintenance | local, private data | Rebuilds local journal/log-derived outputs; never public evidence. |

## Farm, Paper, And Operator Commands

| File | Status | Side effects | Notes |
|---|---|---|---|
| `paper_acceptance_headless_loop.bat` | supported | local, network | Bounded acceptance collector; no dashboard or Telegram send. |
| `paper_product_control_room.bat` | supported | local, network | Preferred visible paper surface. |
| `paper_product_control_room_send.bat` | supported | local, network, credential, send | Explicit paper-card delivery wrapper. |
| `paper_product_headless_loop.bat` | supported | local, network | Preferred low-load paper surface. |
| `paper_product_headless_send_loop.bat` | supported | local, network, credential, send | Explicit low-load card delivery wrapper. |
| `strategy_lab_farm_full_cycle_loop.bat` | supported core | local, network | Farm/worker/validation/paper wrapper; delivery is opt-in only. |
| `strategy_lab_farm_full_cycle_stop.bat` | supported utility | local | Requests canonical loop stop. |
| `strategy_lab_control_room.bat` | supported visible utility | local, network | Opens canonical farm and local visual/status windows. |
| `strategy_lab_status.bat` | supported utility | local | Read-only status. |
| `strategy_lab_status_monitor.bat` | supported utility | local | Read-only periodic status window. |
| `strategy_lab_clear_stop.bat` | supported utility | local | Clears a private stop intent before an intentional restart. |
| `strategy_lab_graceful_stop.bat` | supported utility | local | Requests a generic Strategy Lab graceful stop. |
| `strategy_lab_paper_telegram_sender_loop.bat` | diagnostic fallback | credential, send | Do not run beside the canonical farm; it can duplicate delivery ownership. |

## Scanner, News, And Telegram Surfaces

| File | Status | Side effects | Notes |
|---|---|---|---|
| `news_scanner_loop.bat` | active intake support | network | Scanner plus bounded outcome resolution. |
| `news_scanner_status.bat` | active utility | local | Scanner health/status only. |
| `news_scanner_audit_today.bat` | diagnostic | local | Daily scanner audit. |
| `public_news_loop.bat` | separate public-channel publisher | network, credential, cost, send | Collects and publishes channel news; not a trading signal path. |
| `start_telegram_bot.bat` | separate analyzer surface | credential, network | Starts product Telegram bot only. |
| `run_scout_daily.bat` | reference daily collector | network, local | Keyless forward logger; not farm control. |
| `okx_scanner_day_test_loop.bat` | diagnostic | network | Bounded scanner experiment. |
| `roblox_safe_news_capture.bat` | local diagnostic | network | Special low-load scanner capture, not a standard operator path. |

## Diagnostics And Manual Research Tools

| File | Status | Side effects | Notes |
|---|---|---|---|
| `research_machine_demo_visible.bat` | diagnostic | local, network | One bounded visible machine pass. |
| `strategy_lab_gpu_probe.bat` | diagnostic | local | GPU/CPU parity probe. |
| `strategy_lab_dashboard.bat` | diagnostic | local network | Localhost read-only dashboard. |
| `strategy_lab_graph_viewer.bat` | diagnostic | local | Builds/opens local graph output. |
| `strategy_lab_microscope_scan.bat` | diagnostic | local | Read-only 1m event scan. |
| `strategy_lab_morning_report.bat` | diagnostic | local | Report for a prior bounded run. |
| `strategy_lab_prepare_market_data.bat` | diagnostic | local; optional network | Defaults to dry run. |
| `strategy_lab_prepare_1m_data.bat` | diagnostic | local; optional network | Defaults to dry run. |
| `strategy_lab_export_hard_validation_requests.bat` | diagnostic | local | Exports validation requests; dry run by default. |
| `strategy_lab_run_hard_validation.bat` | diagnostic | local | Manual validation path; dry run by default. |
| `strategy_lab_validate_candidates_pipeline.bat` | diagnostic | local | Manual full validation pipeline; dry run by default. |
| `strategy_lab_export_pack.bat` | diagnostic | local | Exports a private review pack; no API call. |
| `strategy_lab_enqueue_smoke.bat` | diagnostic | local | Queues public-safe smoke configuration. |
| `strategy_lab_enqueue_starter_pack.bat` | diagnostic | local | Queues bounded starter configurations. |
| `strategy_lab_demo_all.bat` | diagnostic | local | Bounded smoke/demo path. |
| `strategy_lab_worker_once.bat` | diagnostic executor | local | Drains one queued research job. |
| `strategy_lab_worker_loop.bat` | off by default | local | Standalone worker, not lifecycle brain. |
| `strategy_lab_sync_db.bat` | repair | local | Repairs/imports private state DB results. |
| `run_micro_recorder.bat` | diagnostic | local, network | Local market-microstructure capture. |
| `start_tape.bat` | diagnostic | local, network | Historical tape capture. |
| `analyze_latest.bat` | execution-adjacent manual tool | credential possible | Never use as a farm launch path. |
| `collect_logs.bat` | local maintenance | local destructive archive action | Copies then deletes local logs after success. |

## Legacy Or Superseded Paths

| File | Status | Side effects | Replacement |
|---|---|---|---|
| `start_scanner.bat` | legacy | network, credential | Use scanner/farm ownership maps. |
| `strategy_lab_start.bat` | legacy lab wrapper | local, optional network | Use supported paper paths. |
| `strategy_lab_loop.bat` | legacy fixed-spec loop | local | Use farm loop or bounded diagnostics. |
| `strategy_lab_once.bat` | legacy fixed smoke | local | Use `strategy_lab_demo_all.bat`. |
| `strategy_lab_autopilot_once.bat` | manual legacy planner | local | Canonical follow-up is owned by `farm_loop`. |
| `strategy_lab_cycle_dry_run.bat` | legacy advisory cycle | local | Use farm dry run/status. |
| `strategy_lab_scanner_bridge_loop.bat` | legacy bridge | local, network | Scanner intake is owned by `farm_loop`. |
| `strategy_lab_research_session_dry_run.bat` | legacy advisory session | local | Use bounded diagnostics. |
| `strategy_lab_research_loop_30m_dry_run.bat` | legacy advisory loop | local | Use farm dry run/status. |
| `strategy_lab_research_loop_30m_apply.bat` | legacy advisory loop | local | Use supported paper paths. |
| `strategy_lab_research_loop_overnight_no_llm.bat` | legacy advisory loop | local | Use headless paper loop. |
| `strategy_lab_research_loop_overnight_calculator.bat` | legacy advisory loop | local | Use canonical calculator sidecar. |
| `strategy_lab_research_loop_overnight_llm.bat` | legacy, cost | credential, cost | Do not use for standard operation. |
| `strategy_lab_ollama_calculator_24x7.bat` | legacy calculator loop | local | Use canonical farm calculator flag. |
| `strategy_lab_obsidian_graph.bat` | legacy visualization | local | Use diagnostic graph viewer only when needed. |
| `strategy_lab_proposals_dry_run.bat` | manual advisory | local | Use farm-owned follow-ups. |
| `strategy_lab_queue_validated_dry_run.bat` | manual advisory | local | Use farm-owned validation queue. |
| `strategy_lab_stop_notes.bat` | legacy note | local | No supported runtime role. |
| `strategy_lab_local_llm_advisor_dry_run.bat` | broken | local | References removed `local_llm_advisor.py`; do not run. |
| `strategy_lab_llm_tiny_test.bat` | special diagnostic | credential, cost | Explicit paid LLM test; never a default path. |

## Rules

1. Never run a legacy or diagnostic loop beside `strategy_lab_farm_full_cycle_loop.bat`.
2. Use at most one paper-card delivery owner at a time.
3. Treat `credential`, `cost`, and `send` as explicit operator opt-ins.
4. Do not infer live-trading authority from any entrypoint. The supported paths
   remain paper-only and keep `execution_allowed=false`.
5. The unified control center supervises existing owners; it does not merge the
   public-news publisher, scanner queue, canonical farm, Telegram bot, or visual
   surfaces into one runtime process.
6. Recovered heartbeat/port processes are display-only. Executable matching is
   an identity check, not authority to stop an external process.
7. Every farm apply mode and standalone worker loop must acquire its durable
   owner lease; file age and `--once` never bypass the owner group.
