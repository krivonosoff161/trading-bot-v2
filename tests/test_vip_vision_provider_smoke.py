import asyncio

from scripts.strategy_lab.vip_vision_provider_smoke import run_smoke


def test_vip_vision_provider_smoke_status_only(tmp_path):
    args = type(
        "Args",
        (),
        {
            "private_root": tmp_path,
            "image": "",
            "category": "crypto",
            "apply": False,
        },
    )()
    summary = asyncio.run(run_smoke(args))

    assert summary["called_provider"] is False
    assert summary["paper_only"] is True
    assert summary["execution_allowed"] is False
    assert (tmp_path / "reports" / "provider_bench" / "vip_vision_provider_smoke.json").exists()
