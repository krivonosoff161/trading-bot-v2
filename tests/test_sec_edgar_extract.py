# -*- coding: utf-8 -*-
"""
test_sec_edgar_extract.py — экстракция machine_doc из SEC-филинга (фикстуры, БЕЗ сети).

Аудит 11.06: все sec_edgar доки были title_only (21-43 символа), AVAX Grayscale 8-K
chief решал по заголовку. Контракт: index → primary doc → текст + метаданные;
малформ/нет primary → None (честный фолбэк title_only).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources import sec_edgar as SEC  # noqa: E402
from src.scout import news_buffer as NB  # noqa: E402

INDEX_URL = ("https://www.sec.gov/Archives/edgar/data/2035053/000203505326000006/"
             "0002035053-26-000006-index.htm")

INDEX_HTML = """
<html><body>
<div id="formHeader"><strong>Form 8-K</strong> - Current report</div>
<div class="formContent">
  <div class="formGrouping">
    <div class="infoHead">Filing Date</div>
    <div class="info">2026-06-10</div>
  </div>
</div>
<span class="companyName">Grayscale Avalanche Trust (AVAX) (Filer)</span>
<table class="tableFile" summary="Document Format Files">
  <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
  <tr><td>1</td><td>8-K Current Report</td>
      <td><a href="/Archives/edgar/data/2035053/000203505326000006/avax8k.htm">avax8k.htm</a></td>
      <td>8-K</td><td>53017</td></tr>
  <tr><td>2</td><td>Press Release</td>
      <td><a href="/Archives/edgar/data/2035053/000203505326000006/ex991.htm">ex991.htm</a></td>
      <td>EX-99.1</td><td>21001</td></tr>
  <tr><td>3</td><td>Graphic</td>
      <td><a href="/Archives/edgar/data/2035053/000203505326000006/logo.jpg">logo.jpg</a></td>
      <td>GRAPHIC</td><td>9001</td></tr>
</table>
</body></html>
"""

PRIMARY_HTML = """
<html><head><style>.x{color:red}</style><script>var a=1;</script></head><body>
<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</p>
<p>FORM 8-K — CURRENT REPORT</p>
<p>Grayscale Avalanche Trust (AVAX)</p>
<p>Item 8.01 Other Events.</p>
<p>On June 10, 2026, Grayscale Investments announced its intent to convert the Trust into
an exchange-traded product with native AVAX staking, subject to regulatory approval.
The Trust held approximately 1,000,000 AVAX as of the date of this report.</p>
<p>Item 9.01 Financial Statements and Exhibits.</p>
<p>Exhibit 99.1 — Press release dated June 10, 2026.</p>
</body></html>
""" * 3   # тело > 500 символов


def _fetch_ok(url: str):
    if url == INDEX_URL:
        return INDEX_HTML
    if url.endswith("avax8k.htm"):
        return PRIMARY_HTML
    return None


def test_extract_filing_primary_doc_and_metadata():
    out = SEC.extract_filing(INDEX_URL, fetch=_fetch_ok)
    assert out and out["method"] == "sec_primary_doc"
    assert "Grayscale Investments announced" in out["text"]
    assert "var a=1" not in out["text"]                      # script/style вычищены
    m = out["metadata"]
    assert m["cik"] == "2035053"
    assert m["accession"] == "0002035053-26-000006"
    assert m["company_name"].startswith("Grayscale Avalanche Trust")
    assert m["form_type"] == "8-K"
    assert m["filed_at"] == "2026-06-10"
    assert m["primary_document_url"].endswith("avax8k.htm")
    assert m["exhibits"] and m["exhibits"][0]["type"] == "EX-99.1"
    assert "8.01" in m["items"] and "9.01" in m["items"]
    assert out["title"] == "SEC 8-K: Grayscale Avalanche Trust"   # имя до первой скобки


def test_malformed_index_falls_back_to_none():
    assert SEC.extract_filing(INDEX_URL, fetch=lambda u: "<html>no table here</html>") is None


def test_no_primary_document_falls_back_to_none():
    only_graphics = INDEX_HTML.replace("avax8k.htm", "avax8k.jpg").replace(">8-K<", ">GRAPHIC<")
    assert SEC.extract_filing(INDEX_URL, fetch=lambda u: only_graphics
                              if u == INDEX_URL else None) is None


def test_primary_fetch_failure_falls_back_to_none():
    assert SEC.extract_filing(INDEX_URL,
                              fetch=lambda u: INDEX_HTML if u == INDEX_URL else None) is None


def test_inline_xbrl_viewer_link_unwrapped():
    ix_html = INDEX_HTML.replace(
        'href="/Archives/edgar/data/2035053/000203505326000006/avax8k.htm"',
        'href="/ix?doc=/Archives/edgar/data/2035053/000203505326000006/avax8k.htm"')
    out = SEC.extract_filing(INDEX_URL,
                             fetch=lambda u: ix_html if u == INDEX_URL else _fetch_ok(u))
    assert out and out["metadata"]["primary_document_url"].endswith("avax8k.htm")


def test_sec_reextract_dry_run_and_apply(tmp_path):
    db = tmp_path / "nb_sec.sqlite"
    item = {"title": "SEC 8-K: Grayscale Avalanche Staking ETF", "url": INDEX_URL,
            "time": "2026-06-10T18:00:00Z", "source": "sec_edgar", "source_class": "api",
            "lead_class": "LEADING", "asset": "AVAX", "okx_inst": "AVAX-USDT-SWAP",
            "layer": 5, "baseline": "BTC-USDT-SWAP", "phase": "REALIZED",
            "event_type": "filing_8-k"}
    NB.ingest_items([item], path=db)
    NB.resolve_pending(limit=10, path=db, dry=True)          # dry → title_only

    dry = NB.sec_reextract(limit=10, path=db, apply=False, fetch=_fetch_ok)
    assert dry["candidates"] == 1 and dry["extracted"] == 1 and not dry["applied"]
    import sqlite3
    con = sqlite3.connect(db)
    method = con.execute("select extraction_method from machine_docs").fetchone()[0]
    assert method == "title_only"                            # dry-run ничего не записал

    res = NB.sec_reextract(limit=10, path=db, apply=True, fetch=_fetch_ok)
    assert res["extracted"] == 1 and res["applied"]
    row = con.execute("select extraction_method, text_len, metadata_json from machine_docs").fetchone()
    assert row[0] == "sec_primary_doc" and row[1] > 500
    assert "sec_filing" in row[2] and "0002035053-26-000006" in row[2]

    # повторный прогон не трогает хороший док
    again = NB.sec_reextract(limit=10, path=db, apply=True, fetch=_fetch_ok)
    assert again["skipped_good"] == 1 and again["candidates"] == 0
