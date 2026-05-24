"""
guard_money.py - PreToolUse guardrail for money-sensitive actions.
Wired via .claude/settings.json hooks (matcher: Edit|Write|Bash).

Reads the hook payload (JSON on stdin). If a tool call touches the API-key
file (.env) or tries to enable live trading (AUTO_TRADE=true), it returns a
"ask" decision so the action requires explicit user confirmation.

Never hard-blocks: exit 0 always; decision is conveyed via stdout JSON.
"""
import sys
import io
import json
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # malformed payload -> stay out of the way

    ti = data.get("tool_input", {}) or {}
    fp = str(ti.get("file_path") or ti.get("path") or "").replace("\\", "/").lower()
    blob = " ".join(str(ti.get(k, "")) for k in ("content", "new_string", "command"))

    reasons = []
    if fp.endswith("/.env") or fp.endswith(".env") or fp == ".env":
        reasons.append("правка .env (API-ключи)")
    if re.search(r"auto_trade\s*[:=]\s*true", blob, re.IGNORECASE):
        reasons.append("включение AUTO_TRADE=true (реальная торговля)")

    if reasons:
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": (
                    "Money-guardrail: " + "; ".join(reasons)
                    + " — требуется явное подтверждение пользователя."
                ),
            }
        }
        print(json.dumps(out, ensure_ascii=False))

    sys.exit(0)

if __name__ == "__main__":
    main()
