# Trading Bot V2

`trading-bot-v2` is a **paper/research workbench** for studying market-data
pipelines, bounded strategy sweeps, independent validation, paper observation,
and auditable outcomes around public OKX market data.

It is not a live trading bot, a signal service, financial advice, or a claim of
profitability. No supported public entrypoint grants order authority.

## What This Repository Does

```text
public market/news intake
  -> bounded calculation farm
  -> deterministic candidate classification
  -> independent honest-backtest validation
  -> paper-only observation and outcome records
  -> evidence-backed role environment candidates
  -> optional, explicitly enabled paper-card delivery
```

The calculation farm owns research scheduling and calculation. The separate
[honest-backtest](https://github.com/krivonosoff161/honest-backtest) repository
owns independent validation. Old execution-capable code is retained only as
reference and is not part of the supported paper path.

## Start Here

1. Read [Docs Home](docs/README.md) for document authority and navigation.
2. Read [Architecture](ARCHITECTURE.md) for the active boundaries.
3. Read [Entrypoint Catalog](docs/entrypoints.md) before launching a Windows
   command.
4. Read [Storage Boundaries](docs/storage_boundaries.md) before collecting or
   exporting any artifact.
5. Read [Adaptive Research Center Contract](docs/adaptive-research-center-contract.md)
   before changing farm, validator, Trader Supervisor, or System Analyst logic.

## Public-Safe Setup

The repository is Windows-first. A Python 3.11 virtual environment is the
supported baseline:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
python -m pytest
python scripts/ci/check_supply_chain_policy.py
python scripts/ci/check_tracked_artifacts.py
```

CI uses `requirements-ci.txt` plus `requirements-ci.sha256` as the public exact
direct-dependency reconstruction input. `requirements.txt` remains the local
developer convenience file and can carry broader comments or optional guidance.

Configuration is optional for tests. Integrations that fetch public market data,
call a provider, or send a paper card need a local `.env` created from
`.env.example`; never commit that file or any generated output.

## Supported Paper Paths

| Need | Windows entrypoint | Effects |
|---|---|---|
| Low-load paper research | `bat\paper_product_headless_loop.bat` | Farm, validation, and paper lifecycle; no Telegram delivery. |
| Low-load paper cards | `bat\paper_product_headless_send_loop.bat` | Same paper path with explicit delivery opt-in. |
| Visible operator room | `bat\paper_product_control_room.bat` | Canonical farm with local dashboard/graph/status windows. |
| Bounded acceptance run | `bat\paper_acceptance_headless_loop.bat` | Private baseline and bounded paper evidence collection. |
| Read-only health | `bat\strategy_lab_status.bat` | Status only. |

Use only one delivery owner at a time. The full command catalog, including
legacy and diagnostic tools, is in [docs/entrypoints.md](docs/entrypoints.md).

## Boundaries

- **Paper only:** `execution_allowed=false`; do not enable `AUTO_TRADE`.
- **Deterministic authority:** code and validators own prices, risk limits,
  verdicts, and permissions. LLMs can only submit bounded advisory JSON.
- **No secrets or private research in Git:** credentials, private datasets,
  raw model conversations, runtime logs, journals, strategy rankings, and
  generated calculations stay local.
- **Independent validation:** only the documented bridge may pass a candidate to
  `honest-backtest`; a result is evidence, not a trading instruction.

## Repository Map

| Location | Responsibility |
|---|---|
| `src/research_lab/` | Bounded farm, candidate lifecycle, paper observation. |
| `scripts/strategy_lab/` | Operators, workers, validation bridge, reports. |
| `src/scout/` | Upstream information intake; not a trade executor. |
| `src/strategy/` | Strategy primitives and technical confirmation helpers. |
| `vendor/honest-backtest/` | Vendored integration surface; independent project remains authoritative. |
| `tests/` | Non-live regression and boundary coverage. |
| `docs/` | Current docs, reference material, and explicitly classified archive. |

For module-level ownership, see [docs/project-map.md](docs/project-map.md).

## License

Public files committed here are licensed under [Apache-2.0](LICENSE). The
[NOTICE](NOTICE) clarifies that uncommitted private research and operational
data are not published or licensed by this repository.
