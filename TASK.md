# TASK / HANDOFF для Claude и Codex — агентный scanner v1 (06.06.2026)

Это файл-связка между агентами в VS Code. Читать перед любыми правками.

## Статус коротко

Фундамент агентной системы уже собран и запушен. Сейчас задача не перепридумывать архитектуру, а аккуратно запустить, проверить Alibaba-петлю и дальше достроить `source -> layer -> expected/realized -> agent -> orchestrator -> chief -> outcome`.

## Что уже сделано

### 1. LLM backend

- `src/utils/llm_client.py` есть.
- Провайдеры: `yandex` fallback и `alibaba`.
- Роли: `cheap`, `mid`, `chief`, `audit`.
- Alibaba OpenAI-compatible endpoint работает.
- Реальный тест Alibaba прошёл 06.06.2026:
  - role: `cheap`;
  - model: `qwen3-30b-a3b-instruct-2507`;
  - response: `{"ok": true, "provider": "alibaba"}`;
  - usage: 53 total tokens;
  - estimated cost: ~0.0019 RUB.

`.env` НЕ коммитить. Ключ находится только локально у трейдера.

Ожидаемый локальный `.env` блок:

```env
LLM_PROVIDER=alibaba
ALIBABA_API_KEY=sk-ws-...
ALIBABA_BASE_URL=https://ws-bylnyb68jyymhk01.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1

LLM_CHEAP_MODEL=qwen3-30b-a3b-instruct-2507
LLM_MID_MODEL=qwen3-next-80b-a3b-instruct
LLM_CHIEF_MODEL=qwen3.7-plus
LLM_AUDIT_MODEL=qwen3.7-plus
```

Важно: `ALIBABA_BASE_URL` без `/chat/completions`; код сам добавляет endpoint.

### 2. Агентная машинерия

Собрана схема:

```text
deterministic router/dedup/materiality/temporal
-> layer_agent cheap
-> orchestrator rules
-> chief only for candidates
-> journal / Telegram
-> outcome resolver
```

Коммиты:

- `b60bdb7` — `llm_client.py`, roles, provider routing.
- `4e5c75a` — 5 layer agents через один класс + 5 промптов, chief, orchestrator.
- `ef91c5f` — проводка orchestrator в `scanner_v0`, chief-карточки в канал, дешёвый NO_GO в журнал.
- `2841c8e` — price-path resolver LONG/SHORT, `outcome_long/short`, `mfe/mae`, `price_after_1h/4h/24h/48h`, `missed_move`.
- `13ff9bc` — расширение вселенной до 40+ активов + multi-RSS.

### 3. Источники и вселенная

Активные сейчас:

```text
cointelegraph
decrypt
google_news_crypto
okx_listings
```

Расширена вселенная:

```text
L1: BTC/ETH/SOL/XRP/BNB/DOGE/ADA...
L2: AVAX/LINK/DOT/TON/TRX/ZEC/PEPE/SHIB/PUMP...
L5: NVDA/TSLA/MSTR/COIN/OPENAI/ANTHROPIC/SPACEX...
```

Сейчас модель работы такая:

```text
общие RSS + OKX listings
-> router определяет asset/layer
-> нужный слой-агент анализирует
```

Это уже работает как слойная агентная система, но это ещё НЕ полноценные 5 независимых специализированных потоков.

### 4. Исходы

Старые 7 зрелых карточек пересчитаны:

```text
NO_GO n=7
long avg:  -4.47%
short avg: +4.47%
missed_move: 7/7
```

Вывод подтверждён данными: старый V0 был однобокий. Он спасал от long, но не видел short. Новый chief уже умеет `side=short`, а resolver теперь это измеряет.

## Что НЕ сделано

Пока нет полноценного механизма:

```text
expected event -> pending_events.jsonl
realized event -> match pending
surprise = realized - expected
chief decision on surprise
```

`pending_events.jsonl` пока skeleton. Без него нет настоящего surprise-edge.

Пока НЕ подключены:

- SEC EDGAR для L5 official filings.
- FRED/FOMC/CPI calendar.
- EIA/OPEC для L4.
- token unlocks / earnings calendar.
- Telegram alpha через Telethon.
- weekly audit-agent.

## Что делать сейчас

### Запуск

Если `.env` уже сохранён с Alibaba:

1. Закрыть старое окно `scanner.bat`.
2. Запустить `scanner.bat` заново.
3. После первого нового события проверить:

```text
logs/scout/scanner_journal.jsonl
```

В новых строках должны появиться:

```text
chief_called
agent_direction
agent_confidence
llm_provider = alibaba
llm_model
side = long|short|none
```

Также смотреть:

```text
logs/scout/llm_budget.jsonl
logs/scout/drops.jsonl
logs/scout/ingest_log.jsonl
```

### Коммит

`.env` не коммитить никогда.

Код последних фич уже запушен. Локально сейчас допустимо коммитить только handoff/docs, если нужно зафиксировать связь между агентами:

```text
TASK.md
docs/video_research_catalog.md
```

Перед любым код-коммитом обязательно:

```bash
python -m pytest tests/test_scanner_isolation.py tests/test_scanner_router.py tests/test_scanner_dedup.py
python src/scout/resolve_outcomes.py --report
```

## Следующий инженерный шаг

Не добавлять хаотично ещё 20 RSS. Следующий правильный шаг:

```text
source_registry.yaml
-> normalized_event contract
-> expected/realized phase policy
-> pending_events writer
-> first calendar/official source
-> matcher expected vs realized
```

Практичный порядок:

1. Дать текущему scanner поработать на Alibaba 1-2 часа.
2. Проверить, что новые события реально идут по разным слоям и `llm_provider=alibaba`.
3. Потом подключать первый специализированный источник.

Рекомендуемый первый источник:

```text
SEC EDGAR -> L5 -> realized official filings
```

Почему SEC EDGAR:

- keyless;
- official;
- хорошо ложится в L5;
- ловит filings раньше новостного цикла;
- меньше шума, чем Google News.

После SEC:

```text
earnings_calendar / token_unlocks / OPEC-EIA / FOMC-CPI
```

И только потом `pending_events` станет полезным.

## Границы

Строго нельзя:

- коммитить `.env`;
- печатать API key;
- включать auto-trade;
- трогать `AUTO_TRADE`, боевые ордера, `config.yaml` торгового исполнения;
- запускать бесконтрольный поток без лимитов;
- тащить код FinceptTerminal внутрь проекта.

Можно:

- править scanner/agents/source configs;
- запускать paper scanner;
- писать JSONL-журналы в `logs/scout`;
- добавлять keyless источники;
- расширять `pending_events` и resolver;
- коммитить документацию/handoff.

## Важное понимание

Система сейчас уже не “одна LLM читает новости”. Она стала:

```text
источники -> роутер -> слой -> cheap agent -> orchestrator -> chief -> journal -> outcome
```

Но целевая система трейдера глубже:

```text
5 специализированных потоков
-> expected/realized
-> surprise
-> agent/chief
-> Telegram + dataset
-> audit/calibration
```

Следующий агент должен двигать именно это, а не снова переписывать фундамент.
