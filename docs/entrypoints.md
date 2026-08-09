# Supported Entrypoints

Status: **CURRENT**

- Verified: 2026-08-09
- Verified against: `5c9ee576c3625955764da042e81c117b4ef43d3f`
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

## Backup Retention Lifecycle

| Goal | Command | Effects and limits |
|---|---|---|
| Check storage budget | `python -m scripts.strategy_lab.manage_backup_retention status --backup-root <exact-root>` | Read-only size/free-space gate; exit code 2 blocks a canary when outside budget. |
| Build cleanup plan | `python -m scripts.strategy_lab.manage_backup_retention plan --backup-root <exact-root> --archive-root <exact-root> --retain-generation <verified-name> --retain-evidence-sha256 <sha256> --output <plan>` | Hashes and classifies every quiescent backup file without deleting it; the retained generation is bound to separate integrity/restore evidence. |
| Apply exact plan | `python -m scripts.strategy_lab.manage_backup_retention apply --plan <plan> --authority <typed-authority> --expected-plan-digest <sha256>` | Archives, restore-verifies, then removes only plan-bound source files. |
| Verify archive | `python -m scripts.strategy_lab.manage_backup_retention verify --plan <plan>` | Independently decompresses and hashes all archived plan objects. |

The same-volume archive is a reclamation/evidence surface, not a replacement
for the retained full backup or an independent disaster-recovery copy. These
commands never start RCC, discover private roots, or mutate canonical databases.

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

Every canonical farm wrapper requires an already active, digest-valid Paper
Evidence v2 cutover marker. It does not create or migrate that authority during
startup. Missing, rolled-back, corrupt, path-mismatched, or account-mismatched
cutover state blocks the farm before paper materialization.

## Paper Evidence v2 Cutover

| Goal | Command | Effects and limits |
|---|---|---|
| Inspect cutover marker | `python -m scripts.strategy_lab.paper_generation_cutover --private-root <exact-root> status` | Reads only the public-safe cutover identity and state; no process or provider action. |
| Compare shadow projection | `python -m scripts.strategy_lab.paper_generation_cutover --private-root <shadow-root> shadow-parity --legacy-projection <shadow-json> --v2-database <shadow-db>` | Content-normalized parity over a synthetic/private shadow root; grants no authority. |
| Build forward shadow replay | `python -m scripts.strategy_lab.paper_generation_cutover --private-root <exact-source-root> shadow-replay --shadow-root <new-shadow-root> --code-identity <exact-revision> --now-ms <bounded-time>` | Copies only the authenticated paper-signal ledger into a distinct root, runs one bounded public-data v2 generation, and reports content parity. It never copies configuration, delivery state, recipients, or credentials. |
| Activate after operational gate | `python -m scripts.strategy_lab.paper_generation_cutover --private-root <exact-root> activate --code-identity <exact-revision> --confirm-quiescent` | Creates/opens only the canonical v2 database, creates immutable paper-account genesis, integrity-checks it, then publishes the active digest-bound marker. Requires external zero-owner, backup/restore, parity and revision proof. |
| Roll back before runtime | `python -m scripts.strategy_lab.paper_generation_cutover --private-root <exact-root> rollback` | Marks the cutover `rolled_back` without deleting database/evidence. Idempotent; does not restore legacy writer authority. |

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
| Plan terminal validation-task disposition | `python -m scripts.strategy_lab.validation_task_disposition plan --private-root <exact-private-root> --output <private-plan> --missing-grace-seconds <seconds> --json` | Read-only, hash-bound classification of unclaimed queued/deferred orphan, malformed, or no-longer-eligible `export_validation` tasks. The plan must stay outside public Git. |
| Apply exact validation-task disposition | `python -m scripts.strategy_lab.validation_task_disposition apply --private-root <exact-private-root> --plan <private-plan> --expected-plan-digest <sha256> --json` | Requires zero active owners and zero running tasks; applies only exact fence/sequence/payload-bound terminal transitions through the canonical task API. A second exact apply changes zero rows. |

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
