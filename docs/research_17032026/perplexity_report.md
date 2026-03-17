# Perplexity Research Report — 17.03.2026
**Тема:** Новая стратегия, управление позицией, LLM архитектура

---

## Блок 1 — Логика сигнала

### Комбинации индикаторов
- EMA 9/21 + RSI(14) + ADX(14>20), R:R=1:2, BTC Binance Futures 2019-2024 → ~55-60% winrate
- EMA pullback: EMA 9/14/20 + Stoch RSI (oversold 25 / overbought 85), BTC futures 1H 2023-2024
- Источник: FMZ 471697, FMZ 444024

### EMA/ADX/RSI пороги
- ADX(14) > 20 — минимум тренда, > 25 — агрессивный фильтр
- RSI(14): для тренд-следящих входов — лонг при RSI < 60, шорт при RSI > 40
- EMA: 9/21 для сигнала, 50 как HTF контекст
- Источник: FMZ 471697, YouTube 4000+ сделок BTC

### Volume фильтр
- Breakout: Volume >= 1.5x SMA(Volume, 20) — стандарт
- Агрессивный: Volume >= 2x SMA(Volume, 20)
- "Слабый откат по объёму" — убрать из mandatory цепочки
- Источник: TradingView volume scripts, FMZ 495593

### Multi-TF архитектура
- 4H: EMA50 направление + ADX > 20
- 1H: EMA9/21 тренд + ADX > 20
- 15m: сетап (pullback к EMA9/14)
- 5m: триггер (breakout + объём)
- Источник: FMZ 484095

---

## Блок 2 — Управление позицией

### SL
- ATR-based SL лучше фиксированного
- Initial SL: 1.8-2.2x ATR(14) от входа
- R:R минимум 1:2, лучше 1:2.5-3
- Источник: defi.holisticactions.com, Binance guides

### Trailing stop
- Chandelier Exit: N=22, K=2.5-3 для входа, K=2.0 для trailing
- REST polling 10-20s даёт ~2-3% потери от идеала — приемлемо
- Источник: QuantifiedStrategies, Binance

### Частичная фиксация
- TP1: 40-50% позиции на 2R
- TP2: 50-60% на 3R + Chandelier trailing
- Источник: defi.holisticactions.com

### Break-even
- Переносить после 1.5R + частичный выход TP1
- Не переносить раньше TP1
- Источник: Binance risk guides

---

## Блок 3 — LLM

### Роль LLM
- LLM = оркестратор/интерпретатор, НЕ генератор сигнала
- Получает: JSON с индикаторами + algo_signal из кода
- Возвращает: JSON decision + text_comment
- algo_signal=NO_TRADE → LLM обязан NO_TRADE
- Источник: GitHub ai-trade, qrak/LLM_trader

### Gemma 3 27B
- Нет специализированных финансовых бенчмарков
- Хорошие общие метрики, доступна через Yandex AI Studio
- Использовать temperature 0-0.2
- Источник: ArtificialAnalysis, HuggingFace

### Prompt engineering
- Жёсткий JSON-шаблон с примерами
- SYSTEM: "algo_signal=NO_TRADE → decision=NO_TRADE всегда"
- Короткий reasoning (≤300 символов)
- Pydantic валидация ответа

---

## Блок 4 — Мультибиржа

### Библиотеки
- ccxt (async) — унификация для market data и нормализации символов
- Нативные API — для специфичных order features (attachAlgoOrds, trailing)
- Freqtrade поддерживает OKX/Bybit/Binance futures официально
- NautilusTrader — более тяжёлая event-driven платформа

### Арбитраж
- Cross-exchange спреды 0.1-2.5% на majors
- Funding арбитраж реален но требует мониторинга
- При $1000-5000 — комиссии съедают большую часть
- **Вывод: в бэклог, не сейчас**

---

## 3 Стратегии

### Стратегия 1: Enhanced EMA + RSI + ADX
- EMA9/21 + RSI(14) + ADX(14>20), R:R=1:2
- Winrate: ~55-60%, BTC 2019-2024
- Источник: FMZ 471697

### Стратегия 2: EMA20 pullback + Stoch RSI
- EMA9/14/20 + Stoch RSI (25/85)
- Winrate: ~55-60%, BTC 1H 2023-2024
- Источник: FMZ 444024

### Стратегия 3: Volume-confirmed breakout + ADX
- 20-bar high/low + ADX>25 + Volume>=1.5x
- Winrate: ~55-60%
- Источник: moltbook.com, TradingView

---

## Рекомендованная архитектура

1. Data Layer: REST polling 10-30s, 4H/1H/15m/5m
2. Signal Engine: алгоритмический, формирует candidate pack
3. LLM Orchestrator: классифицирует/объясняет по algo_signal
4. Execution: native OKX API, лимитки + reduce-only TP + hard SL
5. Position Manager: trailing + BE автомат
6. Telegram: уведомления на каждое событие
7. Risk: 1.5% на сделку, 3 позиции макс, daily stop 3-5%
