# PLAN - Trading Bot V2

**Последнее обновление:** 2026-05-20

> **Режим всех треков: PAPER TRADING / TEST ONLY**
> AUTO_TRADE=false на всех процессах. Реальные деньги — только после прохождения
> критериев каждой фазы на live paper данных. Нет исключений.

---

## 🔬 Research выводы 18.05.2026 (`docs/gpt_full_research_18_05_2026.md`)

GPT прогнал полный анализ по 3 каналам. Главные выводы (полная картина в отчёте):

### Main scanner
- **FAST×DRIFT здоров** — n=71, WR=80.3%, +0.09R unified
- **TRENDING×SWING регрессия** — universe drift (archive=мажоры, live=0 мажоров) + новый veto `min_vol_ratio_trending=1.5` (commit 2ea6a42) резал бы archive trades n=14 WR=78.6%
- **Действие:** main scanner config НЕ трогаем до завершения majors-vs-alts эксперимента (Path A, делегировано GPT)

### Pump (применяем сейчас)
- 🟢 **Safe:** `session_ban_sl_no_tp: 3 → 2` (Sim7, +4.75 п.п.)
- 🟡 **Soft aggressive:** hard-block APR/RIVER/LAB через `pair_risk_overrides` (нет tape coverage = слепая зона)
- ❌ Sim9 full overrides отложен (overfit risk 3-day)

### BB Fade
- Live n=3, слишком мало для каких-либо изменений. Ждём 20+ decisive trades.

---

## Текущий этап: Main regime research (Phase B) + S2.3 + BB Fade F.1

### Три параллельных трека:

**Трек 1 — Concierge Analyzer** (основной, приносит клиентов)
Фаза: S2.3 — Рост и качество

**Трек 2 — Pump / Impulse** (research)
Фаза: Reversal ЗАКРЫТ → continuation/импульс влит в единый regime-research (Phase B, main+pump)

**Трек 3 — BB Fade** (отдельный канал, mean reversion)
Фаза: F.1 — Live процесс + сбор данных

---

### 🆕 21.05.2026 — диагноз майна по режимам (активная research)

Main-скринер настроен под **DRIFT×FAST (WR 91%)** и натянут на все режимы → **TRENDING слишком строгий**
(пропускает реальные тренды), **RANGING читает направление неверно**, вход часто **на пике** (EDEN/SOL).
Recall на реальных движениях ~0-2% (Phase A + разбор картинок трейдером). Сама классификация режима под вопросом.

**Активный шаг — Phase B** (`docs/gpt_regime_model_phaseB_21_05_2026.md`): (А) классификация режима = база
[чекпоинт] → (Б) per-regime модель → (В) вход на пике → (Г) нужные данные → (Д) единый импульс-детектор
main+pump по волатильности пары.

**Insight:** пропуски майна и «рвём топ» на пампе — один импульс-паттерн, edge масштабируется
волатильностью пары. Прод не трогаем, всё в research-харнессе.

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
- [x] Запустить ws_main_screener с фиксами 15.05 (trending фильтры: vol≥1.5 + FVG off) ✅ 16.05
- [ ] 100+ labeled сигналов → analyze_signal_log.py полный прогон (**сейчас 68, осталось ~32**)
- [x] ws_main_screener → анализ за 16-17.05: **WR=80% на 10 закрытых, +9.79R** ✅
  - TRENDING WR=87.5% (7/8) — фильтры vol≥1.5 + FVG off работают
  - DRIFT WR=50% (1/2) — мало данных, нужно больше

**Критерий перехода к S3:** 100+ labeled, WR≥85% на последних 30 сигналах, понятен edge.

**Прогресс 16-17.05 (live):** WR=80% за 36ч на 10 сделках. Если за ~2 недели накопится 32 labeled с тем же темпом — S2.3 закроется.

---

## Трек 2 — Pump Engine (WS)

### 🔴 ТЕКУЩИЙ СТАТУС (20.05.2026)

| Подход | Статус |
|--------|--------|
| Momentum (вход по взрыву, `ws_pump_orchestrator`) | ❌ ЗАКРЫТ — n=560, WR=34.6%, net=−74% |
| Reversal (вход против взрыва, `ws_smart_pump`) | ❌ ЗАКРЫТ 20.05 — **fee-blocked**, постмортем `docs/strategy_pump_reversal_postmortem.md` |
| **Continuation (вход ПО движению серии)** | 🔬 **АКТИВНЫЙ research** — round 2, бриф `docs/gpt_continuation_research_v2_20_05_2026.md` |

**Reversal закрыт:** param sweep 0/320 положительных, гросс-edge < 0.20% тейкер. `ws_smart_pump.py`
остановлен (убран из start_all.bat). Risk-containment секции в config.yaml (`session_ban_sl_no_tp`,
`pair_risk_overrides`) — мёртвые, к процессам не подключены.

**Continuation (новое направление):** реальный edge трейдера — вход ПО направлению импульса/серии,
выход по слому структуры («лесенка»), не против взрыва. Round 1: MFE глубокий (до 13-15%), но
signal-close вход + тугой трейл = 0 net-положительных (вытряхивает шумом до движения). Round 2
тестирует: стоп за импульс + структурный выход + кластер-режим (2+ взрыва/5м → MFE 2.54% vs 1.85%).

**Критерий выхода в прод:** положительный net после 0.20% тейкер + slippage на ВСЕЙ выборке.
Пока этого нет — в config.yaml ничего не добавляем.

> ⚠️ Разделы Phase A-C ниже — **исторический record**. Phase C «7-слойный движок» (OI/funding/CVD/news)
> в таком виде НЕ был построен: вместо него сделан reversal-движок, который теперь закрыт. Оставлено
> как лог решений.

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

### ✅ Фаза B.5 — Минимальные фиксы (ЗАКРЫТА 15.05.2026)
**Цель была:** поднять WR с 40% до >55% минимальными изменениями.
**Итог:** WR потолок ~38-42%, порог >55% недостижим параметрами.

**Бэктест-валидация (GPT, 15.05.2026, `bt_pump_sim.py`):**
- path_a_approx: n=301, WR=37.9% — совпадает с live 37.5% ✅ (логика верна)
- base_path_b: n=71, WR=22.5% — standalone Path B нежизнеспособен
- Sweep 0 конфигураций с WR>55% и n>30 → параметрами не вытащить
- Потолок архитектуры: ~38-42%, PF<1 при любых настройках

**Инсайты для Phase C:**
- vol_ratio <2.0 WR=41.4% (держать фильтр)
- dollar_vol $50k-200k оптимально (не мелкие, не гигантские)
- ATR/price <0.2% WR=28% → фильтровать тихие монеты
- vol_ratio >5× = поздний вход (MFE 0.97 vs baseline 1.36) → фильтровать

**Вывод:** нужна новая архитектура с реальными данными (OI, taker ratio, CVD) → Phase C.

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

- [x] **B.5.6 — Tape-анализ + диагностика входов** (13.05.2026):
  - `pump_day_analysis.py` — срез tape (E:\trading-data\ticks) вокруг каждой сделки
  - Выявлено 5 паттернов потерь: поздний вход (3-4 свечи после пика), стоп-хант (один выброс без продолжения), поглощение (вход в период recovery после большого спайка), реэнтри без объёма, вход против потока
  - Сегодня (13.05): 22 сделки, WR=32%, PnL=-5.23% → CB HALT в 04:48

- [x] **B.5.7 — 2nd candle confirmation + надёжность** (13.05.2026, коммит 6f57535):
  - Pending entry: скринер сигнал → PENDING → подтверждение на следующей свече
  - SKIP если свеча разворот >0.5% или объём мёртвый (<80% baseline)
  - MFE/MAE tracking per позиция + R-кратные в pump_labels.jsonl
  - CB halt auto-reset: cb_daily_halt_cooldown_min=120 мин + сброс при смене UTC-дня
  - Минимальный TP: max(2.5×ATR, min_tp_pct=1.0%) — убирает NEAR +0.69%, BRETT +0.53%
  - Dead vol eviction: 3 свечи ниже 1.5× baseline → пара вылетает из пула
  - run_pump_watchdog.py: авто-рестарт при падении процесса (max 10/час)
  - start_all.bat: pump теперь через watchdog
  - stop.bat: kill по заголовку окна (/T) — реально останавливает все процессы
  - send_message_to: raises RuntimeError вместо silent log через loguru

---

### 📜 Фаза C — 7-слойный движок (НЕ ПОСТРОЕН, исторический план)

> ⚠️ **Этот план не реализован.** Вместо 7-слойного движка (OI/funding/CVD/news) был сделан
> reversal-движок `ws_smart_pump.py`, который ЗАКРЫТ 20.05 (fee-blocked, см. статус-блок выше +
> `docs/strategy_pump_reversal_postmortem.md`). Текст ниже оставлен как лог архитектурных идей —
> часть (CVD, кластер-режим, network context) переиспользуется в continuation research.

> **Всё в тестовом режиме.** AUTO_TRADE=false. Критерий перехода к D — 50+ paper сделок WR>60% PF>2.0.

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

**Текущая статистика pump_orchestrator (B.5.7, не Phase C):**

| Дата | Сделки | WR | NET |
|------|--------|----|----|
| 09.05 | 62 | 37% | -10.94% |
| 10.05 | 52 | 31% | -20.26% |
| 11.05 | 112 | 39% | -2.59% |
| 12.05 | 50 | 50% | +2.63% |
| 13.05 | 30 | 30% | -8.55% |
| 14.05 | 57 | 35% | -0.74% |
| 15.05 | 39 | 36% | +4.32% |
| 16.05 | 47 | 30% | -6.53% |
| 17.05 (частично) | 15 | 53% | +5.64% |
| **Итого** | **464** | **36%** | **-37.02%** |

Б.5.7 фильтры (2nd candle confirmation, dead vol eviction, min TP) — давали один лучший день 17.05 (WR=53%). На полной выборке всё ещё ниже 50% — Phase C критерий не пройден.

---

#### Порядок реализации (все в paper/shadow режиме)

- [x] **C.1** — Typed contracts (PairState, SignalCandidate, GateDecision)
- [x] **C.2** — ExchangeGateway + OKXGateway скелет
- [x] **C.3** — PairMetadata + CoinGeckoClient (кэш 24ч)
- [x] **C.4** — CandleFeed интеграция (WSFeed + row[7] USDT vol)
- [x] **C.5** — Shadow mode (SmartPumpShadow class, prefilter → jsonl) — **запустить, собирать кандидатов**
- [ ] **C.6** — OIStream WS dynamic subscription (подписка только при price_change>2%)
- [ ] **C.7** — FundingCache WS (один канал на все пары)
- [ ] **C.8** — TradesAggregator (CVD + taker_buy_ratio, скользящее окно 60s)
- [ ] **C.9** — MarketContext (BTC/ETH/SOL/BNB 1m slope, parent network regime)
- [ ] **C.10** — SignalGate полный (все 3 слоя AND логика)
- [ ] **C.11** — PositionManager + CircuitBreaker (paper SL/TP, daily_pnl halt)
- [ ] **C.12** — NewsStream (CryptoPanic, boost только, не блокировщик — последним)

**Приоритет прямо сейчас:** C.5 запустить в shadow mode → накопить 200+ кандидатов → анализ quality prefilter → затем C.6-C.8 (данные для реальных фильтров).

**Инсайты из B.5 для SignalGate (C.10):**
- dollar_vol $50k-200k → оптимальное окно
- vol_ratio >5× → поздний вход, фильтровать
- ATR/price <0.2% → тихие монеты, фильтровать
- vol_ratio <2.0 → держать как baseline

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

---

## Трек 3 — BB Fade (WS, отдельный процесс)

### Контекст и результаты бэктеста (15.05.2026)

Mean reversion стратегия: 15m BB касание → 5m wick rejection вход → TP на midline.

**Итерации бэктеста:**
| Версия | Сигналов/60д | WR | avg_net | PF |
|--|--|--|--|--|
| v2: 5m only, pure touch | 22,089 | 61.0% | -0.088% | 0.75 |
| v3: MTF 15m+5m | 6,540 | 64.7% | +0.006% | 1.01 |
| v3 + RSI/vol/session фильтры | 1,236 | 71.8% | +0.194% | 1.58 |
| **v3 + MIN_WIDTH=2.0% (прод)** | **344** | **70.6%** | **+0.478%** | **1.89** |

**Зафиксированные параметры (прод):**
- BB: period=20, std=2.0, на 15m
- Вход: 5m wick rejection (HIGH>=upper15 AND close<upper15 = SHORT, обратное = LONG)
- TP: 15m BB midline, SL: band_width × 0.5 за полосой
- MIN_WIDTH: 2.0% (только широкие полосы — высокая волатильность)
- RSI фильтр: SELL только RSI≤60, BUY только RSI≥40 (убирает импульсные пробои)
- Vol filter: vol_ratio < 1.5 (высокий объём = импульс, не откат)
- Сессия: EU 08-16 UTC + US 16-24 UTC (Азия -0.067% avg_net → пропускаем)
- Trending guard: 1H ADX≥22 + DI_spread≥10 → пропускаем (BB walk в тренде убивает)
- Cooldown: 2 бара между сетапами на паре
- MAX_HOLD: 16 пятиминуток (80 мин)

**Бэктест файлы:**
- `scripts/backtest/bt_bb_fade.py` — основной бэктест (v3 MTF)
- `scripts/backtest/bt_bb_tape_analysis.py` — анализ тейпа вокруг входов

---

### 🔧 Фаза F.1 — Live процесс + сбор данных (СЕЙЧАС)

**Цель:** запустить ws_bb_fade.py, собирать сигналы, лейблить исходы.
**Критерий перехода к F.2:** 50+ labeled сделок, подтверждение WR>65% на live данных.

- [x] **F.1.1** — `scripts/ws/ws_bb_fade.py`: отдельный WS процесс ✅ 15.05
  - Запущен в start_all.bat
- [x] **F.1.2** — Telegram уведомления ✅ 15.05
- [x] **F.1.3** — `scripts/analysis/bb_fade_label_outcomes.py` ✅ 15.05
- [x] **F.1.4** — Сбор тейп данных ✅ автоматически

**Live результаты F.1 (16-17.05):**
- 48 ARMED setups (15m коснулись BB полосы)
- 1 SIGNAL (5m wick rejection сработал)
- **8× меньше ожидаемого** по бэктесту (5.7/день → реальность ~0.6/день)
- Гипотеза: backtest симулирует "что было бы если выйти на high/low бара", а live ждёт closed 5m bar
- TRUTH BUY 16.05 16:30 — outcome ещё не закрыт

**Решение:** дать ещё 5-7 дней мониторинга. Если за неделю <15 сигналов — открывать гипотезу #4 (синхронизация бэктеста и live).

---

### 🔜 Фаза F.2 — Tape filter (гипотезы, после 50+ live сделок)

**Гипотеза 1 — Buy ratio фильтр (подтверждено на 35 сделках):**
- pre_buy_ratio 0.5–0.7 за 5 мин до входа → WR=75%, avg=+0.570%
- pre_buy_ratio <0.3 или >0.7 → WR=0%, avg=-1.4% (импульс продолжается)
- Добавить: загружать тейп в момент сигнала, считать ratio → если вне [0.35, 0.70] → SKIP
- Данных пока мало (35 из 344) → нужно накопить 100+ tape-покрытых сделок

**Гипотеза 2 — US сессия приоритет:**
- US 16-24: WR=75.2%, avg=+0.686% vs EU 08-16: WR=66.8%, avg=+0.304%
- Возможно ограничить только US в F.2 если live данные подтвердят

**Гипотеза 3 — Пары-лидеры:**
- KAT, FLOKI, GALA, PEOPLE стабильно WR>77% с нормальным n
- В F.2 возможен whitelist топ-10 пар вместо всего universe

**Гипотеза 4 — CVD перед входом:**
- CVD нейтральный или в сторону ожидаемого отскока → лучшие результаты
- Требует tape stream в реальном времени (аналог C.5 из pump)

---

### 🔒 Фаза F.3 — Real Trading (после F.2, WR>70% live, PF>1.8)
- AUTO_TRADE=true
- Leverage 5× (avg_net 0.478% × 5 = ~2.4% на сделку)
- Требует средства на OKX счёте

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
- Реальные деньги — ни на одном треке, пока paper не доказал критерии
- Telegram парсер каналов — не в текущей фазе, в BACKLOG

**Pump Engine (до прохождения Phase C):**
- Параметрический тюнинг ws_pump_orchestrator — бэктест доказал потолок, не тратить время
- Trailing SL — после paper данных Phase C
- Шорты по DUMP — нет backtest данных
- Dynamic pair switching — нет рабочей базы
- Leverage — paper x1, живые деньги только в Phase D
- Ликвидации канал — неоднозначная интерпретация, defer
- Multi-exchange — только после Phase C WR>60%
- NewsStream (CryptoPanic) — шаг C.12, последним

**BB Fade (до прохождения F.1):**
- Tape buy_ratio фильтр в прод — ждём 100+ tape-покрытых live сделок
- US-only режим — гипотеза, нужно live подтверждение
- Whitelist пар — после 50+ labeled
- Leverage — только в F.3, после WR>70% live

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
