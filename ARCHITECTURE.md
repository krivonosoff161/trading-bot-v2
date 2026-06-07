# ARCHITECTURE - целевое дерево проекта (обновлено 06.06.2026)

> Принцип: новая система = новая ЛОГИКА на ПОЛКЕ готовых утилит Main. Движок Main заморожен и отдельно.
> Research-пила — в архив. Grounded на полной карте проекта (src/ + logs/ + utils проверены).

## Три ящика

### ❄️ FROZEN — торговый движок Main (off, НЕ трогать)
- `src/strategy/signal.py`, `src/strategy/signal_engine.py` (65КБ монолит), `src/data/main_impulse_*.py`, `src/data/impulse_pump_*.py` — авто-трейд движки.
- Статус: ЗАМОРОЖЕНО (направленный Main закрыт). Только чтение/референс.

### 🟢 NEW - информационная система (активная разработка)
**ЛОГИКА:** скауты (сбор инфополя), durable intake buffer, layer-агенты,
кодовый оркестратор, chief-модель, outcome-resolver, разметка под ЛМ.
**Дом:** `src/scout/` (отдельно от торгового движка и research-архива).
**ПОЛКА (импорт из Main, НЕ дублировать):**
| нужда | переиспользуем |
|---|---|
| доставка | `src/utils/telegram.py` (`send_message_to`, `send_photo_to`) |
| логирование | `src/utils/logger.py` (loguru, `write_signal`) |
| ЛМ-слой | `src/utils/llm_client.py` (Yandex/Alibaba, роли cheap/mid/chief/audit) + legacy `llm_formatter.py` |
| конфиг | `src/config.py` |
| данные OKX | `src/exchange/okx_client.py`, `src/data/ws_feed.py` |
| индикаторы | `src/strategy/indicators.py` |
**ХРАНЕНИЕ:**
- `logs/scout/`: `scanner_journal.jsonl`, `ingest_log.jsonl`, `drops.jsonl`,
  `llm_budget.jsonl`, `scanner_outcomes.jsonl`, `scanner_seen.json`;
- `data/scout/news_buffer.sqlite`: SQLite intake buffer для raw/extracted/normalized news;
- разметка под ЛМ: журнал решений + исходы LONG/SHORT как база будущего обучения.

**Текущий scanner pipeline:**

```text
RSS/listings -> router/materiality/dedup/temporal
  -> layer_agent cheap -> orchestrator rules -> chief strong
  -> Telegram chief-card -> scanner_journal.jsonl
  -> resolve_outcomes.py
```

**Buffer pipeline (default в scanner.bat):**

```text
source item -> raw_items -> machine_docs -> normalized_events -> READY_FOR_AGENT
```

Стабильный запуск `scanner.bat` идет через `scanner_v0.py --buffer --limit N`.
Режим `scanner_v0.py --buffer --limit 0` используется для ingest/extract/normalize smoke
без LLM/Telegram. Старый прямой путь оставлен в bat как fallback через `USE_BUFFER=0`.

### 📦 ARCHIVE — отработанный research (архив-на-месте)
- `scripts/analysis/research/` — ~95 закрытых скриптов (Main-investigation + strategy-hunt). Артефакты, держим; активные скауты переезжают в 🟢.

## Пересборка дерева (обдуманно, по батчам, тест после каждого)
1. `src/scout/` создан и является активным домом scanner.
2. Агентная машинерия собрана: `agents/layer_agent.py`, `agents/orchestrator.py`, `agents/chief.py`.
3. Единый LLM-клиент собран: `src/utils/llm_client.py`, Alibaba включается через `.env`.
4. Журнал scanner и outcome-resolver работают.
5. SQLite intake buffer добавлен и включен в bat по умолчанию.
6. L3/L4 получили источники и OKX baselines: FRED/EIA/OPEC/OilPrice + XAU/CL price-path.
7. Следующий архитектурный шаг - измерить новый поток после cross-layer recall fix и спроектировать
   MARKET_CONTEXT/WATCH_MARKET для макро-заголовков без единого актива. `main_event_engine`
   не строить до подтверждения на данных.

## Граница
- Движок `src/strategy/*`, `src/data/{main_impulse,impulse_pump}_*` — НЕ трогать.
- `.env`/`AUTO_TRADE`/`config.yaml` прод-секции — НЕ трогать.
- Перемещения — только после явного GO трейдера, по одному батчу, с тестом импортов.
