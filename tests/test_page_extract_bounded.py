from types import SimpleNamespace

from src.scout import page_extract


def test_page_extract_applies_bounded_download_and_extraction_timeout(
    monkeypatch,
) -> None:
    observed = {}

    def fetch_url(_url, *, config):
        observed["download"] = config["DEFAULT"]["DOWNLOAD_TIMEOUT"]
        observed["extraction"] = config["DEFAULT"]["EXTRACTION_TIMEOUT"]
        return None

    fake = SimpleNamespace(
        settings=SimpleNamespace(
            DEFAULT_CONFIG={"DEFAULT": {"DOWNLOAD_TIMEOUT": "30"}}
        ),
        fetch_url=fetch_url,
    )
    monkeypatch.setitem(__import__("sys").modules, "trafilatura", fake)

    assert page_extract.extract(
        "https://example.com/synthetic", timeout_seconds=2.1
    ) is None
    assert observed == {"download": "3", "extraction": "3"}
