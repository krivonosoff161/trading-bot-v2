# Trading Bot V2

Status: **CURRENT**

- Verified: 2026-08-01
- Verified against: `c20322f887977c5e3c3ec2c242ca560617d056fa`
- Scope: public entry point, supported paper/research boundary, and non-claims
- Evidence: [Trading Portfolio Roadmap](docs/trading-portfolio-roadmap.md) and
  [public documentation guard](scripts/ci/check_public_docs.py)
- Residual risks: documentation and tests do not prove private runtime health,
  signal quality, profitability, or live readiness.
- Next gate: keep the machine roadmap and both repository projections aligned.

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

1. Read [Trading Portfolio Roadmap](docs/trading-portfolio-roadmap.md) for the
   cross-repository ownership and evidence map.
2. Read [Docs Home](docs/README.md) for document authority and navigation.
3. Read [Architecture](ARCHITECTURE.md) for the active boundaries.
4. Read [Entrypoint Catalog](docs/entrypoints.md) before launching a Windows
   command.
5. Read [Storage Boundaries](docs/storage_boundaries.md) before collecting or
   exporting any artifact.
6. Read [Adaptive Research Center Contract](docs/adaptive-research-center-contract.md)
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

CI installs the pip-compile-generated, fully transitive `requirements-ci.txt`
with `--require-hashes`; every locked distribution has one or more SHA-256
archive hashes. `requirements-ci.in` is the direct input and
`requirements-ci.sha256` is the byte identity of the generated lock.
`requirements.txt` remains the local developer convenience file and can carry
broader comments or optional guidance.

Regenerate and verify the lock with Python 3.11:

```powershell
python -m piptools compile --generate-hashes --resolver=backtracking --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file=requirements-ci.txt requirements-ci.in
python -m pip install --require-hashes -r requirements-ci.txt
```

For a platform-specific offline reconstruction, first populate a wheelhouse on
that platform, then create a clean venv and disable package indexes:

```powershell
python -m pip download --only-binary=:all: --require-hashes -r requirements-ci.txt -d .wheelhouse
py -3.11 -m venv .venv-offline
.\.venv-offline\Scripts\python -m pip install --no-index --find-links .wheelhouse --require-hashes -r requirements-ci.txt
.\.venv-offline\Scripts\python -m pip check
```

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
