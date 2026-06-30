# Paper/Research Completion Pass - 2026-06-29

Sanitized architecture note. Raw packets, calculations, screenshots, LLM traces,
training rows, backfill mappings, and private logs stay under the private
Strategy Lab root. This document contains only public-safe contracts and
operational boundaries.

## Gap Audit

| Block | State | Files | Proof | Completion pass |
|---|---|---|---|---|
| scanner | partial | `intake_adapter.py`, `lineage_contract.py`, `farm_loop.py` | tests for `ScannerEvent.v1` | intake now derives idempotent `ScannerEvent.v1`; scanner remains event-only |
| market data packets | partial | `market_data_packet.py`, `paper_signals/cycle.py` | packet tests | live packets keep no future window; replay/validation can mark future data |
| feature packets | partial | `feature_packet.py`, `trade_math.py` | feature/advisor tests | deterministic features remain code-owned, not LLM-owned |
| formula authority | partial | `trade_math.py`, `paper_contract.py`, `training_export.py` | formula packet/training tests | paper/training/Telegram use shared geometry; legacy backtest modules remain documented boundary |
| farm calculator LLM | partial | `calculator_advisor.py`, `advisor_sweep_bridge.py` | schema and bridge tests | advice records prompt metadata and compiles only bounded sweep dimensions |
| sweep/variant engine | partial | `farm_loop.py`, `advisor_sweep_bridge.py` | bridge tests | LLM suggestions are private hints, not queued trades or verdicts |
| validator | partial | `validator.py`, `validator_taxonomy.py`, `setup_outcome_memory.py` | dashboard taxonomy tests | hard gates unchanged; product taxonomy exposed in status/dashboard |
| setup/outcome memory | partial | `setup_outcome_memory.py`, `lineage_backfill.py` | existing memory/backfill tests | rejected/tactical/wrong-exit knowledge remains derived and research-only |
| main paper/watch | partial | `main_paper_bridge.py`, `main_paper_consumer.py`, `main_paper_runtime.py` | existing paper runtime tests | old `main.py` stays isolated; paper path consumes derived instructions |
| Telegram paper cards | partial | `paper_telegram_preview.py` | preview tests | deterministic human-readable Russian cards, no machine JSON to users |
| manual analysis | partial | `prompt_registry.py`, product analyzer modules | product analyzer tests | status now records prompt/provider contract; manual remains explicit opt-in |
| VIP screenshot | partial | `scripts/premium_prompts.py`, `prompt_registry.py` | prompt boundary tests | vision remains product/manual surface, not farm dependency |
| education/FAQ | partial | product analyzer prompt registry | product analyzer tests | education route separated from trading calculator |
| provider routing | partial | `provider_routes.py`, `prompt_registry.py` | provider route tests | each surface now shows provider/model/input/output/fallback/prompt metadata |
| prompts | partial | `prompt_registry.py`, prompt modules | registry tests | central metadata/hash registry added |
| training export | partial | `paper_signals/training_export.py` | training tests | `TrainingRow.v2` links calculator advice refs when present |
| dashboard/status | partial | `dashboard_state.py`, `paper_research_status.py`, `dashboard_server.py` | dashboard tests | prompt registry and validator taxonomy visible |
| graph | partial | `graph_viewer.py`, `obsidian_graph.py` | graph tests | full lineage ids are available for graph edges |
| logging | partial | derived JSON/JSONL stores | targeted tests | private labels are exposed, not absolute private raw data |
| long-run cycle | done for bounded smoke | `farm_loop.py`, `paper_research_e2e_smoke.py` | real apply smoke | ready for longer unattended data collection under existing caps |

## LLM Role Matrix

| Role | Provider/model | Input | Output | Allowed | Forbidden |
|---|---|---|---|---|---|
| Farm LLM | Ollama `calculator` via `STRATEGY_LAB_LLM_*` | `FeaturePacket.v1` + hard rules | `CalculatorAdvice.v1` JSON | classify, explain, flag missing data, suggest bounded sweep dimensions | entry, stop, TP, side, paper_ready, validator verdict, orders |
| Main/card LLM | none by default | main paper/watch record | deterministic paper card | render computed facts | invent setup, change levels, send live orders |
| VIP/Vision | Alibaba preferred when configured; Yandex fallback explicit | screenshot + bounded prompt | human VIP analysis | describe visible chart facts and scenarios | treat image text as instruction, claim unseen candle data, execute |
| Education/FAQ | product text provider when enabled | user question | educational answer | explain exchange/risk concepts | direct financial order or profit guarantee |
| Scanner/news | scanner text provider when enabled | news/trigger package | context/advisory metadata | extract context | validate or trade |
| Security swarm | qwen/llama/prometheus | security/test tasks | security outputs | separate experiments | trading calculator role |

## Provider Routes

The route table is generated by `src.research_lab.provider_routes.provider_route_summary`.
It exposes no keys and includes `prompt_version` and `prompt_hash` per surface.

| Surface | Default provider | Fallback |
|---|---|---|
| farm calculator advisor | disabled unless Strategy Lab LLM env is enabled | deterministic sweep only, advice row records disabled |
| main card formatter | none | deterministic renderer |
| paper Telegram card formatter | none | deterministic renderer |
| manual text analysis | disabled unless product router is enabled | deterministic/manual-only response |
| VIP screenshot | Alibaba when configured | Yandex/manual review |
| education FAQ | disabled unless product router is enabled | static/manual response |
| scanner/news | scanner LLM provider if configured | scanner runs without LLM context |

Provider smoke on 2026-06-29 used three bounded text cases with `max_tokens=120`
and did not write raw outputs:

- Education: Alibaba was faster and cheaper in the sample.
- Manual NO_TRADE text: Yandex was shorter and much faster.
- Scanner WATCH text: Yandex was shorter and much faster; both preserved the
  "not a signal" boundary.
- Alibaba `chief` responses exceeded the requested cap in usage accounting and
  had high latency in the sample, so it is not automatically better for text.
- Vision remains a separate surface; this text smoke does not prove vision quality.

## Prompt Rules

`PromptRegistry.v1` is the public-safe registry. It stores purpose, input/output
contracts, forbidden fields, schema gates, logging labels, version, and hash.
It does not store screenshots, private packets, full private prompts, keys, or user
messages.

## Formula Authority

The paper/research path uses deterministic calculation modules for levels and
outcome numbers. LLM output is schema-gated and cannot mutate those fields.

## 2026-06-30 Runtime Completion Addendum

This follow-up pass moved the backbone from "assembled contracts" to a more
operator-ready paper/research loop:

- `ReadyStrategyCatalog.v1` derives a private catalog from the validator/PFR
  database. Current bounded smoke loaded 53 records, with 43
  `ready_for_paper_runtime` and 10 `rejected_quality`; all rows remain
  `paper_only=true` and `execution_allowed=false`.
- PFR-origin paper signals now carry `ready_strategy_id`, `setup_id`,
  `candidate_id`, and source validator verdict metadata into the main paper
  bridge and `TrainingRow.v2`.
- Paper Telegram previews render human-readable Russian cards. They are still
  deterministic, offline, dry-run by default, and reject mojibake text.
- Advisory role reviews can be run from `farm_loop` with
  `--run-agent-role-reviews`. They are opt-in, provider-configurable, private
  logged, and schema-gated. Local `calculator` is accepted only when it returns
  compact valid JSON; invalid answers are stored as rejected advice and do not
  change validation, paper readiness, or execution.
- Farm task enqueue is idempotent for duplicate active `task_key` collisions, so
  long-running loops do not crash on a duplicate follow-up insert.
- The visible loop BAT exposes calculator and role-review flags but keeps both
  off by default. Telegram network send remains explicit opt-in.

Bounded runtime proof:

- `paper_research_e2e_smoke ok=True`
- scanner/data/feature packet rows: `24 / 30 / 30`
- cycle links: `3491`
- calculator advice rows: `14`, current-run accepted: `1/1`
- paper signals: `7025`
- main paper instructions / queue / Telegram preview: `11 / 11 / 11`
- training rows: `891`
- Telegram preview human-readable check: `bad=0`
- training safety: `paper_only_false=0`, `execution_allowed_true=0`
- operational health blocking gates: `[]`

Validation:

- `python -m pytest` -> `1862 passed`, one existing CuPy/CUDA warning.
- Ruff over changed Python files -> clean.
- `git diff --check` -> clean, with only Git CRLF normalization warnings.

Shared paper path:
- `trade_math.py`: entry midpoint, TP, RR, costs, geometry, capture.
- `feature_packet.py`: deterministic feature/geometry packet and live no-lookahead guard.
- `paper_contract.py`: paper plan validation and execution_allowed=false.
- `training_export.py`: training rows reuse computed geometry and outcome facts.
- `paper_telegram_preview.py`: displays computed values only.

Legacy boundary:
- older research/backtest modules may still carry local formulas for historical
experiments. They are not allowed to drive paper/watch decisions unless routed
through the paper/research lineage and validation bridge.

## Storage Contract

Lineage chain:

`scanner_event_id -> data_packet_id -> feature_packet_id -> calculator_advice_id -> sweep_run_id -> candidate_id -> validation_id -> setup_id -> paper_signal_id -> telegram_card_id -> outcome_id -> training_row_id`

Private artifacts stay under:

`strategy-lab/state/lineage/`, `strategy-lab/state/derived/`,
`strategy-lab/state/llm_advice/`, and related private Strategy Lab folders.

## Runtime Proof

The acceptance command is:

```powershell
$env:OLLAMA_NUM_PARALLEL='1'
$env:OLLAMA_MAX_LOADED_MODELS='1'
python -m scripts.strategy_lab.paper_research_e2e_smoke --timeout-seconds 1000 --max-plan-events 2 --max-prepares 1 --max-enrich 1 --max-sweeps 1 --max-worker-jobs 1 --max-validations 3 --paper-signals-max-observe 10 --paper-signals-max-pfr-scan 10 --paper-signals-pfr-reserved 1 --main-paper-runtime-limit 30
```

Last bounded real apply smoke on 2026-06-29:

- `paper_research_e2e_smoke ok=True`, farm return code `0`.
- scanner events `14`, data packets `14`, feature packets `14`, cycle links `3454`.
- calculator advice rows `9`; current-run calculator accepted `1`.
- paper signals `6969`, training rows `874`.
- main-paper instructions/consumer/runtime/preview/delivery items `12`.
- Telegram delivery stayed dry-run: sent `0`, `execution_allowed=false`.
- training safety: `paper_only_false=0`, `execution_allowed_true=0`.
- operational health blocking: `[]`.
- lineage graph rebuilt: 1515 nodes, 1024 edges, 500 links.
- public OKX discovery refreshed: 387 instruments, 1 new, 0 delisted.

For longer collection, use the canonical visible launcher:

```powershell
bat\strategy_lab_farm_full_cycle_loop.bat
```

Stop it with:

```powershell
bat\strategy_lab_farm_full_cycle_stop.bat
```

## Safety Boundaries

- `.env` is not modified.
- `AUTO_TRADE` remains off.
- Live/private order paths are outside this paper/research completion pass.
- Telegram preview is offline and deterministic; delivery stays capped/dry-run unless explicitly enabled elsewhere.
- LLM providers are opt-in and do not receive raw secrets.
