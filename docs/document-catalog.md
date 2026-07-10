# Document Catalog

Status: **ACTIVE**. Updated 2026-07-10.

This catalog prevents a dated report from silently becoming an operating
instruction. When documents conflict, use the authority order in
[README.md](README.md).

## Current Authority

| Responsibility | Current document |
|---|---|
| Public scope and safe setup | [../README.md](../README.md) |
| Architecture and boundaries | [../ARCHITECTURE.md](../ARCHITECTURE.md) |
| Current public support state | [../CURRENT_STATE.md](../CURRENT_STATE.md) |
| Development sequence | [../ROADMAP.md](../ROADMAP.md) |
| Agent operating rules | [../CLAUDE.md](../CLAUDE.md) |
| Module ownership | [project-map.md](project-map.md) |
| Windows launch ownership | [entrypoints.md](entrypoints.md) |
| Paper operator path | [farm_runbook.md](farm_runbook.md) |
| Farm lifecycle | [farm_loop_lifecycle.md](farm_loop_lifecycle.md) |
| Storage/public boundary | [storage_boundaries.md](storage_boundaries.md) |
| Cross-repository validation contract | [validation-bridge-contract.md](validation-bridge-contract.md) |
| Local-only artifact request rules | [REMOTE_DATA_MANIFEST.md](REMOTE_DATA_MANIFEST.md) |

## Reference Documents

These describe a stable limited surface but do not supersede current status:

- `llm_proposal_contract.md`
- `farm_ownership_map.md`
- `paper_runtime_design.md`
- `farm_notification_layer.md`
- `scanner_ta_confirmation_contract.md`
- `main_research_verdict_index.md`
- `public_channel_news_flow.md`
- `SCANNER_SPEC.md`

## Deferred Decision

`deferred-adaptive-paper-architecture.md` preserves a reviewed future
initiative. It is not current implementation authority.

## Archive And Local-History Rule

Every dated report, handoff, experiment, AI-provider brief, strategy postmortem,
source survey, or research document not named in the sections above is local
**ARCHIVE** material. It may explain why an earlier decision happened, but it
is not part of the public repository and must not be used to start a process,
claim a current capability, or infer a current strategy parameter.

This includes files with names such as `gpt_*`, `kimi_*`, `brief_*`,
`*_audit_*`, `*_report_*`, `*_handoff_*`, `strategy_*_postmortem`, and dated
research/cycle/revival/forensic documents.

## Local-Only Rule

Raw runtime outputs, journals, individual trade paths, raw model conversations,
media downloads, screenshots, prompt packs, private calculations, and generated
charts are **LOCAL ONLY** even where an archived document mentions them. Public
documents may state a method or an aggregated limitation, but not reproduce the
underlying data.

Historical Git history is a separate remediation item. Current public tracking
is being made safe without rewriting published history; history rewrite requires
an explicit owner decision and an encrypted private backup first.
