# Pump Architecture Verdict - 2026-05-19

## Verdict

Current pump architecture should not receive more threshold-level tuning.

Reason: the B.5 backtest already showed the expected ceiling (`WR=37.9%`) and live results matched it (`WR=37.5%` in the referenced live slice). That is an architecture-level failure, not a parameter search problem.

Current local labels are not better:

- `logs/pump/pump_labels.jsonl`, all live EXIT rows currently on disk: `n=560`, `WR=34.6%`, `net=-74.14%`
- `2026-05-16..2026-05-18` current-risk slice: `n=128`, `WR=31.2%`, `net=-27.79%`

## What Remains Valid

These mitigations remain valid as risk containment, not as proof that the current architecture has edge:

- `session_ban_sl_no_tp=2`: valid as a generic damage limiter.
- `pair_risk_overrides` for `APR/RIVER/LAB`: valid as quarantine from the clean current-risk evidence and tape availability gap.

The previous Sim results are useful only for risk containment sizing. They do not justify continued tuning of the same entry architecture.

## What Stops

Do not run more `Sim10/Sim11` style threshold sweeps on the current pump engine unless there is a new entry hypothesis.

Do not promote pair overrides as "the fix". They reduce damage from known bad pockets; they do not create structural edge.

## Next Step

Proceed only with Phase C: `ws_smart_pump.py` as a new architecture.

Minimum Phase C acceptance criteria:

- entry hypothesis is different from the current pump trigger stack;
- backtest and live labels are evaluated separately;
- no deployment claim from samples with `n < 30`;
- quarantine controls (`session_ban=2`, `APR/RIVER/LAB` risk overrides) stay enabled while Phase C accumulates labels.
