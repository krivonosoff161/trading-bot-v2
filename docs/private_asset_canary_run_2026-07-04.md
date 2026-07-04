# Private Asset Canary Run - 2026-07-04

## Purpose

This document records a controlled private-asset leakage test over the current
paper/research trading-bot chain.

The test checks whether a synthetic private trading-signal marker can move from
an internal paper signal into wider output surfaces such as runtime queues,
Telegram preview, delivery audit, or training export.

This is a defensive canary test. It does not use a real strategy, real subscriber
signal, provider key, Telegram credential, `.env` value, external LLM, or live
order path.

## Boundary

Tested repository:

```text
C:\Users\krivo\trading-bot-v2
```

Private run artifacts:

```text
.internal/agentic_canary/runs/canary_20260704_095123/
```

The private run directory is ignored by Git through `.internal/`. Public docs
must not include the raw canary marker. This document records only the sanitized
result.

## Safety Rules Used

- No `.env` reads.
- No external LLM/API calls.
- No Telegram sends.
- No live orders.
- No exchange private endpoints.
- No real trading signal or real strategy value.
- Synthetic canary only.
- All generated artifacts stayed under `.internal/agentic_canary/runs/...`.

## Tested Agentic Path

The canary was passed through the real local paper/research modules, using an
isolated private root:

```text
canary_generator_agent
  -> paper_signals
  -> main_paper_bridge
  -> main_paper_consumer
  -> main_paper_runtime_queue
  -> paper_telegram_preview
  -> paper_telegram_delivery dry-run
  -> paper_signal_training
  -> leak scanner
```

The path intentionally uses the same core paper modules that the trading bot
uses for derived paper artifacts:

- `src.research_lab.paper_signals.store`
- `src.research_lab.main_paper_bridge`
- `src.research_lab.main_paper_consumer`
- `src.research_lab.main_paper_runtime_adapter`
- `src.research_lab.paper_telegram_preview`
- `src.research_lab.paper_telegram_sender` in dry-run mode
- `src.research_lab.paper_signals.training_export`

## Result

Verdict:

```text
NO_FORBIDDEN_LEAK
```

Summary:

| Sink | Marker present | Policy | Result |
|---|---:|---|---|
| canary input | yes | allowed private | ok |
| paper signals | yes | allowed private | ok |
| main paper instructions | yes | allowed private | ok |
| main paper consumed | no | must be redacted | ok |
| adaptive policy | no | must be redacted | ok |
| runtime queue | no | must be redacted | ok |
| Telegram preview | no | must be redacted | ok |
| Telegram delivery dry-run | no | must be redacted | ok |
| paper signal training export | no | must be redacted | ok |

Safety counters:

```text
external_llm_calls = 0
telegram_sends = 0
env_reads = 0
live_orders = 0
```

## What This Proves

The current paper/research path did not move the synthetic private marker into
forbidden output sinks during this run.

In practical terms:

- Telegram preview did not expose the marker.
- Telegram delivery dry-run did not expose or send the marker.
- Runtime queue did not expose the marker.
- Training export did not expose the marker.
- The consumer layer stopped carrying the raw marker forward.

This is a good defensive result for the current paper chain.

## What This Does Not Prove

This is not a proof that the trading bot is completely secure.

It does not prove:

- real strategies can never leak;
- screenshots, charts, or rendered images are clean;
- future modules will preserve the same boundary;
- external providers are safe;
- all possible split-leak paths are covered;
- malicious prompt chains cannot create a new sink.

It proves only that this specific synthetic marker did not cross the tested
forbidden sinks in this isolated run.

## Important Finding

The canary marker was present in `main_paper_instructions`. That sink is still
classified as private/internal in this test, so this was not counted as a public
leak.

However, the reason is important:

```text
src/research_lab/main_paper_bridge.py
```

The bridge currently preserves the full `validator_context` in the instruction
view. That is broader than necessary for downstream consumers.

Current behavior:

```text
paper signal validator_context
  -> main_paper_bridge
  -> main_paper_instructions
```

Observed protection:

```text
main_paper_consumer and downstream layers do not carry the raw marker forward
```

Recommended hardening:

```text
main_paper_bridge should whitelist safe validator_context fields instead of
copying the full context.
```

Expected whitelist:

- `ready_strategy_id`
- `setup_id`
- `candidate_id`
- `source_validation_verdict`
- non-sensitive status/provenance ids

Fields that should not travel by default:

- raw private markers;
- raw strategy reasoning;
- subscriber-only payloads;
- provider responses;
- private Telegram/channel details;
- credentials or credential-like values;
- arbitrary agent notes.

## Engineering Follow-Up

Recommended next task:

```text
feat: whitelist validator_context in main_paper_bridge
```

Done when:

- `main_paper_bridge` exports only approved context fields;
- tests prove unknown/private context keys are dropped;
- canary rerun shows the marker stops before `main_paper_instructions`;
- Telegram preview, delivery dry-run, runtime queue, and training export remain
  clean;
- no `.env`, Telegram, external provider, or live order path is touched.

## Evidence Artifacts

Private artifacts are available locally under:

```text
.internal/agentic_canary/runs/canary_20260704_095123/
```

Key files:

- `report.md` - human-readable private run report;
- `path_trace.json` - full path trace;
- `leak_scan.json` - sink-by-sink marker scan;
- `private_root/state/derived/*` - isolated generated artifacts.

These files are intentionally not committed.
