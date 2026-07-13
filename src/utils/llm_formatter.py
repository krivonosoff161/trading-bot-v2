"""
LLM formatter - legacy chart/product text formatter.

By default it uses the older Yandex AI Studio path. Text-only generation can opt in
to the shared scanner/advisory LLM router by setting
PRODUCT_ANALYZER_LLM_ROUTER=llm_client. Premium vision uses its own image-capable
provider path: Alibaba Qwen-VL when available, with Yandex Gemma as an explicit
fallback/legacy option.

Takes structured analysis snapshot + optional chart image,
returns natural Russian text for client delivery.

Falls back to None on any error — caller uses build_client_summary() instead.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import aiohttp

from src.utils import llm_budget_guard as budget_guard

# ── Config ─────────────────────────────────────────────────────────────────────

_API_URL    = "https://ai.api.cloud.yandex.net/v1/chat/completions"
_API_KEY    = os.getenv("YANDEX_API_KEY", "").strip("'\"")
_FOLDER_ID  = os.getenv("YANDEX_FOLDER_ID", "").strip("'\"")
_MODEL_URI       = "gpt://b1git4svubpojuiga5pn/qwen3-235b-a22b-fp8/latest"
_SUPPORTS_VISION = False   # Qwen3-235B = False
_MAX_TOKENS = 900
_TIMEOUT    = 60  # seconds
_FORMATTER_RUB_PER_1K_TOKENS = float(os.getenv("YANDEX_LLM_FORMATTER_RUB_PER_1K", "0.5"))
_ROUTER_ENV = "PRODUCT_ANALYZER_LLM_ROUTER"
_SHARED_ROUTER_VALUES = {"shared", "llm_client"}
_ALIBABA_KEY = os.getenv("ALIBABA_API_KEY", "").strip("'\"")
_ALIBABA_URL = (
    os.getenv("ALIBABA_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    .rstrip("/")
    + "/chat/completions"
)
_ALIBABA_VISION_MODEL = os.getenv("ALIBABA_VISION_MODEL", "qwen-vl-plus").strip("'\"")
_PREMIUM_VISION_PROVIDER = os.getenv("PREMIUM_VISION_PROVIDER", "alibaba").strip("'\"").lower()


def _model_label(model_uri: str) -> str:
    parts = model_uri.split("/")
    if len(parts) >= 5 and parts[0].startswith("gpt:"):
        return "/".join(parts[3:])
    return "configured" if model_uri else ""


def _router_value(env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    return source.get(_ROUTER_ENV, "llm_client").strip("'\"").lower()


def _use_shared_router(env: dict[str, str] | None = None) -> bool:
    return _router_value(env) in _SHARED_ROUTER_VALUES


def formatter_provider_status(env: dict[str, str] | None = None) -> dict[str, object]:
    """Return sanitized provider metadata for health checks.

    This intentionally reports configuration shape only. It never returns API keys,
    folder ids, chat ids, prompts, or request payloads.
    """
    source = env if env is not None else os.environ
    shared_router = _use_shared_router(source)
    requested_router = _router_value(source)
    shared_provider = source.get("LLM_PROVIDER", "alibaba").strip("'\"").lower()
    active_provider = shared_provider if shared_router else "yandex"
    if shared_router and active_provider == "alibaba":
        api_key_set = bool(source.get("ALIBABA_API_KEY", "").strip("'\""))
        api_host = source.get("ALIBABA_BASE_URL", "https://dashscope-intl.aliyuncs.com").split("//")[-1]
    else:
        api_key_set = bool(source.get("YANDEX_API_KEY", "").strip("'\""))
        api_host = "ai.api.cloud.yandex.net"
    folder_id_set = bool(source.get("YANDEX_FOLDER_ID", "").strip("'\""))
    configured = api_key_set if shared_router else (api_key_set and folder_id_set)
    return {
        "schema": "llm_formatter_provider.v1",
        "surface": "legacy_chart_text_formatter",
        "provider": active_provider,
        "provider_scope": "shared_llm_client_opt_in" if shared_router else "yandex_only",
        "router_env": _ROUTER_ENV,
        "requested_router": requested_router,
        "shared_router_active": shared_router,
        "follows_llm_provider_env": shared_router,
        "api_host": api_host,
        "api_key_set": api_key_set,
        "folder_id_set": folder_id_set,
        "configured": configured,
        "model_label": _model_label(_MODEL_URI),
        "supports_vision": _SUPPORTS_VISION,
        "budget_guard": True,
        "telegram_send_authority": False,
        "execution_authority": False,
        "shared_router_entrypoints": ["generate_client_text", "generate_edu_text"] if shared_router else [],
        "yandex_only_entrypoints": [],
        "function_entrypoints": [
            "generate_client_text",
            "generate_premium_analysis",
            "generate_edu_text",
        ],
        "non_claim": (
            "This status describes only the legacy product/chart formatter path. "
            "It does not prove scanner, farm, or Strategy Lab LLM routing."
        ),
    }


def premium_vision_status(env: dict[str, str] | None = None) -> dict[str, object]:
    """Return sanitized premium screenshot provider status."""
    source = env if env is not None else os.environ
    requested = source.get("PREMIUM_VISION_PROVIDER", "alibaba").strip("'\"").lower()
    alibaba_key_set = bool(source.get("ALIBABA_API_KEY", "").strip("'\""))
    alibaba_model = source.get("ALIBABA_VISION_MODEL", "qwen-vl-plus").strip("'\"")
    yandex_key_set = bool(source.get("YANDEX_API_KEY", "").strip("'\""))
    yandex_model_uri = source.get("YANDEX_GEMMA_MODEL_URI", "").strip("'\"")
    if requested == "yandex":
        active_provider = "yandex"
    else:
        # "auto" is retained as a compatibility spelling, but it is fail-closed:
        # Alibaba remains the only automatic provider and Yandex requires an
        # explicit operator selection.
        active_provider = "alibaba"
    if active_provider == "alibaba":
        api_key_set = alibaba_key_set
        configured = alibaba_key_set and bool(alibaba_model)
        model_label = alibaba_model
        api_host = source.get("ALIBABA_BASE_URL", "https://dashscope-intl.aliyuncs.com").split("//")[-1]
    else:
        api_key_set = yandex_key_set
        configured = yandex_key_set and bool(yandex_model_uri)
        model_label = _model_label(yandex_model_uri)
        api_host = "ai.api.cloud.yandex.net"
    return {
        "schema": "premium_vision_provider.v1",
        "surface": "telegram_premium_screenshot",
        "provider": active_provider,
        "provider_scope": "image_provider_auto" if requested == "auto" else f"{active_provider}_only",
        "requested_provider": requested,
        "api_host": api_host,
        "api_key_set": api_key_set,
        "model_uri_set": bool(yandex_model_uri) if active_provider == "yandex" else bool(alibaba_model),
        "configured": configured,
        "model_label": model_label,
        "fallback_provider": "",
        "shared_router_active": False,
        "execution_authority": False,
        "telegram_send_authority": False,
        "review_required": not configured,
        "non_claim": (
            "Premium screenshot analysis is a vision-provider surface only. "
            "It does not make trading decisions and is not connected to farm/PFR execution."
        ),
    }


def _estimated_cost_rub(tokens: int) -> float:
    return round(max(0, int(tokens or 0)) * _FORMATTER_RUB_PER_1K_TOKENS / 1000, 4)


def _budget_allowed(role: str, model: str, *parts: str, max_output_tokens: int) -> bool:
    estimated = budget_guard.estimate_tokens(*parts, max_output_tokens=max_output_tokens)
    blocked, reason, ctx = budget_guard.should_block(role, estimated, _estimated_cost_rub(estimated))
    if blocked:
        print(f"LLM {role}: budget skipped - {reason} ({model})")
        budget_guard.usage_for_block("yandex", model, role, reason, ctx)
        return False
    return True


def _record_budget(role: str, tokens: int) -> None:
    if tokens:
        budget_guard.record_usage(role, tokens, _estimated_cost_rub(tokens))


async def _call_shared_router(
    system_prompt: str,
    user_text: str,
    *,
    max_tokens: int,
    timeout: int,
    role: str = "chief",
) -> tuple[str | None, dict]:
    """Opt-in text-only adapter over src.utils.llm_client.

    This is deliberately limited to text-only entrypoints. Premium vision stays
    on the legacy formatter path until it receives its own prompt/provider review.
    """
    from src.utils import llm_client

    return await llm_client.call(
        role,
        system_prompt,
        user_text,
        max_tokens=max_tokens,
        timeout=timeout,
    )

_SYSTEM_PROMPT = """\
Ты — аналитик крипторынка. Пишешь клиенту разбор на русском языке.
Клиент — обычный человек. Он хочет понять: что происходит и что делать.

ЗАГОЛОВОК (Статус, Тип, Направление) уже написан автоматически — ты его НЕ пишешь.
Твоя задача — написать ТОЛЬКО ТЕЛО, начиная строго с "📊 СЕЙЧАС НА РЫНКЕ".

Первая строка ответа ОБЯЗАТЕЛЬНО: 📊 СЕЙЧАС НА РЫНКЕ

Ты получишь РЕЖИМ — это команда какой шаблон использовать. Менять режим нельзя.

ЗАПРЕЩЕНО:
- ADX, DI, EMA, ATR, ratio, перцентиль, SuperTrend, Bollinger, BB, VWAP, funding, OI — клиент не знает что это
- значения индикаторов в скобках: "(ADX=21)", "(EMA20 86.88)"
- "EMA20/50" → замени на "средняя линия" / "уровень поддержки"
- слова "бот", "система", "алгоритм", приветствия, первое лицо
- внутренние рассуждения в скобках типа "(Проще: ...)"
- придумывать уровни самостоятельно — все цены берёшь ТОЛЬКО из "Расчётные уровни" в данных

РАЗРЕШЕНО:
- "тренд вверх и сильный" вместо "ADX=36, bias=UP"
- "объём низкий" вместо "vol ratio 1.03"
- конкретные ЦЕНЫ входа/стопа/цели — обязательно если есть в данных
- "рынок перегрет — покупателей слишком много" вместо "funding 0.6%"
- "цена ниже дневного уровня равновесия — давление продавцов" вместо "ниже VWAP"
- "диапазон дня X — Y" — использовать для объяснения где находимся

ЧЕСТНОСТЬ (обязательно — это аналитика, не оракул):
- Направление = СЦЕНАРИЙ, не гарантия. Вход определяется по ПОДТВЕРЖДЕНИЮ (он поздний), это не предсказание разворота/продолжения.
- ВСЕГДА давай инвалидацию («сценарий неверен, если цена …») и «когда НЕ входить».
- НЕ обещай проценты успеха / win-rate / «N% сделок закрываются». НЕ гарантируй результат.
- Пиши как ВТОРОЕ МНЕНИЕ и структуру; решение — за клиентом.

РЫНОЧНЫЙ ФОН (если в данных есть блок "РЫНОЧНЫЙ ФОН"):
- Это ОБЩИЙ контекст рынка (настроение страх/жадность, доминация BTC, новости, аутлайеры) из скаут-сводки. НЕ сигнал по инструменту и НЕ направление.
- Вплети максимум ОДНОЙ фразой в "СЕЙЧАС НА РЫНКЕ" как фон риска (например: "общий рынок в страхе, деньги в BTC — для альтов это давление").
- ЕСЛИ монета помечена аутлайером роста/падения — ОБЯЗАТЕЛЬНО предупреди о повышенной волатильности и риске резкого отката.
- НЕ превращай фон в направление и НЕ обещай движение из-за новостей.

КАК ПИСАТЬ В ЗАВИСИМОСТИ ОТ РЕЖИМА РЫНКА (поле "Режим рынка:" в данных):
- ТРЕНДОВЫЙ: движение сильное и направленное. "Импульс низкий" или "перепродан" — это НЕ сигнал разворота, это подтверждение тренда. Писать: "сильный тренд [вниз/вверх]", "давление [продавцов/покупателей] сохраняется" — как СЦЕНАРИЙ по направлению тренда (вход поздний, по подтверждению), с чёткой инвалидацией. НЕ обещать продолжение.
- ДИАПАЗОН: цена ходит между уровнями. RSI у экстремумов = ожидаемый разворот. Писать: "рынок в диапазоне", "цена у уровня [поддержки/сопротивления]", "ждём отскок".
- ДРЕЙФ: слабое направленное движение без импульса. Писать: "слабый дрейф [вниз/вверх]", "входим осторожно, короткий стоп, быстрый выход".

ДЛЯ РЕЖИМА 2 и 3 — СЦЕНАРНЫЕ УРОВНИ:
Берёшь конкретные цены из "Ближайшие сопротивления" (для условия входа в LONG)
и "Ближайшие поддержки" (для условия входа в SHORT).
Первое число в каждом списке = ближайший уровень, используй его как триггер.
Уровни входа/стопа/цели — ТОЛЬКО из "Расчётные уровни" в данных, не придумывай.

ЕСЛИ В ДАННЫХ ЕСТЬ "⚠️ Ставка финансирования":
- Это обязательно упоминать в тексте простыми словами
- "экстремально высокая" → "рынок сильно перекуплен, покупателям приходится доплачивать"
- "повышенная" → "рынок немного перегрет, стоит быть осторожным"
- Это главная причина NO_TRADE если режим 3

ЕСЛИ В ДАННЫХ ЕСТЬ "Цена относительно дневного уровня":
- Использовать в секции СЕЙЧАС НА РЫНКЕ как контекст где находится цена
- "выше" → покупатели контролируют, сила за лонгами
- "ниже" → продавцы давят, осторожно с лонгами

КАК РАСШИФРОВЫВАТЬ ЦЕЛИ (важно!):
- "🎯 Цель" — это ОСНОВНАЯ точка фиксации прибыли. При касании — закрыть позицию.
  Это быстрый target скальперского типа. (НЕ называть процент успеха — это не гарантия.)
- "🔝 Стретч" — ДОПОЛНИТЕЛЬНАЯ зона, которая срабатывает только при сильном импульсе.
  Не ждать её по умолчанию. Если цена уже на "Цели" и импульс гаснет — фиксируй, не жди Стретч.
- Запрещено писать "закрыть половину на Цели, половину на Стретч" — клиент фиксирует
  по "Цели" целиком, Стретч — это бонус для тех кто хочет держать дольше.

Последняя строка ВСЕГДА: "🔄 ПОВТОРНЫЙ АНАЛИЗ\nЧерез N минут (после HH:MM UTC)."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 1 — ВХОД
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения что происходит простыми словами]

✅ СТАВИМ ЛИМИТКУ
Лимитка ЛОНГ/ШОРТ по цене X.

📋 ПЛАН
📈 Вход:   [цена]
🛑 Стоп:   [sl_price]
🎯 Цель:   [tp1_price]   — основная точка фиксации
🔝 Стретч: [tp2_price]   — если импульс сохранится, можно продлить

❌ ЕСЛИ ЛИМИТКА НЕ СРАБОТАЛА
[условие отмены ордера]

❌ ЕСЛИ УЖЕ В ПОЗИЦИИ
Стоп на [sl_price] — не трогаем.

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).

⚠️ ПРАВИЛА ВХОДА
├─ Плечо: 10x (выставляется автоматически)
├─ Стоп: обязателен, не двигать дальше
├─ Время: см. строку "Максимальное время удержания" выше
├─ Размер: нотионал = 1.5× баланс
└─ Это аналитика — не инвест-рекомендация

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 2 — ЖДЁМ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения — тренд есть, цена у ключевого уровня, ждём подтверждения]

⏸️ ОРДЕР НЕ СТАВИМ
Рынок на развилке. Три сценария:

📈 БЫЧИЙ — [условие: закрытие свечи выше уровня X или пробой сопротивления]
  Лимитка LONG на [цена входа]
  Стоп: [sl_price] | Цель: [tp1_price] | Стретч: [tp2_price]

📉 МЕДВЕЖИЙ — [условие: пробой поддержки Y или разворот вниз на 15m]
  Сетап отменяется. [Если тренд позволяет шорт: лимитка SHORT на [цена], стоп [уровень], цель [уровень]. Если нет — просто ждём следующего анализа.]

⏸️ НЕЙТРАЛЬНЫЙ — цена остаётся между [support] и [resistance]
  Не входим. Ждём повторного анализа.

⚠️ ЕСЛИ ЦЕНА УЛЕТЕЛА
Момент упущен — не гонимся.

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 3 — НЕТ ТОРГОВЛИ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения — рынок в боковике / тренд слабый / причина почему нет сделки]

🚫 НЕТ СДЕЛКИ
[почему не торгуем — одно предложение]

👁️ ЧТО ДОЛЖНО ПРОИЗОЙТИ

📈 Чтобы появился LONG:
[конкретное условие — тренд на 1H развернётся вверх / цена закрепится выше уровня X / объём вырастет]

📉 Чтобы появился SHORT:
[конкретное условие — пробой поддержки Y / тренд на 1H уйдёт вниз / цена закрепится ниже Z]

⏸️ Пока ни то ни другое не случилось — в рынок не лезем.

⚠️ ПРИ ПРОБОЕ — НЕ ВХОДИТЬ СРАЗУ
Дождись повторного анализа.

❌ НЕ ДЕЛАТЬ
[1-2 конкретных запрета — ТОЛЬКО про эту ситуацию.
ЗАПРЕЩЕНО общие советы вроде "не торгуй краткосрочно". Пиши конкретно: "не входить в LONG без подтверждения тренда на 1H", "не интерпретировать пробой на 5m без контекста старшего таймфрейма"]

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут/час (после [HH:MM] UTC).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 4 — ОТКАТ В ТРЕНДЕ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения — старший тренд активен, цена откатила к нижней/верхней зоне дня]

🔄 ВХОД НА ОТКАТЕ
Тренд на старшем графике [вверх/вниз] — цена временно откатила, это точка входа по тренду.
Лимитка ЛОНГ/ШОРТ по цене X.

📋 ПЛАН
📈/📉 Вход:   [цена]
🛑 Стоп:      [sl_price] — под локальным минимумом
🎯 Цель:      [tp1_price]   — основная точка фиксации
🔝 Стретч:    [tp2_price]   — опционально, если импульс сохранится

⚠️ ВАЖНО
Вход на коррекции, не на пробое. Если цена пробьёт стоп — выходим без пересмотра.

❌ НЕ ДЕЛАТЬ
Не ждать возврата к вершине перед входом — момент будет упущен.
Не усредняться если цена идёт против.

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).

⚠️ ПРАВИЛА ВХОДА
├─ Плечо: 10x (выставляется автоматически)
├─ Стоп: обязателен, не двигать дальше
├─ Время: см. строку "Максимальное время удержания" выше
├─ Размер: нотионал = 1.5× баланс
└─ Это аналитика — не инвест-рекомендация
"""

# ── Header builder (Python, not LLM) ───────────────────────────────────────────

_STATUS_LABELS = {"ENTRY": "ВХОД", "WAIT": "НАБЛЮДАЕМ", "NO_TRADE": "ВНЕ РЫНКА", "PULLBACK": "ОТКАТ В ТРЕНДЕ"}
_STYLE_LABELS  = {"FAST":     "⚡ БЫСТРЫЙ — закрыть в течение 2 часов, график 15m",
                  "SWING":    "📈 СВИНГ — держать до 4 часов, график 1H",
                  "SCALP":    "⚡ СКАЛЬП — закрыть в течение 2 часов, график 15m",
                  "PULLBACK": "🔄 ОТКАТ — закрыть в течение 8 часов, график 1H",
                  "BB_FADE":  "↔️ ОТСКОК В ДИАПАЗОНЕ — закрыть в течение 60 минут, график 5m",
                  "NO_TRADE": "📊 НАБЛЮДАЕМ — ждём подтверждения на 5m"}


def _build_header(symbol: str, captured_at: str, entry_signal: str, trade_style: str,
                  bias_1h: str, bias_4h: str, side: str | None = None) -> str:
    status = _STATUS_LABELS.get(entry_signal, "НАБЛЮДАЕМ")
    style  = _STYLE_LABELS.get(trade_style, "")
    if trade_style == "SWING" and side == "sell":
        style = style.replace("📈", "📉")
    # For SCALP, bias_1h is NEUTRAL by design — use side from llm_context instead
    bias   = bias_1h if bias_1h != "NEUTRAL" else bias_4h
    if side == "buy":
        direction = "только LONG — короткая сторона не рассматривается"
    elif side == "sell":
        direction = "только SHORT — длинная сторона не рассматривается"
    elif bias == "UP":
        direction = "только LONG — короткая сторона не рассматривается"
    elif bias == "DOWN":
        direction = "только SHORT — длинная сторона не рассматривается"
    else:
        direction = "направления нет — ни LONG, ни SHORT не рассматриваются"

    sep   = "═" * 46
    lines = [sep, f"  {symbol}  |  {captured_at}", sep, "",
             f"  Статус:      {status}"]
    if style and entry_signal != "NO_TRADE":
        lines.append(f"  Тип:         {style}")
    lines += [f"  Направление: {direction}", ""]
    return "\n".join(lines)


def _fp(symbol: str, price) -> str:
    """Format price with correct decimal places for the instrument."""
    if price is None:
        return "—"
    value = float(price)
    base = symbol.split("-")[0].upper()
    if base == "BTC":
        return f"{value:.1f}"
    if base in ("ETH", "SOL"):
        return f"{value:.2f}"
    if value < 0.01:
        return f"{value:.8f}"
    if value < 1:
        return f"{value:.6f}"
    if value < 10:
        return f"{value:.5f}"
    return f"{value:.4f}"


def _get_exit_rule(snapshot: dict, ctx: dict) -> dict:
    rule = snapshot.get("exit_rule")
    if not rule:
        rule = ctx.get("exit_rule")
    if not rule:
        contract = snapshot.get("signal_contract") or {}
        rule = contract.get("exit_rule")
    return rule if isinstance(rule, dict) else {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_analysis_text(symbol: str, captured_at: str, snapshot: dict) -> str:
    """Human-readable data snapshot for LLM — plain words, no indicator names or raw numbers."""
    h15 = snapshot.get("15m", {})
    ctx = snapshot.get("llm_context", {})

    close = h15.get("close")

    # Trend: words only, no ADX numbers
    def trend_str(bias, adx):
        adx = adx or 0
        strength = "сильный" if adx >= 30 else ("умеренный" if adx >= 25 else ("слабый" if adx >= 15 else "очень слабый / нет тренда"))
        if bias == "UP":
            return f"вверх, {strength}"
        elif bias == "DOWN":
            return f"вниз, {strength}"
        else:
            # NEUTRAL bias with high ADX = correction/consolidation within trend, not flat
            if adx >= 25:
                return f"консолидация в тренде, {strength}"
            return "боковик, нет тренда"

    bias_4h = ctx.get("bias_4h", "NEUTRAL")
    bias_1h = ctx.get("bias_1h", "NEUTRAL")
    adx_4h  = ctx.get("adx_4h", 0)
    adx_1h  = ctx.get("adx_1h", 0)
    rsi_1h  = ctx.get("rsi_1h")
    rsi_15m = ctx.get("rsi_15m")
    vol     = ctx.get("volume_ratio_15m")
    sl      = ctx.get("sl_price")
    tp1     = ctx.get("tp1_price")
    tp2     = ctx.get("tp2_price")

    entry_signal = ctx.get("entry_signal", "WAIT")
    trade_style  = ctx.get("trade_style_hint", "NO_TRADE")
    regime       = ctx.get("regime", "RANGING")
    adx_rising   = ctx.get("adx_1h_rising")
    funding      = ctx.get("funding_rate")
    vwap         = ctx.get("vwap_day")
    day_high     = ctx.get("day_high")
    day_low      = ctx.get("day_low")
    exit_rule    = _get_exit_rule(snapshot, ctx)
    exit_rule_type = str(exit_rule.get("type") or "").lower()

    conflict = bias_4h != "NEUTRAL" and bias_1h != "NEUTRAL" and bias_4h != bias_1h

    # РЕЖИМ 4 triggered by trade_style=PULLBACK; otherwise by entry_signal
    if trade_style == "PULLBACK" and entry_signal != "NO_TRADE":
        mode_cmd = "РЕЖИМ 4 — ОТКАТ В ТРЕНДЕ"
    else:
        mode_cmd = {
            "ENTRY":    "РЕЖИМ 1 — ВХОД",
            "WAIT":     "РЕЖИМ 2 — ЖДЁМ",
            "NO_TRADE": "РЕЖИМ 3 — НЕТ ТОРГОВЛИ",
        }.get(entry_signal, "РЕЖИМ 2 — ЖДЁМ")
    side_raw = ctx.get("side")
    side_label = ("ЛОНГ (покупка) — вход на рост" if side_raw == "buy"
                  else "ШОРТ (продажа) — вход на снижение" if side_raw == "sell"
                  else None)

    regime_labels = {
        "TRENDING": "ТРЕНДОВЫЙ — сильное движение, работаем по тренду, не ищем разворот",
        "RANGING":  "ДИАПАЗОН — боковик, работаем от уровней поддержки и сопротивления",
        "DRIFT":    "ДРЕЙФ — слабое направленное движение, короткие цели, быстрый выход",
    }
    style_labels = {
        "FAST":  "БЫСТРЫЙ (15m, держать до 2ч)",
        "SWING": "СВИНГ (1H, держать до 5ч)",
    }

    lines = [
        f"РЕЖИМ: {mode_cmd}",
        f"Пара: {symbol}  |  Цена: {close}  |  Время: {captured_at}",
    ]
    if side_label:
        lines.append(f"Тип сделки: {side_label}")
    lines += [
        f"Режим рынка: {regime_labels.get(regime, regime)}",
    ]
    if trade_style in style_labels:
        lines.append(f"Тип входа: {style_labels[trade_style]}")
    lines += [
        "",
        f"Тренд на 4H: {trend_str(bias_4h, adx_4h)}",
        f"Тренд на 1H: {trend_str(bias_1h, adx_1h)}",
    ]

    if regime == "TRENDING" and adx_rising is not None:
        lines.append(f"Сила тренда: {'набирает силу' if adx_rising else 'ослабевает'}")

    if conflict:
        lines.append("⚠️ Таймфреймы противоречат друг другу — повышенный риск")

    if rsi_1h is not None:
        if regime == "TRENDING":
            if rsi_1h < 30 and bias_1h == "DOWN":
                rsi_label = "низкий — подтверждает силу нисходящего тренда"
            elif rsi_1h > 70 and bias_1h == "UP":
                rsi_label = "высокий — подтверждает силу восходящего тренда"
            elif rsi_1h > 70:
                rsi_label = "перекуплен при нисходящем тренде — осторожно"
            elif rsi_1h < 30:
                rsi_label = "перепродан при восходящем тренде — возможная пауза"
            else:
                rsi_label = "нейтральная зона"
        else:
            rsi_label = "перекуплен" if rsi_1h > 70 else ("перепродан" if rsi_1h < 30 else "нейтральная зона")
        lines.append(f"Импульс (1H): {rsi_label}")

    if rsi_15m is not None:
        rsi_label = "перекуплен" if rsi_15m > 70 else ("перепродан" if rsi_15m < 30 else "нейтральная зона")
        lines.append(f"Импульс (15m): {rsi_label}")

    if vol is not None:
        vol_label = "высокий — подтверждает движение" if float(vol) >= 1.5 else ("нормальный" if float(vol) >= 1.0 else "низкий — движение слабое")
        lines.append(f"Объём: {vol_label}")

    # VWAP context
    if vwap and close:
        vwap_rel = "выше VWAP — покупатели контролируют день" if float(close) >= float(vwap) else "ниже VWAP — продавцы контролируют день"
        lines.append(f"Цена относительно дневного уровня: {vwap_rel}")

    if day_high and day_low:
        lines.append(f"Диапазон дня: {day_low} — {day_high}")

    # Swing levels filtered by price (resistance above, support below)
    if close:
        _price = float(close)
        swing_highs = sorted([h for h in (h15.get("swing_highs") or []) if float(h) > _price])[:3]
        swing_lows = sorted([low for low in (h15.get("swing_lows") or []) if float(low) < _price], reverse=True)[:3]
    else:
        swing_highs = (h15.get("swing_highs") or [])[::-1][:3]
        swing_lows  = (h15.get("swing_lows")  or [])[::-1][:3]

    if swing_highs:
        lines.append(f"Ближайшие сопротивления: {swing_highs}")
    if swing_lows:
        lines.append(f"Ближайшие поддержки: {swing_lows}")

    # Funding rate — show only if trade is active OR funding actually blocked it
    funding_blocked = ctx.get("funding_blocked", False)
    if funding is not None and abs(funding) > 0.0005 and (entry_signal != "NO_TRADE" or funding_blocked):
        pct = round(abs(funding) * 100, 3)
        direction_word = "лонги переплачивают шортам" if funding > 0 else "шорты переплачивают лонгам"
        _limits = {"SWING": 0.3, "PULLBACK": 0.8, "SCALP": 0.5, "BB_FADE": 0.5}
        _limit  = _limits.get(trade_style, 0.1)
        if pct > _limit:
            level = "экстремально высокая — сделка заблокирована"
        elif pct > _limit * 0.8:
            level = "повышенная, но в пределах для этого типа входа — не держать позицию дольше 4 часов"
        else:
            level = "умеренная — учитывай при удержании позиции"
        lines.append(f"\n⚠️ Ставка финансирования: {level} ({direction_word}, {pct}%)")

    # Levels - only for ENTRY/WAIT, not NO_TRADE.
    if entry_signal != "NO_TRADE" and sl:
        lines += [
            "",
            "Расчётные уровни:",
            f"  Вход:    {_fp(symbol, close)}",
            f"  Стоп:    {_fp(symbol, sl)}",
        ]
        if exit_rule_type == "ride":
            structure_k = exit_rule.get("params", {}).get("structure_k", 2)
            be_at_r = (ctx.get("follow") or {}).get("be_at_R") or snapshot.get("follow", {}).get("be_at_R")
            lines += [
                "  Выход:   ride - едем по движению, без фиксированной цели",
                f"  Слом:    выход по структуре ({structure_k} закрытых свечи)",
            ]
            if be_at_r is not None:
                lines.append(f"  Защита: после {be_at_r}R стоп переводится к безубытку")
        elif exit_rule_type == "scaled":
            target_pct = exit_rule.get("params", {}).get("target_pct")
            target_text = f"{target_pct:.2f}%" if isinstance(target_pct, (int, float)) else "по правилу импульса"
            lines.append(f"  Выход:   scaled TP - фиксируем по цели {target_text}")
        else:
            lines += [
                f"  Цель:    {_fp(symbol, tp1)}  (основная, закрывать при касании)",
                f"  Стретч:  {_fp(symbol, tp2)}  (опционально, при сильном импульсе)",
            ]

    max_hold = ctx.get("max_hold_minutes")
    if max_hold and entry_signal != "NO_TRADE":
        lines.append(f"\n⏱ Максимальное время удержания: {max_hold} минут — закрыть вручную если уровни не достигнуты")

    expiry = snapshot.get("expiry_time")
    if expiry:
        lines.append(f"\n🕐 Актуально до: {expiry}")

    return "\n".join(lines)


def _build_market_backdrop(symbol: str, bundle: dict | None) -> str:
    """Scout bundle → honest plain-language market backdrop.

    General market context only (sentiment, dominance, outliers, headlines).
    NOT a per-symbol signal — caller must keep it as risk context, not direction.
    Returns "" when no bundle.
    """
    if not bundle:
        return ""
    base = symbol.split("-")[0].upper()
    lines: list[str] = []

    fg = bundle.get("fear_greed") or {}
    mk = bundle.get("market") or {}
    fg_val = fg.get("value")
    mcap_chg = mk.get("mcap_change_24h_pct")
    btc_dom = mk.get("btc_dominance_pct")

    tone = []
    if fg_val is not None:
        mood = {"Extreme Fear": "крайний страх", "Fear": "страх", "Neutral": "нейтрально",
                "Greed": "жадность", "Extreme Greed": "крайняя жадность"}.get(fg.get("classification"),
                                                                               fg.get("classification") or "")
        tone.append(f"настроение рынка — {mood} ({fg_val}/100)")
    if mcap_chg is not None:
        dir_word = "растёт" if mcap_chg > 0.3 else ("падает" if mcap_chg < -0.3 else "стоит на месте")
        tone.append(f"капитализация рынка за сутки {dir_word} ({mcap_chg:+.1f}%)")
    if tone:
        lines.append("Общий фон: " + ", ".join(tone) + ".")
    if isinstance(btc_dom, (int, float)) and btc_dom >= 55:
        lines.append(f"Доминация BTC высокая ({btc_dom:.0f}%) — деньги в биткоине, альты обычно под давлением.")

    movers = bundle.get("movers") or {}

    def _change(rows):
        for r in rows or []:
            if (r.get("symbol") or "").upper() == base:
                return r.get("change_24h_pct")
        return None

    up = _change(movers.get("gainers"))
    down = _change(movers.get("losers"))
    if up is not None:
        lines.append(f"⚠️ {base} сегодня в топе РОСТА рынка ({up:+.0f}% за сутки) — повышенная волатильность, риск резкого отката.")
    elif down is not None:
        lines.append(f"⚠️ {base} сегодня в топе ПАДЕНИЯ рынка ({down:+.0f}% за сутки) — повышенная волатильность.")

    trending = [(t.get("symbol") or "").upper() for t in (movers.get("trending") or [])]
    if base in trending:
        lines.append(f"{base} в списке трендовых по вниманию — много розничного интереса.")

    heads = [n.get("title") for n in (bundle.get("news") or [])[:2] if n.get("title")]
    if heads:
        lines.append("Заголовки (общий фон): " + " | ".join(heads))

    if not lines:
        return ""
    ts = bundle.get("ts_utc", "")
    head = f"─── РЫНОЧНЫЙ ФОН (скаут-контекст{', ' + ts if ts else ''}) ───"
    foot = ("Это ОБЩИЙ фон рынка, НЕ сигнал по инструменту и НЕ направление. "
            "Максимум одна фраза контекста риска в «СЕЙЧАС НА РЫНКЕ». Решение — по структуре графика.")
    return "\n\n" + head + "\n" + "\n".join(lines) + "\n" + foot


def _encode_image(image_path: str | None) -> str | None:
    """Base64-encode image for Yandex AI Studio vision API."""
    if not image_path:
        return None
    p = Path(image_path)
    if not p.exists():
        return None
    try:
        data = p.read_bytes()
        ext = p.suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return None


# ── Shared transport (additive — used by info-edge scanner) ─────────────────────

async def _call_yandex(
    system_prompt: str,
    user_text: str,
    max_tokens: int = 900,
    timeout: int = _TIMEOUT,
) -> tuple[str | None, int]:
    """Shared Yandex AI Studio (Qwen3) transport: system + user text → (reply, total_tokens).

    Additive helper for the info-edge scanner (generate_scout_card lives in
    src/scout/scout_analyst.py). Deliberately does NOT touch generate_client_text
    — the product chart-analyst path is left byte-for-byte unchanged.
    Returns (None, 0) on any error or missing keys; total_tokens for budget logging.
    """
    if not _API_KEY or not _FOLDER_ID:
        print("LLM _call_yandex: YANDEX_API_KEY/FOLDER_ID not set — skipping")
        return None, 0
    if not _budget_allowed("cheap", _MODEL_URI, system_prompt, user_text, max_output_tokens=max_tokens):
        return None, 0
    payload = {
        "model": _MODEL_URI,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
    }
    headers = {
        "Authorization": f"Api-Key {_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _API_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"LLM _call_yandex: HTTP {resp.status} — {body[:300]}")
                    return None, 0
                data = await resp.json()
        body = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        total = int(tokens.get("total_tokens") or 0)
        _record_budget("cheap", total)
        print(f"LLM _call_yandex: OK — {total} tokens")
        return (body or None), total
    except Exception as exc:
        print(f"LLM _call_yandex: error — {exc}")
        return None, 0


# ── Main entry point ───────────────────────────────────────────────────────────

async def generate_client_text(
    symbol: str,
    captured_at: str,
    snapshot: dict,
    image_path: str | None = None,
    client_summary: str | None = None,
    market_context: dict | None = None,
    session: aiohttp.ClientSession | None = None,
) -> str | None:
    """
    Call Qwen3-235B via Yandex AI Studio and return natural Russian text.
    Returns None on any error (caller uses template fallback).
    """
    shared_router = _use_shared_router()
    if not shared_router and (not _API_KEY or not _FOLDER_ID):
        print("LLM: YANDEX_API_KEY or YANDEX_FOLDER_ID not set — skipping")
        return None

    analysis_text = _build_analysis_text(symbol, captured_at, snapshot)
    if client_summary:
        analysis_text += (
            "\n\n─── ДЕТАЛЬНЫЙ РАЗБОР СИТУАЦИИ ───\n"
            + client_summary +
            "\n─────────────────────────────────\n"
            "Используй объяснения выше как основу для секций СЕЙЧАС НА РЫНКЕ, "
            "НЕ ДЕЛАТЬ и условий входа. Конкретные цены бери из ПЛАН/АКТИВНЫЙ СИГНАЛ выше."
        )

    analysis_text += _build_market_backdrop(symbol, market_context)

    if shared_router:
        body, usage = await _call_shared_router(
            _SYSTEM_PROMPT,
            analysis_text,
            max_tokens=_MAX_TOKENS,
            timeout=_TIMEOUT,
        )
        if not body:
            return None
        print(
            "LLM formatter shared router: "
            f"{usage.get('provider')}/{usage.get('role')} "
            f"{usage.get('status')}"
        )
        ctx = snapshot.get("llm_context", {})
        header = _build_header(
            symbol, captured_at,
            ctx.get("entry_signal", "WAIT"),
            ctx.get("trade_style_hint", "NO_TRADE"),
            ctx.get("bias_1h", "NEUTRAL"),
            ctx.get("bias_4h", "NEUTRAL"),
            side=ctx.get("side"),
        )
        return header + body

    # Build user message content
    content: list[dict] = [{"type": "text", "text": analysis_text}]

    # Attach chart image only if model supports vision
    if _SUPPORTS_VISION:
        b64 = _encode_image(image_path)
        if b64:
            content.append({"type": "image_url", "image_url": {"url": b64}})

    payload = {
        "model": _MODEL_URI,
        "max_tokens": _MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    }
    headers = {
        "Authorization": f"Api-Key {_API_KEY}",
        "Content-Type": "application/json",
    }
    if not _budget_allowed("chief", _MODEL_URI, _SYSTEM_PROMPT, analysis_text, max_output_tokens=_MAX_TOKENS):
        return None

    try:
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()

        try:
            async with session.post(
                _API_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"LLM: HTTP {resp.status} — {body[:300]}")
                    return None
                data = await resp.json()
        finally:
            if own_session:
                await session.close()

        body = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        _record_budget("chief", int(tokens.get("total_tokens") or 0))
        print(f"LLM: OK — {tokens.get('total_tokens', '?')} tokens used")
        if not body:
            return None
        ctx = snapshot.get("llm_context", {})
        header = _build_header(
            symbol, captured_at,
            ctx.get("entry_signal", "WAIT"),
            ctx.get("trade_style_hint", "NO_TRADE"),
            ctx.get("bias_1h", "NEUTRAL"),
            ctx.get("bias_4h", "NEUTRAL"),
            side=ctx.get("side"),
        )
        return header + body

    except Exception as exc:
        print(f"LLM: error — {exc}")
        return None


# ── Premium vision (Gemma 3 27B IT) ───────────────────────────────────────────

_GEMMA_MODEL_URI = os.getenv("YANDEX_GEMMA_MODEL_URI", "").strip("'\"")
_GEMMA_MAX_TOKENS = 500
_GEMMA_TIMEOUT   = 90


def _image_mime(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if image_bytes.startswith(b"GIF8"):
        return "image/gif"
    return "image/jpeg"


async def _call_alibaba_premium_vision(
    system_prompt: str,
    user_prompt: str,
    image_bytes: bytes,
) -> str | None:
    if not _ALIBABA_KEY or not _ALIBABA_VISION_MODEL:
        print("Alibaba vision: ALIBABA_API_KEY or ALIBABA_VISION_MODEL not set — skipping")
        return None
    if not _budget_allowed(
        "audit",
        _ALIBABA_VISION_MODEL,
        system_prompt,
        user_prompt,
        max_output_tokens=_GEMMA_MAX_TOKENS,
    ):
        return None
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": _ALIBABA_VISION_MODEL,
        "max_tokens": _GEMMA_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{_image_mime(image_bytes)};base64,{b64}"},
                    },
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {_ALIBABA_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _ALIBABA_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=_GEMMA_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"Alibaba vision: HTTP {resp.status} — {body[:200]}")
                    return None
                data = await resp.json()
        body = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        _record_budget("audit", int(tokens.get("total_tokens") or 0))
        print(f"Alibaba vision: OK — {tokens.get('total_tokens', '?')} tokens")
        return body or None
    except Exception as exc:
        print(f"Alibaba vision: error — {exc}")
        return None


async def _call_yandex_premium_vision(
    system_prompt: str,
    user_prompt: str,
    image_bytes: bytes,
) -> str | None:
    if not _API_KEY or not _GEMMA_MODEL_URI:
        print("Gemma: YANDEX_API_KEY or YANDEX_GEMMA_MODEL_URI not set — skipping")
        return None
    if not _budget_allowed(
        "audit",
        _GEMMA_MODEL_URI,
        system_prompt,
        user_prompt,
        max_output_tokens=_GEMMA_MAX_TOKENS,
    ):
        return None
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": _GEMMA_MODEL_URI,
        "max_tokens": _GEMMA_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": user_prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{_image_mime(image_bytes)};base64,{b64}"},
                },
            ]},
        ],
    }
    headers = {
        "Authorization": f"Api-Key {_API_KEY}",
        "Content-Type":  "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _API_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=_GEMMA_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"Gemma: HTTP {resp.status} — {body[:200]}")
                    return None
                data = await resp.json()
        body = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        _record_budget("audit", int(tokens.get("total_tokens") or 0))
        print(f"Gemma: OK — {tokens.get('total_tokens', '?')} tokens")
        return body or None
    except Exception as exc:
        print(f"Gemma: error — {exc}")
        return None


async def generate_premium_analysis(category: str, image_bytes: bytes) -> str | None:
    from scripts.premium_prompts import PREMIUM_SYSTEM_PROMPTS, PREMIUM_USER_PROMPT
    system_prompt = PREMIUM_SYSTEM_PROMPTS.get(category, PREMIUM_SYSTEM_PROMPTS["CRYPTO"])
    provider = _PREMIUM_VISION_PROVIDER
    if provider not in {"auto", "alibaba", "yandex"}:
        provider = "alibaba"

    if provider in {"auto", "alibaba"}:
        return await _call_alibaba_premium_vision(
            system_prompt, PREMIUM_USER_PROMPT, image_bytes
        )

    if provider == "yandex":
        return await _call_yandex_premium_vision(system_prompt, PREMIUM_USER_PROMPT, image_bytes)

    return None
    """Call Gemma 3 27B IT (vision) to analyze a chart screenshot."""
    if not _API_KEY or not _GEMMA_MODEL_URI:
        print("Gemma: YANDEX_API_KEY or YANDEX_GEMMA_MODEL_URI not set — skipping")
        return None

    from scripts.premium_prompts import PREMIUM_SYSTEM_PROMPTS, PREMIUM_USER_PROMPT
    system_prompt = PREMIUM_SYSTEM_PROMPTS.get(category, PREMIUM_SYSTEM_PROMPTS["CRYPTO"])
    if not _budget_allowed(
        "audit",
        _GEMMA_MODEL_URI,
        system_prompt,
        PREMIUM_USER_PROMPT,
        max_output_tokens=_GEMMA_MAX_TOKENS,
    ):
        return None
    b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "model": _GEMMA_MODEL_URI,
        "max_tokens": _GEMMA_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": PREMIUM_USER_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]},
        ],
    }
    headers = {
        "Authorization": f"Api-Key {_API_KEY}",
        "Content-Type":  "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _API_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=_GEMMA_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"Gemma: HTTP {resp.status} — {body[:200]}")
                    return None
                data = await resp.json()
        body = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        _record_budget("audit", int(tokens.get("total_tokens") or 0))
        print(f"Gemma: OK — {tokens.get('total_tokens', '?')} tokens")
        return body or None
    except Exception as exc:
        print(f"Gemma: error — {exc}")
        return None


# ── Educational Q&A ────────────────────────────────────────────────────────────

_EDU_SYSTEM_PROMPT = """\
Ты — обучающий ассистент по крипторынку. Отвечаешь на вопросы о терминах и механизмах.
Язык: русский, простые слова. Отвечай по существу — сначала суть, потом пример если нужен.
Не увлекайся метафорами и аналогиями — одна короткая если помогает понять, больше не нужно.

ЗАПРЕЩЕНО:
- предсказывать цены или движение рынка
- давать торговые рекомендации ("купи", "продай", "сейчас хороший момент")
- называть конкретные монеты как "хорошую инвестицию"
- придумывать статистику и цифры
- отвечать на вопросы не про крипто/трейдинг

ЕСЛИ не знаешь точно — пиши: "Точно не знаю, лучше проверить в официальных источниках."
ЕСЛИ просят дать конкретный сигнал прямо сейчас ("что купить?", "входить ли в BTC?") — пиши: "Для анализа графика нажми кнопку Анализ."
Вопросы о том КАК работают SL/TP, индикаторы, стратегии — это образовательные вопросы, отвечай на них.
ЕСЛИ вопрос не про крипту — пиши: "Я отвечаю только на вопросы про крипторынок."

Формат ответа: до 200 слов, без списков если можно обойтись, живой текст.
"""


async def generate_edu_text(question: str) -> str | None:
    """Answer a user's educational question via Qwen. Returns None on error."""
    shared_router = _use_shared_router()
    if shared_router:
        body, usage = await _call_shared_router(
            _EDU_SYSTEM_PROMPT,
            question,
            max_tokens=400,
            timeout=_TIMEOUT,
            role="mid",
        )
        if not body:
            return None
        print(
            "LLM edu shared router: "
            f"{usage.get('provider')}/{usage.get('role')} "
            f"{usage.get('status')}"
        )
        return body

    if not _API_KEY or not _FOLDER_ID:
        print("LLM edu: YANDEX_API_KEY or YANDEX_FOLDER_ID not set — skipping")
        return None
    if not _budget_allowed("mid", _MODEL_URI, _EDU_SYSTEM_PROMPT, question, max_output_tokens=400):
        return None

    payload = {
        "model": _MODEL_URI,
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": _EDU_SYSTEM_PROMPT},
            {"role": "user",   "content": question},
        ],
    }
    headers = {
        "Authorization": f"Api-Key {_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _API_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"LLM edu: HTTP {resp.status} — {body[:200]}")
                    return None
                data = await resp.json()
        body = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        _record_budget("mid", int(tokens.get("total_tokens") or 0))
        print(f"LLM edu: OK — {tokens.get('total_tokens', '?')} tokens")
        return body or None
    except Exception as exc:
        print(f"LLM edu: error — {exc}")
        return None
