from scripts.strategy_lab.agent_role_provider_bench import run_bench


class Args:
    private_root = None
    provider = "ollama"
    base_url = ""
    api_key_env = "MISSING_KEY"
    model = ""
    timeout = 1.0
    rate_rub_per_1k = 0.5
    roles = "outcome_reviewer"
    max_cases = 1


def test_provider_bench_writes_summary_with_disabled_provider(tmp_path):
    args = Args()
    args.private_root = tmp_path
    args.provider = "alibaba"
    summary = run_bench(args)

    assert summary["schema"] == "ProviderBenchSummary.v1"
    assert summary["configured"] is False
    assert summary["rows"] == 1
    assert summary["accepted"] == 0
    assert summary["execution_allowed"] is False
    assert (tmp_path / "reports" / "provider_bench" / "agent_role_provider_bench_summary.json").exists()
