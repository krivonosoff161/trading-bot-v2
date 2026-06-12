# -*- coding: utf-8 -*-
"""Local read-only dashboard server for Strategy Research Lab."""

from __future__ import annotations

import argparse
import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from src.research_lab.dashboard_state import DEFAULT_PRIVATE_ROOT, load_dashboard_state


class DashboardHandler(BaseHTTPRequestHandler):
    private_root: Path = DEFAULT_PRIVATE_ROOT

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        if not self._valid_host():
            self.send_error(HTTPStatus.FORBIDDEN, "dashboard accepts localhost host headers only")
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            state = load_dashboard_state(self.private_root)
            self._send_text(render_html(state), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/state":
            state = load_dashboard_state(self.private_root)
            self._send_text(json.dumps(state, ensure_ascii=False, indent=2), "application/json; charset=utf-8")
            return
        if parsed.path == "/health":
            self._send_text("OK\n", "text/plain; charset=utf-8")
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "dashboard is read-only")

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"dashboard: {self.address_string()} - {fmt % args}")

    def _send_text(self, body: str, content_type: str) -> None:
        data = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _valid_host(self) -> bool:
        host = (self.headers.get("Host") or "").split(":", 1)[0].strip().lower()
        return host in {"", "127.0.0.1", "localhost"}


def run_server(host: str = "127.0.0.1", port: int = 8765, private_root: Path = DEFAULT_PRIVATE_ROOT) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("dashboard must bind to 127.0.0.1/localhost unless auth is implemented")
    handler = type("LocalDashboardHandler", (DashboardHandler,), {"private_root": private_root})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Strategy Research Lab dashboard: http://{host}:{port}")
    print("private root label: strategy-lab")
    server.serve_forever()


def render_html(state: dict) -> str:
    latest = state.get("latest_run") or {}
    totals = state.get("totals") or {}
    llm = state.get("llm_cost") or {}
    runs = state.get("runs") or []
    state_db = state.get("state_db") or {}
    queue_counts = state_db.get("queue_counts") or {}
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Strategy Research Lab</title>
  <style>
    :root {{ color-scheme: dark; --bg:#0f1115; --panel:#171b22; --line:#2a313c; --text:#e8edf2; --muted:#9ba7b4; --accent:#62b6ff; --ok:#4dd18b; --warn:#f0c05a; --bad:#ff7575; }}
    body {{ margin:0; font-family: Segoe UI, Arial, sans-serif; background:var(--bg); color:var(--text); }}
    header {{ padding:22px 28px; border-bottom:1px solid var(--line); background:#11161d; }}
    h1 {{ margin:0 0 6px; font-size:24px; }}
    main {{ padding:22px 28px 36px; max-width:1280px; }}
    .muted {{ color:var(--muted); }}
    .grid {{ display:grid; grid-template-columns: repeat(4, minmax(160px,1fr)); gap:14px; margin:18px 0; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }}
    .metric {{ font-size:28px; font-weight:700; margin-top:6px; }}
    .ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .bad {{ color:var(--bad); }}
    table {{ width:100%; border-collapse:collapse; margin-top:12px; }}
    th,td {{ border-bottom:1px solid var(--line); padding:9px 8px; text-align:left; vertical-align:top; }}
    th {{ color:var(--muted); font-weight:600; }}
    code {{ background:#0b0d11; border:1px solid var(--line); padding:2px 5px; border-radius:4px; }}
    .section {{ margin-top:24px; }}
    .pill {{ display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid var(--line); color:var(--muted); }}
    .path {{ word-break:break-all; font-size:13px; color:var(--muted); }}
  </style>
</head>
<body>
<header>
  <h1>Strategy Research Lab</h1>
  <div class="muted">Local read-only dashboard - bound to 127.0.0.1 - no shell - no secrets - no live trading</div>
</header>
<main>
  <div class="grid">
    {metric_card("Runs", totals.get("run_count", 0))}
    {metric_card("Candidates", totals.get("candidate_count", 0))}
    {metric_card("Promoted", (totals.get("decision_counts") or {}).get("PROMOTE_FOR_PRESSURE_TEST", 0), "ok")}
    {metric_card("Queued", queue_counts.get("queued", 0), "warn")}
  </div>

  <section class="card">
    <h2>Latest Run</h2>
    {latest_run_html(latest)}
  </section>

  <section class="section card">
    <h2>Recent Runs</h2>
    {runs_table(runs[:12])}
  </section>

  <section class="section card">
    <h2>Queue</h2>
    {queue_table((state_db.get("queue") or [])[:20])}
  </section>

  <section class="section card">
    <h2>Latest Candidates</h2>
    {candidates_table((latest.get("top_candidates") or [])[:20])}
  </section>

  <section class="section card">
    <h2>LLM Cost Guard</h2>
    <p>today: {esc(llm.get("today_rub", 0))} RUB / {esc(llm.get("today_tokens", 0))} tokens</p>
    <p>total: {esc(llm.get("total_rub", 0))} RUB / {esc(llm.get("total_tokens", 0))} tokens</p>
    <p class="path">budget log: {esc(llm.get("log_label", ""))}</p>
  </section>

  <section class="section card">
    <h2>Paths</h2>
    <p>Private root label:</p>
    <div class="path">{esc(state.get("private_root_label", ""))}</div>
    <p>Obsidian vault:</p>
    <div class="path">{esc(state.get("obsidian_vault_label", ""))}</div>
    <p>State DB:</p>
    <div class="path">{esc(state_db.get("db_label", "not initialized"))}</div>
  </section>
</main>
</body>
</html>"""


def metric_card(label: str, value: object, cls: str = "") -> str:
    klass = f"metric {cls}".strip()
    return f'<div class="card"><div class="muted">{esc(label)}</div><div class="{klass}">{esc(value)}</div></div>'


def latest_run_html(run: dict) -> str:
    if not run:
        return '<p class="muted">No completed runs found.</p>'
    counts = run.get("counts") or {}
    return "\n".join(
        [
            f"<p><strong>{esc(run.get('run_id', ''))}</strong> <span class=\"pill\">{esc(run.get('experiment_id', ''))}</span></p>",
            f"<p>candidates: {esc(run.get('candidate_count', 0))} - promote: {esc(counts.get('PROMOTE_FOR_PRESSURE_TEST', 0))} - observe: {esc(counts.get('OBSERVE', 0))} - reject: {esc(counts.get('REJECT', 0))}</p>",
            f"<p class=\"path\">summary: {esc(run.get('summary_label', ''))}</p>",
            f"<p class=\"path\">llm review prompt: {esc(run.get('llm_review_prompt_label', ''))}</p>",
        ]
    )


def runs_table(runs: list[dict]) -> str:
    if not runs:
        return '<p class="muted">No runs yet.</p>'
    rows = ["<table><thead><tr><th>Run</th><th>Experiment</th><th>Candidates</th><th>Decisions</th><th>Path</th></tr></thead><tbody>"]
    for run in runs:
        counts = run.get("counts") or {}
        rows.append(
            "<tr>"
            f"<td>{esc(run.get('run_id', ''))}</td>"
            f"<td>{esc(run.get('experiment_id', ''))}</td>"
            f"<td>{esc(run.get('candidate_count', 0))}</td>"
            f"<td>P:{esc(counts.get('PROMOTE_FOR_PRESSURE_TEST', 0))} O:{esc(counts.get('OBSERVE', 0))} R:{esc(counts.get('REJECT', 0))}</td>"
            f"<td class=\"path\">{esc(run.get('artifact_label', ''))}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def queue_table(rows_in: list[dict]) -> str:
    if not rows_in:
        return '<p class="muted">No queued jobs yet.</p>'
    rows = ["<table><thead><tr><th>ID</th><th>Status</th><th>Priority</th><th>Spec</th><th>Attempts</th><th>Run</th><th>Error</th></tr></thead><tbody>"]
    for row in rows_in:
        rows.append(
            "<tr>"
            f"<td>{esc(row.get('job_id', ''))}</td>"
            f"<td>{esc(row.get('status', ''))}</td>"
            f"<td>{esc(row.get('priority', ''))}</td>"
            f"<td class=\"path\">{esc(row.get('spec_label', ''))}</td>"
            f"<td>{esc(row.get('attempts', ''))}</td>"
            f"<td class=\"path\">{esc(row.get('run_dir_label', '') or '')}</td>"
            f"<td>{esc(row.get('last_error', '') or '')}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)
def candidates_table(candidates: list[dict]) -> str:
    if not candidates:
        return '<p class="muted">No candidates in latest run.</p>'
    rows = ["<table><thead><tr><th>Run ID</th><th>Symbol</th><th>Family</th><th>Decision</th><th>Avg</th><th>OOS</th><th>PF</th><th>Reasons</th></tr></thead><tbody>"]
    for c in candidates:
        rows.append(
            "<tr>"
            f"<td>{esc(c.get('run_id', ''))}</td>"
            f"<td>{esc(c.get('symbol', ''))}</td>"
            f"<td>{esc(c.get('family', ''))}</td>"
            f"<td>{esc(c.get('decision', ''))}</td>"
            f"<td>{esc(c.get('avg_net_pct', ''))}</td>"
            f"<td>{esc(c.get('test_avg_net_pct', ''))}</td>"
            f"<td>{esc(c.get('profit_factor', ''))}</td>"
            f"<td>{esc(c.get('reasons', ''))}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--private-root", default=str(DEFAULT_PRIVATE_ROOT))
    args = ap.parse_args()
    run_server(args.host, args.port, Path(args.private_root))


if __name__ == "__main__":
    main()
