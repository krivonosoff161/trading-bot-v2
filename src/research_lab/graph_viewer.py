# -*- coding: utf-8 -*-
"""Private standalone HTML graph viewer for Strategy Lab candidates."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.research_lab.paths import resolve_private_root


STATUS_ORDER = ("FORWARD_PAPER", "REGIME_SPECIFIC", "OBSERVE", "REJECT", "UNKNOWN")
STATUS_COLOR = {
    "FORWARD_PAPER": "#16a34a",
    "REGIME_SPECIFIC": "#2563eb",
    "OBSERVE": "#f59e0b",
    "REJECT": "#ef4444",
    "UNKNOWN": "#64748b",
}
TYPE_COLOR = {
    "candidate": "#111827",
    "symbol": "#0f766e",
    "strategy": "#7c3aed",
    "verdict": "#334155",
    "reason": "#b45309",
    "regime": "#0369a1",
    "event": "#0f766e",
    "data_packet": "#2563eb",
    "feature_packet": "#7c3aed",
    "validation": "#b45309",
    "paper_signal": "#16a34a",
    "outcome": "#ef4444",
    "training": "#64748b",
}


def graph_viewer_dir(private_root: Path) -> Path:
    return private_root / "graph-viewer"


def build_graph_data(
    entries: list[dict[str, Any]], *, max_candidates: int = 350
) -> dict[str, Any]:
    """Build a compact graph from candidate registry entries.

    Nodes are intentionally typed for visual scanning:
    candidate -> symbol / strategy / verdict / regime / reasons.
    """
    selected = sorted(
        entries,
        key=lambda e: (
            STATUS_ORDER.index(str(e.get("validation_status") or "UNKNOWN"))
            if str(e.get("validation_status") or "UNKNOWN") in STATUS_ORDER
            else len(STATUS_ORDER),
            str(e.get("created_at") or ""),
        ),
    )[:max_candidates]

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    candidate_count = 0
    symbols: set[str] = set()
    strategies: set[str] = set()
    reasons_seen: set[str] = set()
    by_status: dict[str, int] = {}

    def add_node(node_id: str, label: str, kind: str, **extra: Any) -> None:
        if node_id in nodes:
            return
        nodes[node_id] = {
            "id": node_id,
            "label": label,
            "kind": kind,
            "color": extra.pop("color", TYPE_COLOR.get(kind, "#475569")),
            **extra,
        }

    def add_edge(source: str, target: str, relation: str, weight: float = 1.0) -> None:
        edges.append(
            {"source": source, "target": target, "relation": relation, "weight": weight}
        )

    for entry in selected:
        cid = str(entry.get("candidate_id") or "unknown")
        experiment_id = str(entry.get("experiment_id") or "")
        symbol = str(entry.get("symbol") or "unknown").upper()
        strategy = str(entry.get("strategy_id") or "unknown")
        status = str(entry.get("validation_status") or "UNKNOWN")
        if status not in STATUS_COLOR:
            status = "UNKNOWN"
        regime_summary = (
            raw_regime_summary
            if isinstance(raw_regime_summary := entry.get("regime_summary"), dict)
            else {}
        )
        regime = str(regime_summary.get("dominant_bucket") or "unclassified")
        reasons = [str(r) for r in (entry.get("validation_reasons") or []) if str(r)]
        metrics = (
            raw_metrics
            if isinstance(raw_metrics := entry.get("metrics_summary"), dict)
            else {}
        )

        candidate_node = f"candidate:{experiment_id}:{cid}"
        symbol_node = f"symbol:{symbol}"
        strategy_node = f"strategy:{strategy}"
        status_node = f"verdict:{status}"
        regime_node = f"regime:{regime}"

        by_status[status] = by_status.get(status, 0) + 1
        candidate_count += 1
        symbols.add(symbol)
        strategies.add(strategy)

        add_node(
            candidate_node,
            cid[:18],
            "candidate",
            color=STATUS_COLOR[status],
            symbol=symbol,
            strategy=strategy,
            status=status,
            regime=regime,
            artifact=entry.get("artifact_label") or "",
            next_action=entry.get("next_action") or "",
            next_review=entry.get("next_review") or "",
            n_trades=metrics.get("n_trades"),
            avg_net_pct=metrics.get("avg_net_pct"),
            total_net_pct=metrics.get("total_net_pct"),
            profit_factor=metrics.get("profit_factor"),
        )
        add_node(symbol_node, symbol, "symbol")
        add_node(strategy_node, strategy, "strategy")
        add_node(status_node, status, "verdict", color=STATUS_COLOR[status])
        add_node(regime_node, regime, "regime")

        add_edge(candidate_node, symbol_node, "symbol", 1.8)
        add_edge(candidate_node, strategy_node, "strategy", 1.5)
        add_edge(candidate_node, status_node, "verdict", 1.3)
        add_edge(candidate_node, regime_node, "regime", 1.0)

        for reason in reasons[:5]:
            reason_node = f"reason:{reason}"
            reasons_seen.add(reason)
            add_node(reason_node, reason, "reason")
            add_edge(candidate_node, reason_node, "reason", 0.7)

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "nodes": list(nodes.values()),
        "edges": edges,
        "summary": {
            "candidates": candidate_count,
            "symbols": len(symbols),
            "strategies": len(strategies),
            "reasons": len(reasons_seen),
            "by_status": {
                k: by_status.get(k, 0) for k in STATUS_ORDER if by_status.get(k, 0)
            },
        },
    }


def build_lineage_graph_data(
    rows: list[dict[str, Any]], *, max_links: int = 500
) -> dict[str, Any]:
    """Build a compact graph from LineageLink rows.

    Shape: asset -> event -> data packet -> feature packet -> candidate/setup ->
    validation -> paper signal -> outcome/training.
    """
    selected = rows[-max_links:]
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def add_node(node_id: str, label: str, kind: str, **extra: Any) -> None:
        if not node_id or node_id.endswith(":"):
            return
        if node_id in nodes:
            return
        nodes[node_id] = {
            "id": node_id,
            "label": label,
            "kind": kind,
            "color": extra.pop("color", TYPE_COLOR.get(kind, "#475569")),
            **extra,
        }

    def add_edge(source: str, target: str, relation: str, weight: float = 1.0) -> None:
        if source in nodes and target in nodes:
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "weight": weight,
                }
            )

    by_source: dict[str, int] = {}
    for row in selected:
        symbol = str(row.get("instrument") or row.get("symbol") or "unknown").upper()
        source = str(row.get("source") or "unknown")
        by_source[source] = by_source.get(source, 0) + 1
        event_id = str(row.get("scanner_event_id") or "")
        data_id = str(row.get("data_packet_id") or "")
        feature_id = str(row.get("feature_packet_id") or "")
        setup_id = str(row.get("setup_candidate_id") or "")
        validation_id = str(row.get("validation_id") or "")
        signal_id = str(row.get("paper_signal_id") or "")
        outcome_id = str(row.get("outcome_id") or "")
        training_id = str(row.get("training_row_id") or "")

        asset_node = f"asset:{symbol}"
        event_node = f"event:{event_id}"
        data_node = f"data_packet:{data_id}"
        feature_node = f"feature_packet:{feature_id}"
        setup_node = f"setup:{setup_id}"
        validation_node = f"validation:{validation_id}"
        signal_node = f"paper_signal:{signal_id}"
        outcome_node = f"outcome:{outcome_id}"
        training_node = f"training:{training_id}"

        add_node(asset_node, symbol, "symbol")
        add_node(event_node, event_id[:18], "event", source=source)
        add_node(data_node, data_id[:18], "data_packet", timeframe=row.get("timeframe"))
        add_node(
            feature_node,
            feature_id[:18],
            "feature_packet",
            family=row.get("setup_family"),
        )
        add_node(setup_node, setup_id[:18], "candidate")
        add_node(validation_node, validation_id[:18], "validation")
        add_node(signal_node, signal_id[:18], "paper_signal")
        add_node(outcome_node, outcome_id[:18], "outcome")
        add_node(training_node, training_id[:18], "training")

        add_edge(asset_node, event_node, "event", 1.8)
        add_edge(event_node, data_node, "data", 1.6)
        add_edge(data_node, feature_node, "features", 1.5)
        add_edge(feature_node, setup_node, "setup", 1.2)
        add_edge(setup_node, validation_node, "validation", 1.1)
        add_edge(validation_node, signal_node, "paper", 1.2)
        add_edge(feature_node, signal_node, "paper", 0.8)
        add_edge(signal_node, outcome_node, "outcome", 1.0)
        add_edge(outcome_node, training_node, "training", 1.0)

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "nodes": list(nodes.values()),
        "edges": edges,
        "summary": {
            "links": len(selected),
            "nodes": len(nodes),
            "edges": len(edges),
            "by_source": by_source,
            "paper_only": True,
            "execution_allowed": False,
        },
    }


def write_graph_viewer(
    private_root: Path,
    entries: list[dict[str, Any]],
    *,
    max_candidates: int = 350,
    allow_public_output: bool = False,
) -> dict[str, Any]:
    private_root = resolve_private_root(
        private_root, allow_public_output=allow_public_output
    )
    out_dir = graph_viewer_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build_graph_data(entries, max_candidates=max_candidates)
    html = _html(data)
    out_file = out_dir / "index.html"
    out_file.write_text(html, encoding="utf-8", newline="\n")
    return {
        "viewer_file": str(out_file),
        "viewer_label": "strategy-lab/graph-viewer/index.html",
        "nodes": len(data["nodes"]),
        "edges": len(data["edges"]),
        "candidates": data["summary"]["candidates"],
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_lineage_graph_viewer(
    private_root: Path,
    *,
    max_links: int = 500,
    allow_public_output: bool = False,
) -> dict[str, Any]:
    private_root = resolve_private_root(
        private_root, allow_public_output=allow_public_output
    )
    out_dir = graph_viewer_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _load_jsonl(private_root / "state" / "lineage" / "cycle_links.jsonl")
    data = build_lineage_graph_data(rows, max_links=max_links)
    out_file = out_dir / "lineage.html"
    out_file.write_text(_html(data), encoding="utf-8", newline="\n")
    return {
        "viewer_file": str(out_file),
        "viewer_label": "strategy-lab/graph-viewer/lineage.html",
        "nodes": len(data["nodes"]),
        "edges": len(data["edges"]),
        "links": data["summary"]["links"],
        "paper_only": True,
        "execution_allowed": False,
    }


def _json_for_script(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _html(data: dict[str, Any]) -> str:
    payload = _json_for_script(data)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Strategy Lab Graph Viewer</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --panel: #ffffff;
      --ink: #0f172a;
      --muted: #64748b;
      --line: #dbe3ef;
      --accent: #0f766e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.45 "Segoe UI", Arial, sans-serif;
      color: var(--ink);
      background: var(--bg);
    }}
    header {{
      height: 68px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 0 22px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }}
    h1 {{ margin: 0; font-size: 20px; letter-spacing: 0; }}
    .sub {{ color: var(--muted); font-size: 12px; margin-top: 3px; }}
    .toolbar {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    input, select, button {{
      height: 34px;
      border: 1px solid var(--line);
      background: white;
      color: var(--ink);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
    }}
    button {{
      cursor: pointer;
      background: #0f766e;
      color: white;
      border-color: #0f766e;
    }}
    main {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 330px;
      height: calc(100vh - 68px);
      min-height: 560px;
    }}
    #stage {{ position: relative; overflow: hidden; }}
    canvas {{ width: 100%; height: 100%; display: block; background: linear-gradient(180deg, #f8fafc, #eef6f4); }}
    aside {{
      border-left: 1px solid var(--line);
      background: var(--panel);
      padding: 16px;
      overflow: auto;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 16px;
    }}
    .stat {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fbfdff;
    }}
    .stat strong {{ display: block; font-size: 20px; }}
    .stat span {{ color: var(--muted); font-size: 12px; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      background: white;
      font-size: 12px;
    }}
    .dot {{ width: 10px; height: 10px; border-radius: 999px; display: inline-block; }}
    h2 {{ font-size: 15px; margin: 18px 0 8px; }}
    .details {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 12px;
      background: #fbfdff;
      min-height: 170px;
      word-break: break-word;
    }}
    .row {{ margin: 7px 0; }}
    .row b {{ color: #334155; }}
    .hint {{
      position: absolute;
      left: 16px;
      bottom: 14px;
      background: rgba(255,255,255,.9);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      color: var(--muted);
      font-size: 12px;
      pointer-events: none;
    }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; height: auto; }}
      #stage {{ height: 68vh; min-height: 520px; }}
      aside {{ border-left: 0; border-top: 1px solid var(--line); }}
      header {{ height: auto; min-height: 68px; align-items: flex-start; flex-direction: column; padding: 14px 16px; }}
      .toolbar {{ justify-content: flex-start; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Strategy Lab Graph Viewer</h1>
      <div class="sub">Private research graph. Research labels only, not trading advice. Generated: <span id="generated"></span></div>
    </div>
    <div class="toolbar">
      <input id="search" placeholder="Search symbol / strategy / reason" size="30">
      <select id="status">
        <option value="">All verdicts</option>
        <option value="FORWARD_PAPER">FORWARD_PAPER</option>
        <option value="REGIME_SPECIFIC">REGIME_SPECIFIC</option>
        <option value="OBSERVE">OBSERVE</option>
        <option value="REJECT">REJECT</option>
      </select>
      <button id="reset">Reset view</button>
    </div>
  </header>
  <main>
    <section id="stage">
      <canvas id="graph"></canvas>
      <div class="hint">Drag nodes. Wheel to zoom. Click a node for details.</div>
    </section>
    <aside>
      <div class="stats" id="stats"></div>
      <h2>Legend</h2>
      <div class="legend" id="legend"></div>
      <h2>Selected</h2>
      <div class="details" id="details">Click a node to inspect it.</div>
      <h2>Visible</h2>
      <div class="details" id="visible"></div>
    </aside>
  </main>
  <script>
    const DATA = {payload};
    const TYPE_COLOR = {json.dumps(TYPE_COLOR)};
    const STATUS_COLOR = {json.dumps(STATUS_COLOR)};
    const canvas = document.getElementById('graph');
    const ctx = canvas.getContext('2d');
    const search = document.getElementById('search');
    const statusFilter = document.getElementById('status');
    const details = document.getElementById('details');
    const visibleBox = document.getElementById('visible');
    let nodes = DATA.nodes.map((n, i) => ({{...n, x: 120 + (i % 24) * 38, y: 100 + Math.floor(i / 24) * 34, vx: 0, vy: 0}}));
    let nodeById = new Map(nodes.map(n => [n.id, n]));
    let edges = DATA.edges.map(e => ({{...e, source: nodeById.get(e.source), target: nodeById.get(e.target)}})).filter(e => e.source && e.target);
    let scale = 1, offsetX = 0, offsetY = 0, selected = null, dragging = null, lastMouse = null;

    document.getElementById('generated').textContent = new Date(DATA.generated_at).toLocaleString();
    renderStats();
    renderLegend();
    resize();
    window.addEventListener('resize', resize);
    document.getElementById('reset').addEventListener('click', () => {{ scale = 1; offsetX = 0; offsetY = 0; selected = null; updateDetails(); }});
    search.addEventListener('input', draw);
    statusFilter.addEventListener('change', draw);

    function resize() {{
      const rect = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.max(800, rect.width * ratio);
      canvas.height = Math.max(520, rect.height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      draw();
    }}

    function renderStats() {{
      const s = DATA.summary;
      const items = [
        ['Candidates', s.candidates],
        ['Symbols', s.symbols],
        ['Strategies', s.strategies],
        ['Reasons', s.reasons],
      ];
      document.getElementById('stats').innerHTML = items.map(([k,v]) => `<div class="stat"><strong>${{v}}</strong><span>${{k}}</span></div>`).join('');
    }}

    function renderLegend() {{
      const rows = [
        ['FORWARD_PAPER', STATUS_COLOR.FORWARD_PAPER],
        ['REGIME_SPECIFIC', STATUS_COLOR.REGIME_SPECIFIC],
        ['OBSERVE', STATUS_COLOR.OBSERVE],
        ['REJECT', STATUS_COLOR.REJECT],
        ['Symbol', TYPE_COLOR.symbol],
        ['Strategy', TYPE_COLOR.strategy],
        ['Reason', TYPE_COLOR.reason],
      ];
      document.getElementById('legend').innerHTML = rows.map(([k,c]) => `<span class="pill"><i class="dot" style="background:${{c}}"></i>${{k}}</span>`).join('');
    }}

    function visibleNodes() {{
      const q = search.value.trim().toLowerCase();
      const sf = statusFilter.value;
      let keepCandidate = new Set();
      for (const n of nodes) {{
        if (n.kind !== 'candidate') continue;
        const text = `${{n.label}} ${{n.symbol || ''}} ${{n.strategy || ''}} ${{n.status || ''}} ${{n.regime || ''}} ${{n.next_action || ''}}`.toLowerCase();
        if (sf && n.status !== sf) continue;
        if (q && !text.includes(q)) continue;
        keepCandidate.add(n.id);
      }}
      if (!q && !sf) return new Set(nodes.map(n => n.id));
      const keep = new Set(keepCandidate);
      for (const e of edges) {{
        if (keepCandidate.has(e.source.id)) keep.add(e.target.id);
        if (keepCandidate.has(e.target.id)) keep.add(e.source.id);
      }}
      return keep;
    }}

    function simulate() {{
      const keep = visibleNodes();
      const vnodes = nodes.filter(n => keep.has(n.id));
      const vedges = edges.filter(e => keep.has(e.source.id) && keep.has(e.target.id));
      const w = canvas.clientWidth || 1000, h = canvas.clientHeight || 700;
      const cx = w / 2, cy = h / 2;
      for (const n of vnodes) {{
        n.vx += (cx - n.x) * 0.0008;
        n.vy += (cy - n.y) * 0.0008;
      }}
      for (const e of vedges) {{
        const dx = e.target.x - e.source.x, dy = e.target.y - e.source.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const desired = e.source.kind === 'candidate' ? 95 : 135;
        const force = (dist - desired) * 0.004 * (e.weight || 1);
        const fx = dx / dist * force, fy = dy / dist * force;
        e.source.vx += fx; e.source.vy += fy;
        e.target.vx -= fx; e.target.vy -= fy;
      }}
      for (let i = 0; i < vnodes.length; i++) {{
        for (let j = i + 1; j < vnodes.length; j++) {{
          const a = vnodes[i], b = vnodes[j];
          const dx = b.x - a.x, dy = b.y - a.y;
          const d2 = Math.max(80, dx*dx + dy*dy);
          const f = 80 / d2;
          a.vx -= dx * f; a.vy -= dy * f;
          b.vx += dx * f; b.vy += dy * f;
        }}
      }}
      for (const n of vnodes) {{
        if (n === dragging) continue;
        n.vx *= 0.86; n.vy *= 0.86;
        n.x += n.vx; n.y += n.vy;
      }}
      draw();
      requestAnimationFrame(simulate);
    }}

    function draw() {{
      const keep = visibleNodes();
      const w = canvas.clientWidth || 1000, h = canvas.clientHeight || 700;
      ctx.clearRect(0, 0, w, h);
      ctx.save();
      ctx.translate(offsetX, offsetY);
      ctx.scale(scale, scale);
      for (const e of edges) {{
        if (!keep.has(e.source.id) || !keep.has(e.target.id)) continue;
        ctx.strokeStyle = 'rgba(71,85,105,.22)';
        ctx.lineWidth = Math.max(0.7, e.weight || 1);
        ctx.beginPath();
        ctx.moveTo(e.source.x, e.source.y);
        ctx.lineTo(e.target.x, e.target.y);
        ctx.stroke();
      }}
      for (const n of nodes) {{
        if (!keep.has(n.id)) continue;
        const r = nodeRadius(n);
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = n.color || TYPE_COLOR[n.kind] || '#475569';
        ctx.fill();
        ctx.lineWidth = selected === n ? 4 : 1.5;
        ctx.strokeStyle = selected === n ? '#111827' : 'rgba(255,255,255,.95)';
        ctx.stroke();
        if (scale > 0.55 || n.kind !== 'candidate') {{
          ctx.font = `${{n.kind === 'candidate' ? 11 : 12}}px Segoe UI, Arial`;
          ctx.fillStyle = '#0f172a';
          ctx.textAlign = 'center';
          ctx.fillText(shortLabel(n.label), n.x, n.y + r + 13);
        }}
      }}
      ctx.restore();
      visibleBox.innerHTML = `${{keep.size}} nodes<br>${{edges.filter(e => keep.has(e.source.id) && keep.has(e.target.id)).length}} links`;
    }}

    function nodeRadius(n) {{
      if (n.kind === 'candidate') return 7;
      if (n.kind === 'symbol') return 13;
      if (n.kind === 'strategy') return 11;
      if (n.kind === 'verdict') return 15;
      return 9;
    }}
    function shortLabel(s) {{ s = String(s || ''); return s.length > 22 ? s.slice(0, 21) + '...' : s; }}
    function screenToWorld(ev) {{
      const rect = canvas.getBoundingClientRect();
      return {{x: (ev.clientX - rect.left - offsetX) / scale, y: (ev.clientY - rect.top - offsetY) / scale}};
    }}
    function pick(ev) {{
      const p = screenToWorld(ev), keep = visibleNodes();
      for (let i = nodes.length - 1; i >= 0; i--) {{
        const n = nodes[i];
        if (!keep.has(n.id)) continue;
        if (Math.hypot(n.x - p.x, n.y - p.y) <= nodeRadius(n) + 4) return n;
      }}
      return null;
    }}
    canvas.addEventListener('mousedown', ev => {{
      const n = pick(ev);
      if (n) {{ dragging = n; selected = n; updateDetails(); }}
      else {{ lastMouse = {{x: ev.clientX, y: ev.clientY}}; }}
      draw();
    }});
    canvas.addEventListener('mousemove', ev => {{
      if (dragging) {{
        const p = screenToWorld(ev); dragging.x = p.x; dragging.y = p.y; dragging.vx = 0; dragging.vy = 0; draw();
      }} else if (lastMouse) {{
        offsetX += ev.clientX - lastMouse.x; offsetY += ev.clientY - lastMouse.y; lastMouse = {{x: ev.clientX, y: ev.clientY}}; draw();
      }}
    }});
    window.addEventListener('mouseup', () => {{ dragging = null; lastMouse = null; }});
    canvas.addEventListener('wheel', ev => {{
      ev.preventDefault();
      const factor = ev.deltaY < 0 ? 1.08 : 0.92;
      scale = Math.max(0.22, Math.min(3.5, scale * factor));
      draw();
    }}, {{passive: false}});
    canvas.addEventListener('click', ev => {{
      const n = pick(ev);
      if (n) {{ selected = n; updateDetails(); draw(); }}
    }});

    function updateDetails() {{
      if (!selected) {{ details.textContent = 'Click a node to inspect it.'; return; }}
      const n = selected;
      const rows = [
        ['type', n.kind],
        ['label', n.label],
        ['symbol', n.symbol],
        ['strategy', n.strategy],
        ['verdict', n.status],
        ['regime', n.regime],
        ['trades', n.n_trades],
        ['avg net %', fmt(n.avg_net_pct)],
        ['total net %', fmt(n.total_net_pct)],
        ['profit factor', fmt(n.profit_factor)],
        ['next action', n.next_action],
        ['next review', n.next_review],
        ['artifact', n.artifact],
      ].filter(r => r[1] !== undefined && r[1] !== null && r[1] !== '');
      details.innerHTML = rows.map(([k,v]) => `<div class="row"><b>${{escapeHtml(k)}}:</b> ${{escapeHtml(v)}}</div>`).join('');
    }}
    function fmt(v) {{ return typeof v === 'number' ? Math.round(v * 10000) / 10000 : v; }}
    function escapeHtml(v) {{ return String(v).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
    updateDetails();
    requestAnimationFrame(simulate);
  </script>
</body>
</html>
"""
