# Supported Entrypoints

Status: **CURRENT**

- Verified: 2026-08-01
- Verified against: `c20322f887977c5e3c3ec2c242ca560617d056fa`
- Scope: supported paper/research entrypoints, effects, and incompatible owners
- Evidence: [public documentation guard](../scripts/ci/check_public_docs.py) and
  [Research Control Center tests](../tests/test_research_control_center.py)
- Residual risks: existence of an entrypoint does not prove workstation,
  dependency, credential, provider, or private-data readiness.
- Next gate: require a fresh operational preflight and external owner-authority
  manifest before every state-changing command.

This page lists only supported operator or canonical component entrypoints.
Diagnostics, experiments, maintenance commands, and retained legacy launchers
are classified separately in the [Entrypoint Inventory](entrypoint-inventory.md).
No entrypoint grants live trading authority.

## Canonical Supervisor

| Goal | Command | Effects and limits |
|---|---|---|
| Supervise a bounded profile | `bat\research_control_center.bat` | Canonical UI supervisor. All contours start disabled. A profile launch requires separate owner authority. |

The supported paper-only profile consists of independently owned contours:
`ollama`, `public_news`, `scanner`, `paper_cards`, and `telegram_bot`. The RCC
must prove one canonical process authority, fencing, listener ownership, fresh
progress, and `execution_allowed=false`. It never auto-restarts after a hard
fail.

## Paper And Farm

| Goal | Command | Effects and limits |
|---|---|---|
| Low-load paper research | `bat\paper_product_headless_loop.bat` | Canonical farm, validation, and paper lifecycle; no delivery. |
| Low-load paper cards | `bat\paper_product_headless_send_loop.bat` | Same path with explicit paper-card delivery. |
| Visible paper operation | `bat\paper_product_control_room.bat` | Canonical farm plus local read-only operator views. |
| Visible paper operation with cards | `bat\paper_product_control_room_send.bat` | Same path with explicit paper-card delivery. |
| Bounded acceptance collection | `bat\paper_acceptance_headless_loop.bat` | Paper-only acceptance evidence; no dashboard or delivery. |
| Canonical farm core | `bat\strategy_lab_farm_full_cycle_loop.bat` | Farm/worker/validation/paper lifecycle; one owner only. |
| Visible farm control room | `bat\strategy_lab_control_room.bat` | Canonical farm plus local status views; never beside another farm owner. |

Direct paper/farm launchers are supported contracts, but the canonical RCC is
the preferred multi-contour supervisor. Never run two launchers that compete
for the same owner or delivery resource.

## Canonical Components

| Component | Command | Effects and limits |
|---|---|---|
| Public-news contour | `bat\public_news_loop.bat` | Public/news network and optional delivery surface; not trade authority. |
| Scanner contour | `bat\news_scanner_loop.bat` | Public intake and bounded outcome resolution; not farm or execution authority. |
| Scanner status | `bat\news_scanner_status.bat` | Read-only status. |
| Telegram analyzer contour | `bat\start_telegram_bot.bat` | Human-facing analyzer only; no farm or execution authority. |

These commands are component boundaries used by the RCC. Starting one directly
still requires action-specific authority and duplicate-owner checks.

## Stop, Acknowledge, And Status

| Goal | Command | Effects and limits |
|---|---|---|
| Request canonical farm stop | `bat\strategy_lab_farm_full_cycle_stop.bat` | Writes the documented farm stop marker; the loop exits at a safe boundary. |
| Request generic Strategy Lab stop | `bat\strategy_lab_graceful_stop.bat` | Writes the documented generic stop intent. |
| Fast status | `bat\strategy_lab_status.bat` | Read-only health/status. |
| Periodic status | `bat\strategy_lab_status_monitor.bat` | Read-only periodic view. |
| Clear generic stop | `bat\strategy_lab_clear_stop.bat` | Supported only after quiescence and provenance proof. |
| Clear exact RCC marker generation | `python -m scripts.strategy_lab.clear_rcc_stop_intents` | Hash-bound dry-run/apply for the exact three-marker generation after quiescence. |

Never substitute manual file deletion, raw SQL, arbitrary process termination,
or a legacy stop launcher for the documented mechanisms.

## Effect Labels

- `local`: may read or write the private research root; public Git is not a
  runtime store.
- `network`: public market/news access or localhost UI traffic.
- `credential`: a configured integration may load credentials through its
  normal boundary; agents and public evidence must not read or print them.
- `send`: external delivery requires separate owner authority.
- `paper_only`: simulated research state only; never orders.

## Rules

1. Public documentation, memory, model output, and command availability never
   grant process authority.
2. Use one canonical farm owner and at most one paper-card delivery owner.
3. Keep `AUTO_TRADE` disabled and execution authority absent.
4. Do not use private exchange/account endpoints.
5. Follow the [Farm Ownership Map](farm_ownership_map.md) and
   [Farm Runbook](farm_runbook.md) before any runtime action.
6. Recovered PID, heartbeat, or port evidence is display-only unless the
   current RCC proves exact owned-process identity.
