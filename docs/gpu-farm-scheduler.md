# Continuous GPU farm scheduler

## Current hardware contract

- Ollama runs on CPU only (`OLLAMA_LLM_LIBRARY=cpu` and GPU visibility disabled).
- Numerical research uses the NVIDIA GPU when the selected family has a verified kernel and the batch is large enough.
- The automatic GPU threshold is 8,000 bar-variants. Explicit `backend=gpu` still requests GPU directly.
- The two workloads do not compete for the GTX 1050's 3 GB of VRAM.

## Queue order

Lower numbers run first:

1. manual urgent (`0`)
2. scanner GO (`10`)
3. scanner WATCH (`20`)
4. official announcement (`30`)
5. market mover (`40`)
6. role/recheck (`60`)
7. background sweep (`90`)

Work is preempted only at an atomic slot boundary. A running calculation is not killed halfway; after it finishes, the best waiting task owns the next slot. This keeps urgent latency short without corrupting ledgers or partial results.

## Continuous loop

The expensive full cycle keeps its bounded cadence. Between full cycles, the farm continuously executes short priority slots:

- read new manual/scanner intake;
- prepare at most one missing dataset;
- materialize at most one sweep;
- execute at most one worker job;
- classify at most two completed runs.

Validation, paper delivery and LLM roles remain in the full cycle. Every short slot writes `state/farm_priority_checkpoint.json`; after a restart, durable SQLite/JSONL state is authoritative and the unfinished atomic slot is safely re-queued.

## GPU coverage

Nine of the current 27 signal families have vectorized signal kernels. Candle arrays are uploaded once per family batch and reused across parameter variants. All supported kernels have CPU/GPU parity tests. Unsupported or too-small automatic batches fall back to CPU and record that fact honestly.

Run `python scripts/strategy_lab/gpu_inventory.py` for the current public-safe inventory.

## Operator view

The research control center shows:

- GPU utilization, VRAM and temperature;
- queued/running/manual/GO/WATCH counts;
- current farm stage and active slot;
- latest effective signal and simulation backend;
- a paper-only manual urgent form.

The manual form only creates a research intake event. It cannot place orders or call private exchange endpoints.

## Upgrade path

A real VRAM arbiter, model unloading and dynamic batches are deliberately deferred until a GPU/RAM upgrade. On the current machine, CPU LLM plus GPU numerics is simpler, more stable and leaves all VRAM to the workload that benefits from it.
