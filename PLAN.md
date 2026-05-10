# PLAN - Trading Bot V2

**Последнее обновление:** 2026-05-10

---

## Текущий этап: S2.3 + Pump Engine Phase B.5

### Два параллельных трека:

**Трек 1 — Concierge Analyzer** (основной, приносит клиентов)
Фаза: S2.3 — Рост и качество

**Трек 2 — Pump Engine** (улучшение качества)
Фаза: B.5 — Минимальные фиксы для достижения WR>55%

---

## Трек 1 — Concierge Analyzer

### ✅ Фаза S1.1 — Рабочий операторский пайплайн (ЗАКРЫТА)
Оператор получает разбор по скрину одним запуском.

### ✅ Фаза S1.2 — Клиентский output (ЗАКРЫТА 22.03.2026)
Клиент получает точный сигнал с правильными уровнями и понятным объяснением.
- LLM (Yandex AI Studio Gemma 3 27B) — живой нарратив
- Telegram intake бот — клиент сам присылает скрин
- Premium Screenshot Analysis — 5 категорий (крипта/форекс/акции/ресурсы/фонды)

### ✅ Фаза S1.3 — Демо-пакет (ЗАКРЫТА 29.03.2026)
Материалы для показа сервиса. 3+ кейсов с результатами.

### ✅ Фаза S1.4 — Первые платные проверки (ЗАКРЫТА 09.04.2026)
3 активных подписчика, сигналы доходят, клиенты реагируют.

### ✅ Фаза S2.1 — Data layer (ЗАКРЫТА)
analyze_chart.py считает, llm_formatter.py объясняет.

### ✅ Фаза S2.2 — Консьерж-сервис (ЗАКРЫТА)
Единый Telegram-канал, шаблон ответа, история запросов.

### Фаза S2.3 — Рост и качество (АКТИВНАЯ)
**Старт:** 09.04.2026

**Цель:** улучшить качество сигналов на основе реальных данных.

**Сделано:**
- [x] analyze_signal_log.py — аналитика live сигналов
- [x] BB+volume 5m бэктест — n=29, WR=65.5%, PF=4.79 ✅ 15.04
- [x] V6C ranging_recovery — +9 сигналов, sim +105% ✅ 20.04
- [x] ADX period sync (14→9) — прод = бэктест ✅ 20.04
- [x] FAST hold 150m→90m — sim +107.4%→+114.5% ✅ 25.04
- [x] D2+B3 DRIFT фильтры — DRIFT WR 74%→89%, sim +144.1% ✅ 03.05
- [x] Вкладка "Реальные сделки" в journal.xlsx ✅ 03.05
- [x] label_outcomes.py — авто-лейблинг через OKX history ✅ 03.05
- [x] bt_entry_filters.py — sweep 14 фильтров × 5 hold, TP1→BE→TP2 структура ✅ 08.05
- [x] TRENDING FAST FVG фильтр + hold_trending_fast=120m (PF=3.61 на бэктесте) ✅ 08.05
- [x] ws_feed.py — candle4H поддержка, per-bar буферы ✅ 09.05
- [x] ws_main_screener.py — shadow-mode WS скринер 29 пар (5m/15m/1H/4H) ✅ 09.05
- [x] Логи очищены → свежий старт для новой системы ✅ 09.05

**В работе / остаток S2.3:**
- [ ] ADA в бэктест (в PAIR_PARAMS есть, нужно добавить в SYMBOLS)
- [ ] 100+ labeled сигналов → analyze_signal_log.py полный прогон
- [ ] ws_main_screener shadow-режим → анализ что поймал за 24-48ч → решение о переключении

**Критерий перехода к S3:** 100+ labeled сигналов, понятен edge на реальных данных.

---

## Трек 2 — Pump Engine (WS)

### Архитектура и контекст

WebSocket движок для alt-coin памп-скальпинга.
Источник данных: OKX Business WS, 1m закрытые свечи.
Режим: paper trading (AUTO_TRADE=false, нет средств на счёте).
Текущий файл: `scripts/ws/ws_pump_engine_v2.py`

**Ключевое решение (10.05.2026, Qwen + Claude):**
Архитектурный вариант B — создать отдельный `scripts/ws/ws_smart_pump.py`.
Старый движок продолжает работать пока новый не докажет WR > 60%.
Причина: разные циклы обновления данных (свеча 1m vs OI каждые сек vs фандинг каждые 8ч)
смешивать в одном классе = race conditions и spaghetti логика.

**Данные OKX (проверено 10.05.2026):**
OKX 1m candle WS присылает 9 полей:
  [0]=ts [1]=open [2]=high [3]=low [4]=close
  [5]=vol(контракты) [6]=volCcy [7]=volCcyQuote(USDT!) [8]=confirm
Сейчас парсим только [0-5]. row[7] = USD объём — бесплатно, уже в потоке.
Taker ratio (buy_vol/total_vol) — НЕТ в свечах, нужен WS канал trades + агрегация.
OI — отдельный Business WS канал `open-interest`.
Funding — отдельный Business WS канал `funding-rate` (обновляется редко).

---

### ✅ Фаза A — Fix + Complete (ЗАКРЫТА 03.05.2026)
- config.yaml pump_engine секция
- signal_id, контекст, OpenPosition/SL/TP логика
- pump_labels.jsonl, ротирующий лог (5MB×5)
- WS фиксы: silent death, tracebacks, stale_pairs

---

### ✅ Фаза B — Paper Trading (ЗАКРЫТА 10.05.2026)
**Цель была:** накопить 50+ paper сделок и понять edge.
**Итог:** 58 сделок, WR=40%, Net ≈ -$4.80 на $1000 баланс.
**Порог НЕ пройден** (нужно WR>55%) → переходим к B.5 для улучшения.

**Диагностика проблемы (10.05.2026):**
- Входим на объёмных спайках, которые создаёт маркет-мейкер (OI Brushing)
- Нет защиты от серий потерь: LAYER дал 7 SL подряд за 45 мин (05:00–05:49)
- Фильтр `min_usd_vol` считается в контрактах (row[5]), а не в USDT (row[7]) → неточный
- Вселенная активных пар содержит crime tokens (LAYER, LAB, RAVE) с MM манипуляциями

---

### 🔧 Фаза B.5 — Минимальные фиксы (СЕЙЧАС, в `ws_pump_engine_v2.py`)
**Цель:** поднять WR с 40% до >55% минимальными изменениями.
**Критерий выхода:** 50+ новых paper сделок с WR >55%.

- [ ] **B.5.1 — Circuit breaker** (~30 строк в `_on_candle_close` / отдельный метод):
  - Per-pair: ≥3 SL подряд → cooldown 30 мин (сбрасывается при TP)
  - Per-pair: ≥3 SL за последний час → blacklist пары до конца сессии
  - Global: daily_pnl < -5% → halt_all, запись в лог + TG алерт

- [ ] **B.5.2 — USD объём фильтр** (1 строка в `_on_candle_close`):
  - Сейчас: `current[5]` (vol в контрактах, разный масштаб у разных пар)
  - Надо: `current[7]` (volCcyQuote = USDT объём, универсальный)
  - Требует: обновить `_parse_candle` в `src/data/ws_feed.py` чтобы возвращать row[7]
    ИЛИ читать напрямую из сырого буфера (не менять Candle tuple)

- [ ] **B.5.3 — Поднять prefilter_vol_ratio_min** (в config.yaml):
  - Текущий: 0.5 (слишком низкий, пропускает шум)
  - Новый: 1.0 (только реальные всплески)

---

### 🔜 Фаза C — Новый движок: `ws_smart_pump.py` (после B.5, при WR>55%)
**Цель:** WR > 60%, PF > 2.0. Параллельный paper trading, старый движок не трогаем.

**C.1 — Скелет + агрегатор данных:**
  - Новый файл `scripts/ws/ws_smart_pump.py`
  - Per-pair state dict (Qwen структура 10.05.2026):
    ```
    {candle:{close,vol_usd,ts}, metrics:{taker_ratio,oi_change_5m_pct,funding,vol_oi_ratio},
     status:{in_cooldown, cooldown_until, consecutive_losses, position_open}}
    ```
  - Подписки: candle1m (существующий WSFeed) + open-interest + funding-rate (Business WS)
  - Staleness check: если OI или funding старше 10 сек → пропустить сигнал для этой пары
  - Dynamic subscription: подписываться на OI/funding только для пар прошедших vol prefilter
    (избежать 200 пар × 3 канала = 600 подписок → лимиты OKX)

**C.2 — Фильтры входа (AND логика):**
  - Price spike: >3% за 1m (базовый триггер, как сейчас)
  - USD vol: row[7] >= $50,000 за минуту (против шума на мелких парах)
  - OI sync: ΔOI >+1.5% за 5м И ΔPrice >+1% (новые лонги входят, не шорт-сквиз)
  - Vol/OI ratio: < 15 (anti-brushing: если объём >> OI → MM гоняет сам с собой)
  - Funding gate: funding_rate в диапазоне (-0.01%, +0.05%) (не перегрет, не сквизуемый)

**C.3 — Taker ratio (опционально, сложно):**
  - Вариант A (сложный): WS канал `trades` + скользящее окно 60 сек, порог taker_buy > 0.60
  - Вариант B (прокси): если цена растёт И vol высокий → считаем takers были buyers
  - Решение: начать с Варианта B, A — только если WR стагнирует после C.2

**C.4 — Circuit breaker (усиленная версия):**
  - Per-pair: ≥3 SL подряд → cooldown 30 мин
  - Per-pair: ≥3 SL за 1 час → blacklist до конца сессии
  - Global: daily_pnl_pct < -5% → halt_all

**C.5 — Параллельный paper run:**
  - ws_smart_pump.py пишет в `logs/pump/pump_smart_signals.jsonl`
  - ws_pump_engine_v2.py продолжает работать для сравнения
  - Критерий победы: 50+ сделок, WR smart > WR old, PF > 2.0

---

### 🔒 Фаза D — Real Trading (только после C, WR>60%, PF>2.0)
- AUTO_TRADE=true
- Leverage per coin type (crime tokens x1, ликвидные altcoins x3)
- Требует: средства на OKX счёте

---

### Бэклог Pump Engine (не в текущих фазах)
- **DUMP сигналы (шорты):** нужен отдельный backtest на исторических данных, нет данных сейчас
- **Trailing SL:** внедрить после paper данных докажут пользу, не раньше
- **Taker ratio через WS trades:** вариант A из C.3 — сложная агрегация, defer до нужды
- **Ликвидации (liquidation-orders канал):** двойственная интерпретация (лонг-ликв → цена вниз, шорт-ликв → вверх), риск ошибки направления, defer
- **Dynamic pair switching с hysteresis:** Phase D, нет рабочей базы
- **Шорты DUMP:** после Phase C

---

### Логи pump engine
- `logs/pump/pump_signals.jsonl` — все ENTRY сигналы (ws_pump_engine_v2)
- `logs/pump/pump_labels.jsonl` — исходы (paper P&L)
- `logs/pump/ws_pump_engine_v2.log` — движок (ротирующий 5MB×5)
- `logs/pump/pump_smart_signals.jsonl` — будущий ws_smart_pump (Phase C)

---

## Этап S3 — Приложение / self-serve (будущее)

### Фаза S3.1 — Клиентский интерфейс
- [ ] Telegram mini flow или web-form
- [ ] Загрузка скрина без ручной возни

### Фаза S3.2 — Автоматизация после спроса
- [ ] OCR пара/время
- [ ] Биллинг / тарифы / лимиты

**Правило:** не строить S3 пока S2.3 не закрыт.

---

## Что НЕ делаем сейчас

**Общее:**
- ML, LLM-оркестратор — нет данных
- Walking BB pattern в прод — нужно 90+ дней данных

**Pump Engine (до окончания Фазы C):**
- ws_smart_pump.py — только после WR>55% в Фазе B.5
- Trailing SL — после paper данных
- Шорты по DUMP — нет backtest
- Dynamic pair switching — нет рабочей базы
- Leverage — paper trading x1, живые деньги только в Фазе D
- Taker ratio через WS trades — Вариант A (C.3), только если WR стагнирует
- Ликвидации канал — неоднозначная интерпретация, не трогаем
- Снижение vol thresholds — тестировали, ухудшает PF

---

## Правила этапов

- Не строить следующий слой, пока предыдущий не показал пользы
- Сначала полезный output, потом автоматизация
- Сначала спрос, потом масштабирование
- Бэктест после каждого значимого изменения движка
- Не реанимировать закрытые стратегии без нового research

---

## Источник истины по стратегии

- Закрытая: [docs/strategy_e_postmortem.md](docs/strategy_e_postmortem.md)
- Продовый пивот: [SERVICE_PIVOT.md](SERVICE_PIVOT.md)
- Архив стратегий: `docs/strategy_*_postmortem.md`
