# Agentic Trading LLM Architecture Plan - 2026-06-29

Public-safe research and implementation plan for `trading-bot-v2` on
`feature/calc-farm`. Raw market packets, private logs, LLM traces, screenshots,
training rows, and provider responses stay under the private Strategy Lab root.

This is not a live-trading plan. The target is a self-improving paper/research
system where deterministic code calculates and validates, while LLMs explain,
classify, and suggest bounded follow-up tests.

2026-07-05 implementation update: the first outcome-learning slice now exists.
`OutcomeLearningCase.v1` routes terminal `TrainingRow.v2` outcomes into sanitized
review packs for the existing advisory `outcome_reviewer`; accepted reviews are
written privately to `state/llm_advice/outcome_reviews.jsonl`, and later
`TrainingRow.v2` exports can link them back through `outcome_review_id` and
learning labels. Retest/shadow/promotion automation is still future work and
must reuse existing deterministic gates.

## Scope and Claim Level

This document is a public-safe architecture map and implementation plan. It is
not a claim that an LLM trading edge exists, that any model is production-ready,
or that any external provider has been fully benchmarked. The current claim is
narrow:

- the local paper/research backbone can create auditable paper-only artifacts;
- LLMs can be added as bounded sidecars around that backbone;
- every LLM suggestion must pass deterministic schema, math, validator, and
  paper-only gates before it influences a next test.

Open-source and provider notes below are a first-pass design survey. Phase 1 must
turn them into measured provider/model benchmarks before they become runtime
choices.

## Non-Negotiable Boundaries

- Do not edit `.env`.
- Do not enable `AUTO_TRADE`.
- Do not connect farm/PFR paper outputs to old order-capable `main.py`.
- Do not use private OKX order/account endpoints in farm, validator, paper, or LLM
  paths.
- LLMs must not change entry, stop, TP, RR, risk, validator verdict, or execution
  flags.
- Public repo gets code, tests, safe docs, schemas, and sanitized summaries only.
- Private artifacts stay under `strategy-lab/` in the private research root.

## Current Evidence Snapshot

Local checks on 2026-06-29:

- `git status --short`: clean at the time of this research pass.
- `paper_research_status`: scanner events `17`, data packets `18`, feature packets
  `18`, lineage links `3466`, backfill rows `1156`, feedback rows `0`,
  `execution_allowed=False`.
- `operational_health`: no blocking gates; current warnings are operational hygiene
  and intentional boundaries: journal stale after newer training rows, old main
  remains planned/isolated, manual product analyzer remains review-required.
- `paper_signals`: `6987` rows, all source `farm`; active main-paper queue has `9`
  paper watch rows; Telegram delivery is dry-run.
- `paper_signal_training`: `881` rows, `paper_only_false=0`,
  `execution_allowed_true=0`.

Local model capacity, summarized for public documentation:

| Item | Current fact | Architecture implication |
|---|---|---|
| Host class | Low-end local Windows workstation | Good for orchestration and small local inference only. |
| Memory/GPU class | Constrained local memory and VRAM | Do not run large local multi-agent stacks continuously. |
| Ollama | `0.30.10` | Good enough for bounded local advisor roles. |
| Local models | small Qwen/Llama/Prometheus-family models plus the `calculator` role | Use as cheap classifiers/advisors, not as trading brains. |
| Cloud/API candidates | Alibaba/Yandex/DeepSeek/Kimi/GLM and compatible routes | Treat as optional senior review routes after provider bench. |

## Current Architecture Verdict

The project already has a working deterministic paper/research spine. The missing
piece is not "an LLM trading bot". The missing piece is a measured learning layer
that turns paper outcomes, validator failures, source quality, and product feedback
into structured memory for the next cycle.

| Claim | Verdict | Why |
|---|---|---|
| The farm can create paper-only artifacts | Proven in bounded e2e smoke | scanner/data/feature/paper/Telegram/training rows were generated with `execution_allowed=false`. |
| The local `calculator` can participate safely | Proven narrowly | one bounded farm-loop call accepted `CalculatorAdvice.v1`; forbidden fields are schema-gated. |
| The system is self-learning | Not yet | outcome reviews, source-trust memory, provider/model memory, and family/regime priority feedback are planned but not implemented as one loop. |
| The old main engine is revived | No | old order-capable `main.py` remains isolated by design. |
| Telegram paper cards are a production signal channel | No | preview/dry-run works; network delivery and subscriber policy need a separate operator pass. |
| LLMs can trade or validate | No | LLMs are sidecars only; deterministic code owns math, validator status, and execution flags. |
| Open-source agent frameworks should be imported now | No | current gaps are schemas, memory, reviewer roles, and metrics; a framework import would add complexity before contracts stabilize. |

## Gap Map to the Desired Trading Swarm

| Desired capability | Current state | Build next |
|---|---|---|
| Shared scanner for farm and main paper | scanner events exist, but Scout role is not first-class | `ScoutEvent.v1`/`ScannerEvent.v2` with source, reason, freshness, liquidity, and route policy. |
| News/source context | scanner/product routes exist, source quality not linked to outcomes | `SourceTrustEvent.v1` and source-memory table. |
| LLM selects useful variations | calculator advice can suggest bounded dimensions, but no role registry or provider bench | `AgentRoleRegistry.v1` + provider/model bench over sanitized cases. |
| LLM explains validator rejects | taxonomy is visible, reviewer schema is missing | `ValidatorReview.v1`, private JSONL, dashboard counters. |
| LLM diagnoses paper losses | deterministic diagnosis exists, reviewer schema is missing | `OutcomeReview.v1` linked into `TrainingRow.v2`. |
| Main paper consumes only validated setups | paper bridge exists, old main remains isolated | keep current paper runtime; do not attach old live engine until paper/runtime contract is reviewed. |
| VIP/manual requests become learning data | `signal_event.v1` exists, outcome linkage incomplete | link product events to outcome windows and training rows. |
| Provider/model economics are measured | one text smoke exists, no role-level benchmark | private `provider_bench.v1` with latency/cost/schema violations by role. |
| Operator sees the swarm health | status/graph exist, role health is partial | add role health, spend, schema validity, source trust, review counts. |

## What Already Works

| Block | State | Evidence | Remaining gap |
|---|---|---|---|
| Scanner event layer | Partial/working | `ScannerEvent.v1`, lineage JSONL, farm-loop intake | Need a single explicit Scout Agent contract for live scanner/news/manual/PFR sources. |
| Market data packet | Working foundation | `MarketDataPacket.v1`, no-lookahead split, private packet index | Needs richer source-quality/OI/news refs per event. |
| Feature packet | Working foundation | `FeaturePacket.v1`, deterministic ATR/EMA/RSI/volume/future metrics when allowed | Needs full geometry parity for every strategy family and source context. |
| Formula authority | Partial | `trade_math.py` owns midpoint, risk, RR, costs, net, capture | Legacy backtest/research formulas remain boundary; plan must converge or mark them. |
| Farm calculator LLM | Working bounded | Ollama `calculator` accepted current-run `CalculatorAdvice.v1`; forbidden fields schema-gated | Needs provider bench and model-performance memory. |
| Advisor sweep bridge | Partial | `AdvisorSweepProposal.v1` compiles only allowed dimensions | Needs deterministic compiler from dimensions to actual sweep configs. |
| Validator | Partial/working | Hard validator statuses plus `ValidatorTaxonomy.v1` | Needs Validator Reviewer LLM that explains rejects without changing verdicts. |
| Setup/outcome memory | Partial | `setup_outcome_memory`, paper memory, known-bad hooks | Needs unified Memory Librarian over paper outcomes, source trust, prompt/model performance. |
| Main paper/watch | Working observer | `main_paper_bridge`, consumer, runtime queue/observation; old `main.py` isolated | Needs reviewed paper executor contract only if old main is ever replaced. |
| Telegram paper cards | Working preview | Human-readable `PaperTelegramPreview.v1`, delivery dry-run | Network sends remain explicit opt-in; rate/dedup review required before unattended sends. |
| Manual/VIP/education | Guarded product surface | `signal_event.v1`, provider boundaries, prompt registry | Needs outcome linkage into training export and prompt-quality review. |
| Training export | Working foundation | `TrainingRow.v2`, lineage refs, paper-only checks | Needs Outcome Reviewer LLM diagnosis and prompt/model performance fields for all surfaces. |
| Dashboard/status/graph | Working foundation | health/status/lineage graph counts and provider routing | Needs role-level agent health, cost counters, source trust, reviewer metrics. |
| Long-run loop | Bounded proof exists | `paper_research_e2e_smoke ok=True` from completion pass | Needs 1-2h watched collection and post-run report as operating ritual. |

## Target Agent Roles

| Role | Owner | Input | Output | LLM allowed | LLM forbidden |
|---|---|---|---|---|---|
| Scout Agent | scanner/news/manual adapters | public movers, listings, source events | `ScannerEvent.v1` | classify why an event is worth inspection | BUY/SELL, paper-ready, validator verdict |
| News / Source Trust Agent | new source memory layer | public source refs and news text | source-trust event, context refs | summarize, dedupe, score source reliability | create trades or override price gates |
| Data Curator | deterministic code | `ScannerEvent.v1` | `MarketDataPacket.v1` | none required | lookahead in live mode |
| Feature Engineer | deterministic code | market packet | `FeaturePacket.v1` | none | indicator/geometry mutation |
| Strategist LLM | bounded API/local route | feature summary | hypothesis labels and sweep dimensions | suggest bounded dimensions and explain context | numeric trade levels, verdicts, execution |
| Calculator / Sweep Engine | deterministic code | feature + allowed dimensions | candidates/sweeps | none required | accept raw LLM numbers |
| Validator | deterministic hard judge | candidate results | hard status and taxonomy class | none required | weakened gates |
| Validator Reviewer LLM | API/local review route | validator pack | diagnosis and next-test proposal | explain reject/underpowered/wrong-exit | change validator verdict |
| Paper Runtime | deterministic observer | validated paper instruction | outcome facts | none | live orders |
| Outcome Reviewer LLM | API/local review route | closed paper outcome + facts | diagnosis, learning labels | classify late-entry/wrong-exit/no-event | retroactively change outcome math |
| Memory Librarian | deterministic store + optional LLM summaries | outcomes, reviews, source trust, model performance | memory indices and priority hints | summarize clusters | hide known-bad or promote edge |
| Risk Governor | deterministic safety gate | every artifact/action | allow/deny paper-only action | none | bypass or delegate to LLM |
| Telegram/Product Agent | deterministic renderer + optional text/VIP LLM | paper/product records | human card, education, VIP response | wording/education/vision description | alter computed setup or verdict |

## LLM Role Matrix

| Surface | Preferred model class | Current route | Recommended use |
|---|---|---|---|
| Farm calculator advisor | Local Ollama `calculator` | `STRATEGY_LAB_LLM_*`, disabled by default | Always bounded JSON over `FeaturePacket.v1`; cheap continuous role. |
| Strategist hypothesis | Local first, API on uncertainty | Missing dedicated role | Add as advisory layer that outputs allowed dimensions only. |
| Validator reviewer | API senior model, local fallback | Missing dedicated role | Use when validator rejects/underpowers; write private diagnosis, never verdict. |
| Outcome reviewer | API senior model for samples, local classifier for bulk | Missing dedicated role | Label paper outcomes for memory/training. |
| Daily farm report | API text model or subscription agent | Partial dashboard only | Summarize metrics, blockers, cost, next tests; no trading authority. |
| Manual text analysis | Product shared router opt-in | Guarded via `llm_formatter`/`llm_client` | Human explanation only; writes `signal_event.v1`. |
| VIP screenshot | Alibaba Qwen-VL preferred, Yandex fallback | `premium_vision_provider.v1` | Product/manual vision only, not farm dependency. |
| Education/FAQ | Cheap text model | Product router opt-in | Educational explanations with financial-advice boundary. |
| Scanner/news | Alibaba/Yandex text router | `src.utils.llm_client` | Context extraction and source classification only. |
| Security/local swarm | qwen/llama/prometheus | Separate local models | Keep outside trading calculator role. |

## Provider Routing Plan

| Surface | Provider | Input | Output | Fallback | Logging |
|---|---|---|---|---|---|
| farm calculator advisor | Ollama `calculator` | `FeaturePacket.v1` summary | `CalculatorAdvice.v1` | disabled row + deterministic sweep only | `state/llm_advice/calculator_advice.jsonl` |
| strategist hypothesis | Ollama `calculator`; API if uncertain | feature packet + taxonomy | `StrategyHypothesis.v1` | no proposal | new private `state/llm_advice/strategy_hypotheses.jsonl` |
| validator reviewer | DeepSeek/Kimi/GLM/Alibaba/Yandex after bench | validator pack | `ValidatorReview.v1` | deterministic taxonomy only | new private `state/llm_advice/validator_reviews.jsonl` |
| outcome reviewer | local classifier for bulk, API sample review | outcome + card + features | `OutcomeReview.v1` | deterministic lane.review diagnosis | new private `state/llm_advice/outcome_reviews.jsonl` |
| daily report | API or web subscription manually | sanitized status metrics | narrative report | dashboard only | private reports, sanitized summary optional |
| paper Telegram card | none | main paper consumer row | human card | deterministic renderer | `state/derived/paper_telegram_preview.jsonl` |
| manual text | Alibaba/Yandex shared router opt-in | chart snapshot/report | product answer | deterministic summary | `logs/signals/signal_events.jsonl` |
| VIP screenshot | Alibaba Qwen-VL | screenshot | VIP analysis | Yandex/manual review | `logs/users/<chat>/premium_log.jsonl` |
| education | Alibaba/Yandex text | user question | education answer | static/manual response | product budget/audit log |
| scanner/news | Alibaba/Yandex text | trigger package | context labels | scanner without LLM | scanner budget/journal |

## API vs Subscription vs Local Models

The project should treat provider access as three different tools, not one
interchangeable "LLM" bucket.

| Access mode | Best use | Why | Boundary |
|---|---|---|---|
| Local Ollama models | Always-on classifier/advisor/reviewer for small structured packets | Near-zero marginal cost and private by default | Not strong enough to be the main trading brain or VIP vision reviewer. |
| API models | Automated senior review with JSON/schema gates | Reproducible, logged, rate-limited, and measurable | Only sanitized packets; no secrets, no raw private logs, no authority over math/verdict/execution. |
| Web/subscription agents | Human-in-the-loop architecture and research review | Useful for broad reasoning over sanitized reports | Not a production runtime; do not automate web UI or treat output as pipeline evidence. |

Candidate model families to benchmark in Phase 1:

- local: `calculator` and small Qwen/Llama/Prometheus-family models;
- text/reasoning API: Alibaba, Yandex, DeepSeek, Kimi, GLM/Z.AI;
- vision/product API: Alibaba Qwen-VL, GLM-V/Z.AI, Yandex vision if configured;
- subscription/manual: Kimi/GLM/DeepSeek web agents as external reviewers only.

## Open-Source Review

This is a source-discovery pass, not a final due-diligence audit. These projects
were checked as architecture references. Before adopting any pattern, run a
second pass that reads the relevant modules, licenses, runtime assumptions, and
paper/live claims in detail.

| Project | Link | What it does | Reuse | Do not copy |
|---|---|---|---|---|
| TradingAgents | https://github.com/TauricResearch/TradingAgents | Multi-agent LLM financial trading framework; GitHub metadata: Apache-2.0, high adoption. | Debate/reviewer roles, graph-like analyst/trader/risk decomposition. | Do not let LLM agents issue final trades in this repo. |
| FinRobot | https://github.com/AI4Finance-Foundation/FinRobot | Open-source financial analysis agent platform. | Financial report/research-agent patterns, reusable prompt/role decomposition. | Not a validator; do not treat reports as backtest proof. |
| AI Hedge Fund | https://github.com/virattt/ai-hedge-fund | Multi-agent “AI hedge fund team” demo. | Portfolio of named expert agents as UI/review pattern. | Avoid direct decision authority and simulated-confidence theatrics. |
| FinGPT | https://github.com/AI4Finance-Foundation/FinGPT | Financial LLM models/datasets ecosystem. | Possible future fine-tuning/data-format inspiration. | Do not use raw model output for trading verdicts. |
| Qlib | https://github.com/microsoft/qlib | AI-oriented quant platform for research to production. | Dataset/experiment discipline, ML pipeline ideas, benchmark rigor. | Heavy migration is not justified now; current farm already exists. |
| OpenBB | https://github.com/OpenBB-finance/OpenBB | Financial data platform for analysts, quants, AI agents. | Source/data adapter ideas and analyst tooling. | Avoid adding a large dependency unless a specific data source is needed. |
| Freqtrade | https://github.com/freqtrade/freqtrade | Mature crypto bot framework. | Risk/exchange/backtest/live separation patterns. | Do not port live execution; current project forbids money path. |
| vectorbt | https://github.com/polakowo/vectorbt | Vectorized backtesting/sweep engine. | Useful benchmark for fast sweeps and parameter grids. | Do not replace farm until a narrow performance bottleneck is proven. |
| backtesting.py | https://github.com/kernc/backtesting.py | Lightweight Python backtest framework. | Simple reproducible strategy tests and examples. | Not enough for multi-source lineage/memory by itself. |
| CrewAI | https://github.com/crewAIInc/crewAI | General role-playing multi-agent orchestration. | Role/task/process vocabulary. | Extra framework overhead is unnecessary unless internal orchestration becomes unmanageable. |
| LangGraph | https://docs.langchain.com/oss/python/langgraph/multi-agent | Graph-style multi-agent workflows. | Good conceptual fit for explicit state transitions and bounded nodes. | Do not add until role contracts stabilize. |
| AutoGen | https://microsoft.github.io/autogen/ | Multi-agent framework from Microsoft. | Human-in-the-loop review and group-chat patterns. | Avoid autonomous agent chats in runtime paper loop. |

Conclusion: use open-source projects as design references, not as the core runtime.
The repo already has a safer deterministic farm/paper backbone. The missing layer is
role contracts, memory, review schemas, provider bench, and operator reporting.

## Open-Source Findings That Matter

The strongest external signal is not "copy this framework". The signal is that
serious trading-agent projects converge on explicit roles, state, provider routing,
and memory. They do not remove the need for deterministic validation.

| Finding | External signal | Project implication |
|---|---|---|
| Role decomposition is standard | TradingAgents separates analyst, researcher, trader, risk, and portfolio roles. | Keep role separation, but make every role write typed artifacts instead of free-form chat. |
| Local and OpenAI-compatible endpoints are normal | TradingAgents documents Ollama/OpenAI-compatible use and multi-provider routing. | Our Ollama `calculator` route is directionally correct, but must remain bounded and measured. |
| Research warnings are explicit | TradingAgents labels the framework as research, not financial advice. | Public docs must keep the same discipline: no profit/edge claims from paper/replay. |
| Quant platforms emphasize experiment discipline | Qlib focuses on AI-oriented quant research and production research workflow. | Adopt experiment lineage and repeatability, not a wholesale platform migration. |
| Mature bots separate backtest/paper/live | Freqtrade is useful as a boundary reference. | Keep old live `main.py` isolated until paper runtime has a reviewed execution contract. |
| Agent frameworks are generic | CrewAI/LangGraph/AutoGen provide orchestration concepts. | Do not add a framework before `AgentRoleRegistry.v1` and reviewer schemas are stable. |

## Runtime Milestone: Agent Roles Wired Into The Paper Backbone

Implemented after the architecture pass:

- `AgentRoleRegistry.v1` defines six bounded roles: farm calculator advisor,
  outcome reviewer, validator reviewer, source trust reviewer, VIP vision
  reviewer, and education Q&A.
- `OutcomeReview.v1`, `ValidatorReview.v1`, and `SourceTrustEvent.v1` are written
  only to private Strategy Lab JSONL files.
- Alibaba-compatible text review is measured through a private provider bench.
- Alibaba VIP vision is measured through a separate private smoke runner.
- Dashboard/status now expose role registry, provider bench, role-review cycle,
  and VIP vision smoke summaries without leaking provider responses.

Measured local run on 2026-06-29:

| Check | Result |
|---|---|
| Provider bench | Alibaba configured; 3/3 role sample cases accepted after canonical schema normalization. |
| Real role review cycle | 7/7 private artifacts accepted: 3 paper outcomes, 2 validator-memory rows, 2 scanner events. |
| VIP vision smoke | Alibaba vision configured; one private chart image returned a bounded result. |
| E2E paper/research smoke | `ok=True`; scanner/data/feature/calculator/farm/validator/paper/main-watch/Telegram-preview/training path completed. |
| Safety | `paper_only=True`; `execution_allowed=False`; no `AUTO_TRADE`; no live/private order execution. |

Important nuance: the first Alibaba pass produced useful-looking but non-canonical
JSON keys. The gate rejected those rows. The fix was not to relax the gate; the
fix was to add a small canonicalization layer for common provider synonyms while
keeping forbidden trade/execution fields rejected.

Current claim level:

- the agent roles can review real private artifacts and write bounded memory;
- the roles are advisory only;
- deterministic code still owns math, validator status, paper readiness, and all
  Telegram rendering for paper cards;
- this is not a live trading engine and not proof of a profitable edge.

The 2026 agentic-trading survey signal is also uncomfortable but useful: the
field is growing fast, yet reproducibility, execution semantics, transaction-cost
models, universe handling, and comparable evaluation protocols are still weak in
published work. This means `trading-bot-v2` should not chase "LLM trader" demos.
Its edge as a project is the boring part: traceable data packets, cost-aware math,
paper-only outcomes, validator classes, and repeatable lineage.

Additional open-source notes from this pass:

| Project / source | Useful signal | Risk for this repo |
|---|---|---|
| AgenticTrading | It exposes LLM trading agents, interactive backtests, live-market paper trading, decision logs, and reasoning traces. | It is closer to a playground; do not let BUY/SELL/HOLD decisions bypass our validator. |
| AgenticTrading orchestration | Mentions DAG planning, specialized agent pools, memory, MCP, and A2A style communication. | Useful later for architecture comparison; too early to import before our contracts stabilize. |
| FinLLM/FinAgent examples | LLM signals from news can enrich trading/RL environments. | News sentiment must become source context and trust memory, not a standalone signal. |
| Survey evidence map | Recent survey work reports protocol incomparability and weak reproducibility across many LLM-trading studies. | Our public claim should be stronger on evidence quality, not stronger on profit claims. |
| Awesome LLM quantitative trading papers | The space now contains many 2025-2026 multi-agent/quant-agent papers. | Good backlog source, but each idea needs our cost/no-lookahead/OOS/paper gates. |

## Provider Findings That Matter

Provider choice should be per role, not global.

| Provider family | Observed capability/pricing signal | Project implication |
|---|---|---|
| DeepSeek | Official pricing is low for text and supports JSON/tool-style API features. | Good candidate for validator/outcome reviewer and daily reports after private bench. |
| Kimi | Current docs expose multimodal/code families and token-billed chat completion. | Good human-in-the-loop and code/research review candidate; needs role-level latency/cost bench. |
| GLM/Z.AI | Official pricing lists text, vision, web-search, and agent products. | Candidate for senior reviewer, vision fallback, and search-assisted review, but only with caps. |
| Alibaba Model Studio | Offers Qwen, third-party text models, and image/video understanding models. | Strong candidate for VIP vision and product text; not needed for farm candle math. |
| RunPod/GPU burst | GPU pricing makes burst research feasible but not free. | Use only for batch experiments/fine-tuning after training schema stabilizes. |

The project should not choose "Alibaba vs Yandex vs DeepSeek" by preference. It
should run `provider_bench.v1` by role and route by measured schema validity,
latency, cost, and human usefulness.

## Local Model Verdict

Local models currently available are enough to build a small trading swarm, but
not enough to make a strong autonomous trading brain.

| Local model class | Good role | Bad role |
|---|---|---|
| `qwen2.5:0.5b`, `qwen2.5-coder:0.5b-instruct` | cheap schema-validation probes, tiny classifiers, failure-mode tests | market reasoning, validator review, chart/VIP analysis |
| `llama3.2:1b` | simple natural-language explanation and safety/product wording tests | high-confidence strategy review |
| `qwen2.5:1.5b`, `calculator`, `prometheus-qwen15b-lowctx` | bounded `FeaturePacket` reviewer, sweep-dimension suggestions, local security/trading-role simulation | final verdict, paper-ready, execution, complex multi-day reasoning |
| cloud aliases in Ollama | candidate manual/senior review routes if configured and measurable | hidden runtime dependency without provider bench |

Therefore the right architecture is a two-tier swarm:

1. local always-on cheap roles for structured, bounded review;
2. API/senior roles only for sampled hard cases, daily summaries, validator
   failures, and VIP/product quality.

## Where We Were Wrong or Too Optimistic

| Assumption | Correction |
|---|---|
| "If LLMs are everywhere, the system becomes smarter." | False. Without role contracts and memory, more LLM calls just add cost and inconsistency. |
| "Vision is needed for farm analysis." | Mostly false. The farm should feed structured OHLCV/features/geometry to LLMs. Vision belongs to VIP/product chart explanation. |
| "Provider choice can be decided by one smoke." | False. Alibaba/Yandex/DeepSeek/Kimi/GLM must be compared per role with schema validity, latency, cost, and usefulness. |
| "We can train our own model from current logs now." | Premature. Training data must first carry stable reviewer labels, source trust, prompt/model performance, and outcome math. |
| "Main engine revival is just wiring." | False. Old `main.py` is order-capable and must stay isolated until a reviewed paper executor contract exists. |

## Autonomous Pass Final Opinion

The project is closer to a useful self-learning research machine than to a
finished trading bot. That is not a failure; it is the correct order.

What is real now:

- deterministic paper/research backbone;
- paper-only lineage;
- local calculator advisor through schema gates;
- paper Telegram previews;
- training rows;
- operational health and graph/status surfaces.

What is not real yet:

- LLM-driven self-learning memory;
- validator reviewer;
- outcome reviewer;
- source-trust feedback loop;
- provider/model performance routing;
- old main integration;
- proven trading edge.

The next build should be deliberately narrow:

1. implement `AgentRoleRegistry.v1`;
2. implement private `ProviderBench.v1`;
3. implement `OutcomeReview.v1`;
4. attach outcome-review refs to `TrainingRow.v2`;
5. expose role/provider metrics in status/dashboard.

Only after that should the project add `ValidatorReview.v1`, source-trust memory,
and product/VIP outcome linkage. This sequence creates a real learning loop
instead of a bigger prompt machine.

## Adoption Decision Records

| Decision | Ruling | Reason |
|---|---|---|
| Import a trading-agent framework as runtime | Reject for now | It would move authority toward LLM conversations before the project has role-level contracts, memory, and outcome review. |
| Use local LLMs for every farm step | Reject | The local models are small; use them for bounded cheap classification, not senior reasoning or vision. |
| Use API models for every paper event | Reject | Cost is manageable, but latency and privacy are unnecessary for bulk events. Use API only for sampled/high-value reviewer roles. |
| Use LLM vision for farm candles | Reject | The farm already has structured OHLCV/features; vision belongs to product/VIP chart explanation, not calculation. |
| Teach one model directly from all logs | Defer | First normalize `TrainingRow.v2`, reviewer labels, prompt/model metrics, and source trust. Fine-tuning before schema stability would train noise. |
| Let feedback buttons change priorities | Allow only indirectly | Human feedback can create review rows; deterministic memory logic decides whether it affects future priority. |

## Phase 1 Detailed Acceptance

Phase 1 must be completed before adding more LLM autonomy.

| Work item | Artifact | Acceptance |
|---|---|---|
| Agent role registry | `AgentRoleRegistry.v1` public-safe module/table | every LLM surface declares provider, input schema, output schema, forbidden fields, caps, private log path, and fallback. |
| Provider bench | private `provider_bench.v1` JSONL + sanitized summary | at least 5 role cases: feature advice, validator reject, paper loss, source trust/news, VIP screenshot/text-only fallback. |
| Local model bench | private Ollama benchmark rows | `calculator`, small Qwen/Llama/Prometheus-family models measured for schema validity, latency, and refusal/overreach. |
| Forbidden-field tests | targeted tests | model outputs containing entry/SL/TP/RR/paper_ready/execution fields are rejected for every reviewer role. |
| Cost counters | status/dashboard fields | per-provider call count, latency, estimated cost, schema-valid rate visible without raw prompts. |
| Public/private split | doc + scans | raw responses only private; public doc contains no prompts with private data, no keys, no absolute user-specific paths. |

## Financial Model

Assumptions for rough planning:

- Feature review: 1.5k input tokens + 0.4k output tokens.
- Outcome review: 1.0k input + 0.5k output.
- Daily report: 8k input + 1.5k output.
- Bulk classifier: 0.8k input + 0.15k output.
- USD/RUB conversion is intentionally omitted in public planning; use local billing
  logs for exact RUB after each provider smoke.

Pricing and availability below are point-in-time planning estimates observed on
2026-06-29. Verify provider pages and local billing before any paid run.

DeepSeek official pricing on 2026-06-29: `deepseek-v4-flash` cache-miss input
`$0.14 / 1M`, output `$0.28 / 1M`; `deepseek-v4-pro` input `$0.435 / 1M`,
output `$0.87 / 1M`.

RunPod public GPU examples on 2026-06-29: L4 `$0.39/hr`, RTX 4090 `$0.69/hr`,
A100 PCIe `$1.39/hr`, H100 PCIe `$2.89/hr`.

| Workload | Local-only | Hybrid API sample | API-heavy | Cloud GPU burst |
|---|---:|---:|---:|---:|
| 100 FeaturePacket reviews | near-zero cash, slower/weaker | DeepSeek flash about `$0.03`; pro about `$0.10`; Alibaba/Yandex measured locally around single-digit to tens of RUB per 100 depending prompt | still cheap for text, but needs caps | not needed |
| 100 paper outcome reviews | near-zero cash | flash about `$0.03`; pro about `$0.09` | acceptable if capped | not needed |
| 1 daily report | local weak | flash about `$0.0015`; pro about `$0.0048` | negligible token cost; quality matters more | not needed |
| 1000 bulk classifier calls | local preferred | flash about `$0.15`; pro about `$0.48` | cheap, but rate/cap needed | not needed |
| VIP vision requests | local not viable on GTX 1050 | Alibaba Qwen-VL preferred if configured; exact price must be taken from provider billing | cap per user/day | not useful unless hosting own vision |
| 10h research batch | CPU/local slow | API often cheaper for text review | cost depends volume | RunPod L4 `$3.90`, RTX 4090 `$6.90`, A100 `$13.90`, H100 `$28.90` plus storage |

Recommendation:

- Always-on: local `calculator` for bounded classification and sweep-dimension hints.
- Per-cycle capped: local/cheap API reviewer only for rejected/underpowered/high-value cases.
- Daily: API senior report over sanitized status and outcome deltas.
- Manual: Kimi/GLM/DeepSeek web/subscription agents for human-in-the-loop review of
  sanitized packs, never production runtime.
- Vision: Alibaba Qwen-VL for VIP screenshots, not farm candles.
- GPU burst: only for batch backtests, embeddings, or fine-tuning experiments after
  training schema stabilizes.

## Math Audit Findings

Current formula authority:

- `trade_math.py`: midpoint, stop distance, risk percent, RR, gross/net percent,
  capture, fees/slippage assumptions.
- `feature_packet.py`: deterministic feature packet and future metrics only when
  replay/validation mode allows it.
- `paper_signals/lane.py`: forward observation and review diagnosis.
- `paper_signals/training_export.py`: `TrainingRow.v2` includes outcome facts and
  cost fields.
- `paper_telegram_preview.py`: displays computed values; no LLM card mutation.

Required next convergence:

| Formula | Current state | Plan |
|---|---|---|
| entry mid | in `trade_math.py` and lane/family builders | ensure all Telegram/training/PFR displays call shared geometry helper |
| stop distance / stop_pct | shared helper plus legacy local math | add formula parity tests across PFR, paper, Telegram, training |
| TP/RR | shared for paper path | document legacy backtest boundary or migrate family-by-family |
| fees/slippage/net | shared in `trade_math.py`; lane review also computes | converge lane review to explicit `CostAssumptions` source |
| MFE/MAE/capture | lane review + feature future metrics | enforce no future metrics in live feature packets |
| timeframe horizon | packet split + paper runtime | add status visibility per TF window and defer reasons |

## Validator Plan

Hard validator remains deterministic. Add two non-authoritative LLM layers:

1. `ValidatorReview.v1`
   - Input: candidate id, hard status, failed checks, key metrics, data quality.
   - Output: explanation, root-cause class, next bounded test proposal.
   - Forbidden: changing `hard_status`, setting paper-ready, writing candidates.

2. `OutcomeReview.v1`
   - Input: paper outcome, feature packet refs, card text hash/ref, source context.
   - Output: `late_entry`, `wrong_direction`, `stop_too_tight`,
     `target_too_far`, `gave_back`, `news_priced_in`, `source_noise`,
     `no_follow_through`, `wrong_tf`, `family/regime mismatch`.
   - Forbidden: changing outcome math.

Validator class taxonomy should stay:

- `confirmed_bad`
- `wrong_exit`
- `no_event`
- `underpowered`
- `tactical_candidate`
- `regime_only`
- `data_issue`
- `cost_sensitive`
- `forward_watch_candidate`
- `manual_review_required`

## Self-Learning Memory Loop

Target chain:

`paper outcome -> deterministic review -> OutcomeReview.v1 -> MemoryLibrarian.v1 -> priority hints -> next scanner/farm/validator cycle`

Memory tables/artifacts to add:

| Memory | Contents | Runtime use |
|---|---|---|
| setup memory | positive, confirmed bad, wrong exit, no event, tactical | rank/defer known-bad and revisit candidates |
| source trust memory | source, symbol, event type, follow-through, false-positive rate | news/scanner priority and Telegram channel filtering |
| prompt/model memory | provider/model/prompt hash, schema validity, latency, cost, human rating | route future reviewer calls |
| family/regime memory | setup family, TF, regime, pair, outcome distribution | farm sweep priority |
| product feedback memory | admin feedback: wrong entry, bad card, useful signal, needs context | review queue priority only |

Human feedback must not directly change validator verdict or paper readiness.

## Telegram / Product Plan

Current safe state:

- paper cards are deterministic and human-readable;
- paper delivery is dry-run by default;
- subscriber sender exists but is explicit opt-in;
- manual/VIP product events write `signal_event.v1`;
- VIP screenshot route is separate from farm;
- old product analyzer remains review-required and not farm runtime.

Next work:

1. Add paper card feedback buttons that write `HumanFeedback.v1`.
2. Link product `signal_event.v1` rows to outcome/training rows when symbol/time
   windows match.
3. Add source/news channel policy: public summaries may be sent, full paper setup stays
   subscriber-only.
4. Add prompt-injection guard for screenshot text: visible text in image is data, not
   instruction.
5. Add provider latency/cost/schema metrics to admin status.

## Phased Implementation Plan

### Phase 1 - Provider Bench + Role Registry

- Add `AgentRoleRegistry.v1` public-safe table.
- Add private A/B runner over sanitized cases:
  FeaturePacket, failed outcome, wrong_exit, no_event, daily report, VIP screenshot,
  news/source trust.
- Record `provider/model/prompt_hash/schema_valid/latency/cost/violations`.
- Acceptance: raw responses private, sanitized matrix public, no secrets, no `.env`
  edits, LLM forbidden fields rejected.

### Phase 2 - Outcome Reviewer

- Add `OutcomeReview.v1` schema and private JSONL.
- Run deterministic + local/API reviewer over closed paper outcomes.
- Export review refs into `TrainingRow.v2`.
- Acceptance: reviewer cannot alter outcome; tests for forbidden fields and
  paper-only flags.

### Phase 3 - Validator Reviewer

- Add `ValidatorReview.v1`.
- Attach explanations to rejected/underpowered/data_issue candidates.
- Feed only proposed next-test dimensions into sweep proposal queue.
- Acceptance: hard validator status remains unchanged; dashboard shows classes.

### Phase 4 - Source Trust / News Memory

- Add `SourceTrustEvent.v1` and source memory summary.
- Link scanner/news events to later no_event/follow-through outcomes.
- Acceptance: source trust affects priority only, not trade verdict.

### Phase 5 - VIP/Product Training Logging

- Link `signal_event.v1` product rows to outcome/training rows.
- Add feedback buttons and `HumanFeedback.v1` review queue.
- Acceptance: product surfaces remain non-authoritative; no network-send default
  changes.

### Phase 6 - Dashboard / Graph Expansion

- Add role health, provider spend, schema validity, source trust, reviewer outcomes,
  feedback counts.
- Graph path:
  `asset -> event -> packet -> feature -> advice -> sweep -> validation -> paper -> card -> outcome -> review -> training`.
- Acceptance: graph builds without private raw payloads in public repo.

### Phase 7 - Long-Run Paper Learning Loop

- Run visible 1-2h paper/research loop.
- Stop via stop-file.
- Produce post-run report:
  events, packets, features, advisor calls, sweeps, validations, paper signals,
  Telegram previews, outcomes, reviewer rows, deferred reasons, costs.
- Acceptance: operational health no blocking, training rows paper-only, no live/order
  path touched.

## Commands

Status:

```powershell
python -m scripts.strategy_lab.operational_health --json
python -m scripts.strategy_lab.paper_research_status
python -m scripts.strategy_lab.status
```

Bounded e2e:

```powershell
python -m scripts.strategy_lab.paper_research_e2e_smoke --timeout-seconds 1000 --max-plan-events 2 --max-prepares 1 --max-enrich 1 --max-sweeps 1 --max-worker-jobs 1 --max-validations 3 --paper-signals-max-observe 10 --paper-signals-max-pfr-scan 10 --paper-signals-pfr-reserved 1 --main-paper-runtime-limit 30 --no-discovery-refresh
```

Visible collection:

```powershell
bat\strategy_lab_farm_full_cycle_loop.bat
bat\strategy_lab_farm_full_cycle_stop.bat
```

Provider A/B, explicit and private-root only:

```powershell
python scripts\llm_provider_ab.py --providers yandex,alibaba --max-tokens 500 --apply --private-root "$env:TRADING_BOT_RESEARCH_ROOT"
```

For public runbooks prefer setting `TRADING_BOT_RESEARCH_ROOT` locally and using
that variable instead of writing a user-specific absolute path into commands.

Checks before commit:

```powershell
python -m pytest tests/test_paper_research_e2e_smoke.py tests/test_research_lab_packets_and_advisor.py tests/test_paper_signal_training_export.py tests/test_paper_telegram_preview.py
python -m ruff check docs
git diff --check
git diff -- .env
```

## Final Recommendation

Build the agentic system as a deterministic graph with LLM sidecars, not as an
autonomous LLM trader.

The immediate next implementation should be Phase 1 plus Phase 2:

1. role registry and provider bench;
2. `OutcomeReview.v1`;
3. outcome-review refs in `TrainingRow.v2`;
4. dashboard/provider-cost visibility.

This is the shortest path to a genuinely self-learning paper/research loop:
paper outcomes become structured diagnoses, memory changes future priority, and every
LLM remains bounded by schemas, caps, and deterministic validators.

## Source Links

- TradingAgents: https://github.com/TauricResearch/TradingAgents
- FinRobot: https://github.com/AI4Finance-Foundation/FinRobot
- AI Hedge Fund: https://github.com/virattt/ai-hedge-fund
- FinGPT: https://github.com/AI4Finance-Foundation/FinGPT
- Qlib: https://github.com/microsoft/qlib
- OpenBB: https://github.com/OpenBB-finance/OpenBB
- Freqtrade: https://github.com/freqtrade/freqtrade
- vectorbt: https://github.com/polakowo/vectorbt
- backtesting.py: https://github.com/kernc/backtesting.py
- CrewAI: https://github.com/crewAIInc/crewAI
- LangGraph multi-agent docs: https://docs.langchain.com/oss/python/langgraph/multi-agent
- AutoGen docs: https://microsoft.github.io/autogen/
- DeepSeek pricing: https://api-docs.deepseek.com/quick_start/pricing
- Kimi pricing docs: https://platform.kimi.ai/docs/pricing/chat
- Z.AI / GLM pricing: https://docs.z.ai/guides/overview/pricing
- Alibaba Model Studio models: https://www.alibabacloud.com/help/en/model-studio/models
- RunPod pricing: https://www.runpod.io/pricing
- Modal pricing: https://modal.com/pricing
