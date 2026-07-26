from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_LLM_CLIENT_CALLERS = (
    "src/scout/agents/layer_agent.py",
    "src/scout/agents/chief.py",
    "src/scout/public_channel/editor.py",
    "src/utils/llm_formatter.py",
)


def _llm_client_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "call"
            and isinstance(func.value, ast.Name)
            and func.value.id == "llm_client"
        ):
            calls.append(node)
    return calls


def test_every_canonical_llm_client_call_supplies_trace_context():
    seen = 0
    for relative in CANONICAL_LLM_CLIENT_CALLERS:
        calls = _llm_client_calls(ROOT / relative)
        seen += len(calls)
        for call in calls:
            assert any(
                keyword.arg == "trace_context"
                for keyword in call.keywords
            ), relative
    assert seen == 4


def test_canonical_direct_vision_path_records_start_and_terminal_event():
    tree = ast.parse(
        (ROOT / "src/utils/llm_formatter.py").read_text(encoding="utf-8")
    )
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    target = functions["generate_premium_analysis"]
    called_names = {
        node.func.id
        for node in ast.walk(target)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "start_transport_invocation" in called_names
    assert "finish_transport_invocation" in called_names


def test_legacy_and_diagnostic_llm_paths_are_not_declared_canonical():
    assert "src/scout/scout_analyst.py" not in CANONICAL_LLM_CLIENT_CALLERS
    assert "scripts/llm_provider_ab.py" not in CANONICAL_LLM_CLIENT_CALLERS
    assert "scripts/ws/ws_scanner.py" not in CANONICAL_LLM_CLIENT_CALLERS
    assert "scripts/ws/ws_main_screener.py" not in CANONICAL_LLM_CLIENT_CALLERS
