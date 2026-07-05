# Storage boundaries

Portfolio-level documentation and storage authority is defined in the
[Documentation Contract](https://github.com/krivonosoff161/krivonosoff161/blob/main/docs/documentation-contract.md).
This page narrows that contract for the public trading workbench.

This repository is the trading application and research backbone. Keep Git for
source code, public-safe documentation, configuration templates, tests, and
small deterministic fixtures.

Do not commit raw runtime or farm output:

- `.env` files, provider keys, Telegram tokens, exchange credentials
- live or paper trade ledgers with private routing details
- SQLite databases, worker state, heartbeat snapshots, runtime logs
- raw model prompts or responses
- generated farm experiments, market-data caches, and large backtest outputs
- agentic security canary run directories

Use local storage for raw artifacts:

```text
C:\Users\krivo\research-artifacts\trading-farm\
C:\Users\krivo\research-artifacts\security-harness\
```

Research that should become durable can be summarized in `docs/` or moved to the
private `trading-bot-research` repository after sanitization. The raw data stays
outside this repository.
