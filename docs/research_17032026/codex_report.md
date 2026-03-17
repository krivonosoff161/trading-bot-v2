# Codex (OpenAI) Research Report — 17.03.2026
**Тема:** Верифицированные данные с бирж, ATR по парам, корреляции, архитектура

---

## Ключевые находки (верифицировано реальными данными с API)

### Реальный ATR(14, 15m) на 17.03.2026
| Пара | ATR 15m | ATR% от цены |
|---|---|---|
| BTC | ~411.7 | ~0.56% |
| ETH | ~17.43 | ~0.75% |
| SOL | ~0.666 | ~0.70% |
| XRP | ~0.0130 | ~0.90% |
| DOGE | ~0.000711 | ~0.71% |

**Вчерашние стопы:**
- BTC SL = 324$ = **0.79 ATR** — объективно tight (должно быть 1.6-2.3x)
- SOL SL = 2.26$ = 3.4x ATR — нормально, но позиция открылась в плохой момент
- DOGE SL = 0.0019$ = 2.7x ATR — приемлемо

---

## Корреляции (30-дневные, Binance данные)
| Пара | Корреляция |
|---|---|
| BTC-ETH | **0.924** |
| ETH-XRP | **0.958** |
| BTC-SOL | **0.902** |
| DOGE-XRP | **0.896** |

**Вывод:** BTC/ETH/SOL/XRP/DOGE = ОДИН кластер риска.
Нельзя держать 5 однонаправленных позиций как "диверсификацию".
**Максимум: 2 позиции одновременно.**

---

## Пересечения пар на биржах
BTC, ETH, SOL, XRP, DOGE — есть на всех трёх (OKX, Bybit, Binance). ✅

---

## Блок 1 — Логика сигнала

### Главный вывод
Провал был не из-за недостатка фильтров, а из-за слишком длинной цепочки hard conditions.
Нужны 3 слоя: **regime → setup → trigger**, а не 5 жёстких подтверждений подряд.

### Параметры
- **ADX**: порог 18-20 (не 25!) + растущий
- **EMA**: 21/55 (не 20/50)
- **RSI**: > 52 для лонга, < 48 для шорта (не 70/30)
- **5m триггер**: ОПЦИОНАЛЬНЫЙ, не обязательный gate

### Winrate источники
- MACD + StochRSI + RSI + SuperTrend + MA cross: **75.9% winrate** (ETH_USDT, FMZ 483683)
- H1/H4/D trend score + RSI14 + MACD + ATR exits: **56.67% winrate** (FMZ 484095)
- WazirX paper: **77% accuracy** при движениях > 1.5%

---

## Блок 2 — Управление позицией

### SL по ATR
- BTC/ETH: **1.6-1.8x ATR15**
- SOL/XRP/DOGE: **1.9-2.3x ATR15**
- Формула: `max(k × ATR15, structure_stop + wick_buffer)`

### Trailing
- Exchange-side hard SL/TP при открытии позиции
- REST polling 10s: только для trail update и state sync
- % trailing хуже ATR trailing для крипто

### Частичная фиксация
- TP1: **1R** → закрыть часть
- TP2: **2.5R**
- Остаток: Chandelier/ATR trail
- FMZ 483683 использует 6 partial TPs по 15% каждый

### Break-even
- Не переносить до TP1 + candle-close confirmation
- После TP1: SL оставшейся позиции → в безубыток

---

## Блок 3 — LLM

### Главное правило
**LLM не может создать сделку если algo_signal=NO_TRADE.**

### JSON схема
```json
{
  "algo_signal": "LONG|SHORT|NO_TRADE",
  "decision": "LONG|SHORT|NO_TRADE",
  "confidence": 0.0-1.0,
  "failed_rules": [],
  "orders": [{
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "LIMIT",
    "price": 73569.0,
    "sl": 72537.0,
    "tp1": 74605.0,
    "tp2": 75124.0
  }],
  "text_comment": "Краткий вывод ≤300 символов"
}
```

### Gemma 3 27B
- Нет публичного finance-specific benchmark
- A/B тестировать против YandexGPT 5 Pro, DeepSeek-R1, Qwen3-235B
- Gemma → analyst/vision layer; order decision → под validator

### FailSafeQA данные
- o3-mini фабриковала информацию в **41%** финансовых тестов
- Palmyra-Fin-128k потеряла robustness в **17%** тестов
- "Модель уверенно рассуждает" ≠ "безопасна для ордера"

---

## Блок 4 — Мультибиржа

### Рекомендация
- ccxt: market data + symbol normalization
- Native APIs: order placement, attachAlgoOrds, trailing, position sync
- Freqtrade: официально OKX/Bybit/Binance futures
- NautilusTrader: тяжёлая event-driven платформа (не для нас сейчас)

### Арбитраж (реальные данные 17.03.2026)
- BTC спред OKX-Bybit-Binance: **max 0.013%** — после комиссий ноль
- SOL funding: OKX -0.0024%, Bybit +0.0099%, Binance -0.0015%
- **Вывод: арбитраж нерентабелен при текущем депозите**

---

## Блок 5 — Risk Management

### Параметры для $1000-5000
- Риск на сделку: **0.5-0.75%** ($5-7.5 на $1000)
- Макс одновременных позиций: **2**
- Макс из одного кластера: **1** (BTC или ETH, не оба)
- Дневной стоп: **-2R или -3%**
- Недельный стоп: **-6R или -8%**

---

## Рекомендованная архитектура

```
REST collectors (15s): свечи, volume, positions, orders
Feature engine: EMA21/55, ADX14, ATR14/22, RSI14, Volume ratio, Chandelier
Regime engine: 1H trend aligned + ADX>18 rising + ATR не мёртвый
Setup engine 15m: pullback / breakout (5m опциональный)
LLM layer: candidate pack → JSON с rules
Validator: R:R>=2, SL в ATR bounds, риск≤cap, нет корреляционной перегрузки
Execution: native API, entry + reduce-only TP + hard SL
Position manager: после TP1 → Chandelier trail → BE
Risk manager: 0.5-0.75%, 2 позиции, daily/weekly stop
24/7: asyncio tasks, persistent state, watchdog, heartbeat Telegram
```

---

## Deployable Strategy (синтез)
```
Вход:
- 1H: EMA21 > EMA55 + ADX > 18 rising
- 15m: pullback к EMA21, RSI > 52 (лонг)
- Breakout volume >= 1.5x SMA20

Выход:
- SL: 1.8-2.2x ATR15 (BTC/ETH), 2.1x (SOL/XRP/DOGE)
- TP1: 1R → 50% закрыть
- TP2: 2.5R + Chandelier 3x ATR trailing
```
