# -*- coding: utf-8 -*-
"""
probe_okx_mcp.py — проверяет, что официальный OKX MCP (market+news, read-only) стартует и отдаёт tools.
Спавнит stdio-сервер, делает initialize + tools/list, печатает имена инструментов, гасит процесс. Read-only.
"""
import sys, io, json, subprocess, threading, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
CMD = r"C:\Users\krivo\AppData\Roaming\npm\okx-trade-mcp.cmd"
ARGS = ["--modules", "market,news", "--read-only"]

def main():
    p = subprocess.Popen(["cmd", "/c", CMD] + ARGS, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
    lines = []
    def reader():
        for ln in p.stdout:
            lines.append(ln.strip())
    threading.Thread(target=reader, daemon=True).start()

    def send(obj):
        p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "probe", "version": "1"}}})
    time.sleep(1.0)
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    time.sleep(3.0)
    try: p.terminate()
    except Exception: pass

    tools = []
    srv = None
    for ln in lines:
        try: o = json.loads(ln)
        except Exception: continue
        if o.get("id") == 1:
            srv = (o.get("result", {}).get("serverInfo") or {})
        if o.get("id") == 2:
            tools = o.get("result", {}).get("tools", [])
    print(f"serverInfo: {srv}")
    print(f"инструментов: {len(tools)}")
    for t in tools:
        print(f"  - {t.get('name')}: {(t.get('description') or '')[:70]}")

if __name__ == "__main__":
    main()
