# -*- coding: utf-8 -*-
"""
okx_mcp_call.py — дёргает официальный OKX MCP (stdio) напрямую: смотрит input-схемы news-тулзов
и вызывает их, чтобы понять ГЛУБИНУ данных (история sentiment для бэктеста vs только свежий срез). Read-only.
"""
import sys, io, json, subprocess, threading, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
CMD = r"C:\Users\krivo\AppData\Roaming\npm\okx-trade-mcp.cmd"
ARGS = ["--modules", "market,news", "--read-only"]

class MCP:
    def __init__(self):
        self.p = subprocess.Popen(["cmd", "/c", CMD] + ARGS, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
        self.lines = []
        threading.Thread(target=self._read, daemon=True).start()
        self._id = 0
    def _read(self):
        for ln in self.p.stdout: self.lines.append(ln.strip())
    def send(self, obj): self.p.stdin.write(json.dumps(obj) + "\n"); self.p.stdin.flush()
    def call(self, method, params=None, notify=False):
        if notify: self.send({"jsonrpc": "2.0", "method": method, "params": params or {}}); return None
        self._id += 1; rid = self._id
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        for _ in range(80):
            for ln in self.lines:
                try: o = json.loads(ln)
                except Exception: continue
                if o.get("id") == rid: return o
            time.sleep(0.1)
        return None
    def close(self):
        try: self.p.terminate()
        except Exception: pass

def main():
    m = MCP()
    m.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "p", "version": "1"}})
    m.call("notifications/initialized", notify=True)
    tl = m.call("tools/list")
    tools = {t["name"]: t for t in (tl or {}).get("result", {}).get("tools", [])}
    print("=== INPUT-СХЕМЫ NEWS-ТУЛЗОВ ===")
    for n in ("news_get_coin_sentiment", "news_get_latest", "news_get_by_coin", "news_get_economic_calendar"):
        t = tools.get(n)
        if t: print(f"\n[{n}]\n  props: {json.dumps((t.get('inputSchema') or {}).get('properties', {}), ensure_ascii=False)[:400]}\n  required: {(t.get('inputSchema') or {}).get('required')}")

    def show(name, args):
        print(f"\n=== ВЫЗОВ {name}({args}) ===")
        r = m.call("tools/call", {"name": name, "arguments": args})
        if not r: print("  нет ответа"); return
        if "error" in r: print(f"  ERROR: {r['error']}"); return
        content = r.get("result", {}).get("content", [])
        for c in content:
            txt = c.get("text", "") if isinstance(c, dict) else str(c)
            print("  " + txt[:1500])

    show("news_get_coin_sentiment", {"coins": "BTC", "period": "1h", "trendPoints": 30})
    show("news_get_latest", {"importance": "high"})
    m.close()

if __name__ == "__main__":
    main()
