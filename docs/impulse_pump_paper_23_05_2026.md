# Impulse Pump Paper Runner

Manual runner for the 2026-05-23 impulse pump forward test.

## Safety

- Paper only. The runner does not import an exchange trading client and does not send orders.
- Keep `impulse_pump.auto_trade: false`, `impulse_pump.paper: true`, and `AUTO_TRADE` unset or false.
- The runner is not added to `start_all.bat`. Start it manually only after review.

## Run

```powershell
python scripts/ws/ws_impulse_pump.py --check-config
python scripts/ws/ws_impulse_pump.py
```

By default `impulse_pump.enabled` is `false`, so the process exits without opening streams.
Set it to `true` only for the paper-forward session.

## Data Flow

- Public OKX `trades` websocket provides tick-only entry triggers.
- Existing `WSFeed` provides closed `candle1m` context and structural exits.
- Telegram OPEN/CLOSE messages go to `PUMP_CHAT_ID` from `.env`; no chat ID means no-op.

## Logs

The runner writes JSONL records under `logs/impulse_pump/`:

- `impulse_pump_signals.jsonl` for entry context.
- `impulse_pump_outcomes.jsonl` for exits.
- `impulse_pump_training.jsonl` for joined `TrainingRecord.impulse_pump.v1` rows.

Each signal/outcome pair is joined by `signal_id` and includes a `valid` flag plus
`invalid_reasons` for bad or degenerate levels.
