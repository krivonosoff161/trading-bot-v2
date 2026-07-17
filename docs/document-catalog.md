# Document Catalog

Status: **ACTIVE**. Updated 2026-07-11.

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
| Simulator evidence tiers and claim ceilings | [simulator-truth-tiers.md](simulator-truth-tiers.md) |
| Adaptive role and feedback contract | [adaptive-research-center-contract.md](adaptive-research-center-contract.md) |
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

## Superseded Decision

`deferred-adaptive-paper-architecture.md` preserves the earlier deferred
decision. It is superseded by the active adaptive research-center contract.

## Sanitized Legacy Evidence

`legacy-evidence/` contains a deliberately small set of retrospective
narratives about closed or revised early hypotheses. It is **REFERENCE** only:
the pages preserve limitations and methodology, but do not supply a current
strategy, raw calculation, parameter set, or performance claim.

## Archive And Local-History Rule

Every dated report, handoff, experiment, AI-provider brief, strategy postmortem,
source survey, or research document not named in the sections above is local
**ARCHIVE** material. It may explain why an earlier decision happened, but it
is not part of the public repository and must not be used to start a process,
claim a current capability, or infer a current strategy parameter.

This includes files with names such as `gpt_*`, `kimi_*`, `brief_*`,
`*_audit_*`, `*_report_*`, `*_handoff_*`, `strategy_*_postmortem`, and dated
research/cycle/revival/forensic documents.

The only public exception is a page explicitly written under
`docs/legacy-evidence/`; it must be a sanitized derived narrative and meet the
public-artifact policy.

## Local-Only Rule

Raw runtime outputs, journals, individual trade paths, raw model conversations,
media downloads, screenshots, prompt packs, private calculations, and generated
charts are **LOCAL ONLY** even where an archived document mentions them. Public
documents may state a method or an aggregated limitation, but not reproduce the
underlying data.

Historical Git heads were rewritten on 2026-07-10 after an encrypted private
backup was verified. Current public heads and fresh clones do not include the
removed raw artifacts. GitHub pull-request refs and external clones can retain
older references and remain a separate residual-risk and support-process item.
