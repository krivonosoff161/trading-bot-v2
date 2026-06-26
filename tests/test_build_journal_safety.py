import asyncio

import scripts.build_journal as journal


def test_fetch_positions_is_private_opt_in(monkeypatch):
    monkeypatch.delenv("JOURNAL_ENABLE_PRIVATE_FILLS", raising=False)
    monkeypatch.setenv("OKX_API_KEY", "present-but-must-not-be-used")

    def forbidden_client(*args, **kwargs):
        raise AssertionError("private OKX client must be opt-in for journal builds")

    monkeypatch.setattr(journal, "_create_okx_client", forbidden_client)

    assert asyncio.run(journal._fetch_positions_async()) == []
