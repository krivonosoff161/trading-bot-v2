from src.scout import chief_usage_report as R


def _reasoning(card_id: str, ts: str | None, gate: str = "CHEAP_WATCH") -> dict:
    row = {
        "card_id": card_id,
        "orchestrator": {"chief_called": True, "escalation_gate": gate, "final_verdict": "NO_GO"},
        "usage": [{"role": "cheap", "total_tokens": 10}, {"role": "chief", "total_tokens": 20}],
    }
    if ts:
        row["recorded_at"] = ts
    return row


def test_since_filter_uses_real_timestamps_not_card_id():
    # Hex-like card_id sorts after "2026..." lexically; old implementation leaked it through.
    rows = [
        _reasoning("e9fe632135e8c602", None, "LEGACY_WITHOUT_TS"),
        _reasoning("old", "2026-06-10T00:00:00Z", "OLD"),
        _reasoning("new", "2026-06-12T00:00:00Z", "NEW"),
    ]
    rep = R.summarize(reasoning_rows=rows, journal_rows=[], since="2026-06-11T00:00:00Z")
    assert rep["cards"] == 1
    assert rep["chief_calls_by_gate"] == {"NEW": 1}


def test_since_filter_accepts_journal_ts_utc_for_source_join():
    rows = [_reasoning("new", "2026-06-12T00:00:00Z", "NEW")]
    journal = [{"card_id": "new", "ts_utc": "2026-06-12T00:00:01Z", "source": "sec_edgar", "layer": 5}]
    rep = R.summarize(reasoning_rows=rows, journal_rows=journal, since="2026-06-11T00:00:00Z")
    assert rep["chief_by_source"] == {"sec_edgar": 1}
    assert rep["chief_by_layer"] == {"5": 1}
