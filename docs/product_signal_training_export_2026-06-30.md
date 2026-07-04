# Product signal training export - 2026-06-30

## Purpose

Manual Telegram analysis and VIP chart analysis are product surfaces, not the
farm/PFR paper runtime. They still produce useful evidence: what the user asked,
which provider/model answered, what decision was returned, and which artifacts
were attached. This pass makes that evidence available to the private training
loop without giving product surfaces authority over trading execution.

## Boundary

- Public repository contains only code, tests, and this safe summary.
- Product training rows are written to the private Strategy Lab root.
- `.env`, `AUTO_TRADE`, private OKX endpoints, and order execution are not used.
- Telegram network delivery is not triggered by the exporter.
- Chat IDs and message IDs are hashed before training export.
- The old order-capable `main.py` remains isolated from farm/PFR paper outputs.

## Flow

```text
Telegram manual/VIP surface
  -> logs/signals/signal_events.jsonl
  -> ProductSignalTrainingRow.v1
  -> private state/derived/product_signal_training.jsonl
  -> private state/derived/product_signal_training.json
  -> operational_health / paper_research_status / dashboard state
```

The public-local `signal_events.jsonl` source is already sanitized by
`src.utils.signal_event_log`. The new exporter mirrors only `signal_event.v1`
rows and adds:

- stable `training_row_id`;
- `product_event_id`;
- product source/mode/decision/status;
- symbol, timeframe, side, entry/stop/take-profit facts if present;
- provider/model/prompt version;
- hashed chat/message identifiers;
- artifact references;
- `paper_only=true`;
- `execution_allowed=false`.

## Runtime proof

The bounded smoke checks observed the product training path alongside the current
paper/research cycle:

- product events loaded: 3;
- product training rows exported: 3;
- sources: manual Telegram analysis and VIP screenshot analysis;
- providers observed: Alibaba, Yandex, and no-provider template path;
- `paper_only_false=0`;
- `execution_allowed_true=0`;
- operational health blocking gates: 0.

The farm loop now runs the product training exporter as a side effect of the
paper/research cycle, after paper signal training export. Status and dashboard
collectors expose the row counts and safety counters.

## Verification

Commands used for this pass:

```powershell
python -m pytest tests/test_product_signal_training_export.py tests/test_operational_health.py tests/test_farm_loop_stage_visibility.py tests/test_research_lab_dashboard.py -q
python -m scripts.strategy_lab.product_signal_training_export --json
python -m scripts.strategy_lab.farm_loop --once --apply --run-paper-signals --no-discovery-refresh --max-plan-events 0 --max-prepares 0 --max-enrich 0 --max-sweeps 0 --max-worker-jobs 0 --max-validations 0 --max-paper-cards 0 --paper-signals-max-new 0 --paper-signals-max-pfr-scan 0 --paper-signals-pfr-reserved 0 --paper-signals-max-observe 0 --true-forward-max-candidates 0 --main-paper-runtime-limit 0 --quiet
python -m scripts.strategy_lab.operational_health --fail-on-blocked
python -m scripts.strategy_lab.paper_research_status --json
python -m pytest -q
```

Result:

- targeted tests: 61 passed;
- full suite: 1866 passed, with the existing CuPy/CUDA warning;
- ruff on changed Python files: clean;
- `git diff --check`: clean except expected CRLF normalization warnings.

## What this does not claim

This does not make manual/VIP analysis a trading authority. It only records the
product-side evidence in the private learning loop. The farm/PFR validator and
paper runtime remain the authority for setup validation and paper watch state.
