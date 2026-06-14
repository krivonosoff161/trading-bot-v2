# -*- coding: utf-8 -*-
"""Local read-only dashboard server for Strategy Research Lab."""

from __future__ import annotations

import argparse
import html
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from src.research_lab.dashboard_state import DEFAULT_PRIVATE_ROOT, load_dashboard_state


def default_private_root() -> Path:
    """Return the dashboard private root, honoring the same env var as workers."""
    raw = os.getenv("TRADING_BOT_RESEARCH_ROOT", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_PRIVATE_ROOT


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
    validation_counts = state_db.get("validation_counts") or totals.get("validation_counts") or {}
    registry = state.get("candidate_registry") or {}
    lab_config = state.get("lab_config") or {}
    worker_status = state.get("worker_status") or {}
    llm_review = state.get("llm_review") or {}
    next_run = state.get("next_run") or {}
    obsidian_notes = state.get("obsidian_notes", 0)
    proposals = state.get("proposals") or {}
    event_microscope = state.get("event_microscope") or {}
    data_prep = state.get("last_prepare_1m") or {}
    market_prep = state.get("last_prepare_market_data") or {}
    prepare_workflow = state.get("prepare_workflow") or {}
    last_cycle = state.get("last_cycle") or {}
    last_session = state.get("last_session") or {}
    last_loop = state.get("last_loop") or {}
    llm_loop = state.get("llm_loop") or {}
    queue_capacity = state.get("queue_capacity") or {}
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

  <div class="grid">
    {metric_card("Forward paper", validation_counts.get("FORWARD_PAPER", 0), "ok")}
    {metric_card("Regime specific", validation_counts.get("REGIME_SPECIFIC", 0), "warn")}
    {metric_card("Observe", validation_counts.get("OBSERVE", 0))}
    {metric_card("Reject", validation_counts.get("REJECT", 0), "bad")}
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
    <h2>Worker &amp; Queue Health</h2>
    {worker_health_html(queue_counts, worker_status, llm_review)}
  </section>

  <section class="section card">
    <h2>Research Summary</h2>
    {research_summary_html(latest, next_run, obsidian_notes)}
  </section>

  <section class="section card">
    <h2>Proposals (closed loop)</h2>
    {proposals_html(proposals, llm_review, queue_capacity)}
  </section>

  <section class="section card">
    <h2>Research Cycle &amp; Session</h2>
    {cycle_html(last_cycle, last_session, llm_loop, last_loop)}
  </section>

  <section class="section card">
    <h2>Event Microscope (1m)</h2>
    {microscope_html(event_microscope, data_prep, prepare_workflow, market_prep)}
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
    <h2>Candidate Registry</h2>
    {registry_html(registry)}
  </section>

  <section class="section card">
    <h2>Research Machine Config</h2>
    {lab_config_html(lab_config)}
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
    <p>Obsidian candidate graph:</p>
    <div class="path">{esc(state.get("obsidian_graph_label", ""))}</div>
    <p>Obsidian run vault:</p>
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
    rows = ["<table><thead><tr><th>Run ID</th><th>Symbol</th><th>Family</th><th>Decision</th><th>Validation</th><th>Avg</th><th>OOS</th><th>PF</th><th>Reasons</th></tr></thead><tbody>"]
    for c in candidates:
        rows.append(
            "<tr>"
            f"<td>{esc(c.get('run_id', ''))}</td>"
            f"<td>{esc(c.get('symbol', ''))}</td>"
            f"<td>{esc(c.get('family', ''))}</td>"
            f"<td>{esc(c.get('decision', ''))}</td>"
            f"<td>{esc(c.get('validation_status', ''))}</td>"
            f"<td>{esc(c.get('avg_net_pct', ''))}</td>"
            f"<td>{esc(c.get('test_avg_net_pct', ''))}</td>"
            f"<td>{esc(c.get('profit_factor', ''))}</td>"
            f"<td>{esc(c.get('reasons', ''))}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def registry_html(registry: dict) -> str:
    if not registry.get("exists"):
        return '<p class="muted">Candidate registry not created yet.</p>'
    by_status = registry.get("by_validation_status") or {}
    statuses = " - ".join(f"{esc(k)}: {esc(v)}" for k, v in sorted(by_status.items())) or "empty"
    return "\n".join(
        [
            f"<p>entries: {esc(registry.get('entries', 0))}</p>",
            f"<p>{statuses}</p>",
            f"<p class=\"path\">registry: {esc(registry.get('registry_label', ''))}</p>",
        ]
    )


def proposals_html(proposals: dict, llm_review: dict, queue_capacity: dict | None = None) -> str:
    if not proposals:
        return '<p class="muted">No proposals generated yet.</p>'
    by_status = proposals.get("by_status") or {}
    status_line = " - ".join(f"{esc(k)}: {esc(v)}" for k, v in sorted(by_status.items())) or "none"
    rows = ["<table><thead><tr><th>Proposal</th><th>Status</th><th>Reasons</th></tr></thead><tbody>"]
    for item in (proposals.get("latest_reasons") or [])[:5]:
        rows.append(
            "<tr>"
            f"<td>{esc(item.get('id', ''))}</td>"
            f"<td>{esc(item.get('status', ''))}</td>"
            f"<td>{esc(', '.join(item.get('reasons') or []))}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    table = "\n".join(rows) if (proposals.get("latest_reasons")) else '<p class="muted">No proposals yet.</p>'
    auto_send = "disabled" if not llm_review.get("auto_send", False) else "ENABLED"
    cap = queue_capacity or {}
    queue_line = ""
    if cap:
        full = "FULL" if cap.get("full") else "ok"
        queue_line = (
            f"<p>queue capacity: {esc(cap.get('queued', 0))}/{esc(cap.get('max_queue_size', 0))} "
            f"<span class=\"pill\">{esc(full)}</span> "
            f"(new proposals are skipped when full)</p>"
        )
    return "\n".join([
        f"<p>total: {esc(proposals.get('total', 0))} - {status_line}</p>",
        f"<p>validated waiting for queue: {esc(proposals.get('validated_waiting', 0))} - "
        f"queued from proposals: {esc(proposals.get('queued_from_proposals', 0))}</p>",
        queue_line,
        f"<p>LLM auto-send: <span class=\"pill\">{esc(auto_send)}</span> - "
        f"queue requires explicit apply: <span class=\"pill\">yes</span></p>",
        table,
    ])


def cycle_html(cycle: dict, session: dict | None = None, llm_loop: dict | None = None,
               loop: dict | None = None) -> str:
    session = session or {}
    llm_loop = llm_loop or {}
    loop = loop or {}
    lines: list[str] = []
    if cycle and cycle.get("available"):
        lines += [
            f"<p>last cycle: <span class=\"pill\">{esc(cycle.get('mode', ''))}</span> "
            f"provider={esc(cycle.get('provider', 'null'))} at {esc(cycle.get('generated_at', ''))}</p>",
            f"<p>proposals generated: {esc(cycle.get('proposals_generated', 0))} - "
            f"queued: {esc(cycle.get('proposals_queued', 0))} - "
            f"data missing: {esc(cycle.get('data_missing', 0))} - prepared: {esc(cycle.get('data_prepared', 0))}</p>",
            f"<p>worker: completed {esc(cycle.get('worker_completed', 0))} - "
            f"deferred {esc(cycle.get('worker_deferred', 0))} - failed {esc(cycle.get('worker_failed', 0))}</p>",
        ]
    else:
        lines.append('<p class="muted">No research cycle run yet '
                     '(python -m scripts.strategy_lab.research_cycle --dry-run).</p>')
    if session.get("available"):
        lines.append(
            f"<p>last session: <span class=\"pill\">{esc(session.get('mode', ''))}</span> "
            f"ready={esc(session.get('ready_jobs', 0))} - missing data={esc(session.get('skipped_missing_data', 0))} - "
            f"queued={esc(session.get('proposals_queued', 0))}</p>"
        )
    else:
        lines.append('<p class="muted">No research session run yet '
                     '(python -m scripts.strategy_lab.research_session --dry-run).</p>')
    if loop.get("available"):
        lw = loop.get("last_worker") or {}
        lines.append(
            f"<p>last loop: <span class=\"pill\">{esc(loop.get('mode', ''))}</span> "
            f"{esc(loop.get('iterations', 0))} iters / {esc(loop.get('duration_minutes', 0))} min - "
            f"queued={esc(loop.get('proposals_queued', 0))} - missing data={esc(loop.get('skipped_missing_data', 0))} - "
            f"worker done/deferred={esc(lw.get('completed', 0))}/{esc(lw.get('deferred', 0))} - "
            f"LLM validated={esc(loop.get('llm_validated', 0))} (last {esc(loop.get('last_llm_status', 'n/a'))})</p>"
        )
    spend = llm_loop.get("today_spend") or {}
    lines.append(
        f"<p>LLM proposal loop: <span class=\"pill\">{esc(llm_loop.get('mode', 'disabled'))}</span> "
        f"send={'enabled' if llm_loop.get('enabled') else 'disabled'} - provider={esc(llm_loop.get('provider', 'none'))} "
        f"(advisory; code validates; LLM never executed)</p>"
    )
    lines.append(
        f"<p>LLM spend today (lab-private): {esc(spend.get('requests', 0))} req - "
        f"{esc(spend.get('tokens', 0))} tok - {esc(spend.get('cost_rub', 0.0))} RUB - "
        f"daily cap {'set' if llm_loop.get('daily_cap_present') else 'none'}</p>"
    )
    lines.append(f"<p class=\"muted\">next: {esc((loop.get('next_command') or session.get('next_command') or cycle.get('next_command', '')))}</p>")
    lines.append('<p class="muted">no live trading - no order engine - no paid LLM by default - network fetch is opt-in</p>')
    return "\n".join(lines)


def microscope_html(
    em: dict,
    data_prep: dict | None = None,
    prepare_workflow: dict | None = None,
    market_prep: dict | None = None,
) -> str:
    if not em or em.get("error"):
        return '<p class="muted">Event microscope not available.</p>'
    limits = em.get("limits") or {}
    enabled = bool(em.get("enabled"))
    state = "enabled" if enabled else f"disabled ({esc(em.get('disabled_reason', ''))})"
    counts = em.get("availability_counts") or {}
    counts_line = " - ".join(f"{esc(k)}: {esc(v)}" for k, v in sorted(counts.items())) or "no symbols scanned"
    skipped = em.get("skipped_reasons") or []
    skipped_line = ", ".join(f"{esc(s.get('symbol'))}: {esc(s.get('reason'))}" for s in skipped[:6]) or "none"
    prep = data_prep or {}
    if prep.get("available"):
        prep_line = (
            f"<p>1m data prep: last <span class=\"pill\">{esc(prep.get('mode', ''))}</span> via "
            f"{esc(prep.get('provider', 'null'))} provider - missing: {esc(prep.get('missing', 0))} - "
            f"downloaded: {esc(prep.get('downloaded', 0))} (on demand; no full-market download)</p>"
        )
    else:
        prep_line = '<p class="muted">1m data prep: not run yet (prepared on demand, null provider by default)</p>'
    pw = prepare_workflow or {}
    if pw.get("enabled"):
        auto_line = (
            f"<p>auto-prepare on start: <span class=\"pill\">{esc(pw.get('mode', ''))}</span> "
            f"provider={esc(pw.get('provider', 'null'))} - network fetch: "
            f"<span class=\"pill\">{'yes' if pw.get('will_fetch_network') else 'no'}</span></p>"
        )
    else:
        auto_line = ('<p class="muted">auto-prepare on start: disabled '
                     '(default; no network fetch — set STRATEGY_LAB_PREPARE_1M=1 to enable)</p>')
    prep_market = market_prep or {}
    market_parts = []
    for tf in ("15m", "1h", "4h", "1d"):
        item = prep_market.get(tf) or {}
        if item.get("available"):
            market_parts.append(
                f"{esc(tf)}:<span class=\"pill\">{esc(item.get('mode', ''))}</span> "
                f"{esc(item.get('provider', 'null'))} dl={esc(item.get('downloaded', 0))}"
            )
        else:
            market_parts.append(f"{esc(tf)}:<span class=\"pill\">not run</span>")
    market_line = "<p>market-data prep: " + " - ".join(market_parts) + "</p>"
    return "\n".join([
        f"<p>1m microscope: <span class=\"pill\">{state}</span> "
        f"(trigger-only; no downloader; full-universe 1m sweeps blocked)</p>",
        f"<p>caps: symbols&le;{esc(limits.get('max_symbols', 0))} - "
        f"event windows&le;{esc(limits.get('max_event_windows', 0))} - "
        f"bars/window&le;{esc(limits.get('max_bars_per_window', 0))} - "
        f"variants&le;{esc(limits.get('max_variants', 0))}</p>",
        f"<p>data availability ({esc(em.get('scanned_group', ''))}): {counts_line}</p>",
        f"<p class=\"muted\">skipped: {skipped_line}</p>",
        prep_line,
        market_line,
        auto_line,
    ])


def research_summary_html(latest: dict, next_run: dict, obsidian_notes: object) -> str:
    verdicts = (latest or {}).get("reducer_verdicts") or {}
    verdict_line = " - ".join(f"{esc(k)}: {esc(v)}" for k, v in sorted(verdicts.items())) or "no reducer report yet"
    entry = (latest or {}).get("entry_timing") or {}
    if entry:
        entry_line = (
            f"capture: {esc(entry.get('avg_capture_ratio', 'n/a'))} - "
            f"MFE: {esc(entry.get('avg_mfe_pct', 'n/a'))}% - MAE: {esc(entry.get('avg_mae_pct', 'n/a'))}% - "
            f"late entries: {esc(entry.get('late_entry_rate', 'n/a'))}"
        )
    else:
        entry_line = '<span class="muted">no entry-timing aggregate yet</span>'
    if next_run.get("allowed"):
        next_line = 'next run: <span class="pill">allowed now</span>'
    elif next_run:
        next_line = (
            f"next run: <span class=\"pill\">deferred</span> {esc(next_run.get('reason', ''))} "
            f"(wait {esc(next_run.get('wait_seconds', 0))}s)"
        )
    else:
        next_line = 'next run: <span class="muted">unknown</span>'
    return "\n".join([
        f"<p>latest reducer verdicts: {verdict_line}</p>",
        f"<p>entry timing (latest run): {entry_line}</p>",
        f"<p>Obsidian candidate notes: {esc(obsidian_notes)}</p>",
        f"<p>{next_line}</p>",
    ])


def worker_health_html(queue_counts: dict, worker_status: dict, llm_review: dict) -> str:
    pending = queue_counts.get("queued", 0)
    queue_line = (
        f"pending: {esc(pending)} - running: {esc(queue_counts.get('running', 0))} - "
        f"completed: {esc(queue_counts.get('completed', 0))} - failed: {esc(queue_counts.get('failed', 0))}"
    )
    if worker_status:
        status = worker_status.get("status", "unknown")
        reason = worker_status.get("reason") or worker_status.get("run_label") or ""
        extra = f" - {esc(reason)}" if reason else ""
        worker_line = f"last worker: <span class=\"pill\">{esc(status)}</span>{extra} at {esc(worker_status.get('updated_at', ''))}"
    else:
        worker_line = 'last worker: <span class="muted">no run recorded yet</span>'
    llm_enabled = bool(llm_review.get("enabled"))
    llm_line = (
        f"LLM review: <span class=\"pill\">{'enabled' if llm_enabled else 'disabled'}</span> - "
        f"{esc(llm_review.get('note', 'export-only'))}"
    )
    return "\n".join([f"<p>queue: {queue_line}</p>", f"<p>{worker_line}</p>", f"<p>{llm_line}</p>"])


def lab_config_html(cfg: dict) -> str:
    if not cfg:
        return '<p class="muted">Research-machine config not loaded.</p>'
    profiles = ", ".join(str(p) for p in (cfg.get("timeframe_profiles") or [])) or "none"
    return "\n".join(
        [
            f"<p>universe: {esc(cfg.get('universe_groups', 0))} groups / "
            f"{esc(cfg.get('universe_symbols', 0))} symbols</p>",
            f"<p>timeframe profiles: {esc(profiles)}</p>",
            f"<p>resource mode: <span class=\"pill\">{esc(cfg.get('resource_mode', 'unknown'))}</span> "
            f"workers: {esc(cfg.get('max_workers', 0))} - "
            f"heavy jobs: {esc(cfg.get('allow_heavy_jobs', False))} - "
            f"1m jobs: {esc(cfg.get('allow_1m_jobs', 'unknown'))}</p>",
            f"<p>proposal specs (private): {esc(cfg.get('proposal_specs', 0))}</p>",
        ]
    )


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--private-root", default=str(default_private_root()))
    args = ap.parse_args()
    run_server(args.host, args.port, Path(args.private_root))


if __name__ == "__main__":
    main()
