# -*- coding: utf-8 -*-
"""Setup Outcome Memory — a derived, rebuildable read-model over the farm's canonical
artifacts, plus a read-through gate so a REPEATED signal consults prior outcomes BEFORE
the farm spends compute.

It is NOT a second source of truth and adds NO registry: ``build_memory_index`` rebuilds
entirely from farm_tasks.unique_candidates + ``setup_lifecycle`` (which already joins
verdicts/cards/paper) + ``trade_path_diagnostics`` (reject sub-reason + path facts) +
``exit_recovery`` (recovered exits). Research-only: NO outcome here ever grants
PAPER_FORWARD_READY — that flag stays owned by the hard validator + setup cards.

Two layers:
  * ``build_gate_index`` / ``lookup``: the cheap hot path (unique_candidates columns only),
    consulted by the coordinator at plan time so an identical data fingerprint that already
    produced only dead candidates is not re-swept ("skip_known_bad"), a family cell that is
    historically all-rejected is deprioritised, and an eligible/recovered prior is flagged
    for re-validation ("revisit") instead of a blind fresh sweep.
  * ``build_memory_index`` / views: the rich read-model (joins per-trade path facts and
    recovered exits) for the derived snapshot + the positive / rejected / tactical /
    confirmed-bad / recovered / needs-data sub-views.

No money path, no orders, no live, no .env. Everything is read-only and derived.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.setup_lifecycle import HARD_FAILED, derive_setup_lifecycle
from src.research_lab.trade_path_diagnostics import POWER_FLOOR, characterize_rejects

ELIGIBLE_LITE = {"FORWARD_PAPER", "REGIME_SPECIFIC"}

# Closed outcome vocabulary (research-only labels). POSITIVE_VALIDATED is the ONLY class a
# hard-passed setup gets; none of these is a trade signal or grants paper-forward access.
OUTCOME_CLASSES = (
    "POSITIVE_VALIDATED", "EXIT_RECOVERED", "STATISTICAL_CANDIDATE", "THIN_BUT_PROMISING",
    "TACTICAL_1_2_TRADE", "WRONG_EXIT", "WRONG_TIMEFRAME", "COST_SENSITIVE",
    "NEEDS_OI_DATA", "CONFIRMED_BAD", "INSUFFICIENT_DATA", "UNCHARACTERIZED",
)

_SUBREASON_TO_CLASS = {
    "tactical_candidate": "TACTICAL_1_2_TRADE",
    "validator_too_strict": "THIN_BUT_PROMISING",
    "wrong_exit": "WRONG_EXIT",
    "wrong_timeframe": "WRONG_TIMEFRAME",
    "wrong_costs": "COST_SENSITIVE",
    "missing_oi_micro": "NEEDS_OI_DATA",
    "confirmed_bad": "CONFIRMED_BAD",
    "insufficient_data": "INSUFFICIENT_DATA",
    "uncharacterized": "UNCHARACTERIZED",
}
_POSITIVE_STATES = {"HARD_PASSED", "PAPER_PLAN_READY", "PAPER_POSITIVE_OBSERVED"}

# A family cell needs this many priors, this share rejected with zero eligible, to down-rank.
DEPRIORITIZE_MIN = 5
DEPRIORITIZE_BAD_SHARE = 0.8

# Cost lenses (percent points per round-trip trade): the stored net already carries TAKER, so
# gross = net + TAKER and a maker fill would net = net + (TAKER - MAKER). The mining showed taker cost
# is the dominant killer; cost_class records WHERE a setup sits relative to that, never as edge.
_TAKER_PP = 0.10
_MAKER_PP = 0.02


def cost_class(net_pct: float) -> str:
    """Where a setup sits on the cost ladder (research label, not edge)."""
    net = float(net_pct or 0.0)
    if net > 0:
        return "cost_ok"                       # net-positive even under taker
    if net + (_TAKER_PP - _MAKER_PP) > 0:
        return "cost_bound_maker_unlock"        # taker-dead but a maker fill would flip it (hypothesis)
    if net + _TAKER_PP > 0:
        return "cost_marginal"                  # only zero-cost positive
    return "cost_dead"


def tactical_class(n_trades: int, net_pct: float, hard_status: str) -> str:
    """One-shot vs statistical separation (research label; one-shot is NEVER edge)."""
    if str(hard_status or "") == "PAPER_FORWARD_READY":
        return "statistical_edge_candidate"
    if 1 <= int(n_trades or 0) < POWER_FLOOR and float(net_pct or 0.0) > 0:
        return "one_shot_candidate"
    return ""


# Machine-reason follow-up for the rejected-research pipeline: every rejected/thin setup gets an
# explainable next step (none of which is promotion).
def next_action(outcome_class: str, cost_cls: str, tactical_cls: str) -> str:
    if outcome_class == "CONFIRMED_BAD":
        return "known_bad_freeze"
    if outcome_class == "WRONG_EXIT":
        return "exit_grid_phase2"
    if outcome_class in ("NEEDS_OI_DATA", "INSUFFICIENT_DATA"):
        return "await_new_data"
    if cost_cls == "cost_bound_maker_unlock":
        return "maker_cost_sensitivity_research"     # not a fake-pass: a maker-fill model is needed
    if tactical_cls == "one_shot_candidate":
        return "shadow_forward_watch"
    if outcome_class == "POSITIVE_VALIDATED":
        return "hard_passed_await_human_go"
    return "characterize_or_park"


def derive_outcome_class(lifecycle_state: str, subreason: str, recovered: bool,
                         hard_status: str = "") -> str:
    """Unify hard verdict + lifecycle state + reject sub-reason + recovered flag into one label.

    POSITIVE_VALIDATED means "cleared the hard validator" — it stays positive even if a later
    paper sample is negative (that fact lives in lifecycle_state / paper fields, not here).
    """
    if hard_status == "PAPER_FORWARD_READY" or lifecycle_state in _POSITIVE_STATES:
        return "POSITIVE_VALIDATED"
    if recovered:
        return "EXIT_RECOVERED"
    if lifecycle_state == "HARD_ELIGIBLE":
        return "STATISTICAL_CANDIDATE"
    return _SUBREASON_TO_CLASS.get(subreason, "UNCHARACTERIZED")


def _okx_inst(symbol: str) -> str:
    return str(symbol or "").replace("_", "-")


# ── read-through gate (cheap hot path: unique_candidates columns only) ──────────────────
@dataclass
class GateIndex:
    by_sweep: dict[str, dict[str, Any]] = field(default_factory=dict)   # sym|tf|fam|fingerprint
    by_cell: dict[str, dict[str, Any]] = field(default_factory=dict)    # sym|tf|fam
    by_params: dict[str, dict[str, Any]] = field(default_factory=dict)  # sym|tf|fam|params_hash


@dataclass
class MemoryVerdict:
    action: str            # fresh | skip_known_bad | deprioritize | revisit
    reason: str
    matched: str = "none"  # exact | cell | none
    prior_eligible: int = 0
    prior_confirmed_bad: int = 0
    prior_max_n_trades: int = 0


def _flags(row: dict[str, Any]) -> tuple[bool, bool, bool]:
    """(eligible, confirmed_bad, rejected) for one unique_candidates row."""
    lite = str(row.get("validation_status") or "")
    hard = str(row.get("hard_status") or "")
    decision = str(row.get("decision") or "")
    n = int(row.get("n_trades") or 0)
    avg = float(row.get("avg_net_pct") or 0.0)
    eligible = lite in ELIGIBLE_LITE or hard == "PAPER_FORWARD_READY"
    confirmed_bad = n >= POWER_FLOOR and avg <= 0
    rejected = decision == "REJECT" or lite == "REJECT" or hard in HARD_FAILED
    return eligible, confirmed_bad, rejected


def _as_ms(value: Any) -> int:
    """updated_at as int ms (tolerant of None / float / numeric string)."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def build_gate_index(candidates: list[dict[str, Any]]) -> GateIndex:
    """Aggregate prior outcomes per exact sweep (fingerprint), per param set, and per family cell.

    The by_cell aggregate also remembers WHAT was tried (fingerprints / params_hashes) and WHEN
    (newest_ts) so the revisit policy can decide whether a known-bad cell may be recomputed."""
    idx = GateIndex()
    for row in candidates:
        sym, tf = str(row.get("symbol") or ""), str(row.get("timeframe") or "")
        fam, fp = str(row.get("family") or ""), str(row.get("data_fingerprint") or "")
        ph = str(row.get("params_hash") or "")
        eligible, confirmed_bad, rejected = _flags(row)
        n = int(row.get("n_trades") or 0)
        ts = _as_ms(row.get("updated_at"))
        for key, store in ((f"{sym}|{tf}|{fam}|{fp}", idx.by_sweep),
                           (f"{sym}|{tf}|{fam}|{ph}", idx.by_params),
                           (f"{sym}|{tf}|{fam}", idx.by_cell)):
            agg = store.setdefault(
                key, {"n": 0, "n_eligible": 0, "n_confirmed_bad": 0, "n_rejected": 0, "max_n_trades": 0})
            agg["n"] += 1
            agg["n_eligible"] += int(eligible)
            agg["n_confirmed_bad"] += int(confirmed_bad)
            agg["n_rejected"] += int(rejected)
            agg["max_n_trades"] = max(agg["max_n_trades"], n)
        cell = idx.by_cell[f"{sym}|{tf}|{fam}"]
        cell.setdefault("fingerprints", set()).add(fp)
        cell.setdefault("params_hashes", set()).add(ph)
        cell["newest_ts"] = max(int(cell.get("newest_ts") or 0), ts)
    return idx


def lookup(index: GateIndex, *, symbol: str, timeframe: str, family: str,
           data_fingerprint: str) -> MemoryVerdict:
    """Read-through: what does prior memory say about (re-)computing this exact sweep?"""
    sweep = index.by_sweep.get(f"{symbol}|{timeframe}|{family}|{data_fingerprint}")
    if sweep and sweep["n"] > 0:
        v = MemoryVerdict("", "", "exact", sweep["n_eligible"],
                          sweep["n_confirmed_bad"], sweep["max_n_trades"])
        if sweep["n_eligible"] > 0:
            v.action, v.reason = "revisit", "prior_eligible_on_identical_data"
            return v
        # Identical fingerprint already produced ZERO eligible candidates — re-running the
        # SAME data reproduces it. Skip; the path to more evidence is NEW data / re-validation.
        v.action = "skip_known_bad"
        v.reason = ("confirmed_bad_identical_data" if sweep["n_confirmed_bad"] > 0
                    else "no_eligible_identical_data")
        return v
    cell = index.by_cell.get(f"{symbol}|{timeframe}|{family}")
    if cell and cell["n"] > 0:
        if cell["n_eligible"] > 0:
            return MemoryVerdict("revisit", "cell_has_eligible_prior", "cell",
                                 cell["n_eligible"], cell["n_confirmed_bad"], cell["max_n_trades"])
        if cell["n"] >= DEPRIORITIZE_MIN and cell["n_rejected"] / cell["n"] >= DEPRIORITIZE_BAD_SHARE:
            return MemoryVerdict("deprioritize", "cell_historically_all_rejected", "cell",
                                 0, cell["n_confirmed_bad"], cell["max_n_trades"])
    return MemoryVerdict("fresh", "unseen_or_inconclusive")


@dataclass
class ProposalMemoryVerdict:
    action: str   # known_bad | revisit | fresh
    reason: str
    matched: str = "none"  # params | cell | none


def proposal_verdict(index: GateIndex, *, symbol: str, timeframe: str, family: str,
                     params_hash: str) -> ProposalMemoryVerdict:
    """Memory check for a PROPOSAL (no data fingerprint yet): is this exact param set, or the
    whole family cell, already proven dead? Conservative — rejects only confident known-bad so a
    genuinely new variant in an under-explored cell is still allowed."""
    exact = index.by_params.get(f"{symbol}|{timeframe}|{family}|{params_hash}")
    if exact and exact["n"] > 0 and exact["n_eligible"] == 0 and exact["n_confirmed_bad"] > 0:
        return ProposalMemoryVerdict("known_bad", "params_confirmed_bad", "params")
    cell = index.by_cell.get(f"{symbol}|{timeframe}|{family}")
    if cell and cell["n"] > 0:
        if cell["n_eligible"] > 0:
            return ProposalMemoryVerdict("revisit", "cell_has_eligible_prior", "cell")
        # Whole cell proven dead WITH power -> re-proposing here wastes compute.
        if (cell["n"] >= DEPRIORITIZE_MIN and cell["n_rejected"] / cell["n"] >= DEPRIORITIZE_BAD_SHARE
                and cell["max_n_trades"] >= POWER_FLOOR):
            return ProposalMemoryVerdict("known_bad", "cell_confirmed_dead_with_power", "cell")
    return ProposalMemoryVerdict("fresh", "unseen_or_inconclusive")


# ── revisit policy (rule: do NOT re-compute a known-bad blindly) ────────────────────────
REVISIT_TRIGGERS = (
    "new_data_fingerprint", "new_params_or_exit", "new_timeframe_horizon",
    "new_oi_funding_context", "ttl_expired", "manual_go",
)
DEFAULT_REVISIT_TTL_DAYS = 30
_MS_PER_DAY = 86_400_000


def prior_for_cell(index: GateIndex, *, symbol: str, timeframe: str, family: str) -> dict[str, Any]:
    """The 'what was already tried here' record for the revisit policy (empty if cell unseen)."""
    cell = index.by_cell.get(f"{symbol}|{timeframe}|{family}")
    if not cell:
        return {}
    return {"fingerprints": set(cell.get("fingerprints") or set()),
            "params_hashes": set(cell.get("params_hashes") or set()),
            "newest_ts": int(cell.get("newest_ts") or 0),
            "n_eligible": int(cell.get("n_eligible") or 0)}


def revisit_policy(prior: dict[str, Any], candidate: dict[str, Any], *,
                   ttl_days: int = DEFAULT_REVISIT_TTL_DAYS, now_ts: int | None = None) -> tuple[bool, list[str]]:
    """Decide whether a KNOWN-BAD cell may be recomputed for this candidate.

    Rule (owner): a known-bad is NOT re-swept blindly. A revisit is allowed ONLY if the candidate
    brings something genuinely new — a new data fingerprint, new params/exit, new timeframe-horizon,
    or new OI/funding context — OR a time/human trigger fires (TTL expired / manual GO). Pure and
    deterministic: ``now_ts`` is passed in, never read from the clock here.
    """
    triggers: list[str] = []
    if candidate.get("data_fingerprint") and candidate["data_fingerprint"] not in (prior.get("fingerprints") or set()):
        triggers.append("new_data_fingerprint")
    if candidate.get("params_hash") and candidate["params_hash"] not in (prior.get("params_hashes") or set()):
        triggers.append("new_params_or_exit")
    if candidate.get("horizon") is not None and candidate.get("horizon") not in (prior.get("horizons") or set()):
        triggers.append("new_timeframe_horizon")
    if candidate.get("oi_funding_context") and not prior.get("had_oi_funding_context"):
        triggers.append("new_oi_funding_context")
    newest = int(prior.get("newest_ts") or 0)
    if now_ts is not None and newest > 0 and (int(now_ts) - newest) >= int(ttl_days) * _MS_PER_DAY:
        triggers.append("ttl_expired")
    if candidate.get("manual_go"):
        triggers.append("manual_go")
    return (bool(triggers), triggers)


def gate_distribution(index: GateIndex) -> dict[str, int]:
    """Tally the read-through gate's verdict over every known exact sweep (what memory would do)."""
    out: dict[str, int] = {"skip_known_bad": 0, "revisit": 0, "deprioritize": 0, "fresh": 0}
    for key in index.by_sweep:
        try:
            sym, tf, fam, fp = key.split("|", 3)
        except ValueError:
            continue
        action = lookup(index, symbol=sym, timeframe=tf, family=fam, data_fingerprint=fp).action
        out[action] = out.get(action, 0) + 1
    return out


def knowledge_base_counts(records: list[dict[str, Any]], index: GateIndex, *,
                          survived_shadow: int = 0, tactical_probe: int = 0) -> dict[str, int]:
    """The owner's six knowledge-base counts in one place (research-only; none grants paper access)."""
    gate = gate_distribution(index)
    return {
        "known_bad": gate.get("skip_known_bad", 0),
        "revisit": gate.get("revisit", 0),
        "survived_shadow": int(survived_shadow),
        "tactical_probe": int(tactical_probe),
        "rejected_recyclable": len(rejected_research(records)),
        "rejected_confirmed_bad": len(confirmed_bad_setups(records)),
    }


def memory_prompt_digest(records: list[dict[str, Any]], *, max_families: int = 6) -> str:
    """Compact, deterministic outcome-memory digest to feed the advisory LLM so it proposes
    AGAINST known failures instead of blind. Counts/labels only — no profitability claim."""
    if not records:
        return "OUTCOME MEMORY: empty (no prior results)."
    s = summarize_memory(records)
    dead_by_fam: dict[str, int] = {}
    for r in records:
        if r["outcome_class"] in ("CONFIRMED_BAD", "WRONG_EXIT"):
            dead_by_fam[r["family"]] = dead_by_fam.get(r["family"], 0) + 1
    top_dead = ", ".join(f"{k}({v})" for k, v in
                         sorted(dead_by_fam.items(), key=lambda kv: -kv[1])[:max_families]) or "none"
    cls = s["by_outcome_class"]
    return (
        f"OUTCOME MEMORY DIGEST ({s['total']} setups tested; research-only, no edge claim):\n"
        f"- DEAD, do NOT re-propose same params: confirmed_bad={cls.get('CONFIRMED_BAD', 0)}, "
        f"wrong_exit={cls.get('WRONG_EXIT', 0)} (exit problem, not signal). Dead-heaviest families: {top_dead}.\n"
        f"- Underpowered (need a DIFFERENT angle, not a re-run): tactical_1_2={cls.get('TACTICAL_1_2_TRADE', 0)}, "
        f"thin_but_promising={cls.get('THIN_BUT_PROMISING', 0)}, needs_data={s['needs_data']}.\n"
        f"- Honest re-validation: {s.get('revalidated', 0)} re-tested, {s.get('revalidation_survivors', 0)} "
        f"survived (noise floor, NOT edge). Only mean_reversion_fade showed exit-recoverable signal.\n"
        f"- Propose hypotheses that AVOID confirmed_bad params and target a NEW failure mode "
        f"(exit model, timeframe horizon, regime, OI/funding context)."
    )


# ── rich read-model (joins per-trade path facts + recovered exits) ──────────────────────
def _recovered_cells(private_root: Path) -> set[str]:
    """sym|tf|fam cells flagged by exit_recovery's derived snapshot (coarse — it has no uc_key)."""
    path = Path(private_root) / "state" / "derived" / "exit_recovery.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    cands = data.get("exit_recovered_candidates", []) if isinstance(data, dict) else []
    return {f"{c.get('symbol')}|{c.get('timeframe')}|{c.get('family')}" for c in cands if isinstance(c, dict)}


def _derived_by_uc(private_root: Path, filename: str) -> dict[str, dict[str, Any]]:
    """Read a derived snapshot's by_uc_key map (empty if the snapshot has not been produced)."""
    path = Path(private_root) / "state" / "derived" / filename
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    by_uc = data.get("by_uc_key") if isinstance(data, dict) else None
    return by_uc if isinstance(by_uc, dict) else {}


def _training_memory(
    private_root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Aggregate paper-product training rows for memory enrichment.

    Training rows are already sanitized derived artifacts.  This function keeps
    only numeric/label aggregates needed by the memory gate; it never carries
    Telegram card text, raw LLM payloads, recipient ids, or provider secrets.
    """
    path = Path(private_root) / "state" / "derived" / "paper_signal_training.jsonl"
    by_candidate: dict[str, dict[str, Any]] = {}
    by_cell: dict[str, dict[str, Any]] = {}
    by_geometry_profile_cell: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return by_candidate, by_cell, by_geometry_profile_cell
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return by_candidate, by_cell, by_geometry_profile_cell
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        candidate_ids = {
            str(row.get("candidate_id") or "").strip(),
            str(row.get("setup_candidate_id") or "").strip(),
        }
        candidate_ids.discard("")
        cell_key = _training_cell_key(row.get("symbol") or row.get("okx_inst_id"), row.get("timeframe"), row.get("family"))
        profile_cell_key = _training_geometry_profile_key(
            row.get("symbol") or row.get("okx_inst_id"),
            row.get("timeframe"),
            row.get("family"),
            row.get("farm_geometry_profile_id"),
        )
        for candidate_id in candidate_ids:
            _add_training_row(by_candidate.setdefault(candidate_id, _empty_training_agg()), row)
        if cell_key:
            _add_training_row(by_cell.setdefault(cell_key, _empty_training_agg()), row)
        if profile_cell_key:
            _add_training_row(
                by_geometry_profile_cell.setdefault(profile_cell_key, _empty_training_agg()),
                row,
            )
    return by_candidate, by_cell, by_geometry_profile_cell


def _normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.replace("-", "_")


def _training_cell_key(symbol: Any, timeframe: Any, family: Any) -> str:
    sym = _normalize_symbol(symbol)
    tf = str(timeframe or "").strip()
    fam = str(family or "").strip()
    if not (sym and tf and fam):
        return ""
    return f"{sym}|{tf}|{fam}"


def _training_geometry_profile_key(symbol: Any, timeframe: Any, family: Any, profile_id: Any) -> str:
    cell = _training_cell_key(symbol, timeframe, family)
    profile = str(profile_id or "").strip()
    if not (cell and profile):
        return ""
    return f"{cell}|{profile}"


def _empty_training_agg() -> dict[str, Any]:
    return {
        "rows": 0,
        "terminal_rows": 0,
        "paper_pnl_usdt": 0.0,
        "net_pct_sum": 0.0,
        "win_rows": 0,
        "loss_rows": 0,
        "gave_back_rows": 0,
        "outcome_bucket": {},
        "actionability": {},
    }


def _add_training_row(agg: dict[str, Any], row: dict[str, Any]) -> None:
    agg["rows"] = int(agg.get("rows") or 0) + 1
    pnl = _float_or_none(row.get("paper_pnl_usdt"))
    net = _float_or_none(row.get("net_pct"))
    if pnl is not None or net is not None:
        agg["terminal_rows"] = int(agg.get("terminal_rows") or 0) + 1
    if pnl is not None:
        agg["paper_pnl_usdt"] = round(float(agg.get("paper_pnl_usdt") or 0.0) + pnl, 6)
        if pnl > 0:
            agg["win_rows"] = int(agg.get("win_rows") or 0) + 1
        elif pnl < 0:
            agg["loss_rows"] = int(agg.get("loss_rows") or 0) + 1
    if net is not None:
        agg["net_pct_sum"] = round(float(agg.get("net_pct_sum") or 0.0) + net, 6)
    diagnosis = str(row.get("diagnosis") or "")
    bucket = str(row.get("outcome_learning_bucket") or "")
    actionability = str(row.get("outcome_learning_actionability") or "")
    if diagnosis == "bad_exit_gave_back" or bucket == "gave_back":
        agg["gave_back_rows"] = int(agg.get("gave_back_rows") or 0) + 1
    if bucket:
        counts = agg.setdefault("outcome_bucket", {})
        counts[bucket] = int(counts.get(bucket) or 0) + 1
    if actionability:
        counts = agg.setdefault("actionability", {})
        counts[actionability] = int(counts.get(actionability) or 0) + 1


def _training_summary(agg: dict[str, Any] | None) -> dict[str, Any]:
    if not agg:
        return {
            "rows": 0,
            "terminal_rows": 0,
            "paper_pnl_usdt": 0.0,
            "avg_paper_pnl_usdt": 0.0,
            "avg_net_pct": 0.0,
            "win_rows": 0,
            "loss_rows": 0,
            "gave_back_rows": 0,
            "outcome_bucket": {},
            "actionability": {},
        }
    terminal = int(agg.get("terminal_rows") or 0)
    pnl = round(float(agg.get("paper_pnl_usdt") or 0.0), 6)
    net_sum = round(float(agg.get("net_pct_sum") or 0.0), 6)
    return {
        "rows": int(agg.get("rows") or 0),
        "terminal_rows": terminal,
        "paper_pnl_usdt": pnl,
        "avg_paper_pnl_usdt": round(pnl / terminal, 6) if terminal else 0.0,
        "avg_net_pct": round(net_sum / terminal, 6) if terminal else 0.0,
        "win_rows": int(agg.get("win_rows") or 0),
        "loss_rows": int(agg.get("loss_rows") or 0),
        "gave_back_rows": int(agg.get("gave_back_rows") or 0),
        "outcome_bucket": dict(agg.get("outcome_bucket") or {}),
        "actionability": dict(agg.get("actionability") or {}),
    }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_product_training_memory(private_root: Path) -> dict[str, Any]:
    """Aggregate broad subscriber-facing paper outcomes.

    This is intentionally separate from the strict setup memory: product paper
    rows often use user-facing families (for example ``early_tp_tactical``) while
    hard-validation candidates use research families (for example
    ``momentum_breakout``).  Mixing them would fake validator evidence.  The
    summary keeps the product learning signal visible without promoting it.
    """
    _, by_cell, by_geometry_profile_cell = _training_memory(private_root)
    total = _empty_training_agg()
    by_family: dict[str, dict[str, Any]] = {}
    by_timeframe: dict[str, dict[str, Any]] = {}
    by_geometry_profile: dict[str, dict[str, Any]] = {}
    by_cell_summary: dict[str, dict[str, Any]] = {}
    by_geometry_profile_cell_summary: dict[str, dict[str, Any]] = {}
    for key, agg in by_cell.items():
        parts = key.split("|")
        if len(parts) != 3:
            continue
        _symbol, tf, family = parts
        _merge_training_agg(total, agg)
        _merge_training_agg(by_family.setdefault(family, _empty_training_agg()), agg)
        _merge_training_agg(by_timeframe.setdefault(tf, _empty_training_agg()), agg)
        by_cell_summary[key] = _training_summary(agg)
    for key, agg in by_geometry_profile_cell.items():
        parts = key.split("|")
        if len(parts) != 4:
            continue
        _symbol, _tf, _family, profile_id = parts
        _merge_training_agg(by_geometry_profile.setdefault(profile_id, _empty_training_agg()), agg)
        by_geometry_profile_cell_summary[key] = _training_summary(agg)
    return {
        "schema": "product_paper_memory.v1",
        "disclaimer": "Broad paper-product outcomes only; research signal for review and next sweeps, not validation.",
        "cells": len(by_cell_summary),
        "geometry_profile_cells": len(by_geometry_profile_cell_summary),
        "summary": _training_summary(total),
        "by_family": {k: _training_summary(v) for k, v in sorted(by_family.items())},
        "by_timeframe": {k: _training_summary(v) for k, v in sorted(by_timeframe.items())},
        "by_geometry_profile": {k: _training_summary(v) for k, v in sorted(by_geometry_profile.items())},
        "by_cell": by_cell_summary,
        "by_geometry_profile_cell": by_geometry_profile_cell_summary,
        "paper_only": True,
        "execution_allowed": False,
    }


def _merge_training_agg(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("rows", "terminal_rows", "win_rows", "loss_rows", "gave_back_rows"):
        target[key] = int(target.get(key) or 0) + int(source.get(key) or 0)
    for key in ("paper_pnl_usdt", "net_pct_sum"):
        target[key] = round(float(target.get(key) or 0.0) + float(source.get(key) or 0.0), 6)
    for key in ("outcome_bucket", "actionability"):
        target_counts = target.setdefault(key, {})
        for label, count in (source.get(key) or {}).items():
            target_counts[label] = int(target_counts.get(label) or 0) + int(count or 0)


def _backfill_by_uc(private_root: Path) -> dict[str, dict[str, Any]]:
    """Per-uc trade-path metrics from the bounded backfill snapshot (empty if not run)."""
    return _derived_by_uc(private_root, "trade_path_backfill.json")


def _uc_run_dirs(private_root: Path) -> dict[str, str]:
    path = tasks_db_path(private_root)
    if not path.exists():
        return {}
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT uc_key, run_dir_label FROM unique_candidates").fetchall()
    finally:
        conn.close()
    return {str(r["uc_key"]): str(r["run_dir_label"] or "") for r in rows}


def build_memory_index(private_root: Path) -> list[dict[str, Any]]:
    """One unified outcome record per unique candidate (derived; never written back to source)."""
    private_root = Path(private_root)
    lifecycle = derive_setup_lifecycle(private_root)
    subreason = {r["uc_key"]: r for r in characterize_rejects(private_root)}
    recovered_cells = _recovered_cells(private_root)
    run_dirs = _uc_run_dirs(private_root)
    backfill = _backfill_by_uc(private_root)
    revalidation = _derived_by_uc(private_root, "recyclable_revalidation.json")
    shadow = _derived_by_uc(private_root, "shadow_forward.json")
    exit2 = _derived_by_uc(private_root, "exit_phase2.json")
    training_by_candidate, training_by_cell, _training_by_geometry_profile_cell = _training_memory(private_root)
    records: list[dict[str, Any]] = []
    for lc in lifecycle:
        uc = lc["uc_key"]
        cell = _training_cell_key(lc["symbol"], lc["timeframe"], lc["family"])
        sub = subreason.get(uc, {})
        sub_reason = str(sub.get("reject_subreason") or "")
        recovered = sub_reason == "wrong_exit" and cell in recovered_cells
        outcome = derive_outcome_class(lc["derived_lifecycle_state"], sub_reason, recovered,
                                       lc["hard_status"])
        bf = backfill.get(uc, {})
        product_training = _training_summary(
            training_by_candidate.get(str(lc.get("source_candidate_id") or ""))
            or training_by_candidate.get(str(lc.get("validation_candidate_id") or ""))
            or training_by_cell.get(cell)
        )
        cost_cls = cost_class(lc["avg_net_pct"])
        tactical_cls = tactical_class(lc["n_trades"], lc["avg_net_pct"], lc["hard_status"])
        records.append({
            "uc_key": uc, "symbol": lc["symbol"], "okx_inst": _okx_inst(lc["symbol"]),
            "timeframe": lc["timeframe"], "family": lc["family"],
            "params_hash": lc["params_hash"], "data_fingerprint": lc["data_fingerprint"],
            "regime_bucket": lc["regime_bucket"], "n_trades": lc["n_trades"],
            "baseline_net": lc["avg_net_pct"],
            "product_training": product_training,
            "paper_pnl_usdt": product_training["paper_pnl_usdt"],
            "paper_avg_pnl_usdt": product_training["avg_paper_pnl_usdt"],
            "paper_terminal_rows": product_training["terminal_rows"],
            "paper_gave_back_rows": product_training["gave_back_rows"],
            # Tactical Setup Library dimensions (research labels; none is edge or paper-ready):
            "cost_class": cost_cls, "tactical_class": tactical_cls,
            "next_action": next_action(outcome, cost_cls, tactical_cls),
            "avg_mfe_pct": float(sub.get("avg_mfe_pct") or 0.0),
            "avg_mae_pct": float(sub.get("avg_mae_pct") or 0.0),
            "avg_capture_ratio": float(sub.get("avg_capture_ratio") or 0.0),
            "lite_status": lc["lite_status"], "hard_status": lc["hard_status"],
            "validation_state": lc["validation_state"], "tactical_status": lc["tactical_status"],
            "rejection_reason": sub_reason,
            "recovery_reason": "exit_recovered_cell" if recovered else "",
            "outcome_class": outcome, "lifecycle_state": lc["derived_lifecycle_state"],
            "paper_forward_ready": bool(lc["paper_forward_ready"]),
            "paper_outcome_count": lc["paper_outcome_count"], "artifact_ref": run_dirs.get(uc, ""),
            # Trade-path metrics from the bounded backfill (None until backfill is run for this uc).
            "time_to_mfe": bf.get("avg_time_to_mfe"), "time_to_mae": bf.get("avg_time_to_mae"),
            "tp_before_sl_share": bf.get("tp_before_sl_share"),
            "adverse_first_rate": bf.get("adverse_first_rate"),
            "path_quality": bf.get("path_quality"),
            # Phase-D honest re-validation verdict (research-only): does NOT change
            # paper_forward_ready or outcome_class — a survivor needs a human GO, never auto-promotion.
            "revalidation_status": (revalidation.get(uc) or {}).get("revalidation_status"),
            # Shadow-forward watch status (research-only forward observation; never paper-ready).
            "shadow_status": (shadow.get(uc) or {}).get("status"),
            # Exit Phase-2 dynamic-exit class (research-only; recovered != edge, never paper-ready).
            "exit_phase2_class": (exit2.get(uc) or {}).get("outcome_class"),
        })
    return records


# ── sub-views (the "separate databases" as filters over the one index) ──────────────────
def _of_class(records: list[dict[str, Any]], classes: set[str]) -> list[dict[str, Any]]:
    return [r for r in records if r["outcome_class"] in classes]


def positive_setups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"POSITIVE_VALIDATED"})


def recovered_setups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"EXIT_RECOVERED"})


def statistical_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"STATISTICAL_CANDIDATE"})


def tactical_setups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"TACTICAL_1_2_TRADE", "THIN_BUT_PROMISING"})


def rejected_research(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"WRONG_EXIT", "WRONG_TIMEFRAME", "COST_SENSITIVE",
                               "TACTICAL_1_2_TRADE", "THIN_BUT_PROMISING", "NEEDS_OI_DATA"})


def confirmed_bad_setups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"CONFIRMED_BAD"})


def needs_data_setups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"INSUFFICIENT_DATA", "NEEDS_OI_DATA"})


def _tally_field(records: list[dict[str, Any]], field_name: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        v = str(r.get(field_name) or "")
        if v:
            out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def summarize_memory(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_class: dict[str, int] = {}
    for r in records:
        by_class[r["outcome_class"]] = by_class.get(r["outcome_class"], 0) + 1
    paper_rows = [r for r in records if int(r.get("paper_terminal_rows") or 0) > 0]
    total_paper_pnl = round(sum(float(r.get("paper_pnl_usdt") or 0.0) for r in paper_rows), 6)
    total_paper_terminal = sum(int(r.get("paper_terminal_rows") or 0) for r in paper_rows)
    return {
        "total": len(records),
        "by_outcome_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        # Tactical Setup Library dimensions (research-only): cost ladder + one-shot/statistical + follow-up
        "by_cost_class": _tally_field(records, "cost_class"),
        "by_tactical_class": _tally_field(records, "tactical_class"),
        "by_next_action": _tally_field(records, "next_action"),
        "paper_memory_rows": len(paper_rows),
        "paper_terminal_rows": total_paper_terminal,
        "paper_pnl_usdt": total_paper_pnl,
        "paper_avg_pnl_usdt": (
            round(total_paper_pnl / total_paper_terminal, 6) if total_paper_terminal else 0.0
        ),
        "paper_gave_back_rows": sum(int(r.get("paper_gave_back_rows") or 0) for r in paper_rows),
        "one_shot_candidates": sum(1 for r in records if r.get("tactical_class") == "one_shot_candidate"),
        "cost_bound_maker_unlock": sum(1 for r in records if r.get("cost_class") == "cost_bound_maker_unlock"),
        "positive": len(positive_setups(records)),
        "recovered": len(recovered_setups(records)),
        "statistical_candidates": len(statistical_candidates(records)),
        "tactical": len(tactical_setups(records)),
        "rejected_research": len(rejected_research(records)),
        "confirmed_bad": len(confirmed_bad_setups(records)),
        "needs_data": len(needs_data_setups(records)),
        "revalidated": sum(1 for r in records if r.get("revalidation_status")),
        "revalidation_survivors": sum(
            1 for r in records if r.get("revalidation_status") == "PAPER_FORWARD_READY"),
        # Invariant guard: paper-forward-ready is allowed ONLY behind a hard PAPER_FORWARD_READY
        # verdict. A non-zero count means a research/tactical label leaked paper access — a bug.
        "paper_ready_without_hard_pass": sum(
            1 for r in records if r["paper_forward_ready"] and r.get("hard_status") != "PAPER_FORWARD_READY"),
    }


def write_memory_snapshot(private_root: Path) -> Path:
    """Write the derived, rebuildable read-model (NOT a source of truth, never gates money)."""
    records = build_memory_index(private_root)
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "setup_outcome_memory.json"
    payload = {
        "schema": "setup_outcome_memory.v1",
        "disclaimer": "Derived read-model rebuilt from canonical sources. Research-only: no "
                      "outcome here grants PAPER_FORWARD_READY or is a trade signal.",
        "summary": summarize_memory(records),
        "product_paper_memory": summarize_product_training_memory(private_root),
        "records": records,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="Setup Outcome Memory (derived read-model + gate).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true", help="write the derived snapshot artifact")
    args = ap.parse_args()
    records = build_memory_index(Path(args.private_root))
    print(json.dumps({
        **summarize_memory(records),
        "product_paper_memory": summarize_product_training_memory(Path(args.private_root))["summary"],
    }, ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_memory_snapshot(Path(args.private_root)))


if __name__ == "__main__":
    main()
