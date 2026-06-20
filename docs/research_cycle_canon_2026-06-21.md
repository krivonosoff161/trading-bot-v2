# Research-calculation cycle — canonical reference (2026-06-21)

This is the authoritative description of the Strategy-Lab research cycle after the overnight hardening
pass. It defines what the cycle is, what each result class means, and which claims are allowed. It does
NOT contain edge numbers — those live in the private research repo (see below).

## Canonical cycle

```
intake (scanner WATCH/GO  OR  universe refill  OR  outcome-memory revisit)
  -> farm calculation (coordinator plans sweeps; memory gate skips known-bad; bounded compute)
  -> honest validator (costs / OOS split / significance with multiple-testing deflation /
                       robustness / overfit-PSR / forward-readiness / data-quality)
  -> Setup Outcome Memory (every result becomes a derived, rebuildable read-model)
  -> shadow / forward decision (survivors watched, never promoted)
  -> status / cockpit / docs
```

The continuous loop is **bounded** (duration + iteration + queue + variant + worker caps), has a
**stop-file**, **dedup** (uc_key + proposal_id + memory skip-known-bad), and **backoff** (two breaker
streaks: LLM contract-failures and provider-errors). It runs OHLCV families by default; OI/flow
families are **opt-in** (`STRATEGY_LAB_INCLUDE_OI_FAMILIES`) because OI availability has not proven a
positive result.

## Result classes (all research-only)

| class | meaning | promotable? |
|---|---|---|
| PAPER_FORWARD_READY (hard) | cleared the honest validator | only with a separate human GO |
| shadow_forward_candidate | a survivor watched forward; no execution path | never (forward evidence only) |
| shadow_survived (OOS) | held-out-tail OOS stayed positive + bridge pass | "deserves a real forward watch", NOT edge |
| shadow_noise_floor / _failed_costs / _failed_oos / _underpowered | OOS verdicts | no |
| thin_positive_skew (tactical_probe) | a family whose thin (n<10) setups skew positive | forward probe, NOT edge |
| exit_recovered_candidate / needs_forward_only | dynamic-exit re-sim improved in-sample | forward watch, NOT edge |
| oi_* / oi_diag_* | OI-family results (1h/4h dense; 15m diagnostic) | no (OI availability != edge) |
| CONFIRMED_BAD / WRONG_EXIT / TACTICAL_1_2_TRADE / NEEDS_OI_DATA / ... | rejected-as-knowledge | no |

## Revisit rule

A known-bad cell is NOT recomputed blindly. A revisit is allowed only on a genuinely-new trigger
(new data fingerprint / params-or-exit / timeframe-horizon / OI-funding context) or a time/human
trigger (TTL expired / manual GO). See `setup_outcome_memory.revisit_policy`.

## Forbidden to promote

Nothing in the lab auto-promotes. `paper_forward_ready` is owned solely by the hard validator + setup
cards; a status-report invariant guard (`paper_ready_without_hard_pass`) must stay 0. No survivor,
shadow candidate, tactical probe, exit-recovery, or OI result becomes paper/live without an explicit
owner GO. No `.env` / AUTO_TRADE / private endpoints / order execution / Telegram credentials are
touched by the research cycle.

## Where the private numbers live

Detailed calculations, per-symbol metrics, and dated verdicts are in the **private** research repo
(`scripts/analysis/research/`, separate `.git`, private remote) — e.g. `shadow_oos_2026-06-21.md`,
`tactical_probe_2026-06-21.md`, `llm_governance_audit_2026-06-21.md`, `loop_readiness_2026-06-21.md`.
The public repo carries product/infra + this canon, never edge numbers.

## Allowed vs forbidden public claims

ALLOWED: "the pipeline runs end-to-end and is honest"; "survivor recovered mechanically in-sample";
"shadow-forward candidate"; "thin_positive_skew worth a forward probe"; "OI availability != edge";
"noise-floor"; "needs new data/family/exit model".

FORBIDDEN: "strategy is profitable"; "ready to trade"; "the validator was wrong"; "rejected means bad
forever"; "1 survivor of N = edge"; any profitability or live-readiness claim.
