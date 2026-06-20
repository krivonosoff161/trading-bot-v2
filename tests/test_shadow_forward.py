# -*- coding: utf-8 -*-
"""Shadow-forward watch lane: a re-validation survivor is registered as a research-only
shadow_forward_candidate (never paper-ready), observed forward on NEW bars only, and has NO trading
path (AST-verified: no exchange/order/credential import or token)."""
import ast
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path  # noqa: E402
from src.research_lab.param_schemas import executable_exit_params  # noqa: E402
from src.research_lab.shadow_forward import (  # noqa: E402
    SHADOW_STATUS,
    ShadowCandidate,
    record_observation,
    register_revalidation_survivors,
    summarize_shadow,
)

_TF_MS = {"4h": 14400000}


def _candle_file(tmp_path, symbol, tf, n=80):
    d = tmp_path / "market_data" / tf
    d.mkdir(parents=True, exist_ok=True)
    step = _TF_MS[tf]
    rows = [{"ts": i * step, "date": "", "open": 100, "high": 101, "low": 99, "close": 100, "vol": 10.0}
            for i in range(n)]
    p = d / f"{symbol}_{rows[0]['ts']}_{rows[-1]['ts']}_{tf}.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    return p


def _seed(tmp_path):
    """A brain candidate (params_json) + a re-validation survivor snapshot pointing at it."""
    uc = "CBRS_USDT_SWAP::4h::mean_reversion_fade::ph::fp"
    db = FarmTasksDB(tasks_db_path(tmp_path))
    db.upsert_unique_candidate({
        "uc_key": uc, "symbol": "CBRS_USDT_SWAP", "timeframe": "4h", "family": "mean_reversion_fade",
        "params_hash": "ph", "data_fingerprint": "fp", "decision": "REJECT", "validation_status": "REJECT",
        "hard_status": "", "n_trades": 13, "avg_net_pct": -0.2, "candidate_id": "c1",
        "params": executable_exit_params("mean_reversion_fade")}, now=1.0)
    db.close()
    deriv = tmp_path / "state" / "derived"
    deriv.mkdir(parents=True, exist_ok=True)
    (deriv / "recyclable_revalidation.json").write_text(json.dumps({"summary": {"survivor_rows": [
        {"uc_key": uc, "symbol": "CBRS_USDT_SWAP", "timeframe": "4h", "family": "mean_reversion_fade",
         "n_trades": 13, "exit": "hold_long"}]}}), encoding="utf-8")
    return uc


class TestInvariant:
    def test_shadow_candidate_is_never_paper_ready(self):
        c = ShadowCandidate("k", "X", "4h", "mean_reversion_fade", "hold_long", {})
        assert c.paper_forward_ready is False and c.status == SHADOW_STATUS
        assert c.to_dict()["paper_forward_ready"] is False


class TestRegister:
    def test_register_survivor_research_only(self, tmp_path):
        uc = _seed(tmp_path)
        regd = register_revalidation_survivors(tmp_path)
        assert len(regd) == 1 and regd[0].uc_key == uc
        assert regd[0].recovered_exit == "hold_long" and regd[0].params  # params pulled from brain
        s = summarize_shadow(tmp_path)
        assert s["shadow_candidates"] == 1 and s["all_research_only"] is True

    def test_register_is_idempotent(self, tmp_path):
        _seed(tmp_path)
        register_revalidation_survivors(tmp_path)
        register_revalidation_survivors(tmp_path)
        assert summarize_shadow(tmp_path)["shadow_candidates"] == 1


class TestObservation:
    def test_observation_is_forward_only_and_not_paper_ready(self, tmp_path):
        uc = _seed(tmp_path)
        register_revalidation_survivors(tmp_path)
        _candle_file(tmp_path, "CBRS_USDT_SWAP", "4h", n=80)
        # after_ts past every bar -> zero forward bars/signals (honest: awaiting new data)
        late = record_observation(tmp_path, uc, after_ts=80 * _TF_MS["4h"])
        assert late["forward_bars"] == 0 and late["n_signals"] == 0
        assert late["paper_forward_ready"] is False and late["status"] == SHADOW_STATUS
        # after_ts early -> some forward bars are considered (mechanism runs, still no execution)
        early = record_observation(tmp_path, uc, after_ts=0)
        assert early["forward_bars"] >= 1 and early["paper_forward_ready"] is False

    def test_unregistered_is_skipped(self, tmp_path):
        assert record_observation(tmp_path, "ghost", after_ts=0)["skipped"] == "not_registered"


class TestNoTradingPath:
    _FORBIDDEN_IMPORTS = ("src.exchange", "src.exchange.okx_client", "scripts.auto_execute",
                          "src.utils.telegram", "src.config", "main")
    _FORBIDDEN_TOKENS = ("place_market_order", "place_order", "execute_signal", "set_leverage",
                         "AUTO_TRADE")

    def test_shadow_module_has_no_live_or_order_coupling(self):
        text = (_ROOT / "src" / "research_lab" / "shadow_forward.py").read_text(encoding="utf-8")
        tree = ast.parse(text)
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        for mod in mods:
            for bad in self._FORBIDDEN_IMPORTS:
                assert not (mod == bad or mod.startswith(bad + ".")), f"forbidden import {mod}"
        doc = ast.get_docstring(tree) or ""
        code = text.replace(doc, "", 1)
        for token in self._FORBIDDEN_TOKENS:
            assert token not in code, f"shadow_forward must not reference {token}"
