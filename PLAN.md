# PLAN - Trading Bot V2

**Последнее обновление:** 2026-05-11

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

### 🔧 Фаза B.5 — Минимальные фиксы (СЕЙЧАС, в `ws_pump_orchestrator.py`)
**Цель:** поднять WR с 40% до >55% минимальными изменениями.
**Критерий выхода:** 50+ новых paper сделок с WR >55%.

**Архитектурное изменение (10.05.2026):** вместо правки `ws_pump_engine_v2.py` создан
`ws_pump_orchestrator.py` с pool-логикой (4 слота, main/counter/banned per-pair state).

**Результаты оркестратора (10.05–11.05.2026):** 61 сделка, WR=32.8%, NET=-21.72% — неприемлемо.

**Диагноз (11.05.2026):**
- alert_cooldown_sec=120 → LAYER дал 19/61 сигналов (слишком частые re-entry)
- vol_mult=1.5, price_pct=1.2% → ловим шум (WAL: 3 сигнала за 5 мин)
- Pool thrashing: пары вылетают через 5-10 мин, пул падает до main=3

- [x] **B.5.1 — Circuit breaker + pool-архитектура** (10.05.2026):
  - Per-pair: ≥3 SL подряд → cooldown 30 мин
  - Global: daily_pnl < -5% → halt_all
  - main → counter (2 SL) → banned 24ч

- [x] **B.5.2 — Stagnation filter + USD vol** (10.05.2026):
  - Stagnation: vol_ratio >= 2 И price_change < 0.5% → BLOCK
  - dollar_vol = current[6] (USDT объём из row[7])

- [x] **B.5.3 — Базовые пороги** (10.05.2026):
  - prefilter_vol_ratio_min: 0.5 → 1.0

- [x] **B.5.4 → B.5.5 — Немедленный вход + live SL/TP** (11.05.2026, коммит aa95776):
  - Вход на сигнале screener (~1 сек) вместо закрытия свечи (60 сек)
  - `_on_candle_update` → `_close_position_if_hit` каждую секунду (был `pass`)
  - alert_cooldown_sec: 120 → 90, vol_mult: 1.5 → 2.0, price_pct: 1.2 → 1.5
  - min_pool_dwell_min: 30 добавлен
  - Результат за 11.05: 27 сделок, WR=48%, net +3.28%

---

### 🔜 Фаза C — Новый движок: `ws_smart_pump.py` (после B.5, при WR>55%)

**Цель:** WR > 60%, PF > 2.0. Параллельный paper trading, старый движок не трогаем.
**Файл:** `scripts/ws/ws_smart_pump.py`
**Архитектурное решение (10.05.2026, review GPT + Qwen + Claude):** многослойный фильтр с 7 источниками данных.

---

#### Слои данных (7 штук)

| Слой | Источник | Обновление | max_age_sec |
|------|----------|------------|-------------|
| CandleFeed | OKX WS candle1m (row[7]=USDT vol) | каждую свечу | 90s |
| OIStream | OKX Business WS `open-interest` | динамически | 15s |
| FundingCache | OKX Business WS `funding-rate` | каждые 8ч | 3600s |
| TradesAgg | OKX Business WS `trades` (side field) | потоком | 90s |
| MarketContext | candle1m BTC/ETH/SOL/BNB | каждую свечу | 120s |
| PairMetadata | CoinGecko REST (кэш 24ч) | при старте | — |
| NewsStream | CryptoPanic REST (кэш 5мин) | по запросу | 300s |

**DataFreshness контракт:** перед открытием позиции проверять возраст каждого слоя. Если любой слой старше max_age_sec → пропустить сигнал, логировать причину.

**Dynamic subscription:** подписываться на OI/trades только для пар прошедших prefilter → избегать 200 пар × 3 канала = 600 подписок.

---

#### PairMetadata (сетевая классификация)

Каждая пара тегируется при старте через CoinGecko:
```
parent_network: "SOL" | "ETH" | "BTC" | "BNB" | "OTHER"
```
Примеры: BONK→SOL, SHIB→ETH, ORDI→BTC, CAKE→BNB.
Зачем: SOL-токены реагируют на движение SOL, не BTC — нужен правильный контекст.

---

#### Typed contracts (dataclasses, шаг 1 перед кодом)

```python
@dataclass
class PairState:
    sym: str
    parent_network: str          # "SOL" | "ETH" | "BTC" | "BNB"
    candle_close: float
    vol_usd: float               # row[7] из WS
    price_change_1m_pct: float
    oi_now: float
    oi_5m_ago: float
    funding_rate: float
    taker_buy_ratio_60s: float   # buy_vol / total_vol (WS trades)
    cvd_delta: float             # cumulative buy - sell volume
    news_score: float            # 0.0–1.0 от CryptoPanic
    ts_candle: float
    ts_oi: float
    ts_trades: float

@dataclass
class SignalCandidate:
    sym: str
    direction: str               # "long"
    trigger_reason: str          # "vol_spike+oi_sync"
    gate_passed: bool
    gate_blocked_by: str | None  # причина блока
    ts: float

@dataclass
class GateDecision:
    passed: bool
    reason: str
    score: float                 # 0.0–1.0 (для будущего ML)
```

---

#### Фильтры входа (3 слоя AND логика)

**Слой 1 — Prefilter (быстрый, без deep data):**
- `price_change_1m_pct >= 3.0%` (базовый триггер)
- `vol_ratio >= 2.0` (vol/baseline_15bar)
- `NOT stagnation`: vol_ratio высокий, но price_change < 0.5% → BLOCK (MM wash)

**Слой 2 — Deep Gate (требует OI + trades + funding):**
- `vol_usd >= $50,000` (row[7], USDT vol, против шума мелких пар)
- `ΔOI_5m >= +1.5% AND ΔPrice >= +1.0%` (новые лонги входят, а не шорт-сквиз)
- `vol_oi_ratio < 15` (anti-brushing: vol >> OI → MM сам с собой торгует)
- `funding_rate IN (-0.01%, +0.05%)` (не перегрет, не в зоне сквиза)
- `taker_buy_ratio_60s >= 0.60` (покупатели доминируют по объёму)
- `cvd_delta > 0` (нет скрытой дистрибуции при росте цены)

**Слой 3 — Context Gate (сеть + макро):**
- `parent_network_regime != BEARISH` (SOL/ETH/BNB/BTC контекст не медвежий)
- `BTC_1m_slope >= -1.5%` (нет резкого слива BTC пока памп идёт)
- `news_score_boost`: если есть новость по токену → score +0.2 (не блокирует, усиливает)

---

#### Архитектурные компоненты

```
ExchangeGateway (абстракция)
  └── OKXGateway (конкретная реализация)
       ├── WSFeed (candle1m — уже есть)
       ├── OIStream (WS open-interest, dynamic sub)
       ├── FundingCache (WS funding-rate)
       └── TradesAggregator (WS trades, CVD + taker_ratio)

PairMetadata (CoinGecko REST, кэш 24ч)
MarketContext (BTC/ETH/SOL/BNB 1m candles)
NewsStream (CryptoPanic REST, кэш 5мин)

DataAggregator → объединяет все слои в PairState
PairStateManager → per-pair словарь состояний
SignalGate → все три слоя фильтров → GateDecision
CircuitBreaker → перенесён из v2, усиленный
PositionManager → paper SL/TP, логирование
```

**ExchangeGateway правило:** в SignalGate нет строк типа `"BTC-USDT"` или `okx.subscribe(...)`. Только методы gateway. Позволит добавить Binance/Bybit без переписывания логики.

---

#### C.1 — Скелет + typed contracts + ExchangeGateway
- Новый файл `scripts/ws/ws_smart_pump.py`
- Определить все dataclasses (PairState, SignalCandidate, GateDecision)
- Реализовать ExchangeGateway + OKXGateway skeleton
- Подключить существующий WSFeed для candle1m

#### C.2 — PairMetadata + MarketContext
- CoinGeckoClient: batch REST → parent_network tag для всех пар
- MarketContext: отдельный candle feed для BTC/ETH/SOL/BNB, вычислять slope 1m

#### C.3 — Shadow mode (логирование без позиций)
- SignalGate: prefilter only → пишет `logs/pump/smart_pump_candidates.jsonl`
- Цель: накопить 200+ candidate записей, проверить quality prefilter
- Формат: `{sym, ts, trigger, gate_passed, blocked_by, pair_state}`

#### C.4 — OIStream + FundingCache + DataFreshness
- Dynamic subscription: подписываться на OI только при pricechange > 2%
- FundingCache: одна подписка на все пары, словарь sym→rate
- DataFreshness check перед каждым GateDecision

#### C.5 — TradesAggregator (CVD + taker_ratio)
- WS канал `trades`: парсить side="buy"/"sell"
- Скользящее окно 60 сек: накапливать buy_vol и sell_vol
- taker_buy_ratio = buy_vol / (buy_vol + sell_vol)
- cvd_delta = running sum(buy_vol - sell_vol)

#### C.6 — NewsStream
- CryptoPanic API (бесплатный tier, rate limit учесть)
- Кэш 5 мин, score 0.0–1.0 по sentiment
- Только boost, не блокировщик

#### C.7 — Полный SignalGate (все 3 слоя) + PositionManager
- Все фильтры включены, параллельный paper run
- Пишет в `logs/pump/pump_smart_signals.jsonl`
- ws_pump_engine_v2.py продолжает работать для сравнения

**Критерий победы Phase C:** 50+ сделок в pump_smart_signals.jsonl, WR > 60%, PF > 2.0.

---

#### 12-шаговый порядок реализации

1. Typed contracts (PairState, SignalCandidate, GateDecision)
2. ExchangeGateway + OKXGateway скелет
3. PairMetadata + CoinGeckoClient
4. CandleFeed интеграция (существующий WSFeed + row[7])
5. **Shadow mode** (prefilter only → jsonl) — запустить и собирать данные
6. OIStream WS dynamic subscription
7. FundingCache WS
8. TradesAggregator (CVD + taker_ratio)
9. MarketContext (parent network regime)
10. SignalGate полный (все 3 слоя)
11. PositionManager + CircuitBreaker
12. NewsStream (последним — не критичный путь)

---

### 🔒 Фаза D — Real Trading (только после C, WR>60%, PF>2.0)
- AUTO_TRADE=true
- Leverage per coin type (crime tokens x1, ликвидные altcoins x3)
- Требует: средства на OKX счёте

---

### Бэклог Pump Engine (не в текущих фазах)
- **DUMP сигналы (шорты):** нужен отдельный backtest на исторических данных, нет данных сейчас
- **Trailing SL:** внедрить после paper данных докажут пользу, не раньше
- **Ликвидации (liquidation-orders канал):** двойственная интерпретация (лонг-ликв → цена вниз, шорт-ликв → вверх), риск ошибки направления, defer
- **Dynamic pair switching с hysteresis:** Phase D, нет рабочей базы
- **Шорты DUMP:** после Phase C
- **Multi-exchange (Binance/Bybit):** ExchangeGateway абстракция готова в C.2, но добавлять только после Phase C доказала WR
- **Taker ratio Вариант A (WS trades агрегация):** включён в Phase C.5 как основной путь

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
- Ликвидации канал — неоднозначная интерпретация, не трогаем
- Снижение vol thresholds — тестировали, ухудшает PF
- Multi-exchange — только после Phase C доказала WR > 60%
- NewsStream (CryptoPanic) — шаг 12, не критичный путь, добавляется последним

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
