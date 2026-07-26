# -*- coding: utf-8 -*-
"""
llm_client.py — единый LLM-бэкенд агентного сканера.

Провайдер-роутинг (yandex сейчас / alibaba по ключу) + роли-тиры (cheap/mid/chief/audit)
+ cost-log + retry с backoff. Модели на роль — из env (config-driven), без хардкода в логике.
Alibaba = OpenAI-совместимый; Yandex = нативный AI Studio. Yandex работает дефолтом;
Alibaba активируется когда задан ALIBABA_API_KEY + LLM_PROVIDER=alibaba.

call(role, system, user) → (text, usage{provider,model,role,in,out,total,cost_usd,cost_rub}).
"""
from __future__ import annotations

import asyncio
import os

import aiohttp

from src.research_lab.llm_invocation_ledger import (
    LLMTraceContext,
    finish_transport_invocation,
    start_transport_invocation,
)
from src.utils import llm_budget_guard as budget_guard

# ── провайдер ────────────────────────────────────────────────────────────────
PROVIDER = os.getenv("LLM_PROVIDER", "alibaba").strip().lower()

_YANDEX_KEY = os.getenv("YANDEX_API_KEY", "").strip("'\"")
_YANDEX_URL = "https://ai.api.cloud.yandex.net/v1/chat/completions"
_YANDEX_CHIEF = "gpt://b1git4svubpojuiga5pn/qwen3-235b-a22b-fp8/latest"

_ALIBABA_KEY = os.getenv("ALIBABA_API_KEY", "").strip("'\"")
_ALIBABA_URL = (os.getenv("ALIBABA_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
                .rstrip("/") + "/chat/completions")

# роль → модель (env-driven). Yandex: маленькую под cheap (Gemma), 235b под chief.
_ROLE_MODELS = {
    "yandex": {
        # Gemma URI даёт 403 на chat/completions (он под vision) → cheap дефолтом = chief-модель
        # (работает); реальный дешёвый тир = Alibaba qwen3-30b или рабочий малый Yandex-URI через env.
        "cheap": os.getenv("YANDEX_CHEAP_MODEL", "").strip("'\"") or _YANDEX_CHIEF,
        "mid":   os.getenv("YANDEX_MID_MODEL", "").strip("'\"") or _YANDEX_CHIEF,
        "chief": os.getenv("YANDEX_CHIEF_MODEL", "").strip("'\"") or _YANDEX_CHIEF,
        "audit": os.getenv("YANDEX_AUDIT_MODEL", "").strip("'\"") or _YANDEX_CHIEF,
    },
    "alibaba": {
        "cheap": os.getenv("LLM_CHEAP_MODEL", "qwen3-30b-a3b-instruct-2507"),
        "mid":   os.getenv("LLM_MID_MODEL", "qwen3-next-80b-a3b-instruct"),
        "chief": os.getenv("LLM_CHIEF_MODEL", "qwen3-235b-a22b-instruct-2507"),
        "audit": os.getenv("LLM_AUDIT_MODEL", "qwen3-235b-a22b-thinking-2507"),
    },
}

# Fallback ₽/1k токенов, если нет модельного прайса.
_RATE_RUB_PER_1K = {"yandex": 0.5}
_USD_RUB = float(os.getenv("LLM_USD_RUB", "90"))

# Alibaba PAYG, USD / 1M tokens. Conservative list prices for International/Singapore.
# Override in .env with LLM_PRICE_<MODEL>_IN_USD_PER_1M / ..._OUT_USD_PER_1M if cabinet differs.
_ALIBABA_PRICE_USD_PER_1M = {
    "qwen3-30b-a3b-instruct-2507": (0.20, 0.80),
    "qwen3-next-80b-a3b-instruct": (0.15, 1.20),
    "qwen3-235b-a22b-instruct-2507": (0.23, 0.92),
    "qwen3-235b-a22b-thinking-2507": (0.23, 2.30),
    "qwen3.7-plus": (0.40, 1.60),
}

_TIMEOUT = int(os.getenv("LLM_DEFAULT_TIMEOUT", "60"))
_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))


def model_for(role: str, provider: str | None = None) -> str:
    p = (provider or PROVIDER)
    return _ROLE_MODELS.get(p, _ROLE_MODELS["yandex"]).get(role) or _YANDEX_CHIEF


def _alibaba_rates(model: str) -> tuple[float, float]:
    env_key = model.upper().replace("-", "_").replace(".", "_")
    in_rate, out_rate = _ALIBABA_PRICE_USD_PER_1M.get(model, (0.0, 0.0))
    in_rate = float(os.getenv(f"LLM_PRICE_{env_key}_IN_USD_PER_1M", in_rate))
    out_rate = float(os.getenv(f"LLM_PRICE_{env_key}_OUT_USD_PER_1M", out_rate))
    return in_rate, out_rate


def _estimate_pre_call_cost(provider: str, model: str, system: str, user: str, max_tokens: int) -> tuple[int, float]:
    input_estimate = budget_guard.estimate_tokens(system, user)
    output_estimate = max(0, int(max_tokens or 0))
    total_estimate = input_estimate + output_estimate
    if provider == "alibaba":
        in_rate, out_rate = _alibaba_rates(model)
        cost_usd = input_estimate / 1_000_000 * in_rate + output_estimate / 1_000_000 * out_rate
        return total_estimate, round(cost_usd * _USD_RUB, 4)
    return total_estimate, round(total_estimate / 1000 * _RATE_RUB_PER_1K.get(provider, 0.5), 4)


def _usage(provider: str, model: str, role: str, data: dict, *,
           status: str = "ok", error_type: str | None = None) -> dict:
    u = data.get("usage", {}) or {}
    inp = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
    out = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
    total = int(u.get("total_tokens") or (inp + out))
    cost_usd = 0.0
    if provider == "alibaba":
        in_rate, out_rate = _alibaba_rates(model)
        cost_usd = inp / 1_000_000 * in_rate + out / 1_000_000 * out_rate
        cost = round(cost_usd * _USD_RUB, 4)
    else:
        cost = round(total / 1000 * _RATE_RUB_PER_1K.get(provider, 0.5), 4)
    out_usage = {"provider": provider, "model": model, "role": role,
                 "input_tokens": inp, "output_tokens": out, "total_tokens": total,
                 "cost_usd": round(cost_usd, 6), "cost_rub": cost,
                 "status": status}
    if error_type:
        out_usage["error_type"] = error_type
    return out_usage


async def call(role: str, system: str, user: str, *,
               json_mode: bool = False, max_tokens: int = 900,
               timeout: int | None = None,
               trace_context: LLMTraceContext | None = None) -> tuple[str | None, dict]:
    """Вызвать модель роли на активном провайдере. (text|None, usage). Retry с backoff."""
    provider = PROVIDER
    model = model_for(role, provider)
    timeout = timeout or _TIMEOUT
    permit = None
    if trace_context is not None:
        try:
            permit = start_transport_invocation(
                trace_context,
                role_id=role,
                provider=provider,
                model=model,
                input_payload={
                    "system": system,
                    "user": user,
                    "json_mode": bool(json_mode),
                    "max_tokens": int(max_tokens),
                },
                provider_class="src.utils.llm_client",
            )
        except Exception:
            return None, _usage(
                provider,
                model,
                role,
                {},
                status="error",
                error_type="trace_start_failed",
            )

    def traced_result(
        text: str | None,
        usage: dict,
        *,
        status: str,
        error_type: str = "",
        response_received: bool = False,
        attempt_count: int = 0,
    ) -> tuple[str | None, dict]:
        if trace_context is None or permit is None:
            return text, usage
        try:
            finish_transport_invocation(
                trace_context,
                permit,
                status=status,
                output_text=text,
                usage=usage,
                error_type=error_type,
                response_received=response_received,
                attempt_count=attempt_count,
            )
        except Exception:
            failed = _usage(
                provider,
                model,
                role,
                {},
                status="error",
                error_type="trace_finish_failed",
            )
            failed["correlation_id"] = trace_context.correlation_id
            failed["invocation_id"] = permit.invocation_id
            failed["surface"] = trace_context.surface
            return None, failed
        usage["correlation_id"] = trace_context.correlation_id
        usage["invocation_id"] = permit.invocation_id
        usage["surface"] = trace_context.surface
        return text, usage

    est_tokens, est_cost_rub = _estimate_pre_call_cost(provider, model, system, user, max_tokens)
    blocked, reason, ctx = budget_guard.should_block(role, est_tokens, est_cost_rub)
    if blocked:
        print(f"llm_client[{provider}/{role}]: budget skipped - {reason}")
        usage = budget_guard.usage_for_block(provider, model, role, reason, ctx)
        return traced_result(
            None,
            usage,
            status="blocked",
            error_type=str(reason),
        )

    if provider == "alibaba":
        if not _ALIBABA_KEY:
            print("llm_client: ALIBABA_API_KEY не задан")
            usage = _usage(
                provider,
                model,
                role,
                {},
                status="error",
                error_type="missing_api_key",
            )
            return traced_result(
                None,
                usage,
                status="provider_error",
                error_type="missing_api_key",
            )
        url = _ALIBABA_URL
        headers = {"Authorization": f"Bearer {_ALIBABA_KEY}", "Content-Type": "application/json"}
        payload = {"model": model, "max_tokens": max_tokens,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}     # требует слово JSON в промпте
    else:  # yandex
        if not _YANDEX_KEY:
            print("llm_client: YANDEX_API_KEY не задан")
            usage = _usage(
                provider,
                model,
                role,
                {},
                status="error",
                error_type="missing_api_key",
            )
            return traced_result(
                None,
                usage,
                status="provider_error",
                error_type="missing_api_key",
            )
        url = _YANDEX_URL
        headers = {"Authorization": f"Api-Key {_YANDEX_KEY}", "Content-Type": "application/json"}
        payload = {"model": model, "max_tokens": max_tokens,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}

    last_err = None
    response_received_any = False
    for attempt in range(_RETRIES + 1):
        attempt_count = attempt + 1
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, headers=headers,
                                  timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                    response_received_any = True
                    if r.status == 429 or r.status >= 500:
                        last_err = f"HTTP {r.status}"
                        await asyncio.sleep(min(2 ** attempt, 8))
                        continue
                    if r.status != 200:
                        await r.read()
                        print(f"llm_client[{provider}/{role}]: HTTP {r.status}")
                        error_type = f"http_{r.status}"
                        usage = _usage(
                            provider,
                            model,
                            role,
                            {},
                            status="error",
                            error_type=error_type,
                        )
                        return traced_result(
                            None,
                            usage,
                            status="provider_error",
                            error_type=error_type,
                            response_received=True,
                            attempt_count=attempt_count,
                        )
                    data = await r.json()
            text = (data["choices"][0]["message"]["content"] or "").strip()
            usage = _usage(provider, model, role, data)
            budget_guard.record_usage(role, usage.get("total_tokens") or 0, usage.get("cost_rub") or 0.0)
            return traced_result(
                text or None,
                usage,
                status="accepted",
                response_received=True,
                attempt_count=attempt_count,
            )
        except Exception as e:
            last_err = type(e).__name__
            await asyncio.sleep(min(2 ** attempt, 8))
    print(f"llm_client[{provider}/{role}]: провал после ретраев — {last_err}")
    usage = _usage(
        provider,
        model,
        role,
        {},
        status="error",
        error_type="retry_exhausted",
    )
    return traced_result(
        None,
        usage,
        status="provider_error",
        error_type="retry_exhausted",
        response_received=response_received_any,
        attempt_count=_RETRIES + 1,
    )
