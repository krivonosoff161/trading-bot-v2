# -*- coding: utf-8 -*-
"""Cheap-LLM proposal provider for the Strategy Lab — gated, isolated, off by default.

The cheap model is an advisory *dispatcher*: it only proposes bounded JSON candidates.
It never computes backtests, never makes trading decisions, and its output is never
executed — `proposal_validator` validates everything and code decides the queue.

This mirrors the scanner's OpenAI-compatible style (`src/utils/llm_client.py`) but is a
small, synchronous, stdlib-only client (no aiohttp, no new dependency) with its OWN
private cost log under the research root — it does not touch the scanner runtime or its
budget ledger. Default behaviour is fully offline: no provider unless every env is set.

Env (all optional; default = no network / export-only):
- STRATEGY_LAB_LLM_ENABLED=1
- STRATEGY_LAB_LLM_PROVIDER = alibaba | qwen | openai-compatible | ollama | synthetic
- STRATEGY_LAB_LLM_BASE_URL  (OpenAI-compatible base, e.g. https://.../compatible-mode/v1)
- STRATEGY_LAB_LLM_API_KEY_ENV  (NAME of the env var holding the key; default STRATEGY_LAB_LLM_API_KEY)
- STRATEGY_LAB_LLM_MODEL_CHEAP
- STRATEGY_LAB_LLM_TIMEOUT (seconds), STRATEGY_LAB_LLM_RATE_RUB_PER_1K
- STRATEGY_LAB_OLLAMA_NUM_CTX, STRATEGY_LAB_OLLAMA_NUM_PREDICT
If the STRATEGY_LAB_* provider fields are absent, the lab may reuse scanner-style
aliases (LLM_PROVIDER, ALIBABA_BASE_URL, ALIBABA_API_KEY, LLM_CHEAP_MODEL), but
only after STRATEGY_LAB_LLM_ENABLED=1 is explicitly set.
The API key VALUE is read only into the Authorization header; it is never logged, never
returned, and never written to any report.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from src.research_lab.paths import resolve_private_root

ENV_ENABLED = "STRATEGY_LAB_LLM_ENABLED"
ENV_PROVIDER = "STRATEGY_LAB_LLM_PROVIDER"
ENV_BASE_URL = "STRATEGY_LAB_LLM_BASE_URL"
ENV_API_KEY_ENV = "STRATEGY_LAB_LLM_API_KEY_ENV"
ENV_MODEL_CHEAP = "STRATEGY_LAB_LLM_MODEL_CHEAP"
ENV_TIMEOUT = "STRATEGY_LAB_LLM_TIMEOUT"
ENV_RATE = "STRATEGY_LAB_LLM_RATE_RUB_PER_1K"
ENV_OLLAMA_NUM_CTX = "STRATEGY_LAB_OLLAMA_NUM_CTX"
ENV_OLLAMA_NUM_PREDICT = "STRATEGY_LAB_OLLAMA_NUM_PREDICT"
DEFAULT_API_KEY_ENV = "STRATEGY_LAB_LLM_API_KEY"
SCANNER_ENV_PROVIDER = "LLM_PROVIDER"
SCANNER_ENV_ALIBABA_BASE_URL = "ALIBABA_BASE_URL"
SCANNER_ENV_ALIBABA_API_KEY = "ALIBABA_API_KEY"
SCANNER_ENV_CHEAP_MODEL = "LLM_CHEAP_MODEL"

OPENAI_COMPATIBLE = {"alibaba", "qwen", "openai-compatible", "openai"}
LOCAL_OLLAMA = {"ollama", "ollama-local"}
DEFAULT_TIMEOUT = 30.0
DEFAULT_OLLAMA_TIMEOUT = 120.0
DEFAULT_OLLAMA_NUM_CTX = 2048
DEFAULT_OLLAMA_NUM_PREDICT = 192
DEFAULT_RATE_RUB_PER_1K = 0.5  # fallback cost model, mirrors the scanner's RUB style
_USER_AGENT = "strategy-lab-research/1.0 (+llm-proposals)"


class LLMProviderError(RuntimeError):
    """Network/HTTP/parse failure or a not-configured provider asked to generate."""


@dataclass(frozen=True)
class LLMUsage:
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_rub: float = 0.0
    status: str = "ok"
    error_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "model": self.model,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens, "cost_rub": self.cost_rub,
            "status": self.status, "error_type": self.error_type,
        }


@runtime_checkable
class ProposalProvider(Protocol):
    name: str
    configured: bool

    def generate(self, system: str, user: str) -> tuple[str, LLMUsage]: ...


class NullProposalProvider:
    """Default: no network, never configured."""

    name = "null"
    model_name = ""
    configured = False

    def generate(self, system: str, user: str) -> tuple[str, LLMUsage]:
        raise LLMProviderError("provider_not_configured")


class SyntheticProposalProvider:
    """Offline deterministic provider for testing the loop mechanics. Not a real model."""

    name = "synthetic"
    model_name = "offline"
    configured = True

    def __init__(self, candidates: list[dict[str, Any]]):
        self._candidates = candidates

    def generate(self, system: str, user: str) -> tuple[str, LLMUsage]:
        text = json.dumps(self._candidates, ensure_ascii=False)
        usage = LLMUsage(provider="synthetic", model="offline",
                         input_tokens=0, output_tokens=0, total_tokens=0, cost_rub=0.0)
        return text, usage


HttpPost = Callable[[str, dict, dict, float], Any]  # (url, payload, headers, timeout) -> parsed json


def _default_http_post(url: str, payload: dict, headers: dict, timeout: float) -> Any:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - operator-set base URL
        return json.loads(resp.read().decode("utf-8"))


class OpenAICompatibleProvider:
    """Synchronous OpenAI-compatible chat client (Alibaba/Qwen/OpenAI-compatible)."""

    def __init__(self, *, provider: str, base_url: str, api_key: str, model: str,
                 timeout: float = DEFAULT_TIMEOUT, rate_rub_per_1k: float = DEFAULT_RATE_RUB_PER_1K,
                 http_post: HttpPost | None = None):
        self.name = provider
        self.configured = bool(base_url and api_key and model)
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key  # held only for the Authorization header; never logged/returned
        self._model = model
        self.model_name = model
        self._timeout = float(timeout)
        self._rate = float(rate_rub_per_1k)
        self._http_post = http_post or _default_http_post

    def generate(self, system: str, user: str) -> tuple[str, LLMUsage]:
        if not self.configured:
            raise LLMProviderError("provider_not_configured")
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                   "User-Agent": _USER_AGENT}
        payload = {
            "model": self._model,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            data = self._http_post(self._url, payload, headers, self._timeout)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            raise LLMProviderError(f"llm request failed: {type(exc).__name__}") from exc
        except json.JSONDecodeError as exc:
            raise LLMProviderError("llm returned invalid JSON envelope") from exc
        if not isinstance(data, dict):
            raise LLMProviderError("llm returned an unexpected payload")
        try:
            choice = data["choices"][0]
            finish_reason = str(choice.get("finish_reason") or "")
            if finish_reason == "length":
                raise LLMProviderError("truncated_json")
            text = str(choice["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("llm response missing choices/content") from exc
        usage = self._usage(data.get("usage") or {})
        return text, usage

    def _usage(self, raw: dict) -> LLMUsage:
        inp = int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0)
        out = int(raw.get("completion_tokens") or raw.get("output_tokens") or 0)
        total = int(raw.get("total_tokens") or (inp + out))
        cost = round(total / 1000.0 * self._rate, 4)
        return LLMUsage(provider=self.name, model=self._model, input_tokens=inp,
                        output_tokens=out, total_tokens=total, cost_rub=cost)


class OllamaProposalProvider:
    """Local Ollama chat client. No API key, no paid provider, no tool access."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        num_ctx: int = DEFAULT_OLLAMA_NUM_CTX,
        num_predict: int = DEFAULT_OLLAMA_NUM_PREDICT,
        http_post: HttpPost | None = None,
    ):
        self.configured = bool(base_url and model)
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self.model_name = model
        self._timeout = float(timeout)
        self._num_ctx = max(512, int(num_ctx))
        self._num_predict = max(32, int(num_predict))
        self._http_post = http_post or _default_http_post

    def generate(self, system: str, user: str) -> tuple[str, LLMUsage]:
        if not self.configured:
            raise LLMProviderError("provider_not_configured")
        headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
        payload = {
            "model": self._model,
            "stream": False,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "options": {
                "num_ctx": self._num_ctx,
                "num_predict": self._num_predict,
            },
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            data = self._http_post(self._url, payload, headers, self._timeout)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            raise LLMProviderError(f"ollama request failed: {type(exc).__name__}") from exc
        except json.JSONDecodeError as exc:
            raise LLMProviderError("ollama returned invalid JSON envelope") from exc
        if not isinstance(data, dict):
            raise LLMProviderError("ollama returned an unexpected payload")
        try:
            choice = data["choices"][0]
            text = str(choice["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("ollama response missing choices/content") from exc
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or (inp + out))
        return text, LLMUsage(
            provider=self.name,
            model=self._model,
            input_tokens=inp,
            output_tokens=out,
            total_tokens=total,
            cost_rub=0.0,
        )


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_first(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = str(env.get(name, "") or "").strip()
        if value:
            return value
    return ""


def _provider_name(env: Mapping[str, str]) -> str:
    return _env_first(env, ENV_PROVIDER, SCANNER_ENV_PROVIDER).lower()


def _api_key_env_name(env: Mapping[str, str]) -> str:
    configured = str(env.get(ENV_API_KEY_ENV, "") or "").strip()
    if configured:
        return configured
    if env.get(DEFAULT_API_KEY_ENV):
        return DEFAULT_API_KEY_ENV
    if env.get(SCANNER_ENV_ALIBABA_API_KEY):
        return SCANNER_ENV_ALIBABA_API_KEY
    return DEFAULT_API_KEY_ENV


def load_provider(
    environ: Mapping[str, str] | None = None,
    *,
    allow_synthetic: bool = False,
    synthetic_candidates: list[dict[str, Any]] | None = None,
    http_post: HttpPost | None = None,
) -> ProposalProvider:
    """Resolve the proposal provider from env. Returns Null unless fully configured."""
    env = environ if environ is not None else os.environ
    if not _truthy(env.get(ENV_ENABLED, "")):
        return NullProposalProvider()
    provider = _provider_name(env)
    if provider == "synthetic":
        return SyntheticProposalProvider(synthetic_candidates or []) if allow_synthetic else NullProposalProvider()
    if provider in OPENAI_COMPATIBLE:
        key_env = _api_key_env_name(env)
        api_key = str(env.get(key_env, "") or "").strip()
        base_url = _env_first(env, ENV_BASE_URL, SCANNER_ENV_ALIBABA_BASE_URL)
        model = _env_first(env, ENV_MODEL_CHEAP, SCANNER_ENV_CHEAP_MODEL)
        if api_key and base_url and model:
            return OpenAICompatibleProvider(
                provider=provider, base_url=base_url, api_key=api_key, model=model,
                timeout=float(env.get(ENV_TIMEOUT, DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT),
                rate_rub_per_1k=float(env.get(ENV_RATE, DEFAULT_RATE_RUB_PER_1K) or DEFAULT_RATE_RUB_PER_1K),
                http_post=http_post,
            )
    if provider in LOCAL_OLLAMA:
        base_url = _env_first(env, ENV_BASE_URL) or "http://127.0.0.1:11434/v1"
        model = _env_first(env, ENV_MODEL_CHEAP) or "calculator"
        return OllamaProposalProvider(
            base_url=base_url,
            model=model,
            timeout=float(env.get(ENV_TIMEOUT, DEFAULT_OLLAMA_TIMEOUT) or DEFAULT_OLLAMA_TIMEOUT),
            num_ctx=int(float(env.get(ENV_OLLAMA_NUM_CTX, DEFAULT_OLLAMA_NUM_CTX) or DEFAULT_OLLAMA_NUM_CTX)),
            num_predict=int(float(env.get(ENV_OLLAMA_NUM_PREDICT, DEFAULT_OLLAMA_NUM_PREDICT) or DEFAULT_OLLAMA_NUM_PREDICT)),
            http_post=http_post,
        )
    return NullProposalProvider()


# ── private usage log (lab-only; never the scanner's budget) ───────────────────

def usage_log_path(private_root: Path) -> Path:
    return private_root / "reports" / "llm_usage" / "llm_usage.jsonl"


def record_usage(private_root: Path, usage: LLMUsage, *, allow_public_output: bool = False) -> Path:
    """Append one usage row (tokens/cost, no keys) under the private root."""
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    path = usage_log_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), **usage.to_dict()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def today_usage(private_root: Path) -> dict[str, Any]:
    """Public-safe summary of today's LLM spend (counts/cost only, no keys)."""
    path = usage_log_path(private_root)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    requests = 0
    tokens = 0
    cost_rub = 0.0
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("ts", ""))[:10] != today:
                continue
            requests += 1
            tokens += int(row.get("total_tokens") or 0)
            cost_rub += float(row.get("cost_rub") or 0.0)
    return {"requests": requests, "tokens": tokens, "cost_rub": round(cost_rub, 4)}
