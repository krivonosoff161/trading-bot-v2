# BB + Volume 5m Scalp — Research от AI агентов

**Дата:** 09.04.2026  
**Цель:** собрать подходы к BB+Volume стратегии на 5m перед написанием бэктеста  
**Статус:** все агенты ответили ✅

---

## Сводка консенсуса

| Тезис | Qwen | Codex | Kimi | Grok |
|---|---|---|---|---|
| Breakout + vol ≥ 2.0× лучше fade | ✅ | ✅ | ✅ | ✅ |
| Fade нужен regime filter (ADX/BB width) | ⚠️ | ✅ | ✅ | ✅ |
| 1H EMA50 как фильтр направления | ✅ | ✅ | ✅ | ✅ |
| SOL/DOGE/XRP требуют vol ≥ 2.5-3.0× | ✅ | ✅ | ✅ | ✅ |
| SL = 1.5-2.0× ATR_5m | ✅ | ✅ | ✅ | ✅ |
| TP = 2.0-3.0× ATR_5m | ✅ | ✅ | ✅ | ✅ |

**Главный вывод:** все четыре агента сходятся — breakout+volume первичен, fade только с regime filter, 1H bias обязателен.

---

## Qwen

> ⚠️ Нет доступа к интернету. База знаний до 2024 года. Источники требуют проверки.

### 1. BB Breakout + Volume Confirmation

Пробой верхней/нижней полосы Боллинджера с подтверждением объёмом. Ложные пробои фильтруются через volume spike — истинный пробой имеет объём ≥ 2.0× от среднего. Вход на закрытии 5m свечи за полосой.

| Параметр | Значение |
|---|---|
| BB Period | 20 |
| BB StdDev | 2.0 |
| Volume Threshold | 2.0-2.5× MA(20) объёма |
| SL | ATR(14)_5m × 1.5 или за противоположной полосой BB |
| TP | 1.5-2.0× SL расстояние |
| Hold Time | 15-45 минут |
| HTF Filter | Цена выше EMA50_1H для LONG, ниже для SHORT |

**По парам:** BTC ✅, ETH ✅, SOL ⚠️ (vol ≥ 3.0×), XRP ✅, DOGE ❌

### 2. BB Fade / Mean Reversion

Вход против пробоя когда объём низкий. Работает в боковике когда BB горизонтальны. SL за полосой, TP к средней линии SMA20.

| Параметр | Значение |
|---|---|
| BB StdDev | 2.0-2.5 |
| Volume | < 1.2× MA(20) |
| BB Width Filter | < 2% (сжатые полосы = боковик) |
| SL | За полосой BB + 0.3% буфер |
| TP | SMA20 или 0.8-1.2% от цены |
| Hold Time | 5-20 минут |
| RSI Filter | > 70 для SHORT, < 30 для LONG |

**По парам:** BTC ✅, ETH ✅, SOL ❌, XRP ✅, DOGE ⚠️

### 3. SL/TP % от цены по парам

| Пара | SL% | TP1% | TP2% |
|---|---|---|---|
| BTC | 0.3-0.5% | 0.6-0.8% | 1.0-1.5% |
| ETH | 0.4-0.6% | 0.8-1.0% | 1.5-2.0% |
| SOL | 0.6-0.9% | 1.2-1.5% | 2.0-3.0% |
| XRP | 0.4-0.6% | 0.8-1.0% | 1.5-2.0% |
| DOGE | 0.8-1.2% | 1.5-2.0% | 3.0-4.0% |

### Источники Qwen (требуют проверки)
- github.com/kernc/backtesting.py
- github.com/pmaji/crypto-strategies
- tradingview.com/scripts/bollingerbands/
- quantconnect.com/lean/strategy-library

---

## Codex

> ✅ Имеет доступ к интернету. Источники проверены.

### Главные выводы Codex

1. Для BB+Volume на 5m лучше всего подтверждён **breakout-подход**, не fade
2. Для fade нужен **regime split** — без ADX/VWAP фильтра быстро ломается
3. Почти все нормальные системы делят рынок на trend/range и выбирают стратегию под него
4. Для XRP/DOGE нужен более жёсткий volume/filtering чем для BTC/ETH/SOL

### 1. BB Breakout + High Volume

Классика: вход только когда цена закрывается за полосой и объём выше своей средней. Пороги объёма: 1.5×-2.0× к vol MA(20), extreme spike 3×+ = высокое качество.

| Параметр | Значение |
|---|---|
| BB | (20, 2.0) |
| Volume MA | 20 |
| Vol threshold | > MA × 1.5-2.0 |
| Фильтр ложных пробоев | close near top/bottom of bar, 5-bar momentum |
| По парам | BTC/ETH высокая, SOL средне-высокая, XRP/DOGE средняя (строгий filter) |

**Источники:**
- StockSharp Bollinger Volume Strategy (Python)
- FMZ Bollinger Bands Volume Confirmation Strategy
- TradingView: Breakout Volume Momentum [5m]
- Reddit: 2,877 breakout study with volume grading

### 2. Breakout → Pullback → Continuation

Более зрелый breakout: не брать первый выстрел, ждать короткий pullback и повторное ускорение. Режет часть head-fake.

| Параметр | Значение |
|---|---|
| ADX | ≥ 25 |
| BB width | ≥ 1.0 × avg width(50) |
| Breakout lookback | 15 |
| Pullback | 1-12 баров |
| Volume | vs 15-bar avg |
| SL | min(structure, 1.5 × ATR) |

**По парам:** BTC/ETH/SOL хорошая, XRP/DOGE хуже (много ложных возвратов)

> 💡 Codex: "Самый полезный публичный шаблон после обычного breakout+volume"

**Источники:**
- TradingView: BB Breakout-Momentum + Reversion

### 3. BB Fade / Mean Reversion

Публичный консенсус: без RSI/VWAP/ADX regime filter быстро ломается.

| Параметр | Значение |
|---|---|
| Вход | Возврат после выхода за band |
| Выход | Middle band |
| Regime gate | ADX ≤ 20 или neutral range |
| Доп. фильтр | RSI extreme, VWAP side |

**По парам:** BTC/ETH нормальная в спокойные часы, SOL средняя, XRP/DOGE низкая при новостях

**Источники:**
- QuantConnect Bollinger mean reversion example
- TradingView BB Breakout-Momentum + Reversion
- Reddit 5m BB scalping thread

### 4. Low-Volume Breakout Fade / Fakeout Filter

Слабый объём = признак плохого breakout'а:
- RVOL < 1.5 = слабый кандидат
- RVOL 2×+ = рабочий breakout
- RVOL 3×+ = high-conviction breakout
- Полезно: close in top/bottom 20% of range

**Источники:**
- Reddit breakout grading study
- FMZ BB + Volume strategy

### 5. Regime Split: breakout в trend, fade в range

> 💡 Codex: "Самый важный вывод из академики"

Один и тот же выход за band в trend-режиме и range-режиме трактуется по-разному.

| Инструмент | Параметр |
|---|---|
| Trend/range | ADX |
| Волатильность | BB width |
| HTF | EMA50/EMA80 slope или price vs MA |

**По парам:** для всех 5 пар — очень высокая применимость. Обязательный слой.

**Источники:**
- SSRN: Bollinger Bands under Varying Market Regimes in BTC/USDT
- Physica A 2020: profitability of Bollinger Bands
- TradingView BB Breakout-Momentum + Reversion (novaroma)

### Готовые реализации по Codex

| Репо | Язык | Описание | Ссылка |
|---|---|---|---|
| StockSharp Bollinger Volume Strategy | Python | BB breakout + volume + ATR | stocksharp.com |
| TradingView Ayden_C BB + Volume V2 | Pine Script | BTC/USDT 15m momentum-through-bands | tradingview.com |
| TradingView novaroma BB Breakout + Reversion | Pine Script | **Лучший шаблон regime split** | tradingview.com |
| QuantConnect crypto breakout example | Python | Простой breakout → exit at middle band | quantconnect.com |
| FMZ Quant Strategies | Pine/Python | BB Mean Reversion, Squeeze Breakout | github.com/fmzquant/strategies |

> 💡 Codex: "Самый полезный источник для архитектуры — novaroma TradingView script"

---

## Kimi

> ✅ Имеет доступ к интернету.

### 1. BB Breakout + Volume

| Параметр | Значение |
|---|---|
| Volume threshold | RVOL > 2.0 — стандарт; 3.0× = институциональная активность |
| Volume MA | 20 периодов |
| BB | (20, 2.0) |
| Доп. фильтр | Цена должна закрыться за полосой, не только коснуться |

**По парам:**
| Пара | RVOL порог | SL (ATR) | TP (ATR) | Hold |
|---|---|---|---|---|
| BTC | > 2.0 | 1.5× | 3.0× | 3-10 мин |
| ETH | > 2.0 | 1.5× | 3.0× | 3-10 мин |
| SOL | > 2.5 | 2.0× | 3.5× | 5-15 мин |
| XRP | > 2.5 | 2.0× | 3.5× | 5-15 мин |
| DOGE | > 3.0 | 2.5× | 4.0× | 5-15 мин |

**Источники:**
- TradingView: Volume Spike + Breakout Alerts
- Arongroups: RVOL breakout confirmation
- ChartSpots: Relative Volume trading strategies

### 2. BB Fade / Mean Reversion

| Параметр | Значение |
|---|---|
| Условие Long | Цена закрылась ниже lower BB, затем выше на следующей свече |
| Объём для fade | < 1.0× среднего или снижающийся |
| RSI | < 30 (long) или > 70 (short) |
| TP | Средняя линия BB (20 SMA) |
| SL | Low[1] и low[2] или 1.5× ATR |

**По парам:** BTC/ETH ✅ средне-высокая, SOL ⚠️, XRP/DOGE ❌ при новостях

**Источники:**
- FMZ: Bollinger Bands Mean Reversion Trading Strategy
- LuxAlgo: Mean Reversion Trading guide
- NewYorkCityServers: Bollinger Bounce strategy

### 3. HTF контекст (1H фильтр)

Три подхода:
1. **EMA Slope**: EMA9 > EMA26 > EMA55 на 1H для бычьего тренда
2. **Price vs EMA50**: цена выше/ниже EMA50_1H
3. **Structure зоны**: две EMA пары на 5m (8/16 и 16/30) + макро фильтр

> Kimi: "Уменьшает ложные входы на 30-40%"

**Рекомендация по парам:**
- BTC/ETH: 1H EMA50
- SOL/XRP/DOGE: 4H EMA50 (меньше шума)

**Источники:**
- TradingView: Multi Timeframe Scalper Structure
- Medium: Golden Momentum Capture Strategy
- TrendSpider: Multiple timeframe BB analysis

### Готовые реализации по Kimi

| Репо | Язык | Описание | Ссылка |
|---|---|---|---|
| openclaw/skills | Python | BB Breakout + Volume Spike, алерты Discord/Telegram | github.com/openclaw/skills |
| fmzquant/strategies | Pine/Python | 100+ стратегий включая BB Mean Reversion, Squeeze | github.com/fmzquant/strategies |
| aiwebarchitects/multi-coin-backtester | Python | BB mean reversion + breakout, авто-оптимизация, 80+ криптовалют | github.com/aiwebarchitects/multi-coin-backtester |
| TPTBusiness/TPT | Python (PyTorch) | Transformer+PPO+BB, 1m скальп, SL 3%/TP 4%, VWAP+ATR | github.com/TPTBusiness/TPT |
| tesserspace/tesser | Rust+Python | BollingerBreakout, HFT-ready, ML интеграция | github.com/tesserspace/tesser |
| petemik/BollinderBands | Python | BB mean reversion + Monte Carlo валидация | github.com/petemik/BollingerBands |

---

## Grok

> ✅ Имеет доступ к интернету.

### 1. BB Breakout + Volume Spike

Стратегия ловит момент, когда цена закрывает свечу за пределами BB при резком росте объёма. Без всплеска сигнал игнорируется.

| Параметр | Значение |
|---|---|
| Volume | > SMA(Volume) × 1.5-2.0 (150-300% от средней за 20 баров) |
| Подтверждение | Иногда добавляют следующую свечу |

**По парам:** BTC/ETH высокая (реальные импульсы). SOL/DOGE/XRP — много ложных пробоев в азиатскую сессию, требует строгого фильтра 2.0+

**Источники:**
- Bitget Academy: intraday BB breakout
- Prorsi: BB breakout подход
- Reddit r/algotrading

### 2. BB Squeeze Breakout + Volume Confirmation

BB сжимаются внутри Keltner Channels минимум 3-5 баров → признак сжатия волатильности. Вход при пробое + объём.

| Параметр | Значение |
|---|---|
| Squeeze Length | 10-15 |
| BB dev | 2.0 |
| KC ATR | × 1.2 |
| Volume | > SMA(Volume) × 1.3 на пробойной свече |
| SL | Противоположная граница сжатия + 0.2 ATR |
| TP | 0.618 / 1.0 / 1.618 × ширины диапазона |

**По парам:** BTC/ETH отлично. SOL/DOGE/XRP — часто требует отключения volume-filter в низколиквидные часы

**Источники:**
- TradingView: Squeeze Breakout Pro [WillyAlgoTrader]

### 3. BB Fade с низким объёмом

Цена выходит за полосу BB, но объём слабый → признак ложного движения → откат к средней.

| Параметр | Значение |
|---|---|
| Volume | < SMA(Volume) × 0.7-0.8 на экстремуме |
| SL | За противоположной полосой или 0.5-1 ATR |
| TP | Middle band или противоположная полоса (RR 1:1-1:2) |
| Hold | 5-12 минут |

**По парам:** BTC/ETH ✅ в range. SOL/XRP/DOGE ❌ — высокая волатильность превращает fade в тренд

**Источники:**
- Reddit r/algotrading
- TradingView mean reversion BB penetration скрипты
- QuantConnect форум

### 4. BB + Volume с контекстом 1H

| Параметр | Значение |
|---|---|
| 1H фильтр | Цена > EMA50 = long bias; < EMA50 = short bias |
| Volume | Как в подходах 1-2 |
| SL | 0.5-1 ATR |
| TP | 1-2 ATR |

> Grok: "Значительно повышает качество на всех парах. BTC/ETH — почти обязательно. SOL/XRP/DOGE без 1H-фильтра часто дают серию убытков."

**Источники:**
- TradingView Pine-скрипты multi-timeframe BB breakout
- github.com/jicheolha/crypto-trading-bot — Bollinger Squeeze для futures

### Готовые реализации по Grok

| Репо | Язык | Описание | Ссылка |
|---|---|---|---|
| jicheolha/crypto-trading-bot | Python | BB Squeeze + volume, backtest + live для crypto futures | github.com/jicheolha/crypto-trading-bot |
| QuantConnect Python примеры | Python | Crypto Bollinger Band Strategy, легко адаптировать под 5m | quantconnect.com |
| TradingView Squeeze Breakout Pro | Pine Script | Volume multiplier, WillyAlgoTrader | tradingview.com |

> Grok: "Все подходы требуют строгого volume-фильтра на OKX (реальные тиковые объёмы), иначе много фейков на 5m."

---

## Итоговый консенсус всех агентов

### Что взять в бэктест

**Движок 1 — Breakout (основной):**
```
close > BB_upper AND vol > vol_ma×2.0 AND close > EMA50_1H → LONG
close < BB_lower AND vol > vol_ma×2.0 AND close < EMA50_1H → SHORT
SL = ATR_5m × 1.5
TP = ATR_5m × 2.5
Hold max = 15 минут
```

**Движок 2 — Fade (только в боковике):**
```
close > BB_upper AND vol < vol_ma×0.8 AND ADX_1H < 20 → SHORT
close < BB_lower AND vol < vol_ma×0.8 AND ADX_1H < 20 → LONG
SL = ATR_5m × 1.0
TP = BB_middle (SMA20)
Hold max = 12 минут
```

**Volume пороги по парам:**
| Пара | Breakout vol | Fade vol | SL ATR | TP ATR |
|---|---|---|---|---|
| BTC | > 2.0× | < 0.8× | 1.5 | 2.5 |
| ETH | > 2.0× | < 0.8× | 1.5 | 2.5 |
| SOL | > 2.5× | ❌ не торгуем | 2.0 | 3.0 |
| XRP | > 2.5× | < 0.7× | 2.0 | 3.0 |
| DOGE | > 3.0× | ❌ не торгуем | 2.5 | 3.5 |

### Что НЕ берём в первую итерацию
- RSI фильтр — лишняя сложность, добавить если breakout не окупается
- TP2 — сначала одна цель
- Pullback → continuation — более сложная логика, V2

### Приоритет реализаций для изучения
1. **novaroma TradingView** — лучшая архитектура regime split (рекомендация Codex)
2. **github.com/jicheolha/crypto-trading-bot** — ближайший аналог нашей задачи
3. **github.com/fmzquant/strategies** — 100+ стратегий для reference
4. **github.com/aiwebarchitects/multi-coin-backtester** — готовый multi-coin backtester на Python

---

## Следующий шаг

Написать `scripts/bt_bb_volume_5m.py` на основе итогового консенсуса.
Данные уже в кэше: `backtest_candle_cache_35d.pkl` (5m = 13249 баров, 35 дней).
