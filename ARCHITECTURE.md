# ARCHITECTURE — целевое дерево проекта (31.05.2026)

> Принцип: новая система = новая ЛОГИКА на ПОЛКЕ готовых утилит Main. Движок Main заморожен и отдельно.
> Research-пила — в архив. Grounded на полной карте проекта (src/ + logs/ + utils проверены).

## Три ящика

### ❄️ FROZEN — торговый движок Main (off, НЕ трогать)
- `src/strategy/signal.py`, `src/strategy/signal_engine.py` (65КБ монолит), `src/data/main_impulse_*.py`, `src/data/impulse_pump_*.py` — авто-трейд движки.
- Статус: ЗАМОРОЖЕНО (направленный Main закрыт). Только чтение/референс.

### 🟢 NEW — информационная система (на полке Main)
**ЛОГИКА (новое, пишем):** скауты (сбор инфополя), оркестратор, синтез сводки, разметка под ЛМ.
**Дом:** новый чистый модуль `src/scout/` (отдельно от движка и от research-архива).
**ПОЛКА (импорт из Main, НЕ дублировать):**
| нужда | переиспользуем |
|---|---|
| доставка | `src/utils/telegram.py` (`send_message_to`, `send_photo_to`) |
| логирование | `src/utils/logger.py` (loguru, `write_signal`) |
| ЛМ-слой (данные→текст) | `src/utils/llm_formatter.py` (Yandex Qwen3 + fallback) |
| конфиг | `src/config.py` |
| данные OKX | `src/exchange/okx_client.py`, `src/data/ws_feed.py` |
| индикаторы | `src/strategy/indicators.py` |
**ХРАНЕНИЕ (новое, по паттерну `main_impulse_records.py`):**
- `logs/scout/` : `signals.jsonl` · `outcomes.jsonl` · **`training.jsonl`** (paired под ЛМ) · `forward_series.csv` (накопитель, уже есть в digest_data) ;
- разметка под ЛМ: паттерн `обучение/` (аннотированные примеры) + `training.jsonl`.

### 📦 ARCHIVE — отработанный research (архив-на-месте)
- `scripts/analysis/research/` — ~95 закрытых скриптов (Main-investigation + strategy-hunt). Артефакты, держим; активные скауты переезжают в 🟢.

## Пересборка дерева (обдуманно, по батчам, тест после каждого)
1. Создать дом `src/scout/` (+ `__init__.py`).
2. Переселить активные скауты (`daily_digest_collector`, `research_scout_orchestrator`, `onchain_whales_probe`) и **перевести их с собственного `requests`-fetch на полку** (okx_client/logger; доставку — на telegram; натуральный текст — на llm_formatter).
3. Завести `logs/scout/` (signals/outcomes/training) по паттерну.
4. Отделить research-архив (без поломки импорт-цепочек — они на жёстких путях).
5. ROADMAP/PLAN/SERVICE_PIVOT — свести в один источник правды.

## Граница
- Движок `src/strategy/*`, `src/data/{main_impulse,impulse_pump}_*` — НЕ трогать.
- `.env`/`AUTO_TRADE`/`config.yaml` прод-секции — НЕ трогать.
- Перемещения — только после явного GO трейдера, по одному батчу, с тестом импортов.
