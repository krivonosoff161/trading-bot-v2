# Agent Operating Guide

Status: **ACTIVE**. Updated 2026-07-10.

This repository is a paper/research workbench. The current user request and
repository state take priority over historical handoffs.

## Read Before Work

1. [README](README.md)
2. [Docs Home](docs/README.md)
3. [Architecture](ARCHITECTURE.md)
4. [Current State](CURRENT_STATE.md)
5. [Storage Boundaries](docs/storage_boundaries.md)
6. [Entrypoint Catalog](docs/entrypoints.md) before running any `.bat` file

## Non-Negotiable Boundaries

- Do not edit or print `.env`, credentials, tokens, private provider IDs, or
  user subscription data.
- Do not enable `AUTO_TRADE`, place orders, call private exchange/account
  endpoints, or give any model execution authority.
- Do not publish, commit, or attach private journals, raw market data, candidate
  rankings, raw model prompts/responses, generated calculations, screenshots,
  or local runtime logs.
- Treat old engines and legacy launchers as reference only. Use the catalog to
  find supported paper paths.

## Change Discipline

1. Inspect existing code and documentation before creating a new surface.
2. Keep current documents, reference material, and archive distinct.
3. Prefer deterministic contracts, schema validation, and tests over unbounded
   model behavior.
4. Run targeted tests and `python scripts/ci/check_tracked_artifacts.py` before
   committing public changes.
5. When a change affects the bridge, update both repositories' public contract.

## LLM Boundary

LLMs may provide bounded advisory JSON or presentation text only. Code owns
numbers, risk limits, promotion, validation verdicts, and permissions. See
[docs/llm_proposal_contract.md](docs/llm_proposal_contract.md).

## Historical Material

`PLAN.md`, `TASK.md`, legacy reports, and dated handoffs may explain prior
decisions but are not current authority unless promoted by
[docs/README.md](docs/README.md). The deferred adaptive paper initiative is
tracked in [docs/deferred-adaptive-paper-architecture.md](docs/deferred-adaptive-paper-architecture.md).
